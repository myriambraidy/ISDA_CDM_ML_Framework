"""Tests for mapping_agent.classifier."""

import unittest
from pathlib import Path

from fpml_cdm.mapping_agent.classifier import classify_fpml
from fpml_cdm.mapping_agent.skill_store import load_skill_catalog


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "fpml"


class TestClassifier(unittest.TestCase):
    def setUp(self):
        self.catalog = load_skill_catalog()

    def test_fx_forward(self):
        path = FIXTURES_DIR / "fx_forward.xml"
        if not path.exists():
            self.skipTest(f"Fixture not found: {path}")
        result = classify_fpml(str(path), self.catalog)
        self.assertEqual(result.skill_id, "fx-forward-like")
        self.assertEqual(result.confidence, 1.0)
        self.assertIn("fxForward", result.product_local_names)

    def test_fx_single_leg(self):
        path = FIXTURES_DIR / "fx_single_leg.xml"
        if not path.exists():
            self.skipTest(f"Fixture not found: {path}")
        result = classify_fpml(str(path), self.catalog)
        self.assertEqual(result.skill_id, "fx-forward-like")

    def test_fx_swap(self):
        path = FIXTURES_DIR / "fx_swap.xml"
        if not path.exists():
            self.skipTest(f"Fixture not found: {path}")
        result = classify_fpml(str(path), self.catalog)
        self.assertEqual(result.skill_id, "fx-swap")

    def test_fx_option(self):
        path = FIXTURES_DIR / "fx_option.xml"
        if not path.exists():
            self.skipTest(f"Fixture not found: {path}")
        result = classify_fpml(str(path), self.catalog)
        self.assertEqual(result.skill_id, "fx-option")

    def test_unsupported_product(self):
        path = FIXTURES_DIR / "unsupported_fx_digital_option.xml"
        if not path.exists():
            self.skipTest(f"Fixture not found: {path}")
        result = classify_fpml(str(path), self.catalog)
        self.assertIsNone(result.skill_id)
        self.assertEqual(result.confidence, 0.0)

    def test_nonexistent_file(self):
        result = classify_fpml("/does/not/exist.xml", self.catalog)
        self.assertIsNone(result.skill_id)
        self.assertIn("not found", result.reason.lower())

    def test_to_dict(self):
        result = classify_fpml("/does/not/exist.xml", self.catalog)
        d = result.to_dict()
        self.assertIn("skill_id", d)
        self.assertIn("confidence", d)
        self.assertIn("reason", d)


if __name__ == "__main__":
    unittest.main()
