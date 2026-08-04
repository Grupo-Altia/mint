# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""Tests del saneo de duplicados por prefijo de pasarela 94/094.

El patch da de baja UNA de las dos copias del mismo movimiento (la prefijada
94/094 y la base). Lo que se cubre acá es el selector de víctima, que es donde
está el riesgo: elegir mal borra una conciliación buena.

- _is_free: "en uso" no es solo allocated_amount; también cuenta el status y la
  existencia de filas en `tabBank Transaction Payments` (que quedan en 0.0 mientras
  add_payment_entries todavía no reparte).
- pick_removable: prefiere la prefijada; cae a la base solo si la prefijada está en
  uso; devuelve None si las dos lo están.
- find_prefixed_pairs: la consulta trae el filtro de "en uso" y el de referencia
  vacía.

Corren sin sitio ni BD: pick_removable/_is_free son puras y frappe.db.sql se
intercepta para inspeccionar la consulta.
"""

import unittest
from unittest.mock import MagicMock, patch

from mint.patches.cleanup_94_094_duplicates import (
    _is_free,
    find_prefixed_pairs,
    pick_removable,
)


def _pair(prefixed=(0, "Unreconciled", 0), base=(0, "Unreconciled", 0)):
    """Par de prueba: cada copia es (allocated_amount, status, filas hijas)."""
    return {
        "prefixed_name": "BT-94",
        "prefixed_allocated": prefixed[0],
        "prefixed_status": prefixed[1],
        "prefixed_rows": prefixed[2],
        "base_name": "BT-BASE",
        "base_allocated": base[0],
        "base_status": base[1],
        "base_rows": base[2],
    }


class TestIsFree(unittest.TestCase):
    def test_libre_sin_asignacion_ni_filas(self):
        self.assertTrue(_is_free(0, "Unreconciled", 0))

    def test_en_uso_por_monto_asignado(self):
        self.assertFalse(_is_free(1500.5, "Unreconciled", 0))

    def test_en_uso_por_status_reconciled(self):
        self.assertFalse(_is_free(0, "Reconciled", 0))

    def test_en_uso_por_status_settled(self):
        self.assertFalse(_is_free(0, "Settled", 0))

    def test_en_uso_por_filas_hijas_sin_repartir(self):
        # add_payment_entries inserta las filas con allocated_amount 0.0 antes de que
        # allocate_payment_entries las reparta: el monto solo no lo detecta.
        self.assertFalse(_is_free(0, "Unreconciled", 2))


class TestPickRemovable(unittest.TestCase):
    def test_prefiere_la_prefijada(self):
        self.assertEqual(pick_removable(_pair()), "BT-94")

    def test_cae_a_la_base_si_la_prefijada_esta_conciliada(self):
        pair = _pair(prefixed=(36735.14, "Reconciled", 1))
        self.assertEqual(pick_removable(pair), "BT-BASE")

    def test_no_toca_el_par_si_las_dos_estan_en_uso(self):
        pair = _pair(prefixed=(100, "Reconciled", 1), base=(100, "Reconciled", 1))
        self.assertIsNone(pick_removable(pair))

    def test_no_toca_la_base_conciliada_aunque_la_prefijada_tenga_filas(self):
        pair = _pair(prefixed=(0, "Unreconciled", 1), base=(0, "Reconciled", 1))
        self.assertIsNone(pick_removable(pair))


class TestFindPrefixedPairs(unittest.TestCase):
    def _query(self, amount_field="deposit"):
        with patch("frappe.db", MagicMock()) as db:
            db.sql.return_value = []
            find_prefixed_pairs(amount_field)
            return db.sql.call_args[0][0]

    def test_excluye_las_copias_en_uso(self):
        query = self._query()
        self.assertIn("allocated_amount = 0", query)
        self.assertIn("NOT IN ('Reconciled', 'Settled')", query)
        self.assertIn("NOT EXISTS", query)

    def test_excluye_referencia_base_vacia(self):
        # CONCAT('94', '') = '94' emparejaría con cualquier BT de referencia '94'.
        query = self._query()
        self.assertIn("TRIM(t2.reference_number) != ''", query)

    def test_ignora_las_canceladas(self):
        query = self._query()
        self.assertIn("t1.docstatus < 2", query)
        self.assertIn("t2.docstatus < 2", query)

    def test_usa_el_campo_de_monto_recibido(self):
        self.assertIn("ROUND(t1.withdrawal, 2)", self._query("withdrawal"))


if __name__ == "__main__":
    unittest.main()
