import frappe
import json
from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import get_linked_payments as erpnext_get_linked_payments
from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import check_matching

try:
    import mint.mint.utils.matching
    original_get_matching_rules = mint.mint.utils.matching.get_matching_rules
except ImportError:
    original_get_matching_rules = None

def modify_venezuela_reference(doc, method=None):
    """
    Hook: before_insert en Bank Transaction
    """
    if getattr(doc, "reference_number", None):
        # Esto convierte el Ref N en texto plano y anula cualquier funcion de excel 
        doc.reference_number = str(doc.reference_number).strip()

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

def apply_format_rule(rule, ref):
    clean_ref = str(ref).strip()
    if rule in ["Duplicar si son 4 dígitos", "Duplicar referencia"]:
        return clean_ref + clean_ref
    elif rule == "Tomar los últimos 8 dígitos":
        return clean_ref[-8:]
    elif rule == "Agregar 6 ceros a la izquierda":
        return "000000" + clean_ref
    elif rule == "Agregar 3 ceros a la izquierda":
        return "000" + clean_ref
    elif rule == "Agregar 7 ceros a la izquierda":
        return "0000000" + clean_ref
    elif rule == "Agregar 1 cero a la izquierda":
        return "0" + clean_ref
    return clean_ref

@frappe.whitelist()
def get_linked_payments(
    bank_transaction_name,
    document_types=None,
    from_date=None,
    to_date=None,
    filter_by_reference_date=None,
    from_reference_date=None,
    to_reference_date=None,
):
    if isinstance(document_types, str):
        document_types = json.loads(document_types)
        
    # 1. Llamar a la logica original de ERPNext (búsqueda normal)
    matches = erpnext_get_linked_payments(
        bank_transaction_name,
        document_types,
        from_date,
        to_date,
        filter_by_reference_date,
        from_reference_date,
        to_reference_date
    )
    
    if matches:
        return matches
        
    # 2. Si no hay resultados, intentar aplicar la regla de los bancos origen
    transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
    original_ref = transaction.reference_number
    if not original_ref:
        return []
        
    # Obtener bancos con reglas
    banks_with_rules = frappe.get_all("Bank", filters={"custom_reference_format_rule": ["!=", ""]}, fields=["name", "custom_reference_format_rule"])
    
    for bank in banks_with_rules:
        # Aplicar la regla
        modified_ref = apply_format_rule(bank.custom_reference_format_rule, original_ref)
        if modified_ref == original_ref:
            continue
            
        # Reemplazar temporalmente el numero de referencia en la transaccion (en memoria)
        transaction.reference_number = modified_ref
        
        bank_account = frappe.db.get_values(
            "Bank Account", transaction.bank_account, ["account", "company"], as_dict=True
        )[0]
        
        # Buscar coincidencias con la referencia modificada
        new_matches = check_matching(
            bank_account.account,
            bank_account.company,
            transaction,
            document_types,
            from_date,
            to_date,
            filter_by_reference_date,
            from_reference_date,
            to_reference_date,
        )
        
        # Restaurar la referencia original
        transaction.reference_number = original_ref
        
        if new_matches:
            # Filtrar solo los documentos que tengan configurado este banco origen
            valid_matches = []
            for match in new_matches:
                if match.doctype == "Payment Entry":
                    banco_origen = frappe.db.get_value("Payment Entry", match.name, "custom_banco_origen")
                    if banco_origen == bank.name:
                        valid_matches.append(match)
                # Opcionalmente se podria extender a otros doctypes si tienen custom_banco_origen
            
            if valid_matches:
                return valid_matches
                
    return []