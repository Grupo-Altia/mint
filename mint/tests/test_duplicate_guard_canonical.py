# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""Tests del guard de duplicados comparando la referencia CANÓNICA.

Nacen de un caso medido en producción: el mismo movimiento entró por el webhook
de Bancaribe con referencia '0001768329729' y por el extracto importado con
'1768329729'. Son el mismo número, pero el guard comparaba igualdad exacta y
dejó pasar el duplicado (Bs 26.000).

mint ya sabía normalizar esto —``_canonical_reference`` / ``_CANONICAL_REF_SQL``
lo usa la conciliación—, solo que el guard no lo consultaba: el matcher y el
guard se contradecían.

Corren sin site: ``frappe.local.db`` se bindea a un MagicMock.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from mint.apis.reconciliation import (
    _first_duplicate_by_canonical_reference,
    validate_bank_transaction_duplicate,
)

MODULE = "mint.apis.reconciliation"


class _DbBoundTestCase(unittest.TestCase):
    def setUp(self):
        self._original_db = getattr(frappe.local, "db", None)
        self.mock_db = MagicMock()
        self.mock_db.sql.return_value = []
        frappe.local.db = self.mock_db
        if not hasattr(frappe.local, "flags"):
            frappe.local.flags = frappe._dict()

    def tearDown(self):
        # Restaurar EXACTAMENTE lo que había: dejar el mock puesto hace pasar
        # tests de otros módulos que en realidad fallan (visto en esta suite).
        if self._original_db is not None:
            frappe.local.db = self._original_db
        elif hasattr(frappe.local, "db"):
            delattr(frappe.local, "db")


class TestFirstDuplicateByCanonicalReference(_DbBoundTestCase):
    FILTROS = {
        "name": ["!=", "NUEVO"],
        "date": "2026-07-31",
        "bank_account": "BANCARIBE_DC-01-1120",
        "company": "Galanet Solution C.A.",
        "docstatus": ["<", 2],
        "deposit": [">", 0],
    }

    def sql_ejecutado(self) -> tuple:
        self.assertTrue(self.mock_db.sql.called, "se esperaba una consulta")
        return self.mock_db.sql.call_args.args

    def test_leading_zeros_match_the_same_number(self):
        # El caso real: el existente tiene ceros a la izquierda y el nuevo no.
        self.mock_db.sql.return_value = [("ACC-BTN-EXISTENTE",)]

        encontrado = _first_duplicate_by_canonical_reference(dict(self.FILTROS), ["1768329729"])

        self.assertEqual(encontrado, "ACC-BTN-EXISTENTE")
        consulta, valores = self.sql_ejecutado()
        # La comparación va contra la forma canónica, no contra la columna cruda.
        self.assertIn("TRIM(LEADING '0'", consulta)
        self.assertEqual(valores["refs"], ("1768329729",))

    def test_candidates_are_canonicalized_and_deduplicated(self):
        # '0001768329729' y '1768329729' son el MISMO candidato canónico.
        _first_duplicate_by_canonical_reference(
            dict(self.FILTROS), ["0001768329729", "1768329729"])

        _, valores = self.sql_ejecutado()
        self.assertEqual(valores["refs"], ("1768329729",))

    def test_textual_references_are_not_stripped(self):
        _first_duplicate_by_canonical_reference(dict(self.FILTROS), ["0BANPANAMA"])

        _, valores = self.sql_ejecutado()
        self.assertEqual(valores["refs"], ("0BANPANAMA",))

    def test_reference_that_vanishes_is_discarded(self):
        # '000' canoniza a '' y casaría contra cualquier cosa: no se consulta.
        encontrado = _first_duplicate_by_canonical_reference(dict(self.FILTROS), ["000", "0"])

        self.assertIsNone(encontrado)
        self.mock_db.sql.assert_not_called()

    def test_operators_and_nulls_survive_the_translation(self):
        filtros = dict(self.FILTROS)
        filtros["company"] = None
        _first_duplicate_by_canonical_reference(filtros, ["12345"])

        consulta, valores = self.sql_ejecutado()
        self.assertIn("`name` != %(name)s", consulta)
        self.assertIn("`docstatus` < %(docstatus)s", consulta)
        self.assertIn("`deposit` > %(deposit)s", consulta)
        self.assertIn("`company` IS NULL", consulta)   # None no puede ir como '= NULL'
        self.assertNotIn("company", valores)
        self.assertEqual(valores["docstatus"], 2)

    def test_no_match_returns_none(self):
        self.mock_db.sql.return_value = []

        self.assertIsNone(
            _first_duplicate_by_canonical_reference(dict(self.FILTROS), ["12345"]))


class TestGuardUsesCanonicalComparison(_DbBoundTestCase):
    def doc(self, **kwargs):
        base = {
            "reference_number": "1768329729", "deposit": 26000.0, "withdrawal": 0,
            "date": "2026-07-31", "bank_account": "BANCARIBE_DC-01-1120",
            "company": "Galanet Solution C.A.", "description": "DEPOSITO COMPLETO",
            "name": None,
        }
        base.update(kwargs)
        d = frappe._dict(base)
        d.is_new = lambda: True
        return d

    def test_duplicate_with_different_zero_padding_is_blocked(self):
        with patch(f"{MODULE}.get_matching_description_rule", return_value=None), \
                patch(f"{MODULE}._first_duplicate_by_canonical_reference",
                      return_value="ACC-BTN-GEMELO") as mock_find, \
                patch.object(frappe.db, "get_value", return_value=26000.0), \
                patch(f"{MODULE}.frappe.utils.get_link_to_form", return_value="link"), \
                patch(f"{MODULE}._", side_effect=lambda m: m), \
                patch(f"{MODULE}.frappe.bold", side_effect=str), \
                patch(f"{MODULE}.frappe.throw", side_effect=frappe.ValidationError) as mock_throw:
            with self.assertRaises(frappe.ValidationError):
                validate_bank_transaction_duplicate(self.doc())

        # Se consultó por la referencia, sin meterla en los filtros crudos.
        filtros, candidatos = mock_find.call_args.args
        self.assertNotIn("reference_number", filtros)
        self.assertEqual(candidatos, ["1768329729"])
        self.assertTrue(mock_throw.called)

    def test_prefix_rule_still_expands_candidates(self):
        regla = frappe._dict(apply_prefix_rule=1, prefixes_to_strip="36")
        with patch(f"{MODULE}.get_matching_description_rule", return_value=regla), \
                patch(f"{MODULE}._first_duplicate_by_canonical_reference",
                      return_value=None) as mock_find:
            validate_bank_transaction_duplicate(self.doc(reference_number="361089459494"))

        _, candidatos = mock_find.call_args.args
        self.assertIn("361089459494", candidatos)
        self.assertIn("1089459494", candidatos)

    def test_whitelisted_description_still_allows_the_duplicate(self):
        regla = frappe._dict(apply_prefix_rule=0, prefixes_to_strip=None)
        with patch(f"{MODULE}.get_matching_description_rule", return_value=regla), \
                patch(f"{MODULE}._first_duplicate_by_canonical_reference",
                      return_value="ACC-BTN-GEMELO"), \
                patch(f"{MODULE}.frappe.throw", side_effect=AssertionError("no debía lanzar")):
            validate_bank_transaction_duplicate(self.doc(description="PAGOS A PROVEEDORES"))

    def test_existing_documents_are_not_revalidated(self):
        d = self.doc()
        d.is_new = lambda: False
        with patch(f"{MODULE}._first_duplicate_by_canonical_reference") as mock_find:
            validate_bank_transaction_duplicate(d)

        mock_find.assert_not_called()
