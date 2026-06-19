import frappe

def create_bank_reference_rules():
    rules = [
        {
            "rule_name": "Tomar los últimos 8 dígitos",
            "rule": "f'{str(reference_number)[-8:]}'"
        },
        {
            "rule_name": "Duplicar referencia",
            "rule": "f'{reference_number}{reference_number}'"
        },
        {
            "rule_name": "Agregar 6 ceros a la izquierda",
            "rule": "f'000000{reference_number}'"
        },
        {
            "rule_name": "Agregar 3 ceros a la izquierda",
            "rule": "f'000{reference_number}'"
        },
        {
            "rule_name": "Agregar 7 ceros a la izquierda",
            "rule": "f'0000000{reference_number}'"
        },
        {
            "rule_name": "Agregar 1 cero a la izquierda",
            "rule": "f'0{reference_number}'"
        }
    ]
    
    for rule_data in rules:
        if not frappe.db.exists("Bank Reference Rule", {"rule_name": rule_data["rule_name"]}):
            doc = frappe.new_doc("Bank Reference Rule")
            doc.update(rule_data)
            doc.insert(ignore_permissions=True)
            
    frappe.db.commit()

import frappe
import json

def update_conciliaciones_workspace():
    if not frappe.db.exists("Workspace", "Conciliaciones Bancarias"):
        return
        
    doc = frappe.get_doc("Workspace", "Conciliaciones Bancarias")
    
    # 1. Update shortcuts
    new_shortcuts = []
    has_mint = False
    for s in doc.shortcuts:
        if s.label == "Conciliar Banco" or s.label == "Conciliación Bancaria":
            s.label = "Conciliación Bancaria"
            s.url = "/mint"
            s.link_to = ""
            new_shortcuts.append(s)
            has_mint = True
            
    if not has_mint:
        doc.append("shortcuts", {
            "type": "URL",
            "url": "/mint",
            "label": "Conciliación Bancaria",
            "color": "Green"
        })
    else:
        doc.shortcuts = new_shortcuts

    # 2. Update links
    new_links = []
    for l in doc.links:
        if l.label in ["Registros Bancarios", "Transacciones Bancarias", "Cuentas Bancarias"]:
            new_links.append(l)
        elif l.type == "Card Break" and "Documentos" in l.label:
            l.label = "Documentos"
            new_links.append(l)
            
    doc.links = new_links
    
    # 3. Update content blocks
    if doc.content:
        try:
            content_blocks = json.loads(doc.content)
            new_blocks = []
            for b in content_blocks:
                if b.get("type") == "shortcut":
                    if b["data"].get("shortcut_name") == "Conciliar Banco":
                        b["data"]["shortcut_name"] = "Conciliación Bancaria"
                        new_blocks.append(b)
                    elif b["data"].get("shortcut_name") == "Conciliación Bancaria":
                        new_blocks.append(b)
                    # skip Importar Extracto
                elif b.get("type") == "header":
                    if "Documentos y Catálogos" in b["data"].get("text", ""):
                        b["data"]["text"] = b["data"]["text"].replace("Documentos y Catálogos", "Documentos")
                    new_blocks.append(b)
                elif b.get("type") == "card":
                    cname = b["data"].get("card_name")
                    if cname in ["Herramientas de Conciliación", "Informes de Conciliación"]:
                        continue # Skip these cards
                    new_blocks.append(b)
                else:
                    new_blocks.append(b)
            doc.content = json.dumps(new_blocks)
        except Exception as e:
            pass
    
    doc.flags.ignore_permissions = True
    doc.save()

    # Hide standalone Workspace "Conciliación Bancaria" if it exists
    if frappe.db.exists("Workspace", "Conciliación Bancaria"):
        mint_ws = frappe.get_doc("Workspace", "Conciliación Bancaria")
        mint_ws.is_hidden = 1
        if hasattr(mint_ws, 'public'):
            mint_ws.public = 0
        mint_ws.flags.ignore_permissions = True
        mint_ws.save()

    # Fix Administración Workspace (remove old Bank Reconciliation Tool)
    if frappe.db.exists("Workspace", "Administración"):
        admin_ws = frappe.get_doc("Workspace", "Administración")
        admin_links = []
        modified = False
        for l in admin_ws.links:
            if l.link_to == "Bank Reconciliation Tool":
                modified = True
                continue
            admin_links.append(l)
            
        if modified:
            admin_ws.links = admin_links
            admin_ws.flags.ignore_permissions = True
            admin_ws.save()

def after_install():
    create_bank_reference_rules()

def after_migrate():
    create_bank_reference_rules()
    update_conciliaciones_workspace()
    frappe.db.commit()
