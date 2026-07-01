"""Motor ÚNICO de conciliación de cobros contra su depósito bancario.

Este módulo es ahora el dueño de TODA la lógica de conciliación (migrada desde
l10n_ve.overrides.payment_reconciliation, que quedó como shim de compatibilidad).
l10n_ve aporta solo lo fiscal (CustomPaymentEntry: IGTF/impuestos), que se invoca
como métodos del doc en runtime. domina_isp reacciona al estado conciliado.

Regla central (solo para Payment Entry de tipo cobro / Receive):
  - Efectivo / Gateway: se aprueban sin exigir depósito (por su naturaleza).
  - Cualquier otro modo (banco): NO se puede aprobar hasta conciliarse con el
    depósito bancario cuyo número de referencia coincide con la referencia del
    cobro. Al conciliar, el monto AUTORITATIVO es el del depósito.

REGLAS DE FORMATO de referencia por banco (data-driven, sin nombres quemados):
  Algunos bancos le entregan al cliente una referencia distinta a la que cae en el
  banco de la empresa. La regla vive en el banco ORIGEN del cobro
  (Payment Entry.source_bank → Bank.bank_reference_rule → DocType `Bank Reference Rule`,
  campo `rule` = expresión Python con la variable `reference_number`). Se aplica SIEMPRE
  HACIA ADELANTE (referencia del depósito → referencia del cobro) con apply_format_rule
  vía safe_eval. No hay reversa: para hallar el depósito de un cobro se aplica la regla a
  los depósitos candidatos y se compara (forward-search), lo que también soporta reglas
  lossy como "últimos 8".
"""
from __future__ import annotations

import frappe
import json
from frappe import _
from frappe.utils import flt
from frappe.utils.safe_exec import safe_eval
from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import (
    get_linked_payments as erpnext_get_linked_payments,
)
from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import (
    check_matching,
)

try:
    import mint.mint.utils.matching
    original_get_matching_rules = mint.mint.utils.matching.get_matching_rules
except ImportError:
    original_get_matching_rules = None


# ════════════════════════════════════════════════════════════════════════════
# Constantes del motor (migradas de l10n_ve)
# ════════════════════════════════════════════════════════════════════════════

RECON_PENDING = "Conciliación Pendiente"
RECON_DONE = "Conciliado"
RECON_REVIEW = "Revisar"

# Motivos por los que un cobro queda en RECON_REVIEW. Los consume el detalle de la
# anomalía en el dashboard (domina_isp get_payment_review_detail) para explicar qué revisar.
REVIEW_OTHER_BANK = "other_bank"
REVIEW_DUPLICATE_REFERENCE = "duplicate_reference"

# Modos que no exigen depósito bancario para aprobarse.
CASH_LIKE_TYPES = ("Cash", "Gateway", "Gangway")

# Tolerancia de redondeo (en moneda de la empresa, VEF) admitida al comparar el
# depósito real contra el total del pago. SOLO positiva: "céntimo por encima,
# nunca por debajo". Ajustable si aparecen falsos bloqueos por redondeo USD↔VEF.
DEPOSIT_COVERAGE_TOLERANCE = 1.0

DEPOSIT_FIELDS = ["name", "deposit", "unallocated_amount", "currency", "bank_account"]


# ════════════════════════════════════════════════════════════════════════════
# Reglas de formato de referencia por banco (data-driven vía Bank Reference Rule)
# ════════════════════════════════════════════════════════════════════════════

# Builtins seguros expuestos a las expresiones de las reglas. Son funciones puras de
# formateo; safe_eval ya bloquea imports/dunders/atributos peligrosos. Ampliar aquí si
# se crean reglas que necesiten más (sin agregar nada con efectos de sistema).
_SAFE_BUILTINS = {
    "str": str,
    "int": int,
    "float": float,
    "len": len,
    "abs": abs,
    "round": round,
    "max": max,
    "min": min,
    "format": format,
}


def apply_format_rule(rule_name, reference_number):
    """Aplica una `Bank Reference Rule` a una referencia, HACIA ADELANTE
    (referencia del depósito → referencia del cobro).

    `rule_name` es el nombre de un `Bank Reference Rule` (el valor de
    Bank.bank_reference_rule). Su campo `rule` es una expresión Python con la variable
    `reference_number`, p. ej. `f'{reference_number}{reference_number}'`. Se evalúa
    sandboxeada con safe_eval (sin nombres de regla quemados en el código). Devuelve la
    referencia original (strip) si no hay regla o si la expresión falla.
    """
    ref = str(reference_number or "").strip()
    if not rule_name:
        return ref
    rule_expr = frappe.get_cached_value("Bank Reference Rule", rule_name, "rule")
    if not rule_expr:
        return ref
    try:
        return str(
            safe_eval(
                rule_expr,
                eval_globals=dict(_SAFE_BUILTINS),
                eval_locals={"reference_number": ref},
            )
        )
    except Exception:
        frappe.log_error(
            title="Bank Reference Rule inválida: {0}".format(rule_name),
            message=frappe.get_traceback(),
        )
        return ref


