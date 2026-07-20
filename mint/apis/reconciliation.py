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
from mint.apis.mint_log import log_mint_error, log_mint_warning, log_mint_info

import frappe
import json
import re
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
# El depósito de la referencia existe EMITIDO pero ya fue conciliado con OTRO cobro
# (sin saldo libre): señal de cobro duplicado o depósito mal atribuido.
REVIEW_ALREADY_RECONCILED = "already_reconciled"
# El depósito de la referencia existe pero NO está emitido (borrador/cancelado): no
# es usable hasta emitirlo/depurarlo.
REVIEW_DEPOSIT_NOT_SUBMITTED = "deposit_not_submitted"

# Modos que no exigen depósito bancario para aprobarse.
CASH_LIKE_TYPES = ("Cash", "Gateway", "Gangway")

# Tolerancia de redondeo (en moneda de la empresa, VEF) admitida al comparar el
# depósito real contra el total del pago. SOLO positiva: "céntimo por encima,
# nunca por debajo". Ajustable si aparecen falsos bloqueos por redondeo USD↔VEF.
DEPOSIT_COVERAGE_TOLERANCE = 1.0

# Tolerancia (VEF) al verificar el invariante Σ asignado ≤ monto cobrado del Payment
# Entry. Solo se bloquea cuando la suma SUPERA el cobro por más que esto: absorbe el
# redondeo VEF↔USD sin dejar pasar la sobre-asignación real (los casos de prod excedían
# por miles de bolívares, no por céntimos). Ver check_payment_entry_overallocation.
OVERALLOCATION_TOLERANCE = 1.0

DEPOSIT_FIELDS = ["name", "deposit", "unallocated_amount", "currency", "bank_account", "date"]

# Cotas anti-explosión del motor de reglas (incidente 2026-07-09: el barrido nocturno
# quedó 30+ min de CPU en un solo cobro, atrapado con py-spy en apply_format_rule):
# - MAX_REFERENCE_LENGTH: reference_no es Data(140); un resultado más largo no puede
#   matchear nunca y, encadenado en pipelines, crece exponencialmente (una regla
#   duplicadora dobla la longitud en cada paso).
# - MAX_PIPELINE_ATTEMPTS: itertools.permutations es FACTORIAL — con las reglas de
#   todo el sistema (cobro sin source_bank) 10 reglas ≈ 9,8M pipelines. Se cubren
#   completas hasta 4 reglas (60 permutaciones) y se trunca el resto. Truncar nunca
#   produce un match falso: las reglas SUELTAS se prueban todas siempre, y un cobro
#   sin match queda en revisión manual.
MAX_REFERENCE_LENGTH = 140
MAX_PIPELINE_ATTEMPTS = 100


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
        result = str(
            safe_eval(
                rule_expr,
                eval_globals=dict(_SAFE_BUILTINS),
                eval_locals={"reference_number": ref},
            )
        )
    except Exception:
        log_mint_error(
            title="Bank Reference Rule inválida: {0}".format(rule_name),
            message=frappe.get_traceback(),
        )
        return ref
    if len(result) > MAX_REFERENCE_LENGTH:
        # No puede matchear un reference_no real (Data 140) y encadenado explota.
        return ref
    return result

def get_bank_rules(bank_name=None):
    """Devuelve una lista de nombres de reglas configuradas para el banco."""
    filters = {}
    if bank_name:
        filters["parent"] = bank_name
    rules = frappe.get_all(
        "Mint Bank Reference Rule Link", 
        filters=filters, 
        pluck="bank_reference_rule"
    )
    return list(dict.fromkeys(r for r in rules if r))

def validate_bank_rules(doc, method=None):
    """Evita que un banco tenga múltiples reglas para agregar ceros."""
    if not hasattr(doc, "bank_reference_rule"):
        return
        
    rules = [row.bank_reference_rule for row in doc.get("bank_reference_rule", []) if row.bank_reference_rule]
    agregar_ceros_count = 0
    for rule in rules:
        r_lower = str(rule).lower()
        if "agregar" in r_lower and "cero" in r_lower:
            agregar_ceros_count += 1
            
    if agregar_ceros_count > 1:
        frappe.throw(
            "No se permite tener múltiples reglas de tipo 'Agregar ceros' activas para un mismo banco al mismo tiempo.",
            title="Reglas en conflicto"
        )



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


# ════════════════════════════════════════════════════════════════════════════
# Invariante de asignación: Σ asignado a un Payment Entry ≤ su monto cobrado
# ════════════════════════════════════════════════════════════════════════════
# Nacido del saneo de depósitos duplicados (dossier 2026-07-11): el motor permitió
# asignar a un mismo cobro MÁS dinero del que cobra, repartido entre el depósito real y
# su gemelo ×100 phantom (p. ej. ACC-PAY-2026-02486 cobra 12.656,32 y quedó con 14.612,40
# asignados; ACC-PAY-2026-02573 cobra 218.244,40 y quedó con 285.791,40). Estas guardas
# lo impiden en el punto donde se crea una asignación Bank Transaction → Payment Entry.

def get_pe_allocated_in_other_transactions(
    payment_entry_name: str, exclude_bank_transaction: str | None = None
) -> float:
    """Σ(allocated_amount) asignado a un Payment Entry desde depósitos EMITIDOS,
    excluyendo opcionalmente uno (el que se está por asignar, para no contarlo dos veces).

    En moneda de la empresa (VEF), igual que deposit_covers_payment: allocated_amount de
    Bank Transaction Payments se compara contra base_paid_amount del cobro.
    """
    filters: dict = {
        "payment_entry": payment_entry_name,
        "payment_document": "Payment Entry",
        "docstatus": 1,
    }
    if exclude_bank_transaction:
        filters["parent"] = ["!=", exclude_bank_transaction]
    return flt(
        frappe.db.get_value("Bank Transaction Payments", filters, "sum(allocated_amount)") or 0,
        2,
    )


