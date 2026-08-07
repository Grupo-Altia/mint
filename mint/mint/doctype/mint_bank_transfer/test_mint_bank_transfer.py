# Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and Contributors
# See license.txt

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase
from mint.mint.doctype.mint_bank_transfer.mint_bank_transfer import MintBankTransfer, has_permission, get_permission_query_conditions


class TestMintBankTransfer(FrappeTestCase):
	def test_has_permission_inter_branch(self):
		doc = frappe._dict({
			"from_branch": "San Antonio",
			"to_branch": "Lara"
		})
		res = has_permission(doc, "read", user="Administrator")
		self.assertTrue(res)

	def test_get_gl_entries_no_ignore_permissions_typeerror(self):
		source = inspect.getsource(MintBankTransfer.get_gl_entries)
		self.assertNotIn("ignore_permissions=True", source)
		self.assertNotIn("ignore_permissions = True", source)

