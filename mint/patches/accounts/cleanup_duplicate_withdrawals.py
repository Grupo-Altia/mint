import frappe
from frappe.utils import flt

def _duplicate_groups() -> list:
    return frappe.db.sql(
        """
        SELECT TRIM(reference_number) AS ref, bank_account, company, COUNT(*) AS cnt
        FROM `tabBank Transaction`
        WHERE withdrawal > 0 AND docstatus < 2
          AND reference_number IS NOT NULL AND TRIM(reference_number) != ''
        GROUP BY TRIM(reference_number), bank_account, company
        HAVING cnt > 1
        """,
        as_dict=True,
    )

def _members(group) -> list:
    return frappe.db.sql(
        """
        SELECT name, withdrawal, allocated_amount, unallocated_amount, status, docstatus
        FROM `tabBank Transaction`
        WHERE TRIM(reference_number) = %s AND bank_account = %s AND company = %s
          AND withdrawal > 0 AND docstatus < 2
        ORDER BY withdrawal ASC
        """,
        (group.ref, group.bank_account, group.company),
        as_dict=True,
    )

def _neutralize_redundant(name: str) -> None:
    doc = frappe.get_doc("Bank Transaction", name)
    if doc.docstatus == 1:
        doc.flags.ignore_permissions = True
        doc.cancel()
    else:
        frappe.delete_doc("Bank Transaction", name, ignore_permissions=True)

def execute(dry_run: bool = False) -> None:
    groups = _duplicate_groups()
    counts = {"EXACT_DUP": 0, "MANUAL": 0}
    manual: list = []
    processed = 0

    for group in groups:
        members = _members(group)
        # Verify if they are exact duplicates (same withdrawal)
        # Find exactly same withdrawal amounts
        amounts = {}
        for m in members:
            wth = flt(m.withdrawal)
            if wth not in amounts:
                amounts[wth] = []
            amounts[wth].append(m)
        
        for wth, exact_members in amounts.items():
            if len(exact_members) > 1:
                # We have exact duplicates for this withdrawal amount!
                # Keep one, neutralize the rest
                # Sort by allocated_amount to keep the one that is allocated
                exact_members.sort(key=lambda x: flt(x.allocated_amount), reverse=True)
                keep = exact_members[0]
                for dup in exact_members[1:]:
                    if flt(dup.allocated_amount) >= 0.01:
                        # Both have allocations! Manual intervention
                        counts["MANUAL"] += 1
                        manual.append((group.ref, "Both allocated"))
                    else:
                        counts["EXACT_DUP"] += 1
                        if not dry_run:
                            try:
                                _neutralize_redundant(dup.name)
                                processed += 1
                            except Exception as e:
                                print(f"Error neutralizing {dup.name}: {e}")
                                frappe.db.rollback()
                        
    if not dry_run:
        frappe.db.commit()

    print(f"EXACT_DUP  (cancelar redundante exacto): {counts['EXACT_DUP']}")
    print(f"MANUAL     (omitidos)                 : {counts['MANUAL']}")
    print(f"Procesados OK: {processed}")
