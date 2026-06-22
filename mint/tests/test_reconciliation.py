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
    RECON_REVIEW,
    REVIEW_DUPLICATE_REFERENCE,
    find_duplicate_deposits,
    before_submit_receive_payment,
    reconcile_and_approve,
    apply_format_rule,
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


class TestApplyFormatRule(ReconBaseTestCase):
    """apply_format_rule data-driven: carga la expresión de la Bank Reference Rule y la
    evalúa con safe_eval (sin nombres de regla quemados)."""

    def _run(self, rule_expr, ref="858249469"):
        self.mock_db.get_value.return_value = rule_expr  # Bank Reference Rule.rule
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
        self.mock_db.get_value.return_value = None
        self.assertEqual(apply_format_rule("regla-X", "858249469"), "858249469")

    def test_expresion_invalida_devuelve_original(self):
        # Una expresión rota no rompe la conciliación: se loguea y se devuelve la original.
        with patch(f"{MODULE}.frappe.log_error"):
            self.assertEqual(self._run("f'{variable_inexistente}'"), "858249469")


class TestFindDepositBySourceBankRule(ReconBaseTestCase):
    """Forward-search: aplica la regla del banco origen a los depósitos candidatos y
    compara con la referencia del cobro (sin reversa)."""

    @patch(f"{MODULE}.apply_format_rule")
    @patch(f"{MODULE}._cobro_bank_account", return_value="ACC-BANK")
    def test_forward_search_encuentra_por_regla(self, _bank, mock_apply):
        self.mock_db.get_value.return_value = "Duplicar referencia"  # Bank.bank_reference_rule
        self.mock_db.get_all.return_value = [
            frappe._dict(name="ACC-BTN-1", reference_number="111", deposit=10,
                         unallocated_amount=10, currency="VEF", bank_account="ACC-BANK"),
            frappe._dict(name="ACC-BTN-2", reference_number="858249469", deposit=20,
                         unallocated_amount=20, currency="VEF", bank_account="ACC-BANK"),
        ]
        mock_apply.side_effect = lambda rule, ref: ref + ref  # "duplicar"
        doc = frappe._dict(source_bank="BancoX", reference_no="858249469858249469",
                           paid_on_currency="VEF", paid_to="GL-Acct", paid_from=None)
        result = _find_deposit_by_source_bank_rule(doc)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "ACC-BTN-2")

    @patch(f"{MODULE}._cobro_bank_account", return_value="ACC-BANK")
    def test_sin_source_bank_no_busca(self, _bank):
        doc = frappe._dict(source_bank=None, reference_no="X", paid_on_currency="VEF",
                           paid_to="GL", paid_from=None)
        self.assertIsNone(_find_deposit_by_source_bank_rule(doc))
        self.mock_db.get_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()
