# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""Saneo puntual de las Bank Transactions duplicadas por prefijo de pasarela 94/094.

El mismo movimiento entra dos veces: por la pasarela con la referencia base
(818256476285) y por el extracto del banco con un prefijo (94818256476285 o
094818256476285). El par se identifica por referencia prefijada + cuenta bancaria +
empresa + monto + fecha.

Los prefijos van FIJOS a propósito: esto es una limpieza histórica puntual de un lote
conocido, no el barrido recurrente. El recurrente es
`mint.apis.reconciliation.daily_remove_exact_duplicates`, que sí respeta la
configuración por descripción de `Mint Bank Description Rule`.

Criterio de baja (el mismo de `remove_duplicate_bank_transactions`):

- Solo se toca la copia que NO está en uso. "En uso" no es solo `allocated_amount`:
  ese campo vale 0 en los borradores y también mientras `add_payment_entries` deja
  las filas hijas en `0.0  # Temporary` antes de que `allocate_payment_entries` las
  reparta. Por eso el filtro mira además `status` y la existencia de filas en
  `tabBank Transaction Payments`, y se re-verifica releyendo el documento justo
  antes de tocarlo (la consulta es un snapshot y aquí se commitea por fila).
- Se prefiere dar de baja la prefijada 94/094; si esa está en uso, la base.
- Se CANCELA (docstatus=2) y la baja definitiva va aparte y SIN `force`: si algo la
  enlaza (`DB Bancaribe Log.bank_transaction`, `amended_from`) se conserva cancelada
  en vez de dejar referencias colgando. Cancelada ya sale de la detección de
  colisiones, que filtra por `docstatus < 2`.
