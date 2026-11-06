import unittest

from sims_qa.sql_validation import SQLValidationError, validate_sql


class SQLValidationTests(unittest.TestCase):
    def test_select_cte_and_union_pass(self):
        for sql in (
            "SELECT id FROM pilot.work_orders",
            "WITH recent AS (SELECT id FROM pilot.work_orders) SELECT id FROM recent",
            "SELECT id FROM pilot.work_orders UNION SELECT id FROM pilot.old_work_orders",
        ):
            with self.subTest(sql=sql):
                validated = validate_sql(sql, 50)
                self.assertIn("LIMIT 51", validated)
                self.assertIn("pilot.work_orders", validated)

    def test_outer_limit_caps_sql_even_when_model_asks_for_more(self):
        validated = validate_sql("SELECT id FROM pilot.work_orders LIMIT 1000", 10)
        self.assertIn("LIMIT 1000", validated)
        self.assertTrue(validated.endswith("LIMIT 11"))

    def test_rejects_non_select_and_multiple_statements(self):
        cases = {
            "DELETE FROM pilot.work_orders": "read_only",
            "CREATE TABLE copied AS SELECT * FROM pilot.work_orders": "read_only",
            "SELECT 1; DELETE FROM pilot.work_orders": "multiple_statements",
            "SELECT id FROM pilot.work_orders FOR UPDATE": "read_only",
            "SELECT (": "parse_failed",
            "   ": "empty_sql",
        }
        for sql, code in cases.items():
            with self.subTest(sql=sql), self.assertRaises(SQLValidationError) as raised:
                validate_sql(sql, 50)
            self.assertEqual(raised.exception.code, code)

    def test_invalid_row_cap_is_rejected(self):
        with self.assertRaises(SQLValidationError) as raised:
            validate_sql("SELECT id FROM pilot.work_orders", 0)
        self.assertEqual(raised.exception.code, "invalid_row_limit")
