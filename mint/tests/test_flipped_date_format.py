# Copyright (c) 2026, DominaERP and Contributors
# See license.txt

import unittest
from datetime import datetime

from mint.apis.statement_import import (
    get_inverted_date_format,
    determine_statement_dominant_month,
    parse_and_correct_transaction_date,
    get_final_transactions,
)


class FlippedDateFormatTests(unittest.TestCase):
    def test_get_inverted_date_format(self):
        self.assertEqual(get_inverted_date_format("%d/%m/%Y"), "%m/%d/%Y")
        self.assertEqual(get_inverted_date_format("%m/%d/%Y"), "%d/%m/%Y")
        self.assertEqual(get_inverted_date_format("%d-%m-%Y"), "%m-%d-%Y")
        self.assertEqual(get_inverted_date_format("%d.%m.%Y"), "%m.%d.%Y")
        self.assertIsNone(get_inverted_date_format("%Y-%m-%d"))

    def test_determine_statement_dominant_month(self):
        transactions = [
            {"date": "01/07/2026"},
            {"date": "02/07/2026"},
            {"date": "03/07/2026"},
            {"date": "07/04/2026"},
            {"date": "05/07/2026"},
        ]
        month, year = determine_statement_dominant_month(transactions, "%d/%m/%Y")
        self.assertEqual(month, 7)
        self.assertEqual(year, 2026)

    def test_parse_and_correct_normal_date(self):
        dt = parse_and_correct_transaction_date("08/07/2026", "%d/%m/%Y", expected_month=7)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)
        self.assertEqual(dt.day, 8)

    def test_parse_and_correct_flipped_mda_date_posterior_month(self):
        dt = parse_and_correct_transaction_date("07/08/2026", "%d/%m/%Y", expected_month=7)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)
        self.assertEqual(dt.day, 8)

    def test_parse_and_correct_flipped_mda_date_invalid_month_number(self):
        dt = parse_and_correct_transaction_date("07/15/2026", "%d/%m/%Y", expected_month=7)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)
        self.assertEqual(dt.day, 15)

    def test_parse_and_correct_flipped_mda_date_anterior_month(self):
        dt = parse_and_correct_transaction_date("07/01/2026", "%d/%m/%Y", expected_month=7)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)
        self.assertEqual(dt.day, 1)

    def test_get_final_transactions_with_flipped_dates(self):
        raw_rows = [
            {"date": "01/07/2026", "deposit": "100"},
            {"date": "02/07/2026", "deposit": "200"},
            {"date": "07/08/2026", "deposit": "300"},
            {"date": "07/15/2026", "deposit": "400"},
        ]
        res = get_final_transactions(raw_rows, "%d/%m/%Y", "separate_columns_for_withdrawal_and_deposit")
        self.assertEqual(len(res), 4)
        self.assertEqual(res[0]["date"], "2026-07-01")
        self.assertEqual(res[1]["date"], "2026-07-02")
        self.assertEqual(res[2]["date"], "2026-07-08")
        self.assertEqual(res[3]["date"], "2026-07-15")


if __name__ == "__main__":
    unittest.main()

