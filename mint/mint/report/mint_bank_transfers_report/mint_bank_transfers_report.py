# Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"fieldname": "name",
			"label": _("Transfer ID"),
			"fieldtype": "Link",
			"options": "Mint Bank Transfer",
			"width": 150,
		},
		{
			"fieldname": "date",
			"label": _("Date"),
			"fieldtype": "Date",
			"width": 120,
		},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 150,
		},
		{
			"fieldname": "from_bank_account",
			"label": _("From Bank Account"),
			"fieldtype": "Link",
			"options": "Bank Account",
			"width": 180,
		},
		{
			"fieldname": "to_bank_account",
			"label": _("To Bank Account"),
			"fieldtype": "Link",
			"options": "Bank Account",
			"width": 180,
		},
		{
			"fieldname": "reference_number",
			"label": _("Reference Number"),
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"fieldname": "amount",
			"label": _("Amount"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "status",
			"label": _("Status"),
			"fieldtype": "Data",
			"width": 100,
		},
		{
			"fieldname": "journal_entry",
			"label": _("Journal Entry"),
			"fieldtype": "Link",
			"options": "Journal Entry",
			"width": 150,
		},
	]


def get_data(filters):
	conditions = build_conditions(filters)
	return frappe.get_all(
		"Mint Bank Transfer",
		fields=[
			"name",
			"date",
			"company",
			"from_bank_account",
			"to_bank_account",
			"reference_number",
			"amount",
			"status",
			"journal_entry",
		],
		filters=conditions,
		order_by="date desc, name desc",
	)


def build_conditions(filters):
	conditions = {}

	if filters.get("company"):
		conditions["company"] = filters["company"]

	from_date = filters.get("from_date")
	to_date = filters.get("to_date")

	if from_date and to_date:
		conditions["date"] = ["between", [from_date, to_date]]
	elif from_date:
		conditions["date"] = [">=", from_date]
	elif to_date:
		conditions["date"] = ["<=", to_date]

	if filters.get("from_bank_account"):
		conditions["from_bank_account"] = filters["from_bank_account"]

	if filters.get("to_bank_account"):
		conditions["to_bank_account"] = filters["to_bank_account"]

	if filters.get("status"):
		conditions["status"] = filters["status"]

	return conditions
