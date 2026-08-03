import frappe
from frappe import _
from frappe.utils import flt


def _are_references_matching(ref1: str, ref2: str, orig_ref1: str = "", orig_ref2: str = "", bank_rules: list = None) -> bool:
    """Verifica si dos referencias corresponden a la misma transacción física."""
    r1 = str(ref1 or "").strip()
    r2 = str(ref2 or "").strip()
    o1 = str(orig_ref1 or "").strip()
    o2 = str(orig_ref2 or "").strip()

    if not r1 and not r2 and not o1 and not o2:
        return True  # Ambas sin referencia en la misma fecha y monto

    # Coincidencia exacta
    if r1 and (r1 == r2 or r1 == o2):
        return True
    if r2 and (r2 == r1 or r2 == o1):
        return True

    # Coincidencia sin ceros a la izquierda
    c1 = r1.lstrip('0')
    c2 = r2.lstrip('0')
    co1 = o1.lstrip('0')
    co2 = o2.lstrip('0')

    if c1 and (c1 == c2 or c1 == co2):
        return True
    if c2 and (c2 == c1 or c2 == co1):
        return True

    # Reglas de referencia
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
def clean_unreconciled_duplicates(bank_account: str = None, company: str = None, dry_run: bool = False) -> dict:
    """
    Busca y elimina transacciones bancarias duplicadas verdaderas.
    
    Solo se consideran duplicadas si comparten:
    - Misma cuenta bancaria, fecha y monto.
    - Y coinciden en referencia (exacta, sin ceros iniciales o por reglas).
    
    Transacciones del mismo monto y fecha pero con referencias distintas pertenecen a distintos
    clientes y NO se eliminan.
    """
    filters = {"docstatus": ["!=", 2]}
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

    deleted_count = 0
    scanned_groups = 0
    details = []

    for key, group in grouped.items():
        if len(group) <= 1:
            continue

        bank_name = frappe.get_cached_value("Bank Account", key[0], "bank")
        bank_rules = get_bank_rules(bank_name) if bank_name else []

        # Sub-agrupar solo aquellas que coincidan en referencia
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

            to_delete = []

            if reconciled and unreconciled:
                to_delete = unreconciled
            elif len(unreconciled) > 1 and not reconciled:
                to_delete = unreconciled[1:]

            for target in to_delete:
                details.append({
                    "deleted_transaction": target.name,
                    "bank_account": target.bank_account,
                    "date": str(target.date),
                    "deposit": target.deposit,
                    "withdrawal": target.withdrawal,
                    "reference_number": target.reference_number,
                    "status": target.status
                })

                if not dry_run:
                    try:
                        doc = frappe.get_doc("Bank Transaction", target.name)
                        if doc.docstatus == 1:
                            doc.cancel()
                        frappe.delete_doc("Bank Transaction", target.name, force=True, ignore_permissions=True)
                        deleted_count += 1
                    except Exception:
                        frappe.log_error(
                            title=f"Error eliminando duplicado Bank Transaction {target.name}",
                            message=frappe.get_traceback()
                        )

    if not dry_run and deleted_count > 0:
        frappe.db.commit()

    return {
        "status": "success",
        "scanned_duplicate_groups": scanned_groups,
        "deleted_count": deleted_count if not dry_run else len(details),
        "dry_run": dry_run,
        "details": details
    }
