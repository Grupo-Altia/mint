import frappe
import datetime

def _apply_branch_permissions(filters: dict) -> bool:
    ignore_perms = False
    has_branch_code = frappe.get_meta("Bank Account").has_field("branch_code")
    if has_branch_code:
        allowed_branches = frappe.get_list("VE Branch", pluck="name")
        extended_branches = set(allowed_branches)
        
        has_parent_branch = frappe.get_meta("VE Branch").has_field("parent_ve_branch")
        if has_parent_branch:
            for b in allowed_branches:
                parent = frappe.db.get_value("VE Branch", b, "parent_ve_branch")
                if parent:
                    extended_branches.add(parent)
                    
        if extended_branches:
            filters["branch_code"] = ["in", list(extended_branches)]
            ignore_perms = True
            
    return ignore_perms

@frappe.whitelist(methods=["GET"])
@frappe.read_only()
def get_list(company: str, show_disabled: bool = False):
    if company:
        frappe.has_permission("Company", "read", doc=company, throw=True)

    companies = [company]
    parent_company = frappe.db.get_value("Company", company, "parent_company")
    if parent_company:
        companies.append(parent_company)

    filters = {
        "is_company_account": 1,
        "company": ["in", companies]
    }

    if not show_disabled:
        filters["disabled"] = 0

    ignore_perms = _apply_branch_permissions(filters)

    bank_accounts = frappe.get_list("Bank Account", 
                                    filters=filters, 
                                    order_by="is_default desc",
                                    ignore_permissions=ignore_perms,
                                    fields=["name", "account", "company", "account_name", "is_default", "bank", "account_type", "account_subtype", "bank_account_no", "last_integration_date", "is_credit_card"])

    for bank_account in bank_accounts:
        bank_account.account_currency = frappe.get_cached_value("Account", bank_account.account, "account_currency")
        if bank_account.bank:
            bank_account.bank_logo = frappe.get_cached_value("Bank", bank_account.bank, "bank_logo") or frappe.get_cached_value("Bank", bank_account.bank, "image")

    
    return bank_accounts

@frappe.whitelist(methods=["GET"])
def get_closing_balance_as_per_statement(bank_account: str, date: str):
    """
        Get the closing balance as per statement for a bank account and date
    """
    frappe.has_permission("Bank Account", "read", doc=bank_account, throw=True)
    company = frappe.db.get_value("Bank Account", bank_account, "company")
    if company:
        frappe.has_permission("Company", "read", doc=company, throw=True)
    latest_balance = frappe.get_list("Mint Bank Statement Balance", filters={
        "bank_account": bank_account,
        "date": ["<=", date]
    }, fields=["balance", "date"], order_by="date desc", limit=1)

    if latest_balance:
        return {
            "balance": latest_balance[0].balance,
            "date": latest_balance[0].date
        }
    return {
        "balance": 0,
        "date": None
    }

@frappe.whitelist()
def set_closing_balance_as_per_statement(bank_account: str, date: str | datetime.date, balance: float):
    """
    Set the closing balance as per statement for a bank account and date
    """
    frappe.has_permission("Bank Account", "read", doc=bank_account, throw=True)
    company = frappe.db.get_value("Bank Account", bank_account, "company")
    if company:
        frappe.has_permission("Company", "read", doc=company, throw=True)

    existing = frappe.db.exists("Mint Bank Statement Balance", {
        "bank_account": bank_account,
        "date": date
    })

    if existing:
        doc = frappe.get_doc("Mint Bank Statement Balance", existing)
        doc.balance = balance
        doc.save()
    else:
        doc = frappe.new_doc("Mint Bank Statement Balance")
        doc.bank_account = bank_account
        doc.date = date
        doc.balance = balance
        doc.save()

@frappe.whitelist(methods=["GET"])
@frappe.read_only()
def get_allowed_mode_of_payments(company: str):
    if company:
        frappe.has_permission("Company", "read", doc=company, throw=True)
    companies = [company]
    parent_company = frappe.db.get_value("Company", company, "parent_company")
    if parent_company:
        companies.append(parent_company)

    # Retrieve Bank Accounts the user is allowed to see in this company
    filters = {
        "is_company_account": 1,
        "company": ["in", companies],
        "disabled": 0
    }

    ignore_perms = _apply_branch_permissions(filters)

    allowed_banks = frappe.get_list("Bank Account", filters=filters, pluck="account", ignore_permissions=ignore_perms)
    
    if not allowed_banks:
        return []

    # Get Mode of Payment where default_account matches the bank's account
    mops = frappe.get_all("Mode of Payment Account", filters={"company": company, "default_account": ["in", allowed_banks]}, pluck="parent")
    
    # Return unique modes of payment
    return list(set(mops))