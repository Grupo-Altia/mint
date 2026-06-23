import frappe

@frappe.whitelist()
def get_bank_transactions(bank_account=None, from_date=None, to_date=None, all_transactions=False):
    # returns bank transactions for a bank account
    filters = []
    
    # 1. Validación de seguridad: Solo agregamos el filtro si existe una cuenta
    if bank_account:
        frappe.has_permission("Bank Account", "read", doc=bank_account, throw=True)
        company = frappe.db.get_value("Bank Account", bank_account, "company")
        if company:
            frappe.has_permission("Company", "read", doc=company, throw=True)
        filters.append(["bank_account", "=", bank_account])
    else:
        # Prevent fetching all transactions for all companies if no bank_account is provided
        # Filter by allowed companies
        allowed_companies = frappe.get_all("Company", pluck="name")
        if allowed_companies:
            filters.append(["company", "in", allowed_companies])
        else:
            return []
        
    filters.append(["docstatus", "=", 1])
    
    if not all_transactions:
        filters.append(["unallocated_amount", ">", 0.0])
    if to_date:
        filters.append(["date", "<=", to_date])
    if from_date:
        filters.append(["date", ">=", from_date])

    transactions = frappe.get_list(
        "Bank Transaction",
        fields=[
            "date",
            "deposit",
            "withdrawal",
            "currency",
            "description",
            "transaction_type",
            "name",
            "bank_account",
            "company",
            "allocated_amount",
            "unallocated_amount",
            "reference_number",
            "party_type",
            "party",
            "status",
            "matched_rule"
        ],
        filters=filters,
        order_by="date",
    )
    return transactions


@frappe.whitelist(methods=["GET"])
def get_older_unreconciled_transactions(bank_account: str, from_date: str):
    """
        Get the older unreconciled transactions for a bank account
    """
    frappe.has_permission("Bank Account", "read", doc=bank_account, throw=True)
    company = frappe.db.get_value("Bank Account", bank_account, "company")
    if company:
        frappe.has_permission("Company", "read", doc=company, throw=True)
    count = frappe.db.count("Bank Transaction", filters={
        "bank_account": bank_account,
        "date": ["<", from_date],
        "docstatus": 1,
        "unallocated_amount": [">", 0.0],
    })

    if count > 0:

        oldest_transaction = frappe.db.get_list("Bank Transaction", filters={
            "bank_account": bank_account,
            "date": ["<", from_date],
            "docstatus": 1,
            "unallocated_amount": [">", 0.0],
        }, fields=["date"], order_by="date", limit=1)

        return {
            "count": count,
            "oldest_date": oldest_transaction[0].date
        }
    return {
        "count": 0,
        "oldest_date": None
    }