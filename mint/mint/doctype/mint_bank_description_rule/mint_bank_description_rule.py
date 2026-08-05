# Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MintBankDescriptionRule(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		apply_prefix_rule: DF.Check
		description_text: DF.Data
		match_type: DF.Literal["Exact Match", "Starts With"]
		prefixes_to_strip: DF.Data | None
	# end: auto-generated types
	
	def on_update(self):
		if hasattr(frappe.local, "_mint_bank_description_rules"):
			delattr(frappe.local, "_mint_bank_description_rules")

	def on_trash(self):
		if hasattr(frappe.local, "_mint_bank_description_rules"):
			delattr(frappe.local, "_mint_bank_description_rules")

