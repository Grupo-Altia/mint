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
        return
    except Exception as e:
        primero = e

    # Fallback al Error Log del núcleo. Va en su propio try porque TAMBIÉN puede
    # fallar: `frappe.log_error` arma un `frappe.get_doc({"doctype": "Error Log"})`,
    # que resuelve el módulo del doctype y lo importa. En la corrida del 2026-08-01
    # eso levantó `ImportError: No module named 'mint.mint.doctype.error_log'`.
    try:
        frappe.log_error(
            title=f"Error creating Mint Log: {title}",
            message=f"Original Description: {description}\n\nError: {primero}\n{frappe.get_traceback()}"
        )
        return
    except Exception as segundo:
        # Último recurso: la salida del worker. Nunca se propaga.
        #
        # Esta función se llama desde el `except` de quien la usa, así que una
        # excepción acá NO la atrapa nadie: sube y se lleva puesto el proceso
        # entero. Fue exactamente lo que pasó con el barrido nocturno: un cobro
        # cuyo depósito todavía no estaba en el extracto —caso esperado, para eso
        # está el reintento— hacía caer `_approve_drafts` a mitad del lote pese a
        # tener su try/except por cobro. Del 01 al 05 de agosto no completó una
        # sola noche; el 01 murió tras conciliar 25 de la tanda.
        #
        # Registrar es auxiliar: que falle puede costar visibilidad, nunca el
        # trabajo que se estaba haciendo.
        try:
            print(f"[mint_log] no se pudo registrar '{title}': {primero} | fallback: {segundo}")
        except Exception:
            pass

def log_mint_error(title=None, description=None, message=None, *args):
    """Registra un error de mint. NUNCA levanta: ver la nota en `create_mint_log`.

    El envoltorio cubre el armado del mensaje, no sólo la escritura: `get_traceback`
    y el formateo corren antes de llegar a `create_mint_log`.
    """
    try:
        _log_mint_error(title, description, message, *args)
    except Exception as exc:
        try:
            print(f"[mint_log] fallo registrando el error '{title}': {exc}")
        except Exception:
            pass


def _log_mint_error(title=None, description=None, message=None, *args):
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
    """Registra una advertencia de mint. NUNCA levanta.

    Encola en vez de escribir en línea, y `frappe.enqueue` falla si Redis no está:
    el mismo motivo por el que esto va envuelto.
    """
    try:
        _log_mint_warning(title, description, message, *args)
    except Exception as exc:
        try:
            print(f"[mint_log] fallo registrando la advertencia '{title}': {exc}")
        except Exception:
            pass


def _log_mint_warning(title=None, description=None, message=None, *args):
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

