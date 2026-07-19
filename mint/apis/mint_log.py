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

def log_mint_error(title=None, description=None, message=None, *args):
    desc = description if description is not None else (message or "")
    if args:
        try:
            desc = desc % args
        except Exception:
            desc = "\n".join(map(str, (desc, *args)))
            
    tb = frappe.get_traceback()
    if tb and tb not in desc:
        desc = f"{desc}\n\n{tb}"
        
    title_str = str(title or "Error")[:140]
    
    create_mint_log(
        title=title_str,
        description=desc,
        log_type="Error",
        user=getattr(frappe, "session", None) and getattr(frappe.session, "user", None)
    )

def log_mint_warning(title=None, description=None, message=None, *args):
    desc = description if description is not None else (message or "")
    if args:
        try:
            desc = desc % args
        except Exception:
            desc = "\n".join(map(str, (desc, *args)))
            
    frappe.enqueue(
        "mint.apis.mint_log.create_mint_log",
        queue="short",
        title=str(title or "Warning")[:140],
        description=desc,
        log_type="Warning",
        user=getattr(frappe, "session", None) and getattr(frappe.session, "user", None)
    )

def log_mint_info(title=None, description=None, message=None, *args):
    desc = description if description is not None else (message or "")
    if args:
        try:
            desc = desc % args
        except Exception:
            desc = "\n".join(map(str, (desc, *args)))
            
    frappe.enqueue(
        "mint.apis.mint_log.create_mint_log",
        queue="short",
        title=str(title or "Info")[:140],
        description=desc,
        log_type="Info",
        user=getattr(frappe, "session", None) and getattr(frappe.session, "user", None)
    )

