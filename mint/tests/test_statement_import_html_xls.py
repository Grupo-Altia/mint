# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

"""
Tests del fallback HTML para importación de extractos (.xls que en realidad es
HTML), en mint.apis.statement_import.

Algunos bancos venezolanos (p. ej. Bancamiga) entregan el extracto como una
tabla HTML con extensión .xls. xlrd falla con
"Unsupported format, or corrupt file: Expected BOF record; found b'<table'"
y antes eso propagaba un 500 + Error Log. Ahora get_data detecta esa firma y
parsea el HTML como tabla.

Cubre:
- _is_html_like_xls_error(): reconoce las firmas de xlrd para HTML disfrazado.
- _parse_html_as_table(): extrae la tabla HTML como list[list[str]], compatible
  con get_header_row_index() (detección de encabezados aguas abajo).
- _parse_html_as_table(): lanza un error controlado cuando el HTML no trae tabla.

Corren sin sitio ni BD (solo bs4 + parsing puro).
"""

import unittest
from unittest.mock import patch

from mint.apis.statement_import import (
    _is_html_like_xls_error,
    _parse_html_as_table,
    get_header_row_index,
)

# Extracto real de Bancamiga (recortado) tal como llega con extensión .xls.
BANCAMIGA_HTML = (
    b"<table border='1'>"
    b'<tr><th colspan="7"><img src="logo.png"></th></tr>'
    b'<tr><th colspan="7">Bancamiga Banco Universal</th></tr>'
    b'<tr><th colspan="7">Cuenta: 01720110761109321701</th></tr>'
    b"<tr><th>Nro.</th><th>Fecha</th><th>Referencia</th><th>Concepto</th>"
    b"<th>D&eacute;bito</th><th>Cr&eacute;dito</th><th>Saldo</th></tr>"
    b"<tr><td>1</td><td>01/06/26</td><td>'415255619291</td>"
    b"<td>NC Fondos Recibidos P2C</td><td>0</td><td>16632,06</td><td>656284,12</td></tr>"
    b"<tr><td>2</td><td>02/06/26</td><td>'415255619292</td>"
    b"<td>Pago Movil</td><td>1000,00</td><td>0</td><td>655284,12</td></tr>"
    b"</table>"
)

XLRD_HTML_ERROR = "Unsupported format, or corrupt file: Expected BOF record; found b'<table b'"


class IsHtmlLikeXlsErrorTests(unittest.TestCase):
    def test_detects_xlrd_html_signature(self):
        self.assertTrue(_is_html_like_xls_error(Exception(XLRD_HTML_ERROR)))

    def test_detects_startswith_bytes_signature(self):
        self.assertTrue(
            _is_html_like_xls_error(
                TypeError("startswith first arg must be str or a tuple of str, not bytes")
            )
        )

    def test_ignores_unrelated_error(self):
        self.assertFalse(_is_html_like_xls_error(ValueError("algo totalmente distinto")))


class ParseHtmlAsTableTests(unittest.TestCase):
    def test_parses_rows_from_bytes(self):
        data = _parse_html_as_table(BANCAMIGA_HTML)
        # 3 filas de cabecera del banco + 1 fila de encabezados + 2 transacciones.
        self.assertEqual(len(data), 6)
        self.assertEqual(
            data[3],
            ["Nro.", "Fecha", "Referencia", "Concepto", "Débito", "Crédito", "Saldo"],
        )
        self.assertEqual(
            data[4],
            ["1", "01/06/26", "'415255619291", "NC Fondos Recibidos P2C", "0", "16632,06", "656284,12"],
        )

    def test_accepts_str_content(self):
        data = _parse_html_as_table(BANCAMIGA_HTML.decode("utf-8"))
        self.assertEqual(data[3][1], "Fecha")

    def test_header_row_is_detected_downstream(self):
        # La estructura devuelta debe ser compatible con la detección de
        # encabezados existente: la fila con Fecha/Referencia/Concepto/Saldo.
        data = _parse_html_as_table(BANCAMIGA_HTML)
        self.assertEqual(get_header_row_index(data), 3)

    def test_html_without_table_raises_controlled(self):
        with patch(
            "mint.apis.statement_import.frappe.throw",
            side_effect=ValueError("controlled throw"),
        ) as mock_throw:
            with self.assertRaises(ValueError):
                _parse_html_as_table(b"<html><body>sin tabla</body></html>")
            mock_throw.assert_called_once()


if __name__ == "__main__":
    unittest.main()
