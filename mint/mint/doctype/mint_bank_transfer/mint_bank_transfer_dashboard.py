from frappe import _

def get_data():
	return {
		"fieldname": "payment_entry",
		"internal_links": {
			"Bank Transaction": ["payment_entries", "payment_entry"]
		},
		"transactions": [
			{
				"label": _("Conexiones"),
				"items": ["Bank Transaction"]
			}
		]
	}