def custom_get_matching_rules(bank_transaction_doc):
    """Parche (Monkey Patch): filtra reglas de Mint por banco."""
    if not original_get_matching_rules:
        return []

    all_rules = original_get_matching_rules(bank_transaction_doc)
    filtered = []
    current_bank = bank_transaction_doc.bank_account

    for rule in all_rules:
        rule_bank = frappe.db.get_value("Mint Bank Transaction Rule", rule.name, "bank_account")

        if not rule_bank or rule_bank == current_bank:
            filtered.append(rule)

    return filtered


# Aplicar parche (interno de mint, sobre su propio módulo utils.matching)
if original_get_matching_rules:
    mint.mint.utils.matching.get_matching_rules = custom_get_matching_rules


# ════════════════════════════════════════════════════════════════════════════
# Clasificación de cobros / cobertura (migrado de l10n_ve)
# ════════════════════════════════════════════════════════════════════════════

def _is_bank_receive(doc) -> bool:
    """True si es un cobro que exige depósito bancario (no efectivo/gateway)."""
    if doc.payment_type != "Receive":
        return False
    mop_type = (
        frappe.get_cached_value("Mode of Payment", doc.mode_of_payment, "type")
        if doc.mode_of_payment
        else None
    )
    return mop_type not in CASH_LIKE_TYPES


def _is_cash_like_receive(doc) -> bool:
    """True si es un cobro en efectivo/gateway: no exige depósito bancario; se aprueba
    con el monto tecleado y se aplica directo a la factura (modos privados de oficina)."""
    if doc.payment_type != "Receive":
        return False
    mop_type = (
        frappe.get_cached_value("Mode of Payment", doc.mode_of_payment, "type")
        if doc.mode_of_payment
        else None
    )
    return mop_type in CASH_LIKE_TYPES


def deposit_covers_payment(
    payment_entry_name: str,
    tolerance: float = DEPOSIT_COVERAGE_TOLERANCE,
) -> bool:
    """¿El pago está realmente cubierto por lo que entró al banco?

    Fuente ÚNICA de verdad para decidir si un pago puede marcarse conciliado.
    Compara en moneda de la empresa (VEF):

      Σ(allocated_amount de los depósitos bancarios conciliados al Payment Entry)
          >= base_paid_amount del Payment Entry  - tolerancia

    Devuelve True si el pago es efectivo/gangway (no exige depósito), si el total
    del PE es 0, o si la suma de depósitos conciliados cubre el total. Devuelve
    False si existe un depósito pero queda corto, o si todavía no hay depósito
    suficiente conciliado al PE.
    """
    pe = frappe.db.get_value(
        "Payment Entry",
        payment_entry_name,
        ["base_paid_amount", "mode_of_payment"],
        as_dict=True,
    )
    if not pe:
        return False

    mop_type = (
        frappe.get_cached_value("Mode of Payment", pe.mode_of_payment, "type")
        if pe.mode_of_payment
        else None
    )
    if mop_type in CASH_LIKE_TYPES:
        return True

    pe_total = flt(pe.base_paid_amount, 2)
    if pe_total <= 0:
        return True

    deposited = flt(
        frappe.db.get_value(
            "Bank Transaction Payments",
            {
                "payment_entry": payment_entry_name,
                "payment_document": "Payment Entry",
                "docstatus": 1,
            },
            "sum(allocated_amount)",
        )
        or 0,
        2,
    )

    return deposited + flt(tolerance) >= pe_total


def _cobro_bank_account(doc) -> str | None:
    """Bank Account (cuenta bancaria) del cobro, derivada de su cuenta GL."""
    bank_gl_acc = doc.paid_to or doc.paid_from
    if not bank_gl_acc:
        return None
    return frappe.db.get_value("Bank Account", {"account": bank_gl_acc})


def _deposit_base_filters(doc) -> dict | None:
    """Filtros comunes: referencia exacta + moneda del cobro + saldo disponible."""
    ref = str(doc.reference_no or "").strip()
    if not ref:
        return None
    filters = {
        "reference_number": ref,
        "docstatus": 1,
        "deposit": [">", 0],
        "unallocated_amount": [">", 0.001],
    }
    paid_currency = (doc.paid_on_currency or "").strip()
    if paid_currency:
        filters["currency"] = paid_currency
    return filters


def _first_deposit(filters: dict) -> frappe._dict | None:
    """Primer depósito que cumple los filtros, prefiriendo el de MENOR monto.

    Si por la referencia coexisten varios Bank Transaction (p. ej. un gemelo x100
    por error de parseo del extracto bancario), se concilia contra el de menor
    'deposit' para no usar el inflado. Defensa en profundidad frente a duplicados.
    """
    rows = frappe.get_all(
        "Bank Transaction",
        filters=filters,
        fields=DEPOSIT_FIELDS,
        order_by="deposit asc",
        limit=1,
    )
    return rows[0] if rows else None


