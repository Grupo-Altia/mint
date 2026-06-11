import frappe

# Importar funciones de Mint para el parche (esto no importa mucho porque tecnicamente no se esta usando)
try:
    import mint.mint.utils.matching
    original_get_matching_rules = mint.mint.utils.matching.get_matching_rules
except ImportError:
    original_get_matching_rules = None

def modify_venezuela_reference(doc, method=None):
    """
    Hook: before_insert en Bank Transaction
    """
    if not doc.bank_account or not getattr(doc, "reference_number", None):
        return

    selected_rule = frappe.db.get_value("Bank Account", doc.bank_account, "custom_reference_format_rule")
    
    # Esto convierte el Ref N en texto plano y anula cualquier funcion de excel 
    clean_ref = str(doc.reference_number).strip()
    doc.reference_number = clean_ref 

    #Si no hay regla, salimos pero la referencia ya se guarda como texto plano
    if not selected_rule:
        return

    # Reglas que eran destacadas en el documento (dfaltan las no destacadas porque ya el sistema las hace automaticamente pero.... Mint tambien ? probablemente no)
    if selected_rule == "Duplicar si son 4 dígitos" or selected_rule == "Duplicar referencia":
        doc.reference_number = clean_ref + clean_ref
        
    elif selected_rule == "Tomar los últimos 8 dígitos":
        doc.reference_number = clean_ref[-8:]

    elif selected_rule == "Agregar 6 ceros a la izquierda":
        doc.reference_number = "000000" + clean_ref

    elif selected_rule == "Agregar 3 ceros a la izquierda":
        doc.reference_number = "000" + clean_ref

    elif selected_rule == "Agregar 7 ceros a la izquierda":
        doc.reference_number = "0000000" + clean_ref

    elif selected_rule == "Agregar 1 cero a la izquierda":
        doc.reference_number = "0" + clean_ref

def custom_get_matching_rules(bank_transaction_doc):
    """
    Parche (Monkey Patch): Filtra reglas de Mint por banco
    """
    if not original_get_matching_rules:
        return []

    all_rules = original_get_matching_rules(bank_transaction_doc)
    filtered = []
    current_bank = bank_transaction_doc.bank_account
    
    for rule in all_rules:
        rule_bank = frappe.db.get_value("Mint Bank Transaction Rule", rule.name, "bank_account")
        
        if not rule_bank or rule_bank == current_bank:
            filtered.append(rule)
            
    return filtered

# Aplicar parche
if original_get_matching_rules:
    mint.mint.utils.matching.get_matching_rules = custom_get_matching_rules