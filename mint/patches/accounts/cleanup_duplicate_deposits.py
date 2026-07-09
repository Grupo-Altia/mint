"""
Patch: saneo histórico de depósitos bancarios duplicados (x100 y exactos).

Contraparte de una sola vez del barrido nocturno
`mint.apis.reconciliation.cancel_exact_duplicate_deposits` (que cubre solo los
duplicados EXACTOS de forma recurrente). Este patch, además, corrige los gemelos
x100: el extracto con coma decimal sin separador de miles ('11453,57') se parseaba
x100 — flt('11453,57') devuelve 1145357.0 borrando la coma — creando un Bank
Transaction inflado por cada depósito. El fix de raíz (que evita nuevos x100) vive
en el parser del import; este patch sanea los ya creados.

Por cada grupo (TRIM(reference_number) + bank_account + company con >1 registro,
deposit>0, docstatus<2) se clasifica y actúa:

  DELETE_BIG : el grande (x100) NO tiene asignación -> cancelar+borrar el grande,
               conservar el pequeño correcto.
  FIX_BIG    : el grande carga la asignación del MONTO CORRECTO y el pequeño está
               sin asignar -> corregir deposit del grande al valor real (con
               frappe.db.set_value, sin tocar el enlace del Payment Entry ni el
               GL) + borrar el pequeño redundante.
  EXACT_DUP  : dos depósitos con el MISMO monto (ratio ~1) = el mismo movimiento
               importado dos veces (la referencia es el ID único del banco) ->
               conservar el que tenga asignación (o cualquiera si ninguno) y
               CANCELAR (docstatus=2) el redundante SIN asignar — no se borra: un BT
               cancelado queda fuera de la detección (docstatus<2) y se preserva su
               enlace de auditoría (p. ej. logs de integración bancaria), que sí
               bloquea el delete. Si AMBOS están asignados -> MANUAL.
  MANUAL     : cnt!=2, ratio ni-decimal-ni-1, ambos asignados, o asignación de
               tamaño raro -> se omite y se reporta para revisión manual.

Idempotente: tras una corrida, cada referencia queda con un único Bank Transaction
vivo, así que re-ejecutar (o cada migrate) no encuentra nada que tocar salvo los
MANUAL. Cada grupo se aísla con un savepoint: un error en uno no revierte el lote
ni aborta la migración.

Ejecución:
    bench --site <site> migrate
    -- o manualmente (EJECUTA):
    bench --site <site> execute mint.patches.accounts.cleanup_duplicate_deposits.execute
    -- previsualizar sin mutar:
    bench --site <site> execute mint.patches.accounts.cleanup_duplicate_deposits.execute \
        --kwargs "{'dry_run': True}"
"""
import frappe
from frappe.utils import flt

_DECIMAL_RATIOS: tuple[int, ...] = (10, 100, 1000)
_COMMIT_EVERY: int = 200


def _is_decimal_ratio(ratio: float) -> bool:
    """¿El cociente grande/pequeño es ~x10/x100/x1000 (firma del error decimal)?"""
    return any(abs(ratio - r) < (0.5 if r == 10 else 1) for r in _DECIMAL_RATIOS)


def _duplicate_groups() -> list:
    return frappe.db.sql(
        """
        SELECT TRIM(reference_number) AS ref, bank_account, company, COUNT(*) AS cnt
        FROM `tabBank Transaction`
        WHERE deposit > 0 AND docstatus < 2
          AND reference_number IS NOT NULL AND TRIM(reference_number) != ''
        GROUP BY TRIM(reference_number), bank_account, company
        HAVING cnt > 1
        """,
        as_dict=True,
    )


def _members(group) -> list:
    return frappe.db.sql(
        """
        SELECT name, deposit, allocated_amount, unallocated_amount, status, docstatus
        FROM `tabBank Transaction`
        WHERE TRIM(reference_number) = %s AND bank_account = %s AND company = %s
          AND deposit > 0 AND docstatus < 2
        ORDER BY deposit ASC
        """,
        (group.ref, group.bank_account, group.company),
        as_dict=True,
    )


def _classify(members):
    """Devuelve (accion, conservar, actuar, motivo).

    Para x100 (DELETE_BIG/FIX_BIG): conservar=pequeño correcto, actuar=grande inflado.
    Para EXACT_DUP: conservar=el asignado (o cualquiera si ninguno), actuar=el
    redundante SIN asignar (que se cancela).
    """
    if len(members) != 2:
        return "MANUAL", None, None, f"cnt={len(members)} (no es par)"
    small, big = members[0], members[1]  # ordenados por deposit asc
    mn, mx = flt(small.deposit), flt(big.deposit)
    if mn <= 0:
        return "MANUAL", small, big, "deposit pequeño <= 0"

    small_alloc, big_alloc = flt(small.allocated_amount), flt(big.allocated_amount)

    # Duplicado EXACTO (mismo monto): el mismo movimiento importado dos veces. Se
    # conserva el que tenga asignación y se cancela el redundante SIN asignar. Si ambos
    # están asignados, no se puede tocar (rompería un pago conciliado) -> MANUAL.
    if abs(mx - mn) < 0.01:
        if small_alloc >= 0.01 and big_alloc >= 0.01:
            return "MANUAL", small, big, "duplicado exacto pero ambos asignados"
        # el redundante a cancelar es uno SIN asignación; se conserva el otro.
        if big_alloc < 0.01:
            return "EXACT_DUP", small, big, "duplicado exacto; se cancela el redundante sin asignar"
        return "EXACT_DUP", big, small, "duplicado exacto; se cancela el redundante sin asignar"

    ratio = mx / mn
    if not _is_decimal_ratio(ratio):
        return "MANUAL", small, big, f"ratio {ratio:.2f} no decimal ni 1"
    if big_alloc < 0.01:
        return "DELETE_BIG", small, big, "grande sin asignación"
    if small_alloc >= 0.01:
        return "MANUAL", small, big, "ambos con asignación"
    if abs(big_alloc - mn) / mn < 0.05:
        return "FIX_BIG", small, big, "grande asignado, monto correcto"
    return "MANUAL", small, big, f"asignación grande {big_alloc:.2f} != pequeño {mn:.2f}"


