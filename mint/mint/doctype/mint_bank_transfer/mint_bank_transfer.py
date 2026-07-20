# Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
# For license information, please see license.txt

# import frappe
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
