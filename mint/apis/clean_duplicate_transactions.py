import frappe
from frappe import _
from frappe.utils import flt, add_days, today, sbool


def _are_references_matching(ref1: str, ref2: str, orig_ref1: str = "", orig_ref2: str = "", bank_rules: list = None) -> bool:
    """Verifica si dos referencias corresponden a la misma transacción física.
    
    C1 FIX: Transacciones sin referencia NUNCA se consideran duplicadas entre sí
    para evitar borrar comisiones o cargos idénticos sin referencia del mismo día.
    """
    r1 = str(ref1 or "").strip()
    r2 = str(ref2 or "").strip()
    o1 = str(orig_ref1 or "").strip()
    o2 = str(orig_ref2 or "").strip()

    # Si ambas están sin referencia/origen, NUNCA considerar duplicadas
    if not r1 and not r2 and not o1 and not o2:
        return False

    # Coincidencia exacta de referencia u origen
    if r1 and (r1 == r2 or r1 == o2):
        return True
    if r2 and (r2 == r1 or r2 == o1):
        return True

    # Coincidencia sin ceros a la izquierda (requiere longitud despojada >= 5 dígitos)
    c1 = r1.lstrip('0')
    c2 = r2.lstrip('0')
    co1 = o1.lstrip('0')
    co2 = o2.lstrip('0')

    if len(c1) >= 5 and len(c2) >= 5 and c1 == c2:
        return True
    if len(c1) >= 5 and len(co2) >= 5 and c1 == co2:
        return True
    if len(c2) >= 5 and len(co1) >= 5 and c2 == co1:
        return True

    # Reglas de referencia de Mint
    if bank_rules:
        from mint.apis.reconciliation import check_rules_match
        if r1 and r2 and check_rules_match(bank_rules, r1, r2)[0]:
            return True
        if r1 and o2 and check_rules_match(bank_rules, r1, o2)[0]:
            return True
        if r2 and o1 and check_rules_match(bank_rules, r2, o1)[0]:
            return True

    return False


@frappe.whitelist()
def clean_unreconciled_duplicates(
    bank_account: str = None,
    company: str = None,
    from_date: str = None,
    to_date: str = None,
    dry_run: bool = True,
) -> dict:
    """
    Busca y cancela transacciones bancarias duplicadas verdaderas.
    
    C2 FIX: Permisos estrictos ("Accounts Manager", "System Manager"), dry_run=True por defecto,
    uso de sbool() y exigencia de alcance (bank_account o company).
    A1 FIX: Cancela (docstatus=2) en vez de borrar para preservar rastro forense y evitar bucle de reimportación.
    """
    # C2 FIX: Guarda de permisos de rol
    frappe.only_for(["Accounts Manager", "System Manager"])

    # C2 FIX: Garantizar que dry_run sea booleano (interpretando "0", "false", etc.)
    dry_run_bool = sbool(dry_run) if dry_run is not None else True

    # C2 FIX: Exigir parámetro de alcance
    if not bank_account and not company:
        frappe.throw(_("Debe especificar al menos 'bank_account' o 'company' para limitar la búsqueda de duplicados."))

    # Acotación por ventana temporal (por defecto últimos 60 días)
    if not from_date:
        from_date = add_days(today(), -60)
    if not to_date:
        to_date = today()

    filters = {
        "docstatus": ["!=", 2],
        "date": ["between", [from_date, to_date]],
    }
    if bank_account:
        filters["bank_account"] = bank_account
    if company:
        filters["company"] = company

    txs = frappe.get_all(
        "Bank Transaction",
        filters=filters,
        fields=[
            "name", "bank_account", "company", "date",
            "deposit", "withdrawal", "status", "reference_number",
            "bancaribe_origin_reference", "description", "creation", "docstatus"
        ],
        order_by="creation ASC"
    )

    grouped = {}
    for tx in txs:
        dep = flt(tx.deposit)
        wd = flt(tx.withdrawal)
        if dep <= 0 and wd <= 0:
            continue

        key = (tx.bank_account, str(tx.date), dep, wd)
        grouped.setdefault(key, []).append(tx)

    from mint.apis.reconciliation import get_bank_rules

    bank_rules_cache = {}
    cancelled_count = 0
    scanned_groups = 0
    details = []

    for key, group in grouped.items():
        if len(group) <= 1:
            continue

        bank_acc_name = key[0]
        if bank_acc_name not in bank_rules_cache:
            bank_name = frappe.get_cached_value("Bank Account", bank_acc_name, "bank")
            bank_rules_cache[bank_acc_name] = get_bank_rules(bank_name) if bank_name else []

        bank_rules = bank_rules_cache[bank_acc_name]

        # Sub-agrupar solo aquellas que coincidan en referencia (C1 FIX)
        sub_groups = []
        for tx in group:
            matched_sg = None
            for sg in sub_groups:
                representative = sg[0]
                if _are_references_matching(
                    tx.reference_number, representative.reference_number,
                    tx.bancaribe_origin_reference, representative.bancaribe_origin_reference,
                    bank_rules
                ):
                    matched_sg = sg
                    break

            if matched_sg:
                matched_sg.append(tx)
            else:
                sub_groups.append([tx])

        for sg in sub_groups:
            if len(sg) <= 1:
                continue

            scanned_groups += 1

            reconciled = []
            unreconciled = []

            for tx in sg:
                has_pe = bool(frappe.db.count("Bank Transaction Payments", {"parent": tx.name}))
                if tx.status == "Reconciled" or has_pe:
                    reconciled.append(tx)
                else:
                    unreconciled.append(tx)

            to_cancel = []

            if reconciled and unreconciled:
                to_cancel = unreconciled
            elif len(unreconciled) > 1 and not reconciled:
                to_cancel = unreconciled[1:]

            for target in to_cancel:
                details.append({
                    "cancelled_transaction": target.name,
                    "bank_account": target.bank_account,
                    "date": str(target.date),
                    "deposit": target.deposit,
                    "withdrawal": target.withdrawal,
                    "reference_number": target.reference_number,
                    "status": target.status
                })

                if not dry_run_bool:
                    try:
                        doc = frappe.get_doc("Bank Transaction", target.name)
                        if doc.docstatus == 1:
                            # A1 FIX: Cancelar en lugar de borrar
                            doc.cancel()
                            cancelled_count += 1
                        elif doc.docstatus == 0:
                            # Para borradores (docstatus=0), eliminar sin force
                            frappe.delete_doc("Bank Transaction", target.name, force=False, ignore_permissions=False)
                            cancelled_count += 1
                    except Exception:
                        frappe.log_error(
                            title=f"Error cancelando duplicado Bank Transaction {target.name}",
                            message=frappe.get_traceback()
                        )

    if not dry_run_bool and cancelled_count > 0:
        frappe.db.commit()

    return {
        "status": "success",
        "scanned_duplicate_groups": scanned_groups,
        "cancelled_count": cancelled_count if not dry_run_bool else len(details),
        "dry_run": dry_run_bool,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "details": details
    }
