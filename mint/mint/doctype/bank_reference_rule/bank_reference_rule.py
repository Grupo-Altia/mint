# Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class BankReferenceRule(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		rule: DF.Text
		rule_name: DF.Data
	# end: auto-generated types
	pass

	def before_save(self):
		rule = str(self.rule)
		reference_number = "1234567890"
		rule2 = rule.replace('reference_number', reference_number)
		value = eval(rule2) 
		print(value)
		#value = eval(str(self.rule))
		return