def check_payment_entry_overallocation(
    payment_entry_name: str,
    new_allocation: float,
    exclude_bank_transaction: str | None = None,
    raise_on_violation: bool = True,
) -> tuple[bool, str | None]:
    """Verifica el invariante Σ(asignado en TODOS los depósitos) ≤ base_paid_amount del cobro.

    `new_allocation` es lo que se está por asignar desde el depósito actual (excluido de
    `existing` vía exclude_bank_transaction). Un cobro puede pagarse legítimamente con
    VARIOS depósitos parciales que sumen su total; solo se bloquea cuando la suma lo SUPERA.

    Devuelve (ok, mensaje). Si raise_on_violation y se viola, lanza frappe.throw (mensaje en
    español). El cobro con total 0 (o sin base_paid_amount) no se verifica.

    NO aplica a las transferencias internas (payment_type 'Internal Transfer'): por diseño
    un mismo Payment Entry se concilia contra DOS depósitos (el retiro origen y el depósito
    destino), así que Σ asignado = 2×monto y el invariante no las modela.
    """
    pe = frappe.db.get_value(
        "Payment Entry", payment_entry_name, ["base_paid_amount", "payment_type"], as_dict=True
    )
    if not pe or (pe.payment_type or "") == "Internal Transfer":
        return True, None
    paid = flt(pe.base_paid_amount or 0, 2)
    if paid <= 0:
        return True, None

    existing = get_pe_allocated_in_other_transactions(payment_entry_name, exclude_bank_transaction)
    total = flt(existing + flt(new_allocation), 2)
    if total <= paid + OVERALLOCATION_TOLERANCE:
        return True, None

    if existing >= paid - OVERALLOCATION_TOLERANCE:
        # Segundo invariante del dossier: el cobro ya estaba TOTALMENTE asignado desde
        # otro depósito y este intenta asignarle más (firma de depósito duplicado/×100).
        message = _(
            "El cobro {0} ya está totalmente asignado ({1}) desde otro depósito bancario. "
            "No se le puede asignar {2} más: sería contar el mismo pago dos veces. Revise si "
            "hay un depósito duplicado con la misma referencia."
        ).format(
            payment_entry_name, frappe.bold(existing), frappe.bold(flt(new_allocation, 2))
        )
    else:
        message = _(
            "La asignación total ({0}) al cobro {1} supera el monto cobrado ({2}). Un depósito "
            "no puede aportar a un cobro más de lo que este cobra; revise si hay un depósito "
            "duplicado o inflado con la misma referencia."
        ).format(frappe.bold(total), payment_entry_name, frappe.bold(paid))

    if raise_on_violation:
        frappe.throw(message, title=_("Asignación excede el cobro"))
    return False, message


# ════════════════════════════════════════════════════════════════════════════
# Normalización de referencias bancarias
# ════════════════════════════════════════════════════════════════════════════

_INTERNAL_WHITESPACE_RE = re.compile(r"\s+")


def normalize_reference(reference) -> str:
    """Normaliza una referencia bancaria a su forma canónica para importar y conciliar.

    - Quita saltos de línea / retornos de carro / tabuladores (artefactos del import:
      el dossier documentó refs con `\\n` embebido, p. ej. "162536032237\\n").
    - Colapsa espacios internos múltiples a uno solo y recorta los extremos (preserva las
      referencias textuales tipo "BANPANAMA SR CRISTIAN" sin mutilarlas).
    - Quita el sufijo ".0" que agregan algunos export .xls al castear a número.
    - Quita la comilla simple inicial (export Bancamiga: "'123...").

    Superconjunto de strip(): para una referencia ya limpia devuelve exactamente lo mismo.
    Reemplaza y extiende la lógica de strip_leading_quote_from_reference.
    """
    ref = str(reference or "")
    ref = ref.replace("\r", "").replace("\n", "").replace("\t", "")
    ref = _INTERNAL_WHITESPACE_RE.sub(" ", ref).strip()
    if ref.endswith(".0"):
        ref = ref[:-2]
    if ref.startswith("'"):
        ref = ref[1:]
    return ref.strip()


def _cobro_bank_account(doc) -> str | None:
    """Bank Account (cuenta bancaria) del cobro, derivada de su cuenta GL."""
    bank_gl_acc = doc.paid_to or doc.paid_from
    if not bank_gl_acc:
        return None
    return frappe.db.get_value("Bank Account", {"account": bank_gl_acc})


def _deposit_base_filters(doc) -> dict | None:
    """Filtros comunes: referencia exacta + moneda del cobro + saldo disponible."""
    ref = normalize_reference(doc.reference_no)
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


import itertools

def check_rules_match(rules, raw_ref, target_ref):
    if raw_ref == target_ref:
        return True, None
        
    # Single rules
    for rule in rules:
        if apply_format_rule(rule, raw_ref) == target_ref:
            return True, rule
            
    # Combinations (pipeline en cualquier orden) — ACOTADO: permutations es factorial
    # y con las reglas de TODO el sistema (cobro sin source_bank) explota (ver
    # MAX_PIPELINE_ATTEMPTS). Truncar solo omite pipelines exóticos, no falsea matches.
    if len(rules) > 1:
        attempts = 0
        for r_len in range(2, len(rules) + 1):
            for perm in itertools.permutations(rules, r_len):
                attempts += 1
                if attempts > MAX_PIPELINE_ATTEMPTS:
                    log_mint_warning("Warning", 
                        "check_rules_match: %s reglas; pipelines truncados en %s intentos (ref destino %s)" % (
                            len(rules), MAX_PIPELINE_ATTEMPTS, target_ref
                        )
                    )
                    return False, None
                p_ref = raw_ref
                for r in perm:
                    p_ref = apply_format_rule(r, p_ref)
                if p_ref == target_ref:
                    return True, " + ".join(perm)

    return False, None


def _find_deposit_by_source_bank_rule(doc) -> frappe._dict | None:
    """Fallback de matching por REGLA DE BANCO, HACIA ADELANTE.

    Si el match exacto falla, se aplica la regla de limpieza a la referencia de
    cada depósito CANDIDATO (misma cuenta/moneda/saldo) y se compara con la
    referencia del cobro.
    Si el cobro tiene `source_bank`, se usa solo esa regla. Si no, se prueban
    todas las reglas configuradas en los bancos del sistema.
    """
    target_ref = normalize_reference(doc.reference_no)
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
        rules_to_check = get_bank_rules(source_bank)
    else:
        rules_to_check = get_bank_rules()

    for cand in candidates:
        match, _ = check_rules_match(rules_to_check, cand.reference_number, target_ref)
        if match:
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