def _find_deposit_by_source_bank_rule(doc) -> frappe._dict | None:
    """Fallback de matching por REGLA DE BANCO, HACIA ADELANTE.

    Si el match exacto falla, se aplica la regla de limpieza a la referencia de
    cada depósito CANDIDATO (misma cuenta/moneda/saldo) y se compara con la
    referencia del cobro.
    Si el cobro tiene `source_bank`, se usa solo esa regla. Si no, se prueban
    todas las reglas configuradas en los bancos del sistema.
    """
    target_ref = str(doc.reference_no or "").strip()
    if not target_ref:
        return None
    bank_account = _cobro_bank_account(doc)
    if not bank_account:
        return None

    cand_filters = {
        "docstatus": 1,
        "deposit": [">", 0],
        "unallocated_amount": [">", 0.001],
        "bank_account": bank_account,
    }
    paid_currency = (doc.paid_on_currency or "").strip()
    if paid_currency:
        cand_filters["currency"] = paid_currency

    candidates = frappe.get_all(
        "Bank Transaction",
        filters=cand_filters,
        fields=DEPOSIT_FIELDS + ["reference_number"],
        order_by="deposit asc",
    )
    if not candidates:
        return None

    rules_to_check = []
    source_bank = doc.get("source_bank")
    if source_bank:
        rule_name = frappe.get_cached_value("Bank", source_bank, "bank_reference_rule")
        if rule_name:
            rules_to_check.append(rule_name)
    
    if not rules_to_check:
        banks_with_rules = frappe.get_all("Bank", filters={"bank_reference_rule": ["is", "set"]}, fields=["bank_reference_rule"])
        rules_to_check = list(set(b.bank_reference_rule for b in banks_with_rules))

    for cand in candidates:
        for rule_name in rules_to_check:
            if apply_format_rule(rule_name, cand.reference_number) == target_ref:
                return cand
                
    return None


def find_matching_deposit(doc) -> frappe._dict | None:
    """Depósito (Bank Transaction) que concilia el cobro: misma referencia, misma
    moneda, MISMA cuenta bancaria, y con saldo disponible.

    Se exige la misma cuenta a propósito: si el depósito de la referencia cayó en
    otro banco, NO concilia — es una anomalía a revisar (ver find_deposit_other_bank).

    Si el match exacto falla, intenta el fallback por la regla del banco origen
    (_find_deposit_by_source_bank_rule), aplicada hacia adelante.
    """
    filters = _deposit_base_filters(doc)
    if filters is None:
        return None
    bank_account = _cobro_bank_account(doc)
    if bank_account:
        filters["bank_account"] = bank_account
    deposit = _first_deposit(filters)
    if deposit:
        return deposit
    return _find_deposit_by_source_bank_rule(doc)


def find_deposit_other_bank(doc) -> frappe._dict | None:
    """Depósito con la referencia+moneda del cobro pero en OTRO banco: anomalía.

    El cliente pagó (existe el depósito) pero cayó en una cuenta distinta a la del
    cobro. No se concilia; se marca el cobro para revisión interna ('Revisar Pago').
    """
    filters = _deposit_base_filters(doc)
    if filters is None:
        return None
    bank_account = _cobro_bank_account(doc)
    if not bank_account:
        return None
    filters["bank_account"] = ["!=", bank_account]
    return _first_deposit(filters)


def find_duplicate_deposits(doc) -> list:
    """Depósitos NO cancelados que comparten la referencia del cobro en SU MISMA
    cuenta bancaria y empresa. Si la lista trae más de uno es una COLISIÓN: la
    referencia está duplicada y la conciliación debe DETENERSE hasta que se borre o
    cancele el depósito incorrecto — no se elige "el más chico" a dedo (esa heurística
    anti-x100 era justo la que colapsaba el cobro al gemelo equivocado).

    Mismo criterio que el patch de saneo cleanup_duplicate_x100_deposits
    (TRIM(reference_number) + bank_account + company, deposit>0, docstatus<2) para que
    la guardia en vivo y la limpieza por lote cuenten lo mismo. El TRIM cubre las
    referencias LEGACY con espacios/saltos pegados (el import actual ya las normaliza a
    solo-dígitos en bank_statement_import, así que la data nueva entra limpia); un match
    exacto dejaría escapar esas legacy. El TRIM impide usar el índice de
    reference_number, pero la consulta queda acotada por bank_account (indexado), así
    que el costo es bajo; se podrá soltar el TRIM cuando se normalicen las legacy.
    docstatus<2 excluye los cancelados (status 'Cancelled'): por eso CANCELAR el
    duplicado —no solo borrarlo— ya libera la conciliación.
    """
    ref = str(doc.reference_no or "").strip()
    bank_account = _cobro_bank_account(doc)
    if not ref or not bank_account:
        return []
    return frappe.db.sql(
        """
        SELECT name, deposit, unallocated_amount, status, docstatus
        FROM `tabBank Transaction`
        WHERE TRIM(reference_number) = %(ref)s
          AND bank_account = %(bank_account)s
          AND company = %(company)s
          AND deposit > 0
          AND docstatus < 2
        ORDER BY deposit ASC
        """,
        {"ref": ref, "bank_account": bank_account, "company": doc.company},
        as_dict=True,
    )


