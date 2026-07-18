import frappe
from frappe.utils import now_datetime

def create_mint_log(title, description, log_type="Info", user=None):
    if not user:
        user = frappe.session.user if getattr(frappe, "session", None) and getattr(frappe.session, "user", None) else "Administrator"
        
    try:
        frappe.get_doc({
            "doctype": "Mint Log",
            "title": title,
            "log_type": log_type,
            "description": description,
            "user": user,
            "date": now_datetime()
        }).insert(ignore_permissions=True)
    except Exception as e:
        # Fallback to standard error log if this fails
        frappe.log_error(
            title=f"Error creating Mint Log: {title}",
            message=f"Original Description: {description}\n\nError: {e}\n{frappe.get_traceback()}"
        )

def log_mint_error(title, description):
    frappe.enqueue(
        "mint.apis.mint_log.create_mint_log",
        queue="short",
        title=title,
        description=f"{description}\n\n{frappe.get_traceback()}" if frappe.get_traceback() else description,
        log_type="Error",
        user=getattr(frappe, "session", None) and getattr(frappe.session, "user", None)
    )

def log_mint_warning(title, description):
    frappe.enqueue(
        "mint.apis.mint_log.create_mint_log",
        queue="short",
        title=title,
        description=description,
        log_type="Warning",
        user=getattr(frappe, "session", None) and getattr(frappe.session, "user", None)
    )

def log_mint_info(title, description):
    frappe.enqueue(
        "mint.apis.mint_log.create_mint_log",
        queue="short",
        title=title,
        description=description,
        log_type="Info",
        user=getattr(frappe, "session", None) and getattr(frappe.session, "user", None)
    )