def _adopt_deposit_bank_account(doc, deposit) -> bool:
    """Regla 'manda la cuenta del extracto': el depósito de la referencia cayó en
    OTRA cuenta bancaria distinta a la del cobro → se adopta la cuenta del extracto
    en el cobro para poder conciliar contra ese depósito.

    Mecánica: se busca el Modo de Pago cuya cuenta contable (para la compañía del
    cobro) es la del banco del depósito, y se monta en el cobro (lo que reapunta
    `paid_to` a esa cuenta). El monto lo sigue fijando el banco (`_apply_deposit_amount`).

    Devuelve True si pudo montar el Modo de Pago (existe EXACTAMENTE uno para esa
    cuenta y compañía); False si no hay ninguno o hay ambigüedad (>1) — en ese caso
    el llamador deja el cobro para revisión manual, sin adivinar.

    Nota: NO se toca `branch`/`cost_center` del cobro (el servicio del cliente puede
    estar en otra sucursal que el banco donde depositó); solo se corrige el banco.
    """
    gl_account = frappe.get_cached_value("Bank Account", deposit.bank_account, "account")
    if not gl_account:
        return False

    mops = list(dict.fromkeys(frappe.get_all(
        "Mode of Payment Account",
        filters={"company": doc.company, "default_account": gl_account},
        pluck="parent",
    )))
    if len(mops) != 1:
        return False  # 0 = sin Modo de Pago para esa cuenta; >1 = ambiguo → revisión

    mop = mops[0]
    old_paid_to = doc.paid_to
    doc.mode_of_payment = mop
    doc.paid_to = gl_account
    doc.paid_to_account_currency = frappe.get_cached_value("Account", gl_account, "account_currency")
    doc.add_comment(
        "Comment",
        _(
            "Conciliación: el depósito de la referencia {0} cayó en {1}. Se cambió la "
            "cuenta del cobro de {2} a {3} (Modo de Pago «{4}») — manda la cuenta del "
            "extracto — y se concilió."
        ).format(doc.reference_no or "", deposit.bank_account, old_paid_to, gl_account, mop),
    )
    return True


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


def find_consumed_deposit(doc) -> frappe._dict | None:
    """Depósito EMITIDO con la referencia+moneda del cobro pero YA conciliado con OTRO
    cobro (sin saldo libre). Es la firma de un cobro DUPLICADO (dos Payment Entries con
    la misma referencia; el depósito se fue con uno y este quedó huérfano) o de un
    depósito mal atribuido.

    Solo se llega aquí cuando NO hay depósito con saldo (ni en la cuenta del cobro ni en
    otra): si existe un Bank Transaction emitido (docstatus=1, deposit>0) con esta
    referencia+moneda cuyo saldo lo consumió OTRO Payment Entry, se devuelve el depósito
    y los cobros gemelos. Devuelve None si no aplica.
    """
    ref = normalize_reference(doc.reference_no)
    if not ref:
        return None
    filters: dict = {"reference_number": ref, "docstatus": 1, "deposit": [">", 0]}
    paid_currency = (doc.paid_on_currency or "").strip()
    if paid_currency:
        filters["currency"] = paid_currency

    for bt_name in frappe.get_all("Bank Transaction", filters=filters, pluck="name"):
        others = frappe.get_all(
            "Bank Transaction Payments",
            filters={
                "parent": bt_name,
                "payment_document": "Payment Entry",
                "payment_entry": ["!=", doc.name],
            },
            fields=["payment_entry", "allocated_amount"],
        )
        if others:
            return frappe._dict(bank_transaction=bt_name, other_payments=others)
    return None


