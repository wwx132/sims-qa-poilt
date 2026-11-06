import unittest

from pydantic import ValidationError

from sims_qa.mql import MQLPlan, SCHEMA_EXAMPLE


class MQLTests(unittest.TestCase):
    def test_full_plan_shape(self):
        plan = MQLPlan.model_validate(SCHEMA_EXAMPLE)
        self.assertEqual(plan.source.alias, "wo")
        self.assertEqual(plan.where[0].operator, ">=")
        self.assertNotIn("version", plan.model_dump())

    def test_missing_select_is_rejected(self):
        with self.assertRaises(ValidationError):
            MQLPlan.model_validate({**SCHEMA_EXAMPLE, "select": []})

    def test_governed_plan_requires_config_id(self):
        with self.assertRaises(ValidationError):
            MQLPlan.model_validate({**SCHEMA_EXAMPLE, "config_id": None})
