import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def create_mint_custom_fields():
    custom_fields = {
        "Bank Account": [
            {
                "fieldname": "mint_opening_date",
                "label": "Fecha de Inicio de Operaciones (Mint)",
                "fieldtype": "Date",
                "insert_after": "company",
                "description": "Si se define, el sistema ignorará cualquier saldo o transacción previa a esta fecha para el cálculo de la conciliación en Mint.",
                "translatable": 0
            }
        ]
    }
    
    create_custom_fields(custom_fields)