def find_unsubmitted_deposit(doc) -> frappe._dict | None:
    """Bank Transaction con la referencia+moneda del cobro pero SIN emitir (borrador
    docstatus=0) o cancelado (docstatus=2): el depósito existe pero no es usable para
    conciliar hasta emitirlo/depurarlo. Prefiere el borrador (accionable) sobre el
    cancelado. Devuelve None si no hay ninguno.
    """
    ref = normalize_reference(doc.reference_no)
    if not ref:
        return None
    paid_currency = (doc.paid_on_currency or "").strip()
    rows = frappe.db.sql(
        """
        SELECT name, docstatus, status, deposit, unallocated_amount, bank_account
        FROM `tabBank Transaction`
        WHERE TRIM(reference_number) = %(ref)s AND deposit > 0 AND docstatus <> 1
          {currency}
        ORDER BY docstatus ASC
        LIMIT 1
        """.format(currency="AND currency = %(cur)s" if paid_currency else ""),
        {"ref": ref, "cur": paid_currency},
        as_dict=True,
    )
    return rows[0] if rows else None


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
    ref = normalize_reference(doc.reference_no)
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

    if deposit.get("date"):
        doc.reference_date = deposit.date

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
            # El depósito de la referencia existe pero cayó en OTRA cuenta bancaria.
            # Regla de negocio: MANDA LA CUENTA DEL EXTRACTO. Se adopta la cuenta del
            # depósito en el cobro (montando el Modo de Pago cuya cuenta contable es la
            # del banco del extracto) y se concilia contra ese depósito. Solo si no hay
            # un Modo de Pago para esa cuenta (o es ambiguo) se deja para revisión.
            if _adopt_deposit_bank_account(doc, anomaly):
                deposit = anomaly
            else:
                doc.db_set("custom_reconciliation_status", RECON_REVIEW)
                return {
                    "reconciled": False,
                    "review": True,
                    "reason": REVIEW_OTHER_BANK,
                    "bank_transaction": anomaly.name,
                    "message": _(
                        "El depósito de la referencia {0} cayó en otra cuenta bancaria "
                        "({1}) y no hay un Modo de Pago asociado a esa cuenta. El cobro "
                        "quedó marcado para revisión."
                    ).format(doc.reference_no or "", anomaly.bank_account),
                }
        else:
            # El depósito de la referencia existe EMITIDO pero su saldo ya lo consumió
            # OTRO cobro → firma de cobro DUPLICADO / mala atribución. Se marca para
            # revisión mostrando el/los cobro(s) gemelo(s).
            consumed = find_consumed_deposit(doc)
            if consumed:
                doc.db_set("custom_reconciliation_status", RECON_REVIEW)
                twins = [o.payment_entry for o in consumed.other_payments]
                return {
                    "reconciled": False,
                    "review": True,
                    "reason": REVIEW_ALREADY_RECONCILED,
                    "bank_transaction": consumed.bank_transaction,
                    "other_payments": twins,
                    "message": _(
                        "El depósito de la referencia {0} ya fue conciliado con otro cobro "
                        "({1}). Revise si este cobro está duplicado."
                    ).format(doc.reference_no or "", ", ".join(twins)),
                }

            # El depósito existe pero NO está emitido (borrador/cancelado): no es usable
            # hasta emitirlo o depurarlo.
            unsubmitted = find_unsubmitted_deposit(doc)
            if unsubmitted:
                doc.db_set("custom_reconciliation_status", RECON_REVIEW)
                return {
                    "reconciled": False,
                    "review": True,
                    "reason": REVIEW_DEPOSIT_NOT_SUBMITTED,
                    "bank_transaction": unsubmitted.name,
                    "message": _(
                        "El depósito de la referencia {0} existe pero no está emitido "
                        "(estado {1}). Emítalo o depúrelo y reintente."
                    ).format(doc.reference_no or "", unsubmitted.status),
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

    # Defensa en profundidad del invariante Σ asignado ≤ cobrado: si OTRO depósito ya
    # cubrió por completo este cobro, no se enlaza (sería contar el pago dos veces, el
    # patrón ×100 del dossier). No se lanza throw porque este camino corre en background
    # (reconcile_and_approve); solo se registra y se sale sin enlazar.
    pe_data = frappe.db.get_value("Payment Entry", payment_entry_name, ["base_paid_amount", "payment_type"], as_dict=True)
    if pe_data and pe_data.payment_type != "Internal Transfer":
        paid = flt(pe_data.base_paid_amount or 0, 2)
        if paid > 0:
            existing = get_pe_allocated_in_other_transactions(
                payment_entry_name, exclude_bank_transaction=bt.name
            )
            if existing >= paid - OVERALLOCATION_TOLERANCE:
                log_mint_warning("Warning", 
                    "No se enlaza el depósito %s al cobro %s: ya está totalmente asignado "
                    "(%s de %s) desde otro depósito." % (
                        bt.name, payment_entry_name, existing, paid
                    )
                )
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
        updated_fields = {}
        if not bt.party_type and pe.party_type:
            updated_fields["party_type"] = pe.party_type
        if not bt.party and pe.party:
            updated_fields["party"] = pe.party
        
        original_ref = str(bt.reference_number or "").strip()
        if pe.reference_no and original_ref and pe.reference_no != original_ref:
            # Si ya tenía una regla guardada, no la tocamos
            if not bt.source_bank_reference_rule:
                rules_to_check = get_bank_rules(pe.source_bank) if pe.source_bank else get_bank_rules()
                match, matched_rule = check_rules_match(rules_to_check, original_ref, pe.reference_no)
                if match and matched_rule:
                    updated_fields["source_bank_reference_rule"] = matched_rule[:140]

        if updated_fields:
            frappe.db.set_value("Bank Transaction", bt.name, updated_fields)
            bt.update(updated_fields)

    # Guardar las entradas de pago y demás campos permitidos (allow_on_submit)
    bt.save(ignore_permissions=True)

    # Forzar actualización del estado visual, ya que bt.save actualiza clearance_date
    # silenciosamente por db.set_value sin disparar hooks del Payment Entry.
    pe_data = frappe.db.get_value(
        "Payment Entry", payment_entry_name, 
        ["clearance_date", "custom_reconciliation_status", "payment_type"], 
        as_dict=True
    )
    if pe_data:
        if pe_data.payment_type == "Internal Transfer":
            # Un Internal Transfer se concilia cuando tiene al menos 2 extractos asociados
            linked_bts = frappe.get_all(
                "Bank Transaction Payments", 
                filters={"payment_document": "Payment Entry", "payment_entry": payment_entry_name, "docstatus": 1},
                pluck="parent"
            )
            if len(set(linked_bts)) >= 2:
                updates = {}
                if pe_data.custom_reconciliation_status != RECON_DONE:
                    updates["custom_reconciliation_status"] = RECON_DONE
                if not pe_data.clearance_date:
                    updates["clearance_date"] = bt.date
                if updates:
                    frappe.db.set_value("Payment Entry", payment_entry_name, updates, update_modified=True)
        else:
            if pe_data.clearance_date and pe_data.custom_reconciliation_status != RECON_DONE:
                frappe.db.set_value("Payment Entry", payment_entry_name, "custom_reconciliation_status", RECON_DONE, update_modified=True)


def strip_leading_quote_from_reference(doc, method):
    """Normaliza reference_number al INSERTAR un Bank Transaction (hook before_insert).

    Deja la referencia en su forma canónica (ver normalize_reference): sin comilla inicial
    (export Bancamiga .xls), sin sufijo ".0", y sin saltos de línea / espacios internos
    espurios (artefactos del import que rompían el match por referencia y las guardas de
    duplicados). Toda la data nueva entra limpia.
    """
    if doc.reference_number is not None:
        doc.reference_number = normalize_reference(doc.reference_number)


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
    
    if hasattr(doc, "calculate_igtf_taxes"):
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
    confusión en documentos cancelados. También cancela los ISP Payment Entry enlazados."""
    if doc.get("custom_reconciliation_status") != RECON_PENDING:
        doc.custom_reconciliation_status = RECON_PENDING
        doc.db_set("custom_reconciliation_status", RECON_PENDING)

    if frappe.db.exists("DocType", "ISP Payment Entry"):
        linked = frappe.get_all("ISP Payment Entry", filters={"payment_entry": doc.name}, pluck="name")
        for name in linked:
            isp_doc = frappe.get_doc("ISP Payment Entry", name)
            if isp_doc.docstatus == 1:
                isp_doc.flags.ignore_permissions = True
                isp_doc.cancel()


def on_trash_receive_payment(doc, method=None) -> None:
    """Al eliminar un cobro, elimina únicamente su línea de pago dentro del ISP Payment Entry
    para evitar el bloqueo por Link Validation de Frappe, sin borrar el registro completo."""
    if frappe.db.exists("DocType", "ISP Payment Entry"):
        linked_lines = frappe.get_all(
            "ISP Payment Entry Lines",
            filters={"payment_entry": doc.name},
            fields=["parent"]
        )
        parent_names = {d.parent for d in linked_lines if d.parent}
        for parent_name in parent_names:
            isp_doc = frappe.get_doc("ISP Payment Entry", parent_name)
            lines_to_remove = [line for line in isp_doc.payments if line.payment_entry == doc.name]
            for line in lines_to_remove:
                isp_doc.remove(line)
            isp_doc.save(ignore_permissions=True)



def on_change_payment_entry(doc, method=None) -> None:
    """Si se reconcilia o desconcilia desde la Transacción Bancaria u otra herramienta,
    sincroniza el estado visual con la existencia de la fecha de liquidación."""
    if doc.docstatus == 1:
        if doc.clearance_date and doc.get("custom_reconciliation_status") != RECON_DONE:
            doc.db_set("custom_reconciliation_status", RECON_DONE, update_modified=True)
        elif not doc.clearance_date and doc.get("custom_reconciliation_status") != RECON_PENDING:
            doc.db_set("custom_reconciliation_status", RECON_PENDING, update_modified=True)



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
    if not ref:
        return

    is_dep = flt(doc.deposit) > 0
    is_wth = flt(doc.withdrawal) > 0

    if not is_dep and not is_wth:
        return

    filters = {
        "name": ["!=", doc.name or ""],
        "reference_number": ref,
        "bank_account": doc.bank_account,
        "company": doc.company,
        "docstatus": ["<", 2],
    }

    if is_dep:
        filters["deposit"] = [">", 0]
    else:
        filters["withdrawal"] = [">", 0]

    duplicate = frappe.db.exists("Bank Transaction", filters)
    if duplicate:
        duplicate_link = frappe.utils.get_link_to_form("Bank Transaction", duplicate)
        if is_dep:
            frappe.throw(
                _(
                    "Ya existe un depósito con la referencia {0} en esta cuenta bancaria "
                    "({1}). No se permiten depósitos duplicados con la misma referencia; "
                    "revise el extracto importado."
                ).format(frappe.bold(ref), duplicate_link)
            )
        else:
            # Para retiros: si los montos son distintos, uno es la transacción real
            # y el otro es la comisión bancaria. Se permite la coexistencia.
            existing_amount = frappe.db.get_value("Bank Transaction", duplicate, "withdrawal")
            new_amount = flt(doc.withdrawal)
            if flt(existing_amount) == new_amount:
                frappe.throw(
                    _(
                        "Ya existe un retiro con la referencia {0} y el mismo monto en esta "
                        "cuenta bancaria ({1}). No se permiten retiros duplicados con la misma "
                        "referencia y monto; revise el extracto importado."
                    ).format(frappe.bold(ref), duplicate_link)
                )


def reconcile_drafts_for_deposit(doc, method=None) -> None:
    """Disparador desde el depósito: al confirmarse un Bank Transaction, intenta
    aprobar los cobros en borrador que esperaban esa referencia.

    Se ejecuta en background (enqueue_after_commit) para no bloquear el guardado
    del depósito.
    """
    ref = str(doc.reference_number or "").strip()
    if not ref:
        return

    if flt(doc.deposit) > 0:
        frappe.enqueue(
            "mint.apis.reconciliation.reconcile_drafts_job",
            queue="short",
            reference=ref,
            enqueue_after_commit=True,
        )
        frappe.enqueue(
            "mint.apis.reconciliation.reconcile_mint_bank_transfer_from_bank_transaction",
            queue="short",
            bank_transaction_name=doc.name,
            enqueue_after_commit=True,
        )
    elif flt(doc.withdrawal) > 0:
        frappe.enqueue(
            "mint.apis.reconciliation.reconcile_je_job",
            queue="short",
            bank_transaction_name=doc.name,
            enqueue_after_commit=True,
        )
        frappe.enqueue(
            "mint.apis.reconciliation.reconcile_mint_bank_transfer_from_bank_transaction",
            queue="short",
            bank_transaction_name=doc.name,
            enqueue_after_commit=True,
        )


def _approve_drafts(names) -> int:
    """Reintenta reconcile_and_approve sobre una colección de cobros en borrador.

    Cada cobro es ATÓMICO: se confirma el exitoso (para que un fallo posterior no lo
    arrastre) y un cobro problemático se revierte + se registra sin abortar el lote
    (se corre en background). Devuelve cuántos quedaron conciliados.
    """
    reconciled = 0
    for name in names:
        try:
            result = reconcile_and_approve(name)
            frappe.db.commit()
            if result.get("reconciled"):
                reconciled += 1
        except Exception:
            frappe.db.rollback()
            log_mint_error(
                title=_("Error conciliando cobro {0} (auto)").format(name),
                message=frappe.get_traceback(),
            )
    return reconciled


def cancel_exact_duplicate_deposits() -> int:
    """Cancela depósitos EXACTAMENTE duplicados: el mismo movimiento importado dos (o
    más) veces = misma referencia + cuenta bancaria + empresa + MONTO, deposit>0, no
    cancelados. La referencia es el ID único del banco, así que dos con la misma
    ref+monto en la misma cuenta son el mismo depósito (un re-import).

    Se conserva el que tenga asignación (o el más antiguo si ninguno) y se CANCELA el
    resto SIN asignar, dejando al menos uno. Se CANCELA (docstatus=2), no se borra: un
    BT cancelado sale de la detección de colisiones (docstatus<2) Y no choca con
    enlaces de auditoría (p. ej. DB Bancaribe Log, que bloquea el delete). NUNCA se
    toca un depósito con asignación (rompería un pago conciliado) -> los grupos "ambos
    asignados" se dejan intactos para revisión manual. Cada cancelación es atómica
    (commit por éxito; rollback + Error Log por fallo, sin abortar el lote). Devuelve
    cuántos canceló.

    Es la contraparte RECURRENTE del saneo histórico (patch cleanup_duplicate_x100):
    corre en el barrido nocturno ANTES de conciliar, para liberar las colisiones de
    referencia y que el mismo barrido concilie los cobros que quedan libres.
    """
    groups = frappe.db.sql(
        """
        SELECT TRIM(reference_number) AS ref, bank_account, company,
               ROUND(deposit, 2) AS amount, date
        FROM `tabBank Transaction`
        WHERE deposit > 0 AND docstatus < 2
          AND reference_number IS NOT NULL AND TRIM(reference_number) != ''
        GROUP BY TRIM(reference_number), bank_account, company, ROUND(deposit, 2), date
        HAVING COUNT(*) > 1
        """,
        as_dict=True,
    )
    cancelled = 0
    for group in groups:
        members = frappe.db.sql(
            """
            SELECT name, allocated_amount, docstatus
            FROM `tabBank Transaction`
            WHERE TRIM(reference_number) = %(ref)s AND bank_account = %(ba)s
              AND company = %(co)s AND ROUND(deposit, 2) = %(amt)s AND date = %(dt)s
              AND docstatus < 2
            ORDER BY allocated_amount DESC, creation ASC
            """,
            {"ref": group.ref, "ba": group.bank_account, "co": group.company,
             "amt": group.amount, "dt": group.date},
            as_dict=True,
        )
        # members[0] = el más conservable (asignado y/o más antiguo): se conserva.
        # Del resto, solo se cancelan los que NO tienen asignación.
        for m in members[1:]:
            if flt(m.allocated_amount) >= 0.01:
                continue  # asignado: conservar (cancelarlo rompería un pago)
            try:
                doc = frappe.get_doc("Bank Transaction", m.name)
                if doc.docstatus == 1:
                    doc.cancel()
                else:
                    frappe.delete_doc("Bank Transaction", m.name, ignore_permissions=True)
                frappe.db.commit()
                cancelled += 1
            except Exception:
                frappe.db.rollback()
                log_mint_error(
                    title=_("Error cancelando depósito duplicado {0} (auto)").format(m.name),
                    message=frappe.get_traceback(),
                )
    return cancelled


def find_impossible_date_transactions() -> list:
    """Bank Transactions con fecha IMPOSIBLE: futura (> hoy) o NULL, no canceladas.

    Firma del bug de import ×100 (dossier 2026-07-11): al inflar montos también volteó
    fechas dd/mm↔mm/dd, dejando depósitos con fecha futura (ACC-BTN-2026-04720 → 2026-09-03,
    ACC-BTN-2026-02661 → 2026-10-03) e incluso alguno con date=None (ACC-BTN-2026-03213-1),
    que se escapa de los filtros por fecha del barrido nocturno y del saneo. Se listan para
    que el dashboard/health los muestre y se corrijan a mano.
    """
    today = frappe.utils.today()
    return frappe.db.sql(
        """
        SELECT name, date, reference_number, deposit, withdrawal, bank_account, company,
               docstatus, status
        FROM `tabBank Transaction`
        WHERE docstatus < 2 AND (date IS NULL OR date > %(today)s)
        ORDER BY date DESC
        """,
        {"today": today},
        as_dict=True,
    )


@frappe.whitelist()
def get_reconciliation_health() -> dict:
    """Resumen de anomalías estructurales de conciliación para el dashboard/health de mint.

    Hoy expone las transacciones con fecha imposible (futura o NULL). Pensado para crecer
    con otros chequeos (duplicados, sobre-asignaciones) sin cambiar la firma del endpoint.
    """
    frappe.has_permission("Bank Transaction", "read", throw=True)
    impossible = find_impossible_date_transactions()
    return {
        "impossible_date_transactions": impossible,
        "impossible_date_count": len(impossible),
    }


def reconcile_pending_drafts_nightly() -> None:
    """Barrido nocturno (cron 22:00 hora del site): sanea duplicados exactos y luego
    reintenta conciliar TODOS los cobros en borrador con referencia pendientes.

    Dos pasos en una sola corrida:
      1) cancel_exact_duplicate_deposits(): cancela los depósitos exactamente
         duplicados (re-imports) que bloquean colisiones de referencia.
      2) reconcile_and_approve sobre cada borrador pendiente (idempotente): concilia
         los que ya tienen depósito usable — incluidos los que acaba de liberar el
         paso 1 — y deja pendientes/marca para revisión los demás.

    Cierra el hueco de que la auto-conciliación solo se dispara desde el lado del
    depósito (hook on_submit del Bank Transaction): un cobro creado DESPUÉS de que su
    depósito ya se importó no se reintentaría nunca. Un cobro en borrador nunca está
    'Conciliado' (eso implica emitido), así que docstatus=0 + referencia acota el
    universo.
    """
    deduped = cancel_exact_duplicate_deposits()

    names = frappe.get_all(
        "Payment Entry",
        filters={
            "docstatus": 0,
            "payment_type": "Receive",
            "reference_no": ["!=", ""],
        },
        pluck="name",
    )
    reconciled = _approve_drafts(names)

    # Salud: dejar rastro de los depósitos con fecha imposible (futura o NULL) que se
    # escapan de los filtros por fecha; no se tocan aquí (requieren decisión humana).
    impossible = find_impossible_date_transactions()
    if impossible:
        log_mint_warning(
            "Warning",
            "Barrido nocturno: %s Bank Transactions con fecha imposible (futura o NULL): %s"
            % (len(impossible), ", ".join(t.name for t in impossible[:20])),
        )

    log_mint_info("Info", 
        "Barrido nocturno: %s duplicados exactos cancelados; %s/%s cobros conciliados; "
        "%s con fecha imposible."
        % (deduped, reconciled, len(names), len(impossible))
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

    # Borradores cuya referencia es la del extracto transformada por reglas
    # Buscar qué bancos tienen reglas
    banks = frappe.get_all(
        "Mint Bank Reference Rule Link", 
        fields=["parent", "bank_reference_rule"]
    )
    for b in banks:
        modified_ref = apply_format_rule(b.bank_reference_rule, reference)
        if modified_ref == reference:
            continue
        drafts.update(
            frappe.get_all(
                "Payment Entry",
                filters={
                    "docstatus": 0,
                    "payment_type": "Receive",
                    "reference_no": modified_ref,
                    "source_bank": b.parent,
                },
                pluck="name",
            )
        )

    _approve_drafts(drafts)


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
            
        for b in banks_with_rules:
            if source_bank != b.parent:
                continue
            mod_ref = apply_format_rule(b.bank_reference_rule, original_ref)
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
    
    for b in banks_with_rules:
        modified_ref = apply_format_rule(b.bank_reference_rule, original_ref)
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
                if source_bank == b.parent:
                    key = f"{doctype}-{name}"
                    if key not in existing_keys:
                        if not isinstance(match, dict):
                            match = match.as_dict()
                        match["matched_by_rule"] = True
                        matches.append(match)
                        existing_keys.add(key)
    return matches

def _filter_matches(matches, transaction, strict_matching):
    filtered_matches = []
    for match in matches:
        m = frappe._dict(match) if isinstance(match, dict) else match
        
        match_amount = float(m.get("paid_amount") or m.get("amount") or m.get("allocated_amount") or 0.0)
        if match_amount <= 0.0:
            continue

        if not frappe.utils.cint(strict_matching):
            filtered_matches.append(match)
            continue

        is_exact = False
        if m.get("matched_by_rule"):
            is_exact = True
        elif str(m.get("reference_no") or "").strip() == str(transaction.reference_number or "").strip():
            is_exact = True

        if is_exact:
            filtered_matches.append(match)
            continue

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
        "Mint Bank Reference Rule Link", 
        fields=["parent", "bank_reference_rule"],
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
            doc.db_set("party_type", pe.party_type, update_modified=True)
            modified = True
        if not doc.party and pe.party:
            doc.db_set("party", pe.party, update_modified=True)
            modified = True

        # Referencia origen: solo si difiere de la del depósito y aún no se guardó.
        if not pe.reference_no or doc.source_bank_reference_rule or not original_ref:
            continue
        if pe.reference_no == original_ref:
            continue
            
        rules_to_check = get_bank_rules(pe.source_bank) if pe.source_bank else []
        match, matched_rule = check_rules_match(rules_to_check, original_ref, pe.reference_no)
        if match and matched_rule:
            doc.db_set("source_bank_reference_rule", matched_rule[:140], update_modified=True)
            modified = True

    if modified:
        # Trigger reload in UI by touching modified
        doc.db_set("modified", frappe.utils.now(), update_modified=True)


# ════════════════════════════════════════════════════════════════════════════
# Auto-reconciliation of Journal Entries (Expenses/Withdrawals)
# ════════════════════════════════════════════════════════════════════════════

def _link_withdrawal_to_je(bank_transaction_name: str, je_name: str) -> None:
    bt = frappe.get_doc("Bank Transaction", bank_transaction_name)
    already = any(
        row.payment_entry == je_name and row.payment_document == "Journal Entry"
        for row in bt.payment_entries
    )
    if already or flt(bt.unallocated_amount) <= 0:
        return
    bt.add_payment_entries(
        [{"payment_doctype": "Journal Entry", "payment_name": je_name}]
    )
    bt.save(ignore_permissions=True)

def reconcile_journal_entry(doc, method=None):
    if not doc.cheque_no:
        return
        
    ref = str(doc.cheque_no).strip()
    
    bank_gl_account = None
    je_amount = 0
    for acc in doc.accounts:
        is_bank = frappe.db.get_value("Account", acc.account, "account_type") == "Bank"
        if is_bank and flt(acc.credit) > 0:
            bank_gl_account = acc.account
            je_amount = flt(acc.credit)
            break
            
    if not bank_gl_account or je_amount <= 0:
        return
        
    bank_account = frappe.db.get_value("Bank Account", {"account": bank_gl_account})
    if not bank_account:
        return
        
    cand_filters = {
        "docstatus": 1,
        "withdrawal": [">", 0],
        "unallocated_amount": [">", 0.001],
        "bank_account": bank_account,
        "company": doc.company,
    }
    
    candidates = frappe.get_all(
        "Bank Transaction",
        filters=cand_filters,
        fields=["name", "withdrawal", "reference_number"],
        order_by="withdrawal asc"
    )
    
    rules_to_check = get_bank_rules(bank_account)
    
    for cand in candidates:
        if abs(flt(cand.withdrawal) - je_amount) < 1.0:
            raw_ref = str(cand.reference_number or "").strip()
            match, _ = check_rules_match(rules_to_check, raw_ref, ref)
            if match or (not rules_to_check and raw_ref == ref):
                _link_withdrawal_to_je(cand.name, doc.name)
                break

def reconcile_je_job(bank_transaction_name: str) -> None:
    bt = frappe.db.get_values(
        "Bank Transaction", 
        bank_transaction_name, 
        ["name", "reference_number", "withdrawal", "bank_account", "company", "unallocated_amount"], 
        as_dict=True
    )
    if not bt: return
    bt = bt[0]
    
    if flt(bt.unallocated_amount) <= 0: return
    
    ref = str(bt.reference_number or "").strip()
    if not ref: return
    
    bank_gl_account = frappe.db.get_value("Bank Account", bt.bank_account, "account")
    if not bank_gl_account: return
    
    jes = frappe.get_all(
        "Journal Entry",
        filters={"docstatus": 1, "company": bt.company, "cheque_no": ["!=", ""]},
        fields=["name", "cheque_no"]
    )
    
    rules_to_check = get_bank_rules(bt.bank_account)
    
    for je in jes:
        je_ref = str(je.cheque_no or "").strip()
        if not je_ref: continue
        
        match, _ = check_rules_match(rules_to_check, ref, je_ref)
        if match or (not rules_to_check and ref == je_ref):
            je_doc = frappe.get_doc("Journal Entry", je.name)
            
            match_found = False
            for acc in je_doc.accounts:
                if acc.account == bank_gl_account and abs(flt(acc.credit) - flt(bt.withdrawal)) < 1.0:
                    match_found = True
                    break
                    
            if match_found:
                try:
                    _link_withdrawal_to_je(bt.name, je.name)
                    frappe.db.commit()
                    break
                except Exception:
                    frappe.db.rollback()
                    log_mint_error(f"Error auto-reconciling JE {je.name}", frappe.get_traceback())

def update_expense_journal_entry(doc, method=None):
    """Inyectado al validar un Gasto (Expense). Copia el número de referencia
    al Asiento Contable (Journal Entry) y dispara la conciliación bancaria."""
    if doc.journal_entry and doc.reference_no:
        frappe.db.set_value("Journal Entry", doc.journal_entry, {
            "cheque_no": doc.reference_no,
            "cheque_date": doc.expense_date
        }, update_modified=True)
        
        # Disparar la conciliación de este JE, ya que el hook estándar on_submit
        # de Journal Entry pasó antes de que le inyectáramos la referencia.
        je_doc = frappe.get_doc("Journal Entry", doc.journal_entry)
        reconcile_journal_entry(je_doc)

# ════════════════════════════════════════════════════════════════════════════
# Auto-reconciliation of Mint Bank Transfer
# ════════════════════════════════════════════════════════════════════════════

def reconcile_bank_transfer_on_submit(doc, method=None):
    """Cuando se envía una Transferencia Interna, intenta conciliarla."""
    frappe.enqueue(
        "mint.apis.reconciliation.reconcile_mint_bank_transfer",
        queue="short",
        mbt_name=doc.name,
        enqueue_after_commit=True,
    )

def reconcile_mint_bank_transfer(mbt_name: str) -> None:
    doc = frappe.get_doc("Mint Bank Transfer", mbt_name)
    if doc.reconciliation_status == "Conciliado":
        return

    modified = False

    if not doc.source_reconciled:
        cand_filters = {
            "docstatus": 1,
            "withdrawal": [">", 0],
            "unallocated_amount": [">", 0.001],
            "bank_account": doc.from_bank_account,
            "company": doc.company,
        }
        candidates = frappe.get_all("Bank Transaction", filters=cand_filters, fields=["name", "withdrawal", "reference_number"])
        rules_to_check = get_bank_rules(doc.from_bank_account)
        for cand in candidates:
            if abs(float(cand.withdrawal) - float(doc.amount)) < 1.0:
                raw_ref = str(cand.reference_number or "").strip()
                target_ref = str(doc.reference_number).strip()
                match, _ = check_rules_match(rules_to_check, raw_ref, target_ref)
                if match or (not rules_to_check and raw_ref == target_ref):
                    try:
                        _link_mbt_to_bt(cand.name, doc.name, "source")
                        doc.db_set("source_reconciled", 1)
                        modified = True
                        break
                    except Exception as e:
                        frappe.log_error("Error linking source BT to MBT", str(e))
    
    if not doc.destination_reconciled:
        cand_filters = {
            "docstatus": 1,
            "deposit": [">", 0],
            "unallocated_amount": [">", 0.001],
            "bank_account": doc.to_bank_account,
            "company": doc.company,
        }
        candidates = frappe.get_all("Bank Transaction", filters=cand_filters, fields=["name", "deposit", "reference_number"])
        rules_to_check = get_bank_rules(doc.to_bank_account)
        for cand in candidates:
            if abs(float(cand.deposit) - float(doc.amount)) < 1.0:
                raw_ref = str(cand.reference_number or "").strip()
                target_ref = str(doc.reference_number).strip()
                match, _ = check_rules_match(rules_to_check, raw_ref, target_ref)
                if match or (not rules_to_check and raw_ref == target_ref):
                    try:
                        _link_mbt_to_bt(cand.name, doc.name, "destination")
                        doc.db_set("destination_reconciled", 1)
                        modified = True
                        break
                    except Exception as e:
                        frappe.log_error("Error linking destination BT to MBT", str(e))

    if modified:
        doc.update_reconciliation_status()

def reconcile_mint_bank_transfer_from_bank_transaction(bank_transaction_name: str) -> None:
    bt = frappe.db.get_values(
        "Bank Transaction", 
        bank_transaction_name, 
        ["name", "reference_number", "withdrawal", "deposit", "bank_account", "company", "unallocated_amount"], 
        as_dict=True
    )
    if not bt: return
    bt = bt[0]
    
    if float(bt.unallocated_amount) <= 0: return
    
    raw_ref = str(bt.reference_number or "").strip()
    if not raw_ref: return

    rules_to_check = get_bank_rules(bt.bank_account)

    is_withdrawal = float(bt.withdrawal) > 0
    is_deposit = float(bt.deposit) > 0

    if is_withdrawal:
        mbts = frappe.get_all(
            "Mint Bank Transfer",
            filters={"docstatus": 1, "source_reconciled": 0, "from_bank_account": bt.bank_account, "company": bt.company},
            fields=["name", "reference_number", "amount"]
        )
        for mbt in mbts:
            if abs(float(mbt.amount) - float(bt.withdrawal)) < 1.0:
                target_ref = str(mbt.reference_number).strip()
                match, _ = check_rules_match(rules_to_check, raw_ref, target_ref)
                if match or (not rules_to_check and raw_ref == target_ref):
                    try:
                        _link_mbt_to_bt(bt.name, mbt.name, "source")
                        frappe.db.set_value("Mint Bank Transfer", mbt.name, "source_reconciled", 1)
                        frappe.get_doc("Mint Bank Transfer", mbt.name).update_reconciliation_status()
                        break
                    except Exception as e:
                        frappe.log_error("Error linking source BT to MBT", str(e))

    if is_deposit:
        mbts = frappe.get_all(
            "Mint Bank Transfer",
            filters={"docstatus": 1, "destination_reconciled": 0, "to_bank_account": bt.bank_account, "company": bt.company},
            fields=["name", "reference_number", "amount"]
        )
        for mbt in mbts:
            if abs(float(mbt.amount) - float(bt.deposit)) < 1.0:
                target_ref = str(mbt.reference_number).strip()
                match, _ = check_rules_match(rules_to_check, raw_ref, target_ref)
                if match or (not rules_to_check and raw_ref == target_ref):
                    try:
                        _link_mbt_to_bt(bt.name, mbt.name, "destination")
                        frappe.db.set_value("Mint Bank Transfer", mbt.name, "destination_reconciled", 1)
                        frappe.get_doc("Mint Bank Transfer", mbt.name).update_reconciliation_status()
                        break
                    except Exception as e:
                        frappe.log_error("Error linking destination BT to MBT", str(e))

def _link_mbt_to_bt(bt_name: str, mbt_name: str, side: str) -> None:
    bt = frappe.get_doc("Bank Transaction", bt_name)
    already = any(
        row.payment_entry == mbt_name and row.payment_document == "Mint Bank Transfer"
        for row in bt.payment_entries
    )
    if already or float(bt.unallocated_amount) <= 0:
        return
    
    bt.append("payment_entries", {
        "payment_document": "Mint Bank Transfer",
        "payment_entry": mbt_name,
        "allocated_amount": bt.unallocated_amount
    })
    bt.save(ignore_permissions=True)
    frappe.db.commit()

@frappe.whitelist()
def get_duplicate_bank_transactions():
    """Busca duplicados exactos (mismo retiro o mismo depósito, y misma referencia)."""
    duplicates = []
    from frappe.utils import flt
    
    for doctype_type in ["withdrawal", "deposit"]:
        groups = frappe.db.sql(f"""
            SELECT TRIM(reference_number) AS ref, bank_account, company, {doctype_type} as amount, COUNT(*) AS cnt
            FROM `tabBank Transaction`
            WHERE {doctype_type} > 0 AND docstatus < 2
              AND reference_number IS NOT NULL AND TRIM(reference_number) != ''
            GROUP BY TRIM(reference_number), bank_account, company, {doctype_type}
            HAVING cnt > 1
        """, as_dict=True)
        
        for group in groups:
            members = frappe.db.sql(f"""
                SELECT name, allocated_amount, unallocated_amount, status, docstatus, date
                FROM `tabBank Transaction`
                WHERE TRIM(reference_number) = %s AND bank_account = %s AND company = %s
                  AND {doctype_type} = %s AND docstatus < 2
                ORDER BY allocated_amount DESC, name ASC
            """, (group.ref, group.bank_account, group.company, group.amount), as_dict=True)
            
            if len(members) > 1:
                # El primero es el que tiene mayor asignación, ese se conserva
                keep = members[0]
                for dup in members[1:]:
                    # Si ambos tienen asignaciones, no sugerimos limpieza automática para evitar romper cosas
                    if flt(keep.allocated_amount) >= 0.01 and flt(dup.allocated_amount) >= 0.01:
                        continue
                    
                    duplicates.append({
                        "reference": group.ref,
                        "date": keep.date,
                        "amount": group.amount,
                        "type": "Retiro" if doctype_type == "withdrawal" else "Depósito",
                        "original_name": keep.name,
                        "original_status": keep.status,
                        "duplicate_name": dup.name,
                        "duplicate_status": dup.status
                    })
    
    return duplicates

@frappe.whitelist()
def remove_duplicate_bank_transactions(duplicates_json):
    """Elimina o cancela los duplicados seleccionados."""
    import json
    duplicates = json.loads(duplicates_json)
    processed = 0
    errors = []
    
    for row in duplicates:
        dup_name = row.get("duplicate_name")
        if not dup_name: continue
        
        try:
            doc = frappe.get_doc("Bank Transaction", dup_name)
            if doc.docstatus == 1:
                doc.flags.ignore_permissions = True
                doc.cancel()
            else:
                frappe.delete_doc("Bank Transaction", dup_name, ignore_permissions=True)
            processed += 1
        except Exception as e:
            errors.append(f"Error procesando {dup_name}: {str(e)}")
            frappe.db.rollback()
            
    frappe.db.commit()
    return {"processed": processed, "errors": errors}


