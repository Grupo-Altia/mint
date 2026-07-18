import frappe

def create_mint_log_doctype():
    doctype_name = "Mint Log"
    if not frappe.db.exists("DocType", doctype_name):
        doc = frappe.get_doc({
            "doctype": "DocType",
            "name": doctype_name,
            "module": "Mint",
            "custom": 0,
            "is_submittable": 0,
            "autoname": "format:MINT-LOG-{YYYY}-{MM}-{####}",
            "fields": [
                {
                    "fieldname": "title",
                    "label": "Title",
                    "fieldtype": "Data",
                    "reqd": 1
                },
                {
                    "fieldname": "log_type",
                    "label": "Type",
                    "fieldtype": "Select",
                    "options": "Error\nWarning\nInfo\nSuccess",
                    "reqd": 1
                },
                {
                    "fieldname": "date",
                    "label": "Date",
                    "fieldtype": "Datetime",
                    "default": "Now",
                    "reqd": 1
                },
                {
                    "fieldname": "user",
                    "label": "User",
                    "fieldtype": "Link",
                    "options": "User"
                },
                {
                    "fieldname": "description",
                    "label": "Description",
                    "fieldtype": "Code",
                    "options": "JSON"
                }
            ],
            "permissions": [
                {
                    "role": "System Manager",
                    "read": 1,
                    "write": 1,
                    "create": 1,
                    "delete": 1
                }
            ]
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        print("Mint Log DocType created.")
    else:
        print("Mint Log already exists.")

if __name__ == "__main__":
    create_mint_log_doctype()
