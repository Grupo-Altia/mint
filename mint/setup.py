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

def after_install():
    create_bank_reference_rules()

def after_migrate():
    create_bank_reference_rules()