def _apply_deposit_amount(doc, deposit: frappe._dict) -> None:
    """Fija el monto del cobro al del depósito (monto autoritativo del banco).

    El monto se registra en paid_currency_amount (en paid_on_currency), que es el
    monto de pago real, y se replica en paid_amount/received_amount para que la
    asignación y el GL queden consistentes.

    Caso de una sola factura (o sin referencias). El reparto cuando el depósito
    cubre varias facturas se aborda en la bifurcación de asignación (3.2); por eso
    aquí se bloquea explícitamente el multi-referencia en lugar de adivinar.
    """
    if len(doc.references or []) > 1:
        frappe.throw(
            _(
                "Este cobro tiene varias facturas asociadas; el reparto del depósito "
                "entre ellas aún no está implementado en este flujo."
            )
        )

    company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
    deposit_currency = deposit.currency or company_currency

    # Saldo disponible del depósito, en SU moneda (la búsqueda ya garantizó que
    # coincide con la del cobro): para uno fresco == total; para uno parcial, el
    # resto sin asignar.
    deposit_amount = flt(deposit.unallocated_amount, 2)
    doc.paid_currency_amount = deposit_amount

    if deposit_currency == company_currency:
        # Cobro/depósito en moneda local: bolívares con bolívares.
        doc.paid_on_currency = company_currency
        doc.exchange_rate = 1.0
        doc.paid_amount = deposit_amount
        doc.received_amount = deposit_amount
    else:
        # Cobro/depósito en divisa (p. ej. USD): el monto base es el contravalor a
        # la tasa del cobro; el IGTF se recalcula sobre el monto en divisa.
        rate = flt(doc.exchange_rate) or 1.0
        base = flt(deposit_amount * rate, 2)
        doc.paid_amount = base
        doc.received_amount = base

    # Forzar recálculo del IGTF sobre el nuevo monto: calculate_igtf_taxes solo lo
    # recompone si igtf_amount viene vacío.
    doc.igtf_amount = 0
    doc.total_with_igtf = 0

    if doc.references:
        ref = doc.references[0]
        outstanding = flt(ref.outstanding_amount, 2)
        ref.allocated_amount = (
            min(deposit_amount, outstanding) if outstanding > 0 else deposit_amount
        )


@frappe.whitelist()
def reconcile_and_approve(payment_entry: str) -> dict:
    """Intenta conciliar el cobro con su depósito y, si lo encuentra, fija el
    monto del cobro al del depósito y lo aprueba. Llamada por el botón del
    formulario y por el disparador desde el lado del depósito bancario.
    """
    doc = frappe.get_doc("Payment Entry", payment_entry)

    if doc.docstatus != 0:
        frappe.throw(_("Solo se puede conciliar un cobro en borrador."))
    if not _is_bank_receive(doc):
        frappe.throw(_("Solo aplica a cobros bancarios (no efectivo/gateway)."))

    # Colisión de referencia: si hay más de un depósito con la misma referencia en la
    # cuenta del cobro, NO se elige uno a dedo. Se detiene, se marca para revisión
    # ('Revisar' → badge "Revisar Pago" en el dashboard) y se devuelve el detalle.
    duplicates = find_duplicate_deposits(doc)
    if len(duplicates) > 1:
        if doc.get("custom_reconciliation_status") != RECON_REVIEW:
            doc.db_set("custom_reconciliation_status", RECON_REVIEW)
        return {
            "reconciled": False,
            "review": True,
            "reason": REVIEW_DUPLICATE_REFERENCE,
            "duplicates": [d.name for d in duplicates],
            "message": _(
                "No se puede conciliar: hay {0} depósitos con la referencia {1} en la "
                "cuenta del cobro. Borre o cancele el incorrecto y reintente."
            ).format(len(duplicates), doc.reference_no or ""),
        }

    deposit = find_matching_deposit(doc)
    if not deposit:
        anomaly = find_deposit_other_bank(doc)
        if anomaly:
            # El depósito de la referencia existe pero cayó en OTRO banco: anomalía.
            # No se aprueba; se marca para revisión interna (badge "Revisar Pago"
            # en el dashboard). El cobro del portal sigue "en proceso".
            doc.db_set("custom_reconciliation_status", RECON_REVIEW)
            return {
                "reconciled": False,
                "review": True,
                "reason": REVIEW_OTHER_BANK,
                "bank_transaction": anomaly.name,
                "message": _(
                    "El depósito de la referencia {0} cayó en otra cuenta bancaria "
                    "({1}). El cobro quedó marcado para revisión."
                ).format(doc.reference_no or "", anomaly.bank_account),
            }
        if doc.get("custom_reconciliation_status") != RECON_PENDING:
            doc.db_set("custom_reconciliation_status", RECON_PENDING)
        return {
            "reconciled": False,
            "message": _("Aún no se encontró un depósito con la referencia {0}.").format(
                doc.reference_no or ""
            ),
        }

    _apply_deposit_amount(doc, deposit)
    doc.custom_reconciliation_status = RECON_DONE
    doc.save(ignore_permissions=True)
    # Cachear lo ya verificado aquí para que before_submit_receive_payment (que corre
    # dentro de doc.submit()) no repita las consultas: el depósito encontrado y que la
    # referencia NO está duplicada.
    doc.flags.l10n_ve_matched_deposit_doc = deposit
    doc.flags.l10n_ve_duplicates_checked = True
    doc.submit()

    # Enlazar el depósito al cobro: la conciliación bancaria (y la activación del
    # servicio, vía el hook on_change de Bank Transaction de domina_isp) proceden de aquí.
    _link_deposit_to_payment(deposit.name, doc.name)

    return {
        "reconciled": True,
        "amount": flt(deposit.unallocated_amount, 2),
        "bank_transaction": deposit.name,
        "message": _("Cobro conciliado y aprobado por {0} (depósito {1}).").format(
            flt(deposit.unallocated_amount, 2), deposit.name
        ),
    }


def _link_deposit_to_payment(bank_transaction_name: str, payment_entry_name: str) -> None:
    """Enlaza el Bank Transaction (depósito) al Payment Entry ya aprobado.

    Guardar el Bank Transaction con el pago enlazado dispara su hook on_change,
    que (en domina_isp) concilia la línea del ISP Payment Entry y, si corresponde,
    paga la factura y activa el servicio. Idempotente y tolerante a depósitos sin
    saldo libre.
    """
    bt = frappe.get_doc("Bank Transaction", bank_transaction_name)
    already = any(
        row.payment_entry == payment_entry_name and row.payment_document == "Payment Entry"
        for row in bt.payment_entries
    )
    if already or flt(bt.unallocated_amount) <= 0:
        return
    bt.add_payment_entries(
        [{"payment_doctype": "Payment Entry", "payment_name": payment_entry_name}]
    )

    # Forzar mapeo automático del tercero antes de guardar, ya que en ciertas
    # secuencias de auto-conciliación el hook on_update_after_submit llega tarde.
    pe = frappe.db.get_value(
        "Payment Entry", payment_entry_name,
        ["reference_no", "party_type", "party", "source_bank"], as_dict=True,
    )
    if pe:
        if not bt.party_type and pe.party_type:
            bt.db_set("party_type", pe.party_type)
        if not bt.party and pe.party:
            bt.db_set("party", pe.party)
        
        # Guardar también la referencia origen si aplica usando las reglas globales
        original_ref = str(bt.reference_number or "").strip()
        if pe.reference_no and not bt.source_bank_reference_rule and original_ref and pe.reference_no != original_ref:
            rules_to_check = []
            if pe.source_bank:
                rule_name = frappe.db.get_value("Bank", pe.source_bank, "bank_reference_rule")
                if rule_name:
                    rules_to_check.append(rule_name)
            
            if not rules_to_check:
                banks_with_rules = frappe.get_all("Bank", filters={"bank_reference_rule": ["is", "set"]}, fields=["bank_reference_rule"])
                rules_to_check = list(set(b.bank_reference_rule for b in banks_with_rules))
                
            for rule in rules_to_check:
                if apply_format_rule(rule, original_ref) == pe.reference_no:
                    bt.db_set("source_bank_reference_rule", pe.reference_no)
                    break

    # Forzar actualización del estado visual, ya que bt.save actualiza clearance_date
    # silenciosamente por db.set_value sin disparar hooks del Payment Entry.
    clearance_date, current_status = frappe.db.get_value(
        "Payment Entry", payment_entry_name, ["clearance_date", "custom_reconciliation_status"]
    )
    if clearance_date and current_status != RECON_DONE:
        frappe.db.set_value("Payment Entry", payment_entry_name, "custom_reconciliation_status", RECON_DONE, update_modified=False)


def strip_leading_quote_from_reference(doc, method):
    """
    Strips leading single quotes from reference numbers (common in bank exports like Bancamiga .xls)
    to prevent string matching errors in rules and reconciliation.
    """
    if doc.reference_number is not None:
        ref = str(doc.reference_number).strip()
        if ref.endswith(".0"):
            ref = ref[:-2]
        if ref.startswith("'"):
            ref = ref[1:]
        doc.reference_number = ref


