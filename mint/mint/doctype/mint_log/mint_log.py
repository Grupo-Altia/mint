# Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class MintLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		date: DF.Datetime
		description: DF.Code | None
		log_type: DF.Literal["Error", "Warning", "Info", "Success"]
		title: DF.Data
		user: DF.Link | None
	# end: auto-generated types
	pass
