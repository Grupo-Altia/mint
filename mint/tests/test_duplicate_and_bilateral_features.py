import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today, add_days
from mint.apis.reconciliation import check_rules_match, get_matching_description_rule
from mint.apis.statement_import import is_similar_reference
from mint.apis.clean_duplicate_transactions import (
    _are_references_matching,
    clean_unreconciled_duplicates,
)


class TestDuplicateAndBilateralFeatures(FrappeTestCase):

    def test_starts_with_description_rule(self):
        rule_name = "TRF TRANSFERENCIA REF"
        if frappe.db.exists("Mint Bank Description Rule", rule_name):
            frappe.delete_doc("Mint Bank Description Rule", rule_name, force=True, ignore_permissions=True)

        doc = frappe.get_doc({
            "doctype": "Mint Bank Description Rule",
            "description_text": rule_name,
            "apply_prefix_rule": 0
        }).insert(ignore_permissions=True)
        doc.db_set("match_type", "Starts With", update_modified=False)
        frappe.db.commit()

        if hasattr(frappe.local, "_mint_bank_description_rules"):
            delattr(frappe.local, "_mint_bank_description_rules")

        # Test matching description that starts with "TRF TRANSFERENCIA REF"
        matched = get_matching_description_rule("TRF TRANSFERENCIA REF 00987654321", force_reload=True)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.description_text, rule_name)

        # Test non-matching description
        not_matched = get_matching_description_rule("PAGO DIRECTO NOMINA")
        self.assertIsNone(not_matched)

        # Cleanup
        frappe.delete_doc("Mint Bank Description Rule", rule_name, force=True, ignore_permissions=True)
        frappe.db.commit()

    def test_bilateral_rules_and_zero_stripping(self):
        # 1. Zero stripping >= 5 digits (0012345 vs 12345) -> matches, rule_name is None
        matched, rule = check_rules_match([], "0012345", "12345")
        self.assertTrue(matched)
        self.assertIsNone(rule)

        # Inverse zero stripping (12345 vs 0012345) -> matches, rule_name is None
        matched, rule = check_rules_match([], "12345", "0012345")
        self.assertTrue(matched)
        self.assertIsNone(rule)

        # Short refs (< 5 digits: 0020 vs 20) -> should NOT match via zero stripping
        matched, rule = check_rules_match([], "0020", "20")
        self.assertFalse(matched)

    def test_is_similar_reference_safeguards(self):
        # Both empty -> False (A2 fix)
        self.assertFalse(is_similar_reference("", ""))
        self.assertFalse(is_similar_reference(None, None))
        self.assertFalse(is_similar_reference("12345", None))

        # Equal refs -> True
        self.assertTrue(is_similar_reference("12345", "12345"))
        # Zero stripped >= 5 digits -> True
        self.assertTrue(is_similar_reference("00012345", "12345"))

    def test_are_references_matching_unreferenced_safeguard(self):
        # C1 FIX: Both unreferenced -> FALSE (never treat unreferenced rows as duplicates)
        self.assertFalse(_are_references_matching("", ""))
        self.assertFalse(_are_references_matching(None, None, None, None))

        # Different references -> FALSE
        self.assertFalse(_are_references_matching("10001", "10002"))

        # Matching references >= 5 digits -> TRUE
        self.assertTrue(_are_references_matching("0010001", "10001"))

    def test_clean_unreconciled_duplicates_cancellation(self):
        bank_acc = frappe.db.get_value("Bank Account", {"disabled": 0}, "name")
        if not bank_acc:
            bank_acc = frappe.db.get_value("Bank Account", {}, "name")
        if not bank_acc:
            return

        test_date = today()
        test_amount = 999.11

        # Clean existing test data if any
        frappe.db.delete("Bank Transaction", {
            "bank_account": bank_acc,
            "date": test_date,
            "reference_number": ["in", ["TEST-REC-99", "00TEST-REC-99"]]
        })

        # BT 1: Reconciled
        bt1 = frappe.get_doc({
            "doctype": "Bank Transaction",
            "date": test_date,
            "bank_account": bank_acc,
            "deposit": test_amount,
            "reference_number": "TEST-REC-99",
            "description": "Reconciled Tx",
            "status": "Reconciled"
        }).insert(ignore_permissions=True)
        bt1.submit()
        frappe.db.set_value("Bank Transaction", bt1.name, "status", "Reconciled")

        # BT 2: Unreconciled duplicate
        bt2 = frappe.get_doc({
            "doctype": "Bank Transaction",
            "date": test_date,
            "bank_account": bank_acc,
            "deposit": test_amount,
            "reference_number": "00TEST-REC-99",
            "description": "Unreconciled Duplicate Tx",
            "status": "Unreconciled"
        }).insert(ignore_permissions=True)
        bt2.submit()
        frappe.db.commit()

        # Run dry run
        frappe.set_user("Administrator")
        res_dry = clean_unreconciled_duplicates(bank_account=bank_acc, dry_run=True, from_date=add_days(test_date, -1), to_date=test_date)
        self.assertTrue(res_dry["dry_run"])
        self.assertEqual(res_dry["cancelled_count"], 1)

        # Run real execution
        res_real = clean_unreconciled_duplicates(bank_account=bank_acc, dry_run=False, from_date=add_days(test_date, -1), to_date=test_date)
        self.assertFalse(res_real["dry_run"])
        self.assertEqual(res_real["cancelled_count"], 1)

        # A1 FIX: BT2 should be CANCELLED (docstatus = 2), not deleted
        bt2_doc = frappe.get_doc("Bank Transaction", bt2.name)
        self.assertEqual(bt2_doc.docstatus, 2)

        # BT1 should remain submitted & reconciled
        bt1_doc = frappe.get_doc("Bank Transaction", bt1.name)
        self.assertEqual(bt1_doc.docstatus, 1)
        self.assertEqual(bt1_doc.status, "Reconciled")

        # Cleanup
        bt1.reload()
        bt1.cancel()
        frappe.delete_doc("Bank Transaction", bt1.name, force=True, ignore_permissions=True)
        frappe.delete_doc("Bank Transaction", bt2.name, force=True, ignore_permissions=True)
        frappe.db.commit()


def run():
    t = TestDuplicateAndBilateralFeatures()
    t.test_starts_with_description_rule()
    print("✓ test_starts_with_description_rule passed")
    t.test_bilateral_rules_and_zero_stripping()
    print("✓ test_bilateral_rules_and_zero_stripping passed")
    t.test_is_similar_reference_safeguards()
    print("✓ test_is_similar_reference_safeguards passed")
    t.test_are_references_matching_unreferenced_safeguard()
    print("✓ test_are_references_matching_unreferenced_safeguard passed")
    t.test_clean_unreconciled_duplicates_cancellation()
    print("✓ test_clean_unreconciled_duplicates_cancellation passed")
    print("\nALL UNIT TESTS PASSED SUCCESSFULLY!")
