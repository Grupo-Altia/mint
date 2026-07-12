# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""
Tests de las guardas de ingeniería nacidas del saneo de depósitos duplicados
(dossier apps/resources/mint/docs/manual_duplicate_deposits_review_2026_07.md):

- check_payment_entry_overallocation / get_pe_allocated_in_other_transactions:
  invariante Σ(asignado en TODOS los depósitos) ≤ base_paid_amount del cobro.
  Casos de prod: PE cobra 12.656,32 y quedó con 14.612,40 asignados; PE cobra
  218.244,40 y quedó con 285.791,40 (respaldado por su gemelo ×100 phantom).
- normalize_reference: strip + ".0" + comilla inicial + saltos de línea / espacios
  internos (refs con "\\n" embebido y textuales tipo "BANPANAMA SR CRISTIAN").
- find_impossible_date_transactions / get_reconciliation_health: depósitos con
  fecha futura o NULL (el bug ×100 volteó fechas dd/mm↔mm/dd).

Corren igual que el resto de los tests de mint: frappe.local.db bindeado a un
MagicMock y frappe.throw parcheado para lanzar ValidationError de forma controlada.
"""

import unittest
from unittest.mock import patch, MagicMock

import frappe

from mint.apis.reconciliation import (
    OVERALLOCATION_TOLERANCE,
    check_payment_entry_overallocation,
    get_pe_allocated_in_other_transactions,
    normalize_reference,
    find_impossible_date_transactions,
    get_reconciliation_health,
)

MODULE = "mint.apis.reconciliation"


class ReconBaseTestCase(unittest.TestCase):
    """frappe.local.db bindeado a un mock (restaurado en tearDown)."""

    def setUp(self):
        self._original_db = getattr(frappe.local, "db", None)
        self.mock_db = MagicMock()
        frappe.local.db = self.mock_db
        if not hasattr(frappe.local, "flags"):
            frappe.local.flags = frappe._dict()

    def tearDown(self):
        if self._original_db is not None:
            frappe.local.db = self._original_db
        elif hasattr(frappe.local, "db"):
            delattr(frappe.local, "db")


class TestNormalizeReference(unittest.TestCase):
    """normalize_reference: forma canónica de una referencia bancaria."""

    def test_none_returns_empty(self):
        self.assertEqual(normalize_reference(None), "")

    def test_plain_reference_unchanged(self):
        self.assertEqual(normalize_reference("858249469"), "858249469")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(normalize_reference("  858249469  "), "858249469")

    def test_strips_leading_quote(self):
        self.assertEqual(normalize_reference("'0020"), "0020")

    def test_strips_dot_zero_suffix(self):
        self.assertEqual(normalize_reference("858249469.0"), "858249469")

    def test_float_input_dot_zero(self):
        self.assertEqual(normalize_reference(858249469.0), "858249469")

    def test_removes_trailing_newline(self):
        # Caso del dossier: "162536032237\n" (salto de línea embebido del import).
        self.assertEqual(normalize_reference("162536032237\n"), "162536032237")

    def test_removes_internal_newline(self):
        self.assertEqual(normalize_reference("16253\n6032237"), "162536032237")

    def test_removes_tabs_and_carriage_returns(self):
        self.assertEqual(normalize_reference("\t162536\r032237\n"), "162536032237")

    def test_collapses_internal_spaces_preserving_textual_ref(self):
        # Referencia textual (Bancaribe): no se mutila, solo se colapsa el espacio doble.
        self.assertEqual(
            normalize_reference("BANPANAMA  SR   CRISTIAN"), "BANPANAMA SR CRISTIAN"
        )

    def test_quote_and_dot_zero_together(self):
        self.assertEqual(normalize_reference("'123.0"), "123")


class TestGetPeAllocatedInOtherTransactions(ReconBaseTestCase):

    def test_sums_from_other_transactions(self):
        self.mock_db.get_value.return_value = 22175.25
        self.assertEqual(
            get_pe_allocated_in_other_transactions("ACC-PAY-1", exclude_bank_transaction="ACC-BTN-9"),
            22175.25,
        )
        # excluye el depósito actual y solo cuenta emitidos (docstatus=1)
        filters = self.mock_db.get_value.call_args.args[1]
        self.assertEqual(filters["parent"], ["!=", "ACC-BTN-9"])
        self.assertEqual(filters["docstatus"], 1)
        self.assertEqual(filters["payment_document"], "Payment Entry")

    def test_none_sum_returns_zero(self):
        self.mock_db.get_value.return_value = None
        self.assertEqual(get_pe_allocated_in_other_transactions("ACC-PAY-1"), 0.0)


class TestCheckPaymentEntryOverallocation(ReconBaseTestCase):
    """Invariante Σ asignado ≤ base_paid_amount del Payment Entry."""

    def _setup(self, paid, existing, payment_type="Receive"):
        """get_value: {base_paid_amount, payment_type} del Payment Entry; sum(allocated) resto."""
        def _gv(doctype, name, field=None, *a, **k):
            if doctype == "Payment Entry":
                return frappe._dict(base_paid_amount=paid, payment_type=payment_type)
            return existing
        self.mock_db.get_value.side_effect = _gv

    def test_under_limit_ok(self):
        self._setup(paid=100.0, existing=0.0)
        ok, msg = check_payment_entry_overallocation("ACC-PAY-1", 50.0)
        self.assertTrue(ok)
        self.assertIsNone(msg)

    def test_two_partial_deposits_summing_exact_ok(self):
        # Un cobro pagado por DOS depósitos parciales que suman su total es legítimo.
        self._setup(paid=100.0, existing=60.0)
        ok, _msg = check_payment_entry_overallocation("ACC-PAY-1", 40.0)
        self.assertTrue(ok)

    def test_within_tolerance_ok(self):
        self._setup(paid=100.0, existing=0.0)
        ok, _msg = check_payment_entry_overallocation("ACC-PAY-1", 100.0 + OVERALLOCATION_TOLERANCE)
        self.assertTrue(ok)

    def test_zero_paid_skips_without_querying_allocations(self):
        self._setup(paid=0.0, existing=999.0)
        ok, msg = check_payment_entry_overallocation("ACC-PAY-1", 500.0)
        self.assertTrue(ok)
        self.assertIsNone(msg)
        # solo consultó base_paid_amount; no llegó a sumar asignaciones
        self.assertEqual(self.mock_db.get_value.call_count, 1)

    @patch(f"{MODULE}.frappe.throw", side_effect=frappe.exceptions.ValidationError)
    def test_over_limit_throws(self, _throw):
        # PE cobra 12.656,32 y se le intenta asignar 14.612,40 en total (caso ACC-PAY-2026-02486).
        self._setup(paid=12656.32, existing=0.0)
        with self.assertRaises(frappe.exceptions.ValidationError):
            check_payment_entry_overallocation("ACC-PAY-2026-02486", 14612.40)

    def test_over_limit_no_raise_returns_message(self):
        self._setup(paid=12656.32, existing=0.0)
        ok, msg = check_payment_entry_overallocation(
            "ACC-PAY-1", 14612.40, raise_on_violation=False
        )
        self.assertFalse(ok)
        self.assertIn("supera el monto cobrado", msg)

    def test_already_fully_allocated_uses_specific_message(self):
        # El cobro ya está TOTALMENTE asignado desde otro depósito (invariante #2 del dossier).
        self._setup(paid=100.0, existing=100.0)
        ok, msg = check_payment_entry_overallocation(
            "ACC-PAY-3832", 50.0, raise_on_violation=False
        )
        self.assertFalse(ok)
        self.assertIn("totalmente asignado", msg)

    def test_internal_transfer_skipped(self):
        # Una transferencia interna se concilia contra DOS depósitos por diseño (Σ = 2×monto):
        # el invariante no aplica y no debe bloquearla.
        self._setup(paid=100.0, existing=100.0, payment_type="Internal Transfer")
        ok, msg = check_payment_entry_overallocation("ACC-PAY-IT", 100.0)
        self.assertTrue(ok)
        self.assertIsNone(msg)


class TestImpossibleDateTransactions(ReconBaseTestCase):

    @patch(f"{MODULE}.frappe.utils.today", return_value="2026-07-11")
    def test_queries_future_or_null_dates(self, _today):
        self.mock_db.sql.return_value = [
            frappe._dict(name="ACC-BTN-2026-04720", date="2026-09-03"),
            frappe._dict(name="ACC-BTN-2026-03213-1", date=None),
        ]
        rows = find_impossible_date_transactions()
        self.assertEqual(len(rows), 2)
        params = self.mock_db.sql.call_args.args[1]
        self.assertEqual(params["today"], "2026-07-11")

    @patch(f"{MODULE}.find_impossible_date_transactions")
    @patch(f"{MODULE}.frappe.has_permission", return_value=True)
    def test_health_summary_shape(self, _perm, mock_find):
        mock_find.return_value = [frappe._dict(name="ACC-BTN-2026-04720", date="2026-09-03")]
        health = get_reconciliation_health()
        self.assertEqual(health["impossible_date_count"], 1)
        self.assertEqual(health["impossible_date_transactions"][0]["name"], "ACC-BTN-2026-04720")


if __name__ == "__main__":
    unittest.main()
