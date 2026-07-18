import frappe
print([f.fieldname for f in frappe.get_meta("Bank Account").fields])
