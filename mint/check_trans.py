import frappe

def execute():
    trans = frappe.translate.get_all_translations("es")
    print("KEYS IN ES:")
    for k in ["Starts with", "Select Bank", "Configure settings for Mint.", "Mint", "Number of days to match transfers"]:
        print(f"'{k}': '{trans.get(k)}'")
