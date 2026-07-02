import frappe
def run():
    doc = frappe.get_meta("ISP Payment Entry")
    for d in doc.fields:
        if d.fieldtype == "Link":
            print(f"{d.fieldname} -> {d.options}")