def before_submit_receive_payment(doc, method=None) -> None:
    """Al aprobar un cobro bancario: buscar su depósito por referencia y conciliar.

    - Si NO hay un depósito con esa referencia en la cuenta del cobro → error
      (no se aprueba).
    - Si lo hay → se usa el MONTO DEL DEPÓSITO (no el tecleado) y se recalculan
      los montos base; el depósito se enlaza en on_submit.

    El botón "Intentar conciliar" (reconcile_and_approve) hace lo mismo de forma
    explícita desde el formulario.
    """
    if not _is_bank_receive(doc):
        return

    # Colisión de referencia: detener la aprobación si hay más de un depósito con la
    # misma referencia en la cuenta del cobro. El operador debe borrar o cancelar el
    # incorrecto antes de aprobar (no se concilia contra uno elegido a dedo).
    # reconcile_and_approve ya lo verificó antes de submit: no repetir la consulta.
    duplicates = [] if doc.flags.get("l10n_ve_duplicates_checked") else find_duplicate_deposits(doc)
    if len(duplicates) > 1:
        dup_list = ", ".join("{0} ({1})".format(d.name, d.deposit) for d in duplicates)
        frappe.throw(
            _(
                "Este cobro no puede aprobarse: hay {0} depósitos bancarios con la "
                "referencia {1} en la cuenta del cobro. Borre o cancele el depósito "
                "incorrecto y reintente la conciliación. Depósitos: {2}."
            ).format(len(duplicates), frappe.bold(doc.reference_no or ""), dup_list)
        )

    deposit = doc.flags.get("l10n_ve_matched_deposit_doc") or find_matching_deposit(doc)
    if not deposit:
        frappe.throw(
            _(
                "Este cobro no puede aprobarse: no se encontró un depósito bancario "
                "con la referencia {0} en la cuenta del cobro. Verifique la referencia "
                "y la cuenta bancaria, o espere a que el depósito ingrese."
            ).format(frappe.bold(doc.reference_no or ""))
        )

    # El monto autoritativo es el del banco: fijarlo y recalcular TODO
    # (bases, montos después de impuestos, monto en letras y descripción), para
    # que ningún campo quede apuntando al monto tecleado original. calculate_igtf_taxes
    # / set_amounts / ... son métodos del CustomPaymentEntry de l10n_ve (runtime).
    _apply_deposit_amount(doc, deposit)
    doc.calculate_igtf_taxes()  # recalcula IGTF si el cobro es en divisa
    doc.set_amounts()
    doc.set_amounts_after_tax()
    doc.set_total_in_words()
    doc.set_remarks()
    doc.custom_reconciliation_status = RECON_DONE
    doc.flags.l10n_ve_matched_deposit = deposit.name


def on_submit_receive_payment(doc, method=None) -> None:
    """Tras aprobar el cobro, enlazar su depósito bancario: el guardado del Bank
    Transaction dispara la conciliación y la activación por sus hooks."""
    if not _is_bank_receive(doc):
        return

    bt_name = doc.flags.get("l10n_ve_matched_deposit")
    if not bt_name:
        dep = find_matching_deposit(doc)
        bt_name = dep.name if dep else None
    if bt_name:
        _link_deposit_to_payment(bt_name, doc.name)


def on_cancel_receive_payment(doc, method=None) -> None:
    """Al cancelar un cobro, se resetea su estado de conciliación visual para evitar
    confusión en documentos cancelados."""
    if doc.get("custom_reconciliation_status") != RECON_PENDING:
        doc.custom_reconciliation_status = RECON_PENDING
        doc.db_set("custom_reconciliation_status", RECON_PENDING)


def on_change_payment_entry(doc, method=None) -> None:
    """Si se reconcilia o desconcilia desde la Transacción Bancaria u otra herramienta,
    sincroniza el estado visual con la existencia de la fecha de liquidación."""
    if doc.docstatus == 1:
        if doc.clearance_date and doc.get("custom_reconciliation_status") != RECON_DONE:
            doc.db_set("custom_reconciliation_status", RECON_DONE, update_modified=False)
        elif not doc.clearance_date and doc.get("custom_reconciliation_status") != RECON_PENDING:
            doc.db_set("custom_reconciliation_status", RECON_PENDING, update_modified=False)



def validate_bank_transaction_duplicate(doc, method=None) -> None:
    """No permite dos depósitos con la misma referencia en la misma cuenta
    bancaria y empresa.

    Un import duplicado del extracto (misma referencia con el monto distinto,
    p. ej. por decimales corridos) rompe la conciliación por referencia: el
    sistema puede ver la referencia "ya conciliada" en el depósito equivocado.
    El monto NO entra en la clave a propósito: justo queremos atrapar la misma
    referencia con cualquier monto. Solo aplica a depósitos no cancelados.

    Solo corre al CREAR: previene duplicados nuevos del import. En updates (p. ej.
    al conciliar, que guarda el Bank Transaction) no debe correr, para no bloquear
    operaciones sobre duplicados legacy ya existentes.
    """
    if not doc.is_new():
        return
    ref = str(doc.reference_number or "").strip()
    if not ref or flt(doc.deposit) <= 0:
        return

    duplicate = frappe.db.exists(
        "Bank Transaction",
        {
            "name": ["!=", doc.name or ""],
            "reference_number": ref,
            "bank_account": doc.bank_account,
            "company": doc.company,
            "deposit": [">", 0],
            "docstatus": ["<", 2],
        },
    )
    if duplicate:
        duplicate_link = frappe.utils.get_link_to_form("Bank Transaction", duplicate)
        frappe.throw(
            _(
                "Ya existe un depósito con la referencia {0} en esta cuenta bancaria "
                "({1}). No se permiten depósitos duplicados con la misma referencia; "
                "revise el extracto importado."
            ).format(frappe.bold(ref), duplicate_link)
        )


