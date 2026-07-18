import frappe
from mint.apis.reconciliation import get_linked_payments
def run():
    bt_name = frappe.db.get_value("Bank Transaction", {"reference_number": "23375805"})
    if not bt_name:
        print("Not found")
        return
    print(f"Testing for BT: {bt_name}")
    matches = get_linked_payments(bt_name)
    for m in matches:
        print(f"DocType: {m.doctype}, Name: {m.name}, Paid: {m.paid_amount}")