def _delete_bt(name: str) -> None:
    """Cancela (si está enviado) y borra un Bank Transaction sin asignaciones."""
    doc = frappe.get_doc("Bank Transaction", name)
    if doc.docstatus == 1:
        doc.cancel()
    frappe.delete_doc("Bank Transaction", name, ignore_permissions=True)


def _neutralize_redundant(name: str) -> None:
    """Saca de circulación un depósito redundante (EXACT_DUP) sin borrarlo: si está
    enviado, lo CANCELA (docstatus=2 → queda fuera de la detección de duplicados y se
    preserva su enlace de auditoría, p. ej. un log de integración bancaria, que
    bloquearía el delete). Si es borrador, lo borra (no hay enlace de submit que
    proteger)."""
    doc = frappe.get_doc("Bank Transaction", name)
    if doc.docstatus == 1:
        doc.cancel()
    else:
        frappe.delete_doc("Bank Transaction", name, ignore_permissions=True)


def execute(dry_run: bool = False) -> None:
    """Por defecto EJECUTA (dry_run=False): así lo invoca el runner de patches.
    Para previsualizar sin mutar, pasar --kwargs "{'dry_run': True}".
    """
    mode = "DRY-RUN (no muta)" if dry_run else "EJECUCIÓN (muta)"
    print(f"\n=== Saneo depósitos duplicados (x100 y exactos) — {mode} ===\n")

    groups = _duplicate_groups()
    counts = {"DELETE_BIG": 0, "FIX_BIG": 0, "EXACT_DUP": 0, "MANUAL": 0}
    manual: list = []
    errors: list = []
    processed = 0

    for group in groups:
        members = _members(group)
        action, small, big, reason = _classify(members)
        counts[action] += 1

        if action == "MANUAL":
            manual.append((group.ref, group.cnt, reason))
            continue
        if dry_run:
            continue

        try:
            frappe.db.savepoint("dup_clean")
            if action == "DELETE_BIG":
                _delete_bt(big.name)  # gemelo x100 inflado (basura): cancelar+borrar
            elif action == "EXACT_DUP":
                _neutralize_redundant(big.name)  # redundante: cancelar (preserva enlaces)
            else:  # FIX_BIG
                real = flt(small.deposit, 2)
                alloc = flt(big.allocated_amount, 2)
                unalloc = max(0.0, flt(real - alloc, 2))
                frappe.db.set_value(
                    "Bank Transaction",
                    big.name,
                    {
                        "deposit": real,
                        "unallocated_amount": unalloc,
                        "status": "Reconciled" if unalloc <= 0.001 else "Unreconciled",
                    },
                )
                _delete_bt(small.name)
            processed += 1
            if processed % _COMMIT_EVERY == 0:
                frappe.db.commit()
                print(f"  ... {processed} procesados")
        except Exception as exc:  # noqa: BLE001 — aislar por grupo, no abortar migración
            frappe.db.rollback(save_point="dup_clean")
            errors.append((group.ref, action, str(exc)[:150]))

    if not dry_run:
        frappe.db.commit()

    verb = "a aplicar" if dry_run else "aplicados"
    print(f"\n  DELETE_BIG (borrar grande x100)       : {counts['DELETE_BIG']} {verb}")
    print(f"  FIX_BIG    (corregir grande + borrar) : {counts['FIX_BIG']} {verb}")
    print(f"  EXACT_DUP  (cancelar redundante exacto): {counts['EXACT_DUP']} {verb}")
    print(f"  MANUAL     (omitidos)                 : {counts['MANUAL']}")
    if not dry_run:
        print(f"  Procesados OK: {processed}   Errores: {len(errors)}")

    if manual:
        print(f"\n  -- {len(manual)} grupos MANUAL (revisar a mano):")
        for ref, cnt, reason in manual[:50]:
            print(f"     ref={ref} cnt={cnt}: {reason}")
        if len(manual) > 50:
            print(f"     ... y {len(manual) - 50} más")

    if errors:
        print(f"\n  -- {len(errors)} ERRORES:")
        for ref, action, err in errors[:20]:
            print(f"     {action} ref={ref}: {err}")

    if not dry_run:
        remaining = frappe.db.sql(
            """
            SELECT COUNT(*) FROM (
              SELECT 1 FROM `tabBank Transaction`
              WHERE deposit > 0 AND docstatus < 2
                AND reference_number IS NOT NULL AND TRIM(reference_number) != ''
              GROUP BY TRIM(reference_number), bank_account, company
              HAVING COUNT(*) > 1
            ) t
            """
        )[0][0]
        print(
            f"\n=== Verificación: {remaining} grupos duplicados restantes "
            f"(esperado == MANUAL = {counts['MANUAL']}) ===\n"
        )
