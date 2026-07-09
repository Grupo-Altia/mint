# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""Tests de la clasificación del saneo de depósitos duplicados (x100 y exactos).

_classify(members) es una función pura (solo lee deposit/allocated_amount de dos
Bank Transaction ordenados por deposit ASC) y decide la acción:
DELETE_BIG / FIX_BIG (x100), EXACT_DUP (mismo monto, re-import), MANUAL.

Corre sin sitio ni BD: flt() de un solo argumento no consulta System Settings.
"""

import unittest

import frappe

from mint.patches.accounts.cleanup_duplicate_deposits import _classify


def _bt(name, deposit, allocated=0.0):
    return frappe._dict({
        "name": name,
        "deposit": deposit,
        "allocated_amount": allocated,
        "unallocated_amount": deposit - allocated,
        "status": "Unreconciled",
        "docstatus": 1,
    })


class TestClassify(unittest.TestCase):
    # ---- EXACT_DUP (mismo monto: re-import) ----
    def test_exact_dup_keeps_allocated_deletes_unallocated(self):
        # small sin asignar, big asignado -> conserva big, cancela small
        small, big = _bt("A", 100.0, 0.0), _bt("B", 100.0, 100.0)
        action, keep, act, _ = _classify([small, big])
        self.assertEqual(action, "EXACT_DUP")
        self.assertEqual(keep.name, "B")   # el asignado se conserva
        self.assertEqual(act.name, "A")    # el redundante sin asignar se cancela

    def test_exact_dup_small_allocated(self):
        # small asignado, big sin asignar -> conserva small, cancela big
        small, big = _bt("A", 100.0, 100.0), _bt("B", 100.0, 0.0)
        action, keep, act, _ = _classify([small, big])
        self.assertEqual(action, "EXACT_DUP")
        self.assertEqual(keep.name, "A")
        self.assertEqual(act.name, "B")

    def test_exact_dup_both_unallocated(self):
        # ambos sin asignar -> EXACT_DUP, se conserva uno y se cancela el otro
        action, keep, act, _ = _classify([_bt("A", 100.0), _bt("B", 100.0)])
        self.assertEqual(action, "EXACT_DUP")
        self.assertEqual({keep.name, act.name}, {"A", "B"})

    def test_exact_dup_both_allocated_is_manual(self):
        action, _, _, reason = _classify([_bt("A", 100.0, 100.0), _bt("B", 100.0, 100.0)])
        self.assertEqual(action, "MANUAL")
        self.assertIn("ambos asignados", reason)

    # ---- x100 (no debe cambiar el comportamiento existente) ----
    def test_x100_delete_big(self):
        # grande = 100x, sin asignación -> DELETE_BIG
        action, keep, big, _ = _classify([_bt("S", 114.53), _bt("B", 11453.0)])
        self.assertEqual(action, "DELETE_BIG")
        self.assertEqual(big.name, "B")

    def test_x100_fix_big(self):
        # grande carga la asignación del monto correcto -> FIX_BIG
        action, small, big, _ = _classify([_bt("S", 114.53, 0.0), _bt("B", 11453.0, 114.53)])
        self.assertEqual(action, "FIX_BIG")

    # ---- MANUAL ----
    def test_ratio_not_decimal_nor_one(self):
        action, _, _, reason = _classify([_bt("A", 100.0), _bt("B", 150.0)])
        self.assertEqual(action, "MANUAL")
        self.assertIn("no decimal ni 1", reason)

    def test_not_a_pair(self):
        action, _, _, reason = _classify([_bt("A", 100.0)])
        self.assertEqual(action, "MANUAL")
        self.assertIn("no es par", reason)


if __name__ == "__main__":
    unittest.main()