def reconcile_drafts_for_deposit(doc, method=None) -> None:
    """Disparador desde el depósito: al confirmarse un Bank Transaction, intenta
    aprobar los cobros en borrador que esperaban esa referencia.

    Se ejecuta en background (enqueue_after_commit) para no bloquear el guardado
    del depósito.
    """
    ref = str(doc.reference_number or "").strip()
    if not ref or flt(doc.deposit) <= 0:
        return

    frappe.enqueue(
        "mint.apis.reconciliation.reconcile_drafts_job",
        queue="short",
        reference=ref,
        enqueue_after_commit=True,
    )


def reconcile_drafts_job(reference: str) -> None:
    """Aprueba los cobros en borrador cuya referencia coincide con el depósito.

    Unificado: busca por referencia EXACTA y también por la referencia del depósito
    transformada HACIA ADELANTE por la regla del banco origen (source_bank) — borradores
    cuyo reference_no == apply_format_rule(banco.regla, referencia-del-depósito).
    """
    drafts = set(
        frappe.get_all(
            "Payment Entry",
            filters={"docstatus": 0, "payment_type": "Receive", "reference_no": reference},
            pluck="name",
        )
    )

    # Borradores cuya referencia es la del extracto transformada por la regla del banco.
    banks_with_rules = frappe.get_all(
        "Bank",
        filters={"bank_reference_rule": ["!=", ""]},
        fields=["name", "bank_reference_rule"],
    )
    for bank in banks_with_rules:
        modified_ref = apply_format_rule(bank.bank_reference_rule, reference)
        if modified_ref == reference:
            continue
        drafts.update(
            frappe.get_all(
                "Payment Entry",
                filters={
                    "docstatus": 0,
                    "payment_type": "Receive",
                    "reference_no": modified_ref,
                    "source_bank": bank.name,
                },
                pluck="name",
            )
        )

    for name in drafts:
        try:
            reconcile_and_approve(name)
            # Cada cobro es atómico: se confirma el exitoso para que un fallo
            # posterior no lo arrastre.
            frappe.db.commit()
        except Exception:
            # Un cobro problemático NO debe abortar el lote (es background): se
            # revierten sus escrituras parciales, se registra el traceback y se sigue.
            frappe.db.rollback()
            frappe.log_error(
                title=_("Error conciliando cobro {0} (auto)").format(name),
                message=frappe.get_traceback(),
            )
            continue


# ════════════════════════════════════════════════════════════════════════════
# Bank Reconciliation Tool: matching extendido por reglas (mint)
# ════════════════════════════════════════════════════════════════════════════

def _get_payment_entry_source_banks(names):
    if not names: return {}
    pes = frappe.get_all("Payment Entry", filters={"name": ("in", names)}, fields=["name", "source_bank"])
    return {p.name: p.source_bank for p in pes}

def _tag_existing_matches_by_rule(matches, original_ref, banks_with_rules):
    pe_names = [m.get("name") or m.name for m in matches if (m.get("doctype") or m.doctype) == "Payment Entry"]
    source_banks = _get_payment_entry_source_banks(pe_names)

    for match in matches:
        pe_ref = match.get("reference_no") or match.reference_no
        if not pe_ref or pe_ref == original_ref:
            continue
            
        doctype = match.get("doctype") or match.doctype
        if doctype != "Payment Entry":
            continue
            
        name = match.get("name") or match.name
        source_bank = source_banks.get(name)
        if not source_bank:
            continue
            
        for bank in banks_with_rules:
            if source_bank != bank.name:
                continue
            mod_ref = apply_format_rule(bank.bank_reference_rule, original_ref)
            if mod_ref == pe_ref:
                if isinstance(match, dict):
                    match["matched_by_rule"] = True
                else:
                    match.matched_by_rule = True
                break
    return matches

