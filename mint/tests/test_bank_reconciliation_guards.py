# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""
Tests de las dos guardas de conciliación bancaria (mint.apis.bank_reconciliation).

Nacen del incidente ref 61873142037 (JUAN JOSE FERRER USECHE): un extracto
emitido quedó conciliado contra un Payment Entry en BORRADOR y contra un PE de
OTRO cliente, y al desconciliar solo se limpiaba la mitad de las filas.

Cubre:
- reconcile_vouchers(): rechaza conciliar un voucher que no esté EMITIDO
  (borrador / cancelado / inexistente); con emitido, enlaza y guarda.
- unreconcile_transaction(): limpia TODAS las filas aunque remove_payment_entry
  mute la lista (el core itera-y-muta y salta una fila de cada dos).

Corren sin site: frappe.local.db se bindea a un MagicMock y se parchean las
dependencias del módulo.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

import frappe

from mint.apis.bank_reconciliation import reconcile_vouchers, unreconcile_transaction

MODULE = "mint.apis.bank_reconciliation"


class _DbBoundTestCase(unittest.TestCase):
    """Bindea frappe.local.db a un mock (restaurado en tearDown), como el resto
    de los tests de mint que corren sin site."""

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


class TestReconcileVouchersDocstatusGuard(_DbBoundTestCase):
    """reconcile_vouchers solo concilia contra comprobantes EMITIDOS (docstatus=1)."""

    def _reconcile_with(self, voucher_docstatus, unallocated=100.0):
        self.mock_db.get_value.return_value = voucher_docstatus
        txn = MagicMock()
        txn.name = "ACC-BTN-1"
        txn.unallocated_amount = unallocated
        vouchers = json.dumps([{"payment_doctype": "Payment Entry", "payment_name": "ACC-PAY-1"}])
        # La guarda de sobre-asignación se prueba aparte; aquí se aísla la de docstatus.
        with patch(f"{MODULE}.frappe.get_doc", return_value=txn), \
             patch(f"{MODULE}.check_payment_entry_overallocation"), \
             patch(f"{MODULE}.frappe.throw", side_effect=frappe.exceptions.ValidationError):
            reconcile_vouchers("ACC-BTN-1", vouchers)
        return txn

    def test_draft_voucher_blocked(self):
        """Voucher en borrador (docstatus=0) -> throw, no se enlaza."""
        with self.assertRaises(frappe.exceptions.ValidationError):
            self._reconcile_with(0)

    def test_cancelled_voucher_blocked(self):
        """Voucher cancelado (docstatus=2) -> throw."""
        with self.assertRaises(frappe.exceptions.ValidationError):
            self._reconcile_with(2)

    def test_missing_voucher_blocked(self):
        """Voucher inexistente (get_value -> None) -> throw."""
        with self.assertRaises(frappe.exceptions.ValidationError):
            self._reconcile_with(None)

    def test_submitted_voucher_appends_and_saves(self):
        """Voucher emitido (docstatus=1) -> pasa la guarda, enlaza y guarda."""
        txn = self._reconcile_with(1)
        txn.append.assert_called_once()
        txn.save.assert_called_once()

    def test_already_reconciled_blocked_before_docstatus(self):
        """Si el extracto ya esta lleno (unallocated<=0) se detiene antes."""
        with self.assertRaises(frappe.exceptions.ValidationError):
            self._reconcile_with(1, unallocated=0.0)


class _FakeBT:
    """Bank Transaction minimo cuyo remove_payment_entry MUTA la lista, igual que
    el core de ERPNext (por eso el core, al iterar la lista viva, salta filas)."""

    def __init__(self, rows):
        self.payment_entries = list(rows)
        self.removed = []
        self.saved = False

    def remove_payment_entry(self, entry):
        self.removed.append(entry)
        self.payment_entries.remove(entry)

    def save(self):
        self.saved = True


class TestUnreconcileFullClear(unittest.TestCase):
    """unreconcile_transaction limpia TODAS las filas (no salta por iterar-y-mutar)."""

    @staticmethod
    def _rows(n, recon_type="Matched"):
        return [
            frappe._dict(
                payment_document="Payment Entry",
                payment_entry=f"PE-{i}",
                reconciliation_type=recon_type,
            )
            for i in range(n)
        ]

    def test_removes_all_rows_despite_list_mutation(self):
        """4 filas -> las 4 removidas (con el bug del core quedarian 2)."""
        bt = _FakeBT(self._rows(4))
        with patch(f"{MODULE}.frappe.get_doc", return_value=bt):
            unreconcile_transaction("ACC-BTN-1")
        self.assertEqual(bt.payment_entries, [])
        self.assertEqual(len(bt.removed), 4)
        self.assertTrue(bt.saved)

    def test_odd_number_of_rows_all_removed(self):
        """N impar de filas tambien se limpia entero (5 -> 5)."""
        bt = _FakeBT(self._rows(5))
        with patch(f"{MODULE}.frappe.get_doc", return_value=bt):
            unreconcile_transaction("ACC-BTN-1")
        self.assertEqual(bt.payment_entries, [])
        self.assertEqual(len(bt.removed), 5)

    def test_matched_rows_not_cancelled(self):
        """Filas 'Matched' solo se desenlazan: no se carga ningun voucher a cancelar."""
        bt = _FakeBT(self._rows(3))
        with patch(f"{MODULE}.frappe.get_doc", return_value=bt) as gd:
            unreconcile_transaction("ACC-BTN-1")
        self.assertEqual(gd.call_count, 1)  # solo el Bank Transaction
        self.assertEqual(bt.payment_entries, [])

    def test_voucher_created_rows_are_cancelled(self):
        """Filas 'Voucher Created' -> el voucher se cancela tras desenlazar."""
        bt = _FakeBT(self._rows(1, recon_type="Voucher Created"))
        voucher_doc = MagicMock()

        def get_doc(doctype, name):
            return bt if doctype == "Bank Transaction" else voucher_doc

        with patch(f"{MODULE}.frappe.get_doc", side_effect=get_doc):
            unreconcile_transaction("ACC-BTN-1")
        self.assertEqual(bt.payment_entries, [])
        voucher_doc.cancel.assert_called_once()


