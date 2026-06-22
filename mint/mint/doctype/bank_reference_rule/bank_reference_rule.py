# Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
# For license information, please see license.txt

import frappe
from frappe import _
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
		"""Valida que la expresión de la regla corre de forma SEGURA: la evalúa con
		safe_eval (que bloquea imports/dunders) usando una referencia de prueba y los
		builtins seguros del motor. Sustituye al eval() crudo anterior."""
		from frappe.utils.safe_exec import safe_eval
		from mint.apis.reconciliation import _SAFE_BUILTINS

		try:
			safe_eval(
				str(self.rule),
				eval_globals=dict(_SAFE_BUILTINS),
				eval_locals={"reference_number": "1234567890"},
			)
		except Exception as e:
			frappe.throw(_("La expresión de la regla no es válida: {0}").format(e))
	