"""

import frappe
from frappe.utils import flt

from mint.apis.mint_log import log_mint_error

# Estados de Bank Transaction que implican conciliación en curso o cerrada.
IN_USE_STATUSES = ("Reconciled", "Settled")

SAVEPOINT_CANCEL = "dup_94_cancel"
SAVEPOINT_DELETE = "dup_94_delete"


def _is_free(allocated, status, linked_rows) -> bool:
    """La copia no está en uso y se puede dar de baja sin romper una conciliación."""
    return flt(allocated) == 0 and status not in IN_USE_STATUSES and not linked_rows


def pick_removable(pair) -> str | None:
    """Cuál de las dos copias del par se puede dar de baja, o None si ninguna.

    Se prefiere la prefijada 94/094 (es la que sobra); si esa está conciliada, se da
    de baja la base. Si las dos están en uso, el par se deja intacto para revisión
    manual.
    """
    if _is_free(pair.get("prefixed_allocated"), pair.get("prefixed_status"), pair.get("prefixed_rows")):
        return pair.get("prefixed_name")

    if _is_free(pair.get("base_allocated"), pair.get("base_status"), pair.get("base_rows")):
        return pair.get("base_name")

    return None


def find_prefixed_pairs(amount_field: str) -> list:
    """Pares (prefijada 94/094, base) del mismo movimiento con al menos una copia libre.

    El filtro de "en uso" va en la consulta a propósito: los pares donde las dos
    copias están conciliadas no tienen nada que hacer aquí y no se traen.
    """
    # amount_field solo puede ser "withdrawal" o "deposit" (literales de execute()).
    return frappe.db.sql(
        f"""
        SELECT
            t1.name AS prefixed_name,
            TRIM(t1.reference_number) AS prefixed_ref,
            t1.allocated_amount AS prefixed_allocated,
            t1.status AS prefixed_status,
            (SELECT COUNT(*) FROM `tabBank Transaction Payments` p
              WHERE p.parent = t1.name) AS prefixed_rows,
            t2.name AS base_name,
            TRIM(t2.reference_number) AS base_ref,
            t2.allocated_amount AS base_allocated,
            t2.status AS base_status,
            (SELECT COUNT(*) FROM `tabBank Transaction Payments` p
              WHERE p.parent = t2.name) AS base_rows
        FROM `tabBank Transaction` t1
        INNER JOIN `tabBank Transaction` t2
            ON (TRIM(t1.reference_number) = CONCAT('94', TRIM(t2.reference_number))
                OR TRIM(t1.reference_number) = CONCAT('094', TRIM(t2.reference_number)))
            AND t1.bank_account = t2.bank_account
            AND t1.company = t2.company
            AND ROUND(t1.{amount_field}, 2) = ROUND(t2.{amount_field}, 2)
            AND t1.date = t2.date
        WHERE t1.{amount_field} > 0
          AND t1.docstatus < 2
          AND t2.docstatus < 2
          AND t2.reference_number IS NOT NULL
          AND TRIM(t2.reference_number) != ''
          AND (
                (t1.allocated_amount = 0
                    AND t1.status NOT IN ('Reconciled', 'Settled')
                    AND NOT EXISTS (SELECT 1 FROM `tabBank Transaction Payments` p
                                     WHERE p.parent = t1.name))
                OR
                (t2.allocated_amount = 0
                    AND t2.status NOT IN ('Reconciled', 'Settled')
                    AND NOT EXISTS (SELECT 1 FROM `tabBank Transaction Payments` p
                                     WHERE p.parent = t2.name))
          )
        ORDER BY t1.name
        """,
        as_dict=True,
    )


def _remove(target: str) -> tuple:
    """Neutraliza y, si nadie lo enlaza, borra. Devuelve (cancelado, borrado, aviso)."""
    try:
        frappe.db.savepoint(SAVEPOINT_CANCEL)
        doc = frappe.get_doc("Bank Transaction", target)

        # Re-verificación contra el estado ACTUAL: la consulta es un snapshot y abajo
        # se commitea por fila, así que entre medio la copia pudo conciliarse o ya
        # haberse dado de baja al resolver otro par (cadenas 94 + 094 sobre la misma
        # base).
        if doc.docstatus == 2:
            frappe.db.rollback(save_point=SAVEPOINT_CANCEL)
            return 0, 0, None

        if not _is_free(doc.allocated_amount, doc.status, len(doc.payment_entries)):
            frappe.db.rollback(save_point=SAVEPOINT_CANCEL)
            return 0, 0, f"{target}: quedó en uso después de la consulta; se omite"

        cancelled = 0
        if doc.docstatus == 1:
            doc.flags.ignore_permissions = True
            doc.cancel()
            cancelled = 1

        frappe.db.commit()
    except frappe.DoesNotExistError:
        frappe.db.rollback(save_point=SAVEPOINT_CANCEL)
        return 0, 0, None
    except Exception:
        frappe.db.rollback(save_point=SAVEPOINT_CANCEL)
        log_mint_error(
            title=f"Error neutralizando duplicado 94/094 {target}",
            description=frappe.get_traceback(),
        )
        return 0, 0, f"{target}: no se pudo neutralizar; ver Mint Log"

    # Baja definitiva aparte y SIN force: si algo lo enlaza se conserva cancelado en
    # vez de dejar referencias colgando (DB Bancaribe Log, amended_from).
    try:
        frappe.db.savepoint(SAVEPOINT_DELETE)
        frappe.delete_doc("Bank Transaction", target, ignore_permissions=True)
        frappe.db.commit()
        return cancelled, 1, None
    except Exception:
        frappe.db.rollback(save_point=SAVEPOINT_DELETE)
        estado = "cancelado" if cancelled else "en borrador"
        return cancelled, 0, f"{target}: {estado}, pero no se pudo borrar (tiene enlaces); se conserva"


def execute():
    """Da de baja las Bank Transactions duplicadas por los prefijos 94/094."""
    total_cancelled = 0
    total_deleted = 0
    errors = []
    processed = set()

    for amount_field in ("withdrawal", "deposit"):
        pairs = find_prefixed_pairs(amount_field)
        cancelled = deleted = 0

        for pair in pairs:
            target = pick_removable(pair)
            if not target or target in processed:
                continue

            processed.add(target)
            row_cancelled, row_deleted, warning = _remove(target)
            cancelled += row_cancelled
            deleted += row_deleted
            if warning:
                errors.append(warning)

        print(
            f"Duplicados 94/094 en {amount_field}: {len(pairs)} pares, "
            f"{cancelled} cancelados, {deleted} borrados"
        )
        total_cancelled += cancelled
        total_deleted += deleted

    print(
        f"Total 94/094: {total_cancelled} cancelados, {total_deleted} borrados, "
        f"{len(errors)} avisos"
    )
    for warning in errors:
        print(f" - {warning}")

    if errors:
        log_mint_error(
            title="Avisos del saneo de duplicados 94/094",
            description="\n".join(errors),
        )