class _FakeReconcileTxn:
    """Bank Transaction mínimo para reconcile_vouchers: append() agrega la fila y
    allocate_payment_entries() le fija el allocated_amount previsto (simula el core).
    Empieza SIN filas para no gatillar el early-return `all_linked`."""

    def __init__(self, alloc_by_pe=None, unallocated=100.0):
        self.name = "ACC-BTN-1"
        self.unallocated_amount = unallocated
        self.payment_entries = []
        self.saved = False
        self._alloc = alloc_by_pe or {}

    def append(self, _fieldname, d):
        self.payment_entries.append(frappe._dict(
            payment_document=d["payment_document"],
            payment_entry=d["payment_entry"],
            allocated_amount=d.get("allocated_amount", 0.0),
        ))

    def validate_duplicate_references(self):
        pass

    def allocate_payment_entries(self):
        for row in self.payment_entries:
            if row.payment_entry in self._alloc:
                row.allocated_amount = self._alloc[row.payment_entry]

    def update_allocated_amount(self):
        pass

    def set_status(self):
        pass

    def save(self):
        self.saved = True


class TestReconcileVouchersOverallocationGuard(_DbBoundTestCase):
    """reconcile_vouchers rechaza asignar a un Payment Entry más de lo que cobra
    (invariante Σ asignado ≤ base_paid_amount). Nacido del saneo de depósitos
    duplicados: PEs respaldados por su gemelo ×100 phantom."""

    def test_guard_called_with_this_bt_allocation(self):
        """Se llama la guarda con la suma asignada por ESTE depósito y excluyéndolo del resto."""
        self.mock_db.get_value.return_value = 1  # docstatus del voucher: emitido
        txn = _FakeReconcileTxn(alloc_by_pe={"ACC-PAY-1": 60.0})
        vouchers = json.dumps([{"payment_doctype": "Payment Entry", "payment_name": "ACC-PAY-1"}])
        with patch(f"{MODULE}.frappe.get_doc", return_value=txn), \
             patch(f"{MODULE}.check_payment_entry_overallocation") as mock_guard:
            reconcile_vouchers("ACC-BTN-1", vouchers)
        mock_guard.assert_called_once_with(
            "ACC-PAY-1", 60.0, exclude_bank_transaction="ACC-BTN-1"
        )
        self.assertTrue(txn.saved)

    def test_guard_violation_propagates_and_blocks_save(self):
        """Si la guarda lanza, reconcile_vouchers no guarda el extracto."""
        self.mock_db.get_value.return_value = 1
        txn = _FakeReconcileTxn(alloc_by_pe={"ACC-PAY-1": 90.0})
        vouchers = json.dumps([{"payment_doctype": "Payment Entry", "payment_name": "ACC-PAY-1"}])
        with patch(f"{MODULE}.frappe.get_doc", return_value=txn), \
             patch(f"{MODULE}.check_payment_entry_overallocation",
                   side_effect=frappe.exceptions.ValidationError):
            with self.assertRaises(frappe.exceptions.ValidationError):
                reconcile_vouchers("ACC-BTN-1", vouchers)
        self.assertFalse(txn.saved)

    def test_journal_entry_voucher_skips_guard(self):
        """La guarda es solo para Payment Entry; un Journal Entry no se verifica."""
        self.mock_db.get_value.return_value = 1
        txn = _FakeReconcileTxn(alloc_by_pe={"ACC-JE-1": 100.0})
        vouchers = json.dumps([{"payment_doctype": "Journal Entry", "payment_name": "ACC-JE-1"}])
        with patch(f"{MODULE}.frappe.get_doc", return_value=txn), \
             patch(f"{MODULE}.check_payment_entry_overallocation") as mock_guard:
            reconcile_vouchers("ACC-BTN-1", vouchers)
        mock_guard.assert_not_called()
        self.assertTrue(txn.saved)


if __name__ == "__main__":
    unittest.main()
