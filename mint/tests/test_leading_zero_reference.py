# Copyright (c) 2026, Domina Software and Contributors
# See license.txt

"""Conciliación insensible a los ceros a la izquierda de la referencia.

Contexto (caso NOC, decenas de cobros atascados): el cliente paga y el depósito
entra al banco, pero el extracto rellena la referencia con ceros de forma
inconsistente ('0012345' vs '12345'). El match EXACTO de referencia dejaba esos
cobros sin conciliar → servicio cortado pese a haber pagado.

Fix: `_canonical_reference` (Python) + `_CANONICAL_REF_SQL` (BD) canonicalizan la
referencia quitando los ceros a la izquierda SOLO en refs numéricas; las
textuales quedan intactas para no fusionar referencias distintas.

Cubre:
- _canonical_reference: numérica sin ceros; textual intacta; todo-ceros → '';
  se apoya en normalize_reference (whitespace/.0/comilla).
- _deposit_base_filters guarda la ref canónica.
- find_duplicate_deposits y _first_deposit pasan la ref canónica Y su SQL
  canonicaliza el valor guardado (contiene el TRIM(LEADING '0' ...)).
Todo con mocks (sin tocar la BD): son funciones puras salvo el frappe.db.sql,
que se intercepta para inspeccionar query + params.
"""

import unittest
from unittest.mock import patch

import frappe

from mint.apis.reconciliation import (
    _canonical_reference,
    _deposit_base_filters,
    _first_deposit,
    find_duplicate_deposits,
    _CANONICAL_REF_SQL,
)

MODULE = "mint.apis.reconciliation"


class TestCanonicalReference(unittest.TestCase):
    """La forma canónica ignora ceros a la izquierda solo en refs numéricas."""

    def test_numeric_strips_leading_zeros(self) -> None:
        self.assertEqual(_canonical_reference("0012345"), "12345")
        self.assertEqual(_canonical_reference("00000123"), "123")

    def test_numeric_without_leading_zeros_unchanged(self) -> None:
        self.assertEqual(_canonical_reference("12345"), "12345")

    def test_textual_reference_untouched(self) -> None:
        # No es puramente numérica → se deja tal cual (no se fusionan textuales).
        self.assertEqual(
            _canonical_reference("BANPANAMA SR CRISTIAN"), "BANPANAMA SR CRISTIAN"
        )
        self.assertEqual(_canonical_reference("007 BOND"), "007 BOND")

    def test_all_zeros_becomes_empty(self) -> None:
        # '0', '000'… → '' para que el llamador aborte (guard) y no matchee espurio.
        self.assertEqual(_canonical_reference("0"), "")
        self.assertEqual(_canonical_reference("0000"), "")

    def test_empty_and_none(self) -> None:
        self.assertEqual(_canonical_reference(""), "")
        self.assertEqual(_canonical_reference(None), "")

    def test_applies_normalize_first(self) -> None:
        # whitespace / .0 / comilla inicial (normalize_reference) + ceros.
        self.assertEqual(_canonical_reference("  0012345  "), "12345")
        self.assertEqual(_canonical_reference("0012345.0"), "12345")
        self.assertEqual(_canonical_reference("'0012345"), "12345")


class TestDepositBaseFiltersCanonical(unittest.TestCase):
    def test_filters_store_canonical_ref(self) -> None:
        doc = frappe._dict(reference_no="0012345", paid_on_currency="VEF")
        filters = _deposit_base_filters(doc)
        self.assertEqual(filters["reference_number"], "12345")
        self.assertEqual(filters["currency"], "VEF")

    def test_all_zeros_ref_returns_none(self) -> None:
        doc = frappe._dict(reference_no="000", paid_on_currency="")
        self.assertIsNone(_deposit_base_filters(doc))


class TestFindDuplicateDepositsCanonical(unittest.TestCase):
    @patch(f"{MODULE}.frappe.db.sql", return_value=[])
    @patch(f"{MODULE}._cobro_bank_account", return_value="BANK-ACC")
    def test_uses_canonical_ref_and_sql(self, _mock_ba, mock_sql) -> None:
        doc = frappe._dict(reference_no="0012345", company="C", paid_to="X")
        find_duplicate_deposits(doc)
        query, params = mock_sql.call_args[0][0], mock_sql.call_args[0][1]
        self.assertEqual(params["ref"], "12345")
        self.assertIn("TRIM(LEADING '0'", query)  # canonicaliza el valor guardado


class TestFirstDepositCanonical(unittest.TestCase):
    @patch(f"{MODULE}.frappe.db.sql", return_value=[])
    def test_canonical_ref_currency_and_bank(self, mock_sql) -> None:
        _first_deposit(
            {"reference_number": "12345", "currency": "VEF", "bank_account": "B1"}
        )
        query, params = mock_sql.call_args[0][0], mock_sql.call_args[0][1]
        self.assertEqual(params["ref"], "12345")
        self.assertIn("TRIM(LEADING '0'", query)
        self.assertEqual(params["bank_account"], "B1")
        self.assertIn("bank_account = %(bank_account)s", query)
        self.assertIn("currency = %(currency)s", query)

    @patch(f"{MODULE}.frappe.db.sql", return_value=[])
    def test_other_bank_operator(self, mock_sql) -> None:
        # forma ["!=", cuenta] de find_deposit_other_bank
        _first_deposit({"reference_number": "12345", "bank_account": ["!=", "B1"]})
        query, params = mock_sql.call_args[0][0], mock_sql.call_args[0][1]
        self.assertIn("bank_account != %(bank_account)s", query)
        self.assertEqual(params["bank_account"], "B1")

    @patch(f"{MODULE}.frappe.db.sql", return_value=[])
    def test_empty_ref_no_query(self, mock_sql) -> None:
        self.assertIsNone(_first_deposit({"reference_number": ""}))
        mock_sql.assert_not_called()


if __name__ == "__main__":
    unittest.main()