def _find_extra_matches_by_rule(matches, transaction, original_ref, banks_with_rules, document_types, filter_by_reference_date, from_reference_date, to_reference_date):
    bank_account = frappe.db.get_values(
        "Bank Account", transaction.bank_account, ["account", "company"], as_dict=True
    )[0]
    
    existing_keys = set(f"{m.get('doctype') or m.doctype}-{m.get('name') or m.name}" for m in matches)
    
    for bank in banks_with_rules:
        modified_ref = apply_format_rule(bank.bank_reference_rule, original_ref)
        if modified_ref == original_ref:
            continue

        transaction.reference_number = modified_ref
        new_matches = check_matching(
            bank_account.account, bank_account.company, transaction, document_types,
            None, None, filter_by_reference_date, from_reference_date, to_reference_date
        )
        transaction.reference_number = original_ref
        
        if not new_matches:
            continue
            
        pe_names = [m.get("name") if isinstance(m, dict) else m.name for m in new_matches if (m.get("doctype") if isinstance(m, dict) else m.doctype) == "Payment Entry"]
        source_banks = _get_payment_entry_source_banks(pe_names)
            
        for match in new_matches:
            doctype = match.get("doctype") if isinstance(match, dict) else match.doctype
            name = match.get("name") if isinstance(match, dict) else match.name
            
            if doctype == "Payment Entry":
                source_bank = source_banks.get(name)
                if source_bank == bank.name:
                    key = f"{doctype}-{name}"
                    if key not in existing_keys:
                        if not isinstance(match, dict):
                            match = match.as_dict()
                        match["matched_by_rule"] = True
                        matches.append(match)
                        existing_keys.add(key)
    return matches

def _filter_matches(matches, transaction, strict_matching):
    if not frappe.utils.cint(strict_matching):
        return matches

    filtered_matches = []
    for match in matches:
        m = frappe._dict(match) if isinstance(match, dict) else match

        is_exact = False
        if m.get("matched_by_rule"):
            is_exact = True
        elif str(m.get("reference_no") or "").strip() == str(transaction.reference_number or "").strip():
            is_exact = True

        if is_exact:
            filtered_matches.append(match)
            continue

        match_amount = float(m.get("paid_amount") or m.get("amount") or m.get("allocated_amount") or 0.0)
        txn_amount = float(transaction.unallocated_amount)

        match_date = m.get("posting_date") or m.get("date") or m.get("reference_date")
        txn_date = transaction.date

        if match_amount == txn_amount and str(match_date) == str(txn_date):
            filtered_matches.append(match)

    return filtered_matches

@frappe.whitelist()
def get_linked_payments(
    bank_transaction_name,
    document_types=None,
    from_date=None,
    to_date=None,
    filter_by_reference_date=None,
    from_reference_date=None,
    to_reference_date=None,
    strict_matching=1,
):
    if isinstance(document_types, str):
        document_types = json.loads(document_types)
    if not document_types:
        document_types = ["Payment Entry", "Journal Entry", "Sales Invoice", "Purchase Invoice", "Expense Claim", "Loan Repayment"]

    matches = erpnext_get_linked_payments(
        bank_transaction_name, document_types, from_date, to_date, 
        filter_by_reference_date, from_reference_date, to_reference_date
    )
    if not matches:
        matches = []

    transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
    original_ref = str(transaction.reference_number or "").strip()
    if not original_ref:
        return matches

    banks_with_rules = frappe.get_all(
        "Bank", filters={"bank_reference_rule": ["!=", ""]},
        fields=["name", "bank_reference_rule"],
    )
    
    if not banks_with_rules:
        return _filter_matches(matches, transaction, strict_matching)

    matches = _tag_existing_matches_by_rule(matches, original_ref, banks_with_rules)
    matches = _find_extra_matches_by_rule(
        matches, transaction, original_ref, banks_with_rules, 
        document_types, filter_by_reference_date, from_reference_date, to_reference_date
    )

    return _filter_matches(matches, transaction, strict_matching)


# ════════════════════════════════════════════════════════════════════════════
# Post-conciliación: mapear tercero y referencia origen (mint)
# ════════════════════════════════════════════════════════════════════════════

def update_source_reference_on_reconcile(doc, method=None) -> None:
    """Si la transacción bancaria se concilia, mapea el tercero del cobro y guarda la
    referencia origen (la del cobro, obtenida aplicando la regla del banco origen a la
    referencia del depósito)."""
    if doc.status != "Reconciled":
        return

    modified = False
    original_ref = str(doc.reference_number or "").strip()
    for row in doc.payment_entries:
        if row.payment_document != "Payment Entry" or not row.payment_entry:
            continue
        pe = frappe.db.get_value(
            "Payment Entry", row.payment_entry,
            ["reference_no", "party_type", "party", "source_bank"], as_dict=True,
        )
        if not pe:
            continue

        # Mapeo automático del tercero si está vacío.
        if not doc.party_type and pe.party_type:
            doc.db_set("party_type", pe.party_type, update_modified=False)
            modified = True
        if not doc.party and pe.party:
            doc.db_set("party", pe.party, update_modified=False)
            modified = True

        # Referencia origen: solo si difiere de la del depósito y aún no se guardó.
        if not pe.reference_no or doc.source_bank_reference_rule or not original_ref:
            continue
        if pe.reference_no == original_ref:
            continue
        rule_name = (
            frappe.db.get_value("Bank", pe.source_bank, "bank_reference_rule")
            if pe.source_bank else None
        )
        if rule_name and apply_format_rule(rule_name, original_ref) == pe.reference_no:
            doc.db_set("source_bank_reference_rule", pe.reference_no, update_modified=False)
            modified = True

    if modified:
        # Trigger reload in UI by touching modified
        doc.db_set("modified", frappe.utils.now(), update_modified=True)
