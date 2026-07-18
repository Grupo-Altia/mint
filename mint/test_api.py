import frappe
import json

def run():
    meta = frappe.get_meta("VE Branch")
    print("VE Branch is_tree:", meta.is_tree)
    print("VE Branch fields:")
    for f in meta.fields:
        print(f.fieldname, "-", f.fieldtype)

    
run()
