import frappe
import datetime

@frappe.whitelist(methods=["GET"])
@frappe.read_only()
def get_list(company: str, show_disabled: bool = False):
    if company:
        frappe.has_permission("Company", "read", doc=company, throw=True)

    filters = {
        "is_company_account": 1,
        "company": company
    }

    if not show_disabled:
        filters["disabled"] = 0

    bank_accounts = frappe.get_list("Bank Account", 
                                    filters=filters, 
                                    order_by="is_default desc",
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
    # Retrieve Bank Accounts the user is allowed to see in this company
    filters = {
        "is_company_account": 1,
        "company": company,
        "disabled": 0
    }
    allowed_banks = frappe.get_list("Bank Account", filters=filters, pluck="account")
    
    if not allowed_banks:
        return []

    # Get Mode of Payment where default_account matches the bank's account
    mops = frappe.get_all("Mode of Payment Account", filters={"company": company, "default_account": ["in", allowed_banks]}, pluck="parent")
    
    # Return unique modes of payment
    return list(set(mops))