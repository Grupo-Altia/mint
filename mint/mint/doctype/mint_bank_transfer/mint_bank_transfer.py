# Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MintBankTransfer(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amended_from: DF.Link | None
		amount: DF.Currency
		company: DF.Link
		date: DF.Date
		description: DF.SmallText | None
		from_bank_account: DF.Link
		journal_entry: DF.Link | None
		reference_number: DF.Data | None
		status: DF.Literal["Draft", "Submitted", "Cancelled"]
		to_bank_account: DF.Link
	# end: auto-generated types
	def validate(self):
		if self.from_bank_account == self.to_bank_account:
			import frappe
			frappe.throw(_("From Bank Account and To Bank Account cannot be the same."))

	def before_submit(self):
		self.status = "Submitted"
		self.reconciliation_status = "No Conciliado"
		self.source_reconciled = 0
		self.destination_reconciled = 0

	def update_reconciliation_status(self):
		if self.source_reconciled and self.destination_reconciled:
			self.reconciliation_status = "Conciliado"
		elif self.source_reconciled or self.destination_reconciled:
			self.reconciliation_status = "Parcialmente Conciliado"
		else:
			self.reconciliation_status = "No Conciliado"
		self.db_update()

	def on_submit(self):
		self.make_gl_entries()

	def before_cancel(self):
		self.status = "Cancelled"

	def on_cancel(self):
		self.ignore_linked_doctypes = ["GL Entry"]
		self.make_gl_entries(cancel=1)

	def on_trash(self):
		import frappe
		for gle in frappe.get_all("GL Entry", filters={"voucher_type": self.doctype, "voucher_no": self.name}):
			frappe.db.delete("GL Entry", {"name": gle.name})

	def make_gl_entries(self, cancel=0):
		from erpnext.accounts.general_ledger import make_gl_entries

		gl_entries = self.get_gl_entries()
		if gl_entries:
			make_gl_entries(gl_entries, cancel=cancel)

	def get_gl_entries(self):
		import frappe

		gl_entries = []

		# frappe.db.get_value es SQL directo: no aplica User Permissions y no acepta
		# ignore_permissions (su firma no tiene **kwargs → TypeError en on_submit).
		from_account = frappe.db.get_value("Bank Account", self.from_bank_account, "account")
		to_account = frappe.db.get_value("Bank Account", self.to_bank_account, "account")

		if not from_account:
			frappe.throw(_("Bank Account '{0}' does not have a linked GL Account.").format(self.from_bank_account))
		if not to_account:
			frappe.throw(_("Bank Account '{0}' does not have a linked GL Account.").format(self.to_bank_account))

		# Credit source bank account
		gl_entries.append(
			frappe._dict(
				account=from_account,
				against=to_account,
				credit=self.amount,
				credit_in_account_currency=self.amount,
				debit=0.0,
				debit_in_account_currency=0.0,
				cost_center=frappe.get_cached_value("Company", self.company, "cost_center"),
				remarks=self.description or f"Bank Transfer: {self.name}",
				voucher_type=self.doctype,
				voucher_no=self.name,
				posting_date=self.date,
				company=self.company,
				party_type=None,
				party=None,
				is_opening="No"
			)
		)

		# Debit destination bank account
		gl_entries.append(
			frappe._dict(
				account=to_account,
				against=from_account,
				debit=self.amount,
				debit_in_account_currency=self.amount,
				credit=0.0,
				credit_in_account_currency=0.0,
				cost_center=frappe.get_cached_value("Company", self.company, "cost_center"),
				remarks=self.description or f"Bank Transfer: {self.name}",
				voucher_type=self.doctype,
				voucher_no=self.name,
				posting_date=self.date,
				company=self.company,
				party_type=None,
				party=None,
				is_opening="No"
			)
		)

		return gl_entries


def _allowed_branches(user=None):
	"""Sucursales del usuario según l10n_ve. Devuelve None si el motor de sucursales
	no está disponible (app no instalada) o si el usuario es admin: en ambos casos
	este DocType no debe filtrarse."""
	try:
		from l10n_ve.permissions import get_allowed_branches, is_admin_user
	except ImportError:
		return None

	user = user or frappe.session.user
	if is_admin_user(user):
		return None
	return get_allowed_branches(user, include_ancestors=False)


def get_permission_query_conditions(user=None):
	"""Una transferencia interna es visible si el usuario participa en CUALQUIERA de
	sus dos puntas (origen o destino): por naturaleza involucra dos sucursales y el
	filtro genérico de sucursal la ocultaría a ambos lados."""
	allowed = _allowed_branches(user)
	if allowed is None:
		return ""
	if not allowed:
		return "1=0"

	branches = ", ".join(frappe.db.escape(b) for b in allowed)
	return (
		"(`tabMint Bank Transfer`.`from_branch` in ({0})"
		" or `tabMint Bank Transfer`.`to_branch` in ({0}))".format(branches)
	)


def has_permission(doc, ptype="read", user=None):
	"""Permite operar la transferencia si el usuario participa en alguna de sus dos
	sucursales, aunque la otra punta le sea ajena (caso legítimo: mover fondos entre
	cuentas propias de la empresa).

	Devuelve None ("sin opinión") en el resto de los casos: los controller hooks solo
	pueden denegar, nunca otorgar permisos que el usuario no tenga, así que dejamos
	decidir a los demás hooks (p. ej. el de sucursal de l10n_ve)."""
	allowed = _allowed_branches(user)
	if allowed is None:
		return True
	if doc.get("from_branch") in allowed or doc.get("to_branch") in allowed:
		return True
	return None

