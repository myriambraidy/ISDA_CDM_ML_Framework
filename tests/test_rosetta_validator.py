"""Standalone tests for the Rosetta validator integration.

These tests verify the Python <-> Java bridge works correctly,
independent of the rest of the pipeline.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fpml_cdm.rosetta_validator import (
    RosettaValidationResult,
    find_jar,
    java_available,
    validate_cdm_rosetta,
    validate_cdm_rosetta_with_retry,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class RosettaValidatorAvailabilityTests(unittest.TestCase):
    """Check that Java and the JAR are reachable."""

    def test_java_is_on_path(self):
        self.assertTrue(java_available(), "Java not found on PATH")

    def test_jar_is_built(self):
        jar = find_jar()
        self.assertIsNotNone(jar, "Rosetta validator JAR not found — run: cd rosetta-validator && mvn package -q")
        self.assertTrue(jar.exists())


class RosettaValidatorResultTests(unittest.TestCase):
    """Test the RosettaValidationResult dataclass."""

    def test_valid_result_produces_no_issues(self):
        result = RosettaValidationResult(valid=True, failures=[], exit_code=0)
        self.assertEqual(result.to_issues(), [])

    def test_failures_convert_to_issues(self):
        result = RosettaValidationResult(
            valid=False,
            failures=[
                {"name": "TestRule", "type": "DATA_RULE", "path": "Trade", "failureMessage": "something broke"}
            ],
            exit_code=1,
        )
        issues = result.to_issues()
        self.assertEqual(len(issues), 1)
        self.assertIn("DATA_RULE", issues[0].message)
        self.assertIn("something broke", issues[0].message)

    def test_error_converts_to_issue(self):
        result = RosettaValidationResult(valid=False, error="JVM crashed", exit_code=2)
        issues = result.to_issues()
        self.assertEqual(len(issues), 1)
        self.assertIn("JVM crashed", issues[0].message)

    def test_to_dict_roundtrip(self):
        result = RosettaValidationResult(valid=True, failures=[], exit_code=0)
        d = result.to_dict()
        self.assertTrue(d["valid"])
        self.assertEqual(d["failureCount"], 0)

    def test_retry_helper_retries_transient_error(self):
        seq = [
            RosettaValidationResult(valid=False, failures=[], error="Rosetta validator timed out after 1s", exit_code=-1),
            RosettaValidationResult(valid=True, failures=[], exit_code=0),
        ]
        with patch("fpml_cdm.rosetta_validator.find_jar", return_value=Path("dummy.jar")), patch(
            "fpml_cdm.rosetta_validator.java_available", return_value=True
        ), patch("fpml_cdm.rosetta_validator.validate_cdm_rosetta", side_effect=seq):
            out = validate_cdm_rosetta_with_retry({"trade": {}}, timeout_seconds=1, max_attempts=2)
        self.assertTrue(out.valid)

    def test_retry_helper_fails_closed_on_missing_infra(self):
        with patch("fpml_cdm.rosetta_validator.find_jar", return_value=None):
            out = validate_cdm_rosetta_with_retry({"trade": {}}, max_attempts=2)
        self.assertFalse(out.valid)
        self.assertIn("JAR not found", out.error or "")


@unittest.skipUnless(find_jar() and java_available(), "Rosetta JAR or Java not available")
class RosettaValidatorIntegrationTests(unittest.TestCase):
    """Actually invoke the Java validator and check the results."""

    def test_fixture_cdm_json_returns_structured_result(self):
        cdm_path = FIXTURES / "expected" / "fx_forward_cdm.json"
        with open(cdm_path, "r", encoding="utf-8") as f:
            cdm_data = json.load(f)

        result = validate_cdm_rosetta(cdm_data)

        self.assertIsInstance(result, RosettaValidationResult)
        self.assertIsInstance(result.valid, bool)
        self.assertIsInstance(result.failures, list)
        self.assertIsNone(result.error)
        self.assertIn(result.exit_code, (0, 1))

    def test_fixture_passes_rosetta_validation(self):
        cdm_path = FIXTURES / "expected" / "fx_forward_cdm.json"
        with open(cdm_path, "r", encoding="utf-8") as f:
            cdm_data = json.load(f)

        result = validate_cdm_rosetta(cdm_data)

        self.assertTrue(result.valid, f"Expected valid=True but got failures: {result.failures}")
        self.assertEqual(len(result.failures), 0)

    def test_fx_swap_fixture_passes_rosetta_validation(self):
        cdm_path = FIXTURES / "expected" / "fx_swap_cdm.json"
        with open(cdm_path, "r", encoding="utf-8") as f:
            cdm_data = json.load(f)

        result = validate_cdm_rosetta(cdm_data)
        self.assertTrue(result.valid, f"Expected valid=True but got failures: {result.failures}")
        self.assertEqual(len(result.failures), 0)

    def test_empty_trade_returns_failures(self):
        result = validate_cdm_rosetta({"trade": {}})

        self.assertFalse(result.valid)
        self.assertGreater(len(result.failures), 0)

    def test_garbage_json_returns_error(self):
        result = validate_cdm_rosetta({"not_a_trade": 123})

        self.assertIn(result.exit_code, (1, 2))

    def test_to_issues_integration(self):
        """Verify a valid CDM run produces zero ValidationIssue items."""
        cdm_path = FIXTURES / "expected" / "fx_forward_cdm.json"
        with open(cdm_path, "r", encoding="utf-8") as f:
            cdm_data = json.load(f)

        result = validate_cdm_rosetta(cdm_data)
        issues = result.to_issues()

        self.assertEqual(len(issues), 0)


if __name__ == "__main__":
    unittest.main()
