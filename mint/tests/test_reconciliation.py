# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""
Tests unitarios de la guardia de REFERENCIA DUPLICADA en la conciliación de
cobros (mint.apis.reconciliation).

Cubre:
- find_duplicate_deposits(): consulta los depósitos no cancelados con la misma
  referencia en la cuenta del cobro (TRIM + docstatus<2); sale temprano sin
  referencia o sin cuenta bancaria.
- before_submit_receive_payment(): DETIENE la aprobación (frappe.throw) cuando hay
  >1 depósito con la referencia; deja pasar con 0/1.
- reconcile_and_approve(): marca 'Revisar' y devuelve el detalle de la colisión
  SIN conciliar ni aprobar cuando hay >1 depósito.

Corren sin sitio ni BD: frappe.local.db se bindea a un MagicMock y se parchean las
dependencias del módulo. Sin contexto de sitio frappe.throw lanza RuntimeError
('object is not bound'), así que se parchea para que lance ValidationError de
forma controlada.
"""

import unittest
from unittest.mock import patch, MagicMock

import frappe

from mint.apis.reconciliation import (
    RECON_DONE,
    RECON_PENDING,
    RECON_REVIEW,
    REVIEW_DUPLICATE_REFERENCE,
    REVIEW_OTHER_BANK,
    REVIEW_ALREADY_RECONCILED,
    REVIEW_DEPOSIT_NOT_SUBMITTED,
    find_duplicate_deposits,
    find_consumed_deposit,
    find_unsubmitted_deposit,
    before_submit_receive_payment,
    reconcile_and_approve,
    reconcile_pending_drafts_nightly,
    cancel_exact_duplicate_deposits,
    apply_format_rule,
    check_rules_match,
    _adopt_deposit_bank_account,
    _approve_drafts,
    _find_deposit_by_source_bank_rule,
)

MODULE = "mint.apis.reconciliation"


def _bt(name, deposit, status="Unreconciled", unallocated=None, docstatus=1):
    """Mock de una fila de Bank Transaction (depósito)."""
    return frappe._dict({
        "name": name,
        "deposit": deposit,
        "unallocated_amount": deposit if unallocated is None else unallocated,
        "status": status,
        "docstatus": docstatus,
    })


class ReconBaseTestCase(unittest.TestCase):
    """Base: frappe.local.db bindeado a un mock (restaurado en tearDown).

    Bajo bench run-tests frappe.local.db es la conexión real del site: se guarda
    en setUp y se RESTAURA en tearDown (borrarla dejaría sin BD al runner).
    """

    def setUp(self):
        self._original_db = getattr(frappe.local, "db", None)
        self.mock_db = MagicMock()
        frappe.local.db = self.mock_db
        # El wrapper @frappe.whitelist() consulta local.flags.in_test; bajo bench ya
        # existe (in_test=True) — solo lo creamos si falta (corrida standalone).
        if not hasattr(frappe.local, "flags"):
            frappe.local.flags = frappe._dict()

    def tearDown(self):
        if self._original_db is not None:
            frappe.local.db = self._original_db
        elif hasattr(frappe.local, "db"):
            delattr(frappe.local, "db")


class TestFindDuplicateDeposits(ReconBaseTestCase):

    @patch(f"{MODULE}._cobro_bank_account", return_value="1701 BANCAMIGA - X")
    def test_returns_rows_from_sql(self, _mock_bank):
        self.mock_db.sql.return_value = [_bt("ACC-BTN-1", 1.0), _bt("ACC-BTN-2", 100.0)]
        doc = frappe._dict(reference_no="43575451473", company="Galanet")
        rows = find_duplicate_deposits(doc)
        self.assertEqual(len(rows), 2)
        self.assertTrue(self.mock_db.sql.called)

    @patch(f"{MODULE}._cobro_bank_account", return_value="1701 BANCAMIGA - X")
    def test_trims_reference_in_params(self, _mock_bank):
        self.mock_db.sql.return_value = []
        doc = frappe._dict(reference_no="  43575451473  ", company="Galanet")
        find_duplicate_deposits(doc)
        params = self.mock_db.sql.call_args.args[1]
        self.assertEqual(params["ref"], "43575451473")
        self.assertEqual(params["company"], "Galanet")

    def test_empty_reference_skips_query(self):
        doc = frappe._dict(reference_no="", company="Galanet")
        self.assertEqual(find_duplicate_deposits(doc), [])
        self.mock_db.sql.assert_not_called()

    @patch(f"{MODULE}._cobro_bank_account", return_value=None)
    def test_no_bank_account_skips_query(self, _mock_bank):
        doc = frappe._dict(reference_no="43575451473", company="Galanet")
        self.assertEqual(find_duplicate_deposits(doc), [])
        self.mock_db.sql.assert_not_called()


class TestBeforeSubmitCollision(ReconBaseTestCase):

    @patch(f"{MODULE}.find_matching_deposit")
    @patch(f"{MODULE}.find_duplicate_deposits")
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    @patch(f"{MODULE}.frappe.throw", side_effect=frappe.exceptions.ValidationError)
    def test_blocks_on_duplicate_reference(self, _throw, _is_bank, mock_dups, mock_match):
        mock_dups.return_value = [_bt("ACC-BTN-1", 1.0), _bt("ACC-BTN-2", 100.0)]
        doc = frappe._dict(reference_no="43575451473", flags=frappe._dict())
        with self.assertRaises(frappe.exceptions.ValidationError):
            before_submit_receive_payment(doc)
        # no llega a buscar/aplicar el depósito
        mock_match.assert_not_called()

    @patch(f"{MODULE}._apply_deposit_amount")
    @patch(f"{MODULE}.find_matching_deposit")
    @patch(f"{MODULE}.find_duplicate_deposits")
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    def test_proceeds_with_single_deposit(self, _is_bank, mock_dups, mock_match, _apply):
        mock_dups.return_value = [_bt("ACC-BTN-1", 200.0)]
        mock_match.return_value = _bt("ACC-BTN-1", 200.0)
        doc = MagicMock()
        doc.reference_no = "43575451473"
        doc.flags = frappe._dict()
        before_submit_receive_payment(doc)
        mock_match.assert_called_once()
        self.assertEqual(doc.custom_reconciliation_status, RECON_DONE)
        self.assertEqual(doc.flags.get("l10n_ve_matched_deposit"), "ACC-BTN-1")

    @patch(f"{MODULE}.find_duplicate_deposits")
    @patch(f"{MODULE}._is_bank_receive", return_value=False)
    def test_skips_non_bank_receive(self, _is_bank, mock_dups):
        doc = MagicMock()
        self.assertIsNone(before_submit_receive_payment(doc))
        mock_dups.assert_not_called()

    @patch(f"{MODULE}._apply_deposit_amount")
    @patch(f"{MODULE}.find_matching_deposit")
    @patch(f"{MODULE}.find_duplicate_deposits")
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    def test_skips_duplicate_check_when_already_checked(self, _is_bank, mock_dups, mock_match, _apply):
        # reconcile_and_approve ya verificó la colisión antes de submit (flag cacheado):
        # before_submit no debe repetir find_duplicate_deposits.
        mock_match.return_value = _bt("ACC-BTN-1", 200.0)
        doc = MagicMock()
        doc.reference_no = "43575451473"
        doc.flags = frappe._dict(l10n_ve_duplicates_checked=True)
        before_submit_receive_payment(doc)
        mock_dups.assert_not_called()
        self.assertEqual(doc.custom_reconciliation_status, RECON_DONE)


class TestReconcileAndApproveCollision(ReconBaseTestCase):

    @patch(f"{MODULE}.find_matching_deposit")
    @patch(f"{MODULE}.find_duplicate_deposits")
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    @patch(f"{MODULE}.frappe.get_doc")
    def test_marks_review_and_returns_detail(self, mock_get_doc, _is_bank, mock_dups, mock_match):
        doc = MagicMock()
        doc.docstatus = 0
        doc.reference_no = "43575451473"
        doc.get.return_value = None
        mock_get_doc.return_value = doc
        mock_dups.return_value = [_bt("ACC-BTN-1", 1.0), _bt("ACC-BTN-2", 100.0)]

        result = reconcile_and_approve("ACC-PAY-2026-07772")

        self.assertFalse(result["reconciled"])
        self.assertTrue(result["review"])
        self.assertEqual(result["reason"], REVIEW_DUPLICATE_REFERENCE)
        self.assertEqual(result["duplicates"], ["ACC-BTN-1", "ACC-BTN-2"])
        doc.db_set.assert_called_once_with("custom_reconciliation_status", RECON_REVIEW)
        # no concilia ni aprueba
        mock_match.assert_not_called()
        doc.submit.assert_not_called()

    @patch(f"{MODULE}._link_deposit_to_payment")
    @patch(f"{MODULE}._apply_deposit_amount")
    @patch(f"{MODULE}.find_matching_deposit")
    @patch(f"{MODULE}.find_duplicate_deposits")
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    @patch(f"{MODULE}.frappe.get_doc")
    def test_reconciles_with_single_deposit(
        self, mock_get_doc, _is_bank, mock_dups, mock_match, _apply, mock_link
    ):
        # Sin colisión (1 depósito): concilia, aprueba, enlaza y cachea los flags.
        doc = MagicMock()
        doc.docstatus = 0
        doc.reference_no = "43575451473"
        doc.name = "ACC-PAY-X"
        doc.flags = frappe._dict()
        mock_get_doc.return_value = doc
        mock_dups.return_value = [_bt("ACC-BTN-1", 200.0)]
        mock_match.return_value = _bt("ACC-BTN-1", 200.0)

        result = reconcile_and_approve("ACC-PAY-X")

        self.assertTrue(result["reconciled"])
        self.assertEqual(result["bank_transaction"], "ACC-BTN-1")
        self.assertEqual(doc.custom_reconciliation_status, RECON_DONE)
        doc.submit.assert_called_once()
        mock_link.assert_called_once()
        # flags de caché para que before_submit no repita las consultas
        self.assertTrue(doc.flags.get("l10n_ve_duplicates_checked"))
        self.assertEqual(doc.flags.get("l10n_ve_matched_deposit_doc").name, "ACC-BTN-1")


class TestAdoptDepositBankAccount(ReconBaseTestCase):
    """_adopt_deposit_bank_account: 'manda la cuenta del extracto' — monta el Modo de
    Pago cuya cuenta contable es la del banco del depósito y reapunta paid_to."""

    def _doc(self):
        doc = MagicMock()
        doc.company = "Galanet"
        doc.reference_no = "000890344746"
        doc.paid_to = "1102037 - Bancaribe DC-01 - Galanet"
        return doc

    @patch(f"{MODULE}.frappe.get_cached_value")
    @patch(f"{MODULE}.frappe.get_all")
    def test_adopts_when_single_mop(self, mock_get_all, mock_gcv):
        # get_cached_value: primero Bank Account->cuenta GL, luego Account->moneda
        mock_gcv.side_effect = lambda dt, name, field: (
            "1102039 - Bancaribe ARA-02 - Galanet" if dt == "Bank Account" else "VEF"
        )
        mock_get_all.return_value = ["BANCARIBE_ARA-02-2127"]
        doc = self._doc()
        deposit = frappe._dict(name="ACC-BTN-OB", bank_account="BANCARIBE_ARA-02-2127 - BANCARIBE")

        self.assertTrue(_adopt_deposit_bank_account(doc, deposit))
        self.assertEqual(doc.mode_of_payment, "BANCARIBE_ARA-02-2127")
        self.assertEqual(doc.paid_to, "1102039 - Bancaribe ARA-02 - Galanet")
        self.assertEqual(doc.paid_to_account_currency, "VEF")
        doc.add_comment.assert_called_once()

    @patch(f"{MODULE}.frappe.get_cached_value", return_value="1102039 - X")
    @patch(f"{MODULE}.frappe.get_all", return_value=[])
    def test_no_mop_returns_false(self, _mock_get_all, _gcv):
        doc = self._doc()
        deposit = frappe._dict(name="ACC-BTN-OB", bank_account="BANCARIBE_ARA-02")
        self.assertFalse(_adopt_deposit_bank_account(doc, deposit))
        doc.add_comment.assert_not_called()

    @patch(f"{MODULE}.frappe.get_cached_value", return_value="1102039 - X")
    @patch(f"{MODULE}.frappe.get_all", return_value=["MOP-A", "MOP-B"])
    def test_ambiguous_mop_returns_false(self, _mock_get_all, _gcv):
        # >1 Modo de Pago para la misma cuenta: no adivina, deja para revisión.
        doc = self._doc()
        deposit = frappe._dict(name="ACC-BTN-OB", bank_account="X")
        self.assertFalse(_adopt_deposit_bank_account(doc, deposit))
        doc.add_comment.assert_not_called()

    @patch(f"{MODULE}.frappe.get_cached_value", return_value=None)  # Bank Account sin cuenta
    @patch(f"{MODULE}.frappe.get_all")
    def test_no_gl_account_returns_false(self, mock_get_all, _gcv):
        doc = self._doc()
        deposit = frappe._dict(name="ACC-BTN-OB", bank_account="X")
        self.assertFalse(_adopt_deposit_bank_account(doc, deposit))
        mock_get_all.assert_not_called()


class TestReconcileAndApproveOtherBank(ReconBaseTestCase):
    """reconcile_and_approve, rama 'depósito en otro banco': con Modo de Pago para esa
    cuenta → adopta la cuenta del extracto y concilia; sin él → deja en 'Revisar'."""

    def _base_doc(self):
        doc = MagicMock()
        doc.docstatus = 0
        doc.reference_no = "000890344746"
        doc.name = "ACC-PAY-OB"
        doc.flags = frappe._dict()
        doc.get.return_value = None
        return doc

    @patch(f"{MODULE}._link_deposit_to_payment")
    @patch(f"{MODULE}._apply_deposit_amount")
    @patch(f"{MODULE}._adopt_deposit_bank_account", return_value=True)
    @patch(f"{MODULE}.find_deposit_other_bank")
    @patch(f"{MODULE}.find_matching_deposit", return_value=None)
    @patch(f"{MODULE}.find_duplicate_deposits", return_value=[])
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    @patch(f"{MODULE}.frappe.get_doc")
    def test_adopts_other_bank_and_reconciles(
        self, mock_get_doc, _is_bank, _dups, _match, mock_ob, mock_adopt, mock_apply, mock_link
    ):
        doc = self._base_doc()
        mock_get_doc.return_value = doc
        anomaly = frappe._dict(name="ACC-BTN-OB", bank_account="BANCARIBE_ARA-02", unallocated_amount=22041.08)
        mock_ob.return_value = anomaly

        result = reconcile_and_approve("ACC-PAY-OB")

        self.assertTrue(result["reconciled"])
        self.assertEqual(result["bank_transaction"], "ACC-BTN-OB")
        mock_adopt.assert_called_once_with(doc, anomaly)
        mock_apply.assert_called_once_with(doc, anomaly)  # concilia contra el depósito adoptado
        self.assertEqual(doc.custom_reconciliation_status, RECON_DONE)
        doc.submit.assert_called_once()
        mock_link.assert_called_once()
        self.assertEqual(doc.flags.get("l10n_ve_matched_deposit_doc"), anomaly)

    @patch(f"{MODULE}._adopt_deposit_bank_account", return_value=False)
    @patch(f"{MODULE}.find_deposit_other_bank")
    @patch(f"{MODULE}.find_matching_deposit", return_value=None)
    @patch(f"{MODULE}.find_duplicate_deposits", return_value=[])
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    @patch(f"{MODULE}.frappe.get_doc")
    def test_falls_back_to_review_when_no_mop(
        self, mock_get_doc, _is_bank, _dups, _match, mock_ob, _adopt
    ):
        doc = self._base_doc()
        mock_get_doc.return_value = doc
        mock_ob.return_value = frappe._dict(name="ACC-BTN-OB", bank_account="BANCARIBE_ARA-02")

        result = reconcile_and_approve("ACC-PAY-OB")

        self.assertFalse(result["reconciled"])
        self.assertTrue(result["review"])
        self.assertEqual(result["reason"], REVIEW_OTHER_BANK)
        doc.db_set.assert_called_once_with("custom_reconciliation_status", RECON_REVIEW)
        doc.submit.assert_not_called()


class TestConsumedAndUnsubmittedDeposit(ReconBaseTestCase):
    """find_consumed_deposit / find_unsubmitted_deposit: detectores de los motivos
    'referencia ya conciliada por otro cobro' y 'depósito sin emitir'."""

    @patch(f"{MODULE}.frappe.get_all")
    def test_consumed_returns_bt_and_twins(self, mock_get_all):
        # 1ª llamada: BTs emitidos con la ref; 2ª: pagos de OTROS cobros en ese BT.
        mock_get_all.side_effect = [
            ["ACC-BTN-1"],
            [frappe._dict(payment_entry="ACC-PAY-TWIN", allocated_amount=100.0)],
        ]
        doc = frappe._dict(reference_no="R1", name="ACC-PAY-X", paid_on_currency="VEF")
        res = find_consumed_deposit(doc)
        self.assertEqual(res.bank_transaction, "ACC-BTN-1")
        self.assertEqual(res.other_payments[0].payment_entry, "ACC-PAY-TWIN")

    @patch(f"{MODULE}.frappe.get_all")
    def test_consumed_none_when_no_other_payment(self, mock_get_all):
        mock_get_all.side_effect = [["ACC-BTN-1"], []]  # el BT no lo consume otro cobro
        doc = frappe._dict(reference_no="R1", name="ACC-PAY-X", paid_on_currency="VEF")
        self.assertIsNone(find_consumed_deposit(doc))

    def test_consumed_empty_reference(self):
        self.assertIsNone(find_consumed_deposit(frappe._dict(reference_no="", name="X")))

    def test_unsubmitted_returns_first_row(self):
        self.mock_db.sql.return_value = [
            frappe._dict(name="ACC-BTN-DRAFT", docstatus=0, status="Pending", deposit=500.0)
        ]
        doc = frappe._dict(reference_no="R1", paid_on_currency="VEF")
        res = find_unsubmitted_deposit(doc)
        self.assertEqual(res.name, "ACC-BTN-DRAFT")

    def test_unsubmitted_none_when_empty(self):
        self.mock_db.sql.return_value = []
        self.assertIsNone(find_unsubmitted_deposit(frappe._dict(reference_no="R1", paid_on_currency="VEF")))


class TestReconcileAndApproveMoreReasons(ReconBaseTestCase):
    """reconcile_and_approve: motivos already_reconciled y deposit_not_submitted."""

    def _doc(self):
        doc = MagicMock()
        doc.docstatus = 0
        doc.reference_no = "R1"
        doc.name = "ACC-PAY-X"
        doc.flags = frappe._dict()
        doc.get.return_value = None
        return doc

    @patch(f"{MODULE}.find_unsubmitted_deposit", return_value=None)
    @patch(f"{MODULE}.find_consumed_deposit")
    @patch(f"{MODULE}.find_deposit_other_bank", return_value=None)
    @patch(f"{MODULE}.find_matching_deposit", return_value=None)
    @patch(f"{MODULE}.find_duplicate_deposits", return_value=[])
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    @patch(f"{MODULE}.frappe.get_doc")
    def test_already_reconciled_flags_review_with_twin(
        self, mock_get_doc, _is_bank, _dups, _match, _ob, mock_consumed, _unsub
    ):
        doc = self._doc()
        mock_get_doc.return_value = doc
        mock_consumed.return_value = frappe._dict(
            bank_transaction="ACC-BTN-1",
            other_payments=[frappe._dict(payment_entry="ACC-PAY-TWIN", allocated_amount=100.0)],
        )
        result = reconcile_and_approve("ACC-PAY-X")
        self.assertTrue(result["review"])
        self.assertEqual(result["reason"], REVIEW_ALREADY_RECONCILED)
        self.assertEqual(result["other_payments"], ["ACC-PAY-TWIN"])
        doc.db_set.assert_called_once_with("custom_reconciliation_status", RECON_REVIEW)
        doc.submit.assert_not_called()

    @patch(f"{MODULE}.find_unsubmitted_deposit")
    @patch(f"{MODULE}.find_consumed_deposit", return_value=None)
    @patch(f"{MODULE}.find_deposit_other_bank", return_value=None)
    @patch(f"{MODULE}.find_matching_deposit", return_value=None)
    @patch(f"{MODULE}.find_duplicate_deposits", return_value=[])
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    @patch(f"{MODULE}.frappe.get_doc")
    def test_deposit_not_submitted_flags_review(
        self, mock_get_doc, _is_bank, _dups, _match, _ob, _consumed, mock_unsub
    ):
        doc = self._doc()
        mock_get_doc.return_value = doc
        mock_unsub.return_value = frappe._dict(name="ACC-BTN-DRAFT", status="Pending")
        result = reconcile_and_approve("ACC-PAY-X")
        self.assertEqual(result["reason"], REVIEW_DEPOSIT_NOT_SUBMITTED)
        self.assertEqual(result["bank_transaction"], "ACC-BTN-DRAFT")
        doc.db_set.assert_called_once_with("custom_reconciliation_status", RECON_REVIEW)

    @patch(f"{MODULE}.find_unsubmitted_deposit", return_value=None)
    @patch(f"{MODULE}.find_consumed_deposit", return_value=None)
    @patch(f"{MODULE}.find_deposit_other_bank", return_value=None)
    @patch(f"{MODULE}.find_matching_deposit", return_value=None)
    @patch(f"{MODULE}.find_duplicate_deposits", return_value=[])
    @patch(f"{MODULE}._is_bank_receive", return_value=True)
    @patch(f"{MODULE}.frappe.get_doc")
    def test_pending_when_no_deposit_anywhere(
        self, mock_get_doc, _is_bank, _dups, _match, _ob, _consumed, _unsub
    ):
        doc = self._doc()
        mock_get_doc.return_value = doc
        result = reconcile_and_approve("ACC-PAY-X")
        self.assertFalse(result["reconciled"])
        self.assertNotIn("review", result)
        doc.db_set.assert_called_once_with("custom_reconciliation_status", RECON_PENDING)
        doc.submit.assert_not_called()


class TestNightlySweep(ReconBaseTestCase):
    """Barrido nocturno: reintenta reconcile_and_approve sobre los borradores
    pendientes; cada cobro atómico (commit por éxito, rollback+log por fallo)."""

    @patch(f"{MODULE}.reconcile_and_approve")
    def test_approve_drafts_commits_and_counts(self, mock_ra):
        mock_ra.side_effect = [{"reconciled": True}, {"reconciled": False}, {"reconciled": True}]
        self.assertEqual(_approve_drafts(["A", "B", "C"]), 2)
        # commit tras cada intento que no lanzó (aunque no concilie)
        self.assertEqual(self.mock_db.commit.call_count, 3)

    @patch(f"{MODULE}.frappe.get_traceback", return_value="tb")
    @patch(f"{MODULE}.frappe.log_error")
    @patch(f"{MODULE}.reconcile_and_approve")
    def test_approve_drafts_isolates_failures(self, mock_ra, mock_log, _tb):
        # B falla: se revierte y se registra, pero A y C igual se procesan.
        mock_ra.side_effect = [{"reconciled": True}, Exception("boom"), {"reconciled": True}]
        self.assertEqual(_approve_drafts(["A", "B", "C"]), 2)
        self.assertTrue(self.mock_db.rollback.called)
        mock_log.assert_called_once()

    @patch(f"{MODULE}.frappe.logger")
    @patch(f"{MODULE}.cancel_exact_duplicate_deposits", return_value=0)
    @patch(f"{MODULE}._approve_drafts", return_value=5)
    @patch(f"{MODULE}.frappe.get_all")
    def test_nightly_sweep_filters_and_delegates(self, mock_get_all, mock_approve, mock_dedup, _logger):
        mock_get_all.return_value = ["A", "B", "C"]
        reconcile_pending_drafts_nightly()
        # primero sanea duplicados exactos, luego concilia
        mock_dedup.assert_called_once()
        filters = mock_get_all.call_args.kwargs["filters"]
        self.assertEqual(filters["docstatus"], 0)
        self.assertEqual(filters["payment_type"], "Receive")
        self.assertEqual(filters["reference_no"], ["!=", ""])  # solo con referencia
        self.assertNotIn("custom_reconciliation_status", filters)  # borrador ⇒ nunca Conciliado
        mock_approve.assert_called_once_with(["A", "B", "C"])


class TestCancelExactDuplicates(ReconBaseTestCase):
    """cancel_exact_duplicate_deposits: cancela el redundante SIN asignar de cada
    grupo de depósitos exactamente iguales; nunca toca uno asignado; conserva uno."""

    @patch(f"{MODULE}.frappe.get_doc")
    def test_cancels_unallocated_keeps_allocated(self, mock_get_doc):
        doc = MagicMock()
        doc.docstatus = 1
        mock_get_doc.return_value = doc
        self.mock_db.sql.side_effect = [
            [frappe._dict(ref="R1", bank_account="BA", company="C", amount=100.0)],  # grupos
            [frappe._dict(name="A", allocated_amount=100.0, docstatus=1),            # miembros
             frappe._dict(name="B", allocated_amount=0.0, docstatus=1)],
        ]
        self.assertEqual(cancel_exact_duplicate_deposits(), 1)  # cancela B (sin asignar)
        doc.cancel.assert_called_once()

    @patch(f"{MODULE}.frappe.get_doc")
    def test_both_allocated_cancels_none(self, mock_get_doc):
        self.mock_db.sql.side_effect = [
            [frappe._dict(ref="R1", bank_account="BA", company="C", amount=100.0)],
            [frappe._dict(name="A", allocated_amount=100.0, docstatus=1),
             frappe._dict(name="B", allocated_amount=100.0, docstatus=1)],
        ]
        self.assertEqual(cancel_exact_duplicate_deposits(), 0)
        mock_get_doc.assert_not_called()

    @patch(f"{MODULE}.frappe.get_doc")
    def test_all_unallocated_keeps_one(self, mock_get_doc):
        doc = MagicMock()
        doc.docstatus = 1
        mock_get_doc.return_value = doc
        self.mock_db.sql.side_effect = [
            [frappe._dict(ref="R1", bank_account="BA", company="C", amount=100.0)],
            [frappe._dict(name="A", allocated_amount=0.0, docstatus=1),
             frappe._dict(name="B", allocated_amount=0.0, docstatus=1),
             frappe._dict(name="C", allocated_amount=0.0, docstatus=1)],
        ]
        self.assertEqual(cancel_exact_duplicate_deposits(), 2)  # conserva 1, cancela 2

    def test_no_groups(self):
        self.mock_db.sql.side_effect = [[]]
        self.assertEqual(cancel_exact_duplicate_deposits(), 0)


class TestApplyFormatRule(ReconBaseTestCase):
    """apply_format_rule data-driven: carga la expresión de la Bank Reference Rule y la
    evalúa con safe_eval (sin nombres de regla quemados)."""

    def _run(self, rule_expr, ref="858249469"):
        # get_cached_value se parchea directo: bajo el mock de BD la ruta real pasa
        # por el document cache -> get_controller y revienta sin sitio.
        with patch(f"{MODULE}.frappe.get_cached_value", return_value=rule_expr):
            return apply_format_rule("regla-X", ref)

    def test_duplicar_referencia(self):
        self.assertEqual(self._run("f'{reference_number}{reference_number}'"), "858249469858249469")

    def test_ultimos_8_lossy(self):
        # Usa str(...) → requiere _SAFE_BUILTINS; safe_eval sin builtins fallaría.
        self.assertEqual(self._run("f'{str(reference_number)[-8:]}'"), "58249469")

    def test_agregar_3_ceros(self):
        self.assertEqual(self._run("f'000{reference_number}'"), "000858249469")

    def test_agregar_7_ceros(self):
        self.assertEqual(self._run("f'0000000{reference_number}'"), "0000000858249469")

    def test_sin_rule_name_devuelve_original(self):
        self.assertEqual(apply_format_rule(None, "  858249469  "), "858249469")

    def test_rule_sin_expresion_devuelve_original(self):
        with patch(f"{MODULE}.frappe.get_cached_value", return_value=None):
            self.assertEqual(apply_format_rule("regla-X", "858249469"), "858249469")

    def test_resultado_inflado_devuelve_original(self):
        # Cota anti-explosión: una regla que INFLA la referencia más allá de Data(140)
        # no puede matchear y encadenada crece exponencialmente -> se descarta.
        ref = "8" * 100
        self.assertEqual(self._run("f'{reference_number}{reference_number}'", ref=ref), ref)

    def test_expresion_invalida_devuelve_original(self):
        # Una expresión rota no rompe la conciliación: se loguea y se devuelve la original.
        with patch(f"{MODULE}.frappe.log_error"):
            self.assertEqual(self._run("f'{variable_inexistente}'"), "858249469")


class TestFindDepositBySourceBankRule(ReconBaseTestCase):
    """Forward-search: aplica la regla del banco origen a los depósitos candidatos y
    compara con la referencia del cobro (sin reversa)."""

    _CANDIDATES = [
        frappe._dict(name="ACC-BTN-1", reference_number="111", deposit=10,
                     unallocated_amount=10, currency="VEF", bank_account="ACC-BANK"),
        frappe._dict(name="ACC-BTN-2", reference_number="858249469", deposit=20,
                     unallocated_amount=20, currency="VEF", bank_account="ACC-BANK"),
    ]

    @patch(f"{MODULE}.apply_format_rule")
    @patch(f"{MODULE}.get_bank_rules", return_value=["Duplicar referencia"])
    @patch(f"{MODULE}.frappe.get_all")
    @patch(f"{MODULE}._cobro_bank_account", return_value="ACC-BANK")
    def test_forward_search_encuentra_por_regla(self, _bank, mock_get_all, mock_rules, mock_apply):
        mock_get_all.return_value = self._CANDIDATES
        mock_apply.side_effect = lambda rule, ref: ref + ref  # "duplicar"
        doc = frappe._dict(source_bank="BancoX", reference_no="858249469858249469",
                           paid_on_currency="VEF", paid_to="GL-Acct", paid_from=None)
        result = _find_deposit_by_source_bank_rule(doc)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "ACC-BTN-2")
        mock_rules.assert_called_once_with("BancoX")

    @patch(f"{MODULE}.apply_format_rule")
    @patch(f"{MODULE}.get_bank_rules", return_value=["Duplicar referencia"])
    @patch(f"{MODULE}.frappe.get_all")
    @patch(f"{MODULE}._cobro_bank_account", return_value="ACC-BANK")
    def test_sin_source_bank_prueba_todas_las_reglas(self, _bank, mock_get_all, mock_rules, mock_apply):
        # Sin source_bank el fallback prueba TODAS las reglas del sistema (docstring):
        # get_bank_rules() sin argumento.
        mock_get_all.return_value = self._CANDIDATES
        mock_apply.side_effect = lambda rule, ref: ref + ref
        doc = frappe._dict(source_bank=None, reference_no="858249469858249469",
                           paid_on_currency="VEF", paid_to="GL-Acct", paid_from=None)
        result = _find_deposit_by_source_bank_rule(doc)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "ACC-BTN-2")
        mock_rules.assert_called_once_with()


class TestCheckRulesMatchBounds(ReconBaseTestCase):
    """check_rules_match: los pipelines de permutaciones quedan ACOTADOS
    (MAX_PIPELINE_ATTEMPTS) — cota nacida del incidente 2026-07-09 (barrido nocturno
    30+ min de CPU en un solo cobro, con las reglas de todo el sistema)."""

    @patch(f"{MODULE}.apply_format_rule")
    def test_pipeline_de_dos_reglas_sigue_matcheando(self, mock_apply):
        # r1 agrega 'A', r2 agrega 'B'; el target sale de aplicar r1 y LUEGO r2.
        mock_apply.side_effect = lambda rule, ref: ref + rule
        ok, detail = check_rules_match(["A", "B"], "ref", "refAB")
        self.assertTrue(ok)
        self.assertEqual(detail, "A + B")

    @patch(f"{MODULE}.apply_format_rule")
    def test_intentos_de_pipeline_acotados(self, mock_apply):
        # 10 reglas sin match: sin cota serían ~9,8M permutaciones; con la cota el
        # total de aplicaciones queda en cientos (10 sueltas + <=100 pipelines).
        mock_apply.side_effect = lambda rule, ref: ref + "x"
        rules = [f"R{i}" for i in range(10)]
        with patch(f"{MODULE}.frappe.logger") as mock_logger:
            ok, detail = check_rules_match(rules, "raw", "imposible")
        self.assertFalse(ok)
        self.assertIsNone(detail)
        self.assertLessEqual(mock_apply.call_count, 400)
        mock_logger.assert_called()  # deja rastro de que truncó


if __name__ == "__main__":
    unittest.main()
