# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""
Tests de la conciliación automática de cobros en efectivo / pasarela.

Nacen de una queja de ATC (03-ago-2026): clientes pagaban por pasarela (C2P,
Biopago) y sus cobros quedaban en «Conciliación Pendiente» para siempre, porque
`on_submit_receive_payment` sólo marcaba conciliado el cobro **si aparecía un
depósito bancario con su referencia** -- y de una pasarela ese depósito no llega
nunca. Medido en producción: 20 cobros de pasarela y 480 en efectivo, ninguno con
`clearance_date`.

Cubre:
- Sin depósito, el cobro igual queda conciliado, con la fecha del propio cobro.
- Con depósito, se enlaza y manda la fecha del depósito.
- Se escribe `clearance_date`, no sólo el estado: `on_change_payment_entry` deriva
  el estado de esa fecha y sin ella devuelve el cobro a «Conciliación Pendiente».
  Es la razón por la que el arreglo no puede tocar sólo el Select.
- Es idempotente: repetir no vuelve a escribir.
- Un cobro bancario NO se autoconcilia: ése sí exige su depósito.

Corren sin site, con las dependencias del módulo parcheadas.
"""

import unittest
from unittest.mock import patch

import frappe

from mint.apis.reconciliation import (
    RECON_DONE,
    RECON_PENDING,
    mark_cash_like_reconciled,
    on_change_payment_entry,
    on_submit_receive_payment,
)

MODULE = "mint.apis.reconciliation"


class _FakePE:
    """Payment Entry mínimo: lo que toca la conciliación de un cobro."""

    def __init__(self, mode_of_payment="BANCARIBE C2P", clearance_date=None,
                 status=RECON_PENDING, posting_date="2026-08-03", docstatus=1):
        self.name = "ACC-PAY-2026-15303"
        self.payment_type = "Receive"
        self.mode_of_payment = mode_of_payment
        self.posting_date = posting_date
        self.clearance_date = clearance_date
        self.custom_reconciliation_status = status
        self.docstatus = docstatus
        self.flags = frappe._dict()
        self.written = []

    def get(self, field, default=None):
        return getattr(self, field, default)

    def db_set(self, field, value, **kwargs):
        setattr(self, field, value)
        self.written.append((field, value))


class _FakeDeposit:
    def __init__(self, name="BT-001", date="2026-08-01"):
        self.name = name
        self.date = date


class _CashLikeTestCase(unittest.TestCase):
    """Trata todo modo de pago como de tipo pasarela salvo que se diga otra cosa."""

    MOP_TYPE = "Gangway"

    def setUp(self):
        self._mop = patch(
            f"{MODULE}.frappe.get_cached_value", side_effect=lambda *a, **k: self.MOP_TYPE
        )
        self._mop.start()
        self.addCleanup(self._mop.stop)

        self._link = patch(f"{MODULE}._link_deposit_to_payment")
        self.mock_link = self._link.start()
        self.addCleanup(self._link.stop)


class TestCashLikeReconciledWithoutDeposit(_CashLikeTestCase):

    def test_gateway_payment_without_deposit_is_reconciled(self):
        """El bug: sin depósito quedaba pendiente para siempre."""
        pe = _FakePE()
        with patch(f"{MODULE}.find_matching_deposit", return_value=None):
            changed = mark_cash_like_reconciled(pe)

        self.assertTrue(changed)
        self.assertEqual(pe.custom_reconciliation_status, RECON_DONE)
        self.assertEqual(pe.clearance_date, "2026-08-03")
        self.mock_link.assert_not_called()

    def test_clearance_date_is_the_payment_date(self):
        """Sin depósito la fecha sale del cobro, no de hoy: es cuando entró el dinero."""
        pe = _FakePE(posting_date="2026-07-15")
        with patch(f"{MODULE}.find_matching_deposit", return_value=None):
            mark_cash_like_reconciled(pe)

        self.assertEqual(pe.clearance_date, "2026-07-15")

    def test_clearance_date_is_persisted_not_only_the_status(self):
        """Regresión: `on_change_payment_entry` deriva el estado de `clearance_date` y
        devuelve a pendiente todo cobro sin fecha. Escribir sólo el Select se deshacía
        en el siguiente guardado, así que la fecha tiene que quedar en la BD."""
        pe = _FakePE()
        with patch(f"{MODULE}.find_matching_deposit", return_value=None):
            mark_cash_like_reconciled(pe)

        self.assertIn("clearance_date", [campo for campo, _ in pe.written])

        on_change_payment_entry(pe)
        self.assertEqual(pe.custom_reconciliation_status, RECON_DONE)

    def test_is_idempotent(self):
        """Ya conciliado: no vuelve a escribir (el barrido nocturno los repasa)."""
        pe = _FakePE(clearance_date="2026-08-03", status=RECON_DONE)
        with patch(f"{MODULE}.find_matching_deposit", return_value=None) as buscar:
            changed = mark_cash_like_reconciled(pe)

        self.assertFalse(changed)
        self.assertEqual(pe.written, [])
        buscar.assert_not_called()

    def test_status_done_without_date_is_still_repaired(self):
        """Un cobro marcado conciliado pero sin fecha está a medias: se completa.
        Si no, `on_change_payment_entry` lo devolvería a pendiente."""
        pe = _FakePE(clearance_date=None, status=RECON_DONE)
        with patch(f"{MODULE}.find_matching_deposit", return_value=None):
            changed = mark_cash_like_reconciled(pe)

        self.assertTrue(changed)
        self.assertEqual(pe.clearance_date, "2026-08-03")


class TestCashLikeReconciledWithDeposit(_CashLikeTestCase):

    def test_deposit_is_linked_and_its_date_wins(self):
        """Si el depósito existe se enlaza igual que antes: el extracto queda cuadrado."""
        pe = _FakePE()
        deposit = _FakeDeposit(name="BT-777", date="2026-08-01")
        with patch(f"{MODULE}.find_matching_deposit", return_value=deposit):
            changed = mark_cash_like_reconciled(pe)

        self.assertTrue(changed)
        self.mock_link.assert_called_once_with("BT-777", pe.name)
        self.assertEqual(pe.clearance_date, "2026-08-01")
        self.assertEqual(pe.custom_reconciliation_status, RECON_DONE)


class TestOnSubmitRoutesCashLike(_CashLikeTestCase):

    def test_cash_like_receive_is_reconciled_on_submit(self):
        """El cobro de pasarela sale conciliado del propio submit."""
        pe = _FakePE()
        with patch(f"{MODULE}.find_matching_deposit", return_value=None):
            on_submit_receive_payment(pe)

        self.assertEqual(pe.custom_reconciliation_status, RECON_DONE)

    def test_cash_receive_is_reconciled_on_submit(self):
        """Efectivo: misma regla. Tampoco tiene depósito que esperar."""
        self.MOP_TYPE = "Cash"
        pe = _FakePE(mode_of_payment="EFECTIVO")
        with patch(f"{MODULE}.find_matching_deposit", return_value=None):
            on_submit_receive_payment(pe)

        self.assertEqual(pe.custom_reconciliation_status, RECON_DONE)

    def test_bank_receive_is_not_auto_reconciled(self):
        """Un cobro bancario SÍ exige su depósito: no se autoconcilia."""
        self.MOP_TYPE = "Bank"
        pe = _FakePE(mode_of_payment="TRANSFERENCIA")
        with patch(f"{MODULE}.find_matching_deposit", return_value=None):
            on_submit_receive_payment(pe)

        self.assertEqual(pe.custom_reconciliation_status, RECON_PENDING)
        self.assertIsNone(pe.clearance_date)


if __name__ == "__main__":
    unittest.main()
