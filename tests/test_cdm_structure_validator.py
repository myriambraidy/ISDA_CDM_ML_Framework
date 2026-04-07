"""Tests for unified CDM structure validation (schema + Rosetta + supplementary)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fpml_cdm.cdm_structure_validator import (
    CdmStructureIssueCode,
    RosettaBlock,
    validate_cdm_structure,
    infra_blocked,
)
from fpml_cdm.rosetta_validator import RosettaValidationResult, find_jar, java_available

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CDM_FIXTURE = FIXTURES / "expected" / "fx_forward_cdm.json"


def _rosetta_ready() -> bool:
    return find_jar() is not None and java_available()


class CdmStructureReportTests(unittest.TestCase):
    def test_minimal_report_to_dict_sorts_top_level_keys(self) -> None:
        from fpml_cdm.cdm_structure_validator import CdmStructureReport

        r = CdmStructureReport(
            structure_ok=True,
            layers_executed=["envelope"],
            layer_ok={"envelope": True},
            error_count_by_layer={"envelope": 0, "json_schema": 0, "rosetta": 0, "supplementary": 0},
            issues=[],
            rosetta=RosettaBlock(ran=False, valid=None, exit_code=None, failure_count=0, error=None, failures=[]),
            metadata={"cdm_version": "6"},
        )
        d = r.to_dict()
        self.assertEqual(list(d.keys()), sorted(d.keys()))
        self.assertTrue(d["structure_ok"])

    def test_issues_sorted_in_validate_envelope_only(self) -> None:
        rep = validate_cdm_structure([], target_type="trade", run_schema=False, run_rosetta=False, supplementary=False)
        self.assertFalse(rep.structure_ok)
        self.assertTrue(all(i.layer == "envelope" for i in rep.issues))


class EnvelopeAndSchemaTests(unittest.TestCase):
    def test_not_object_root(self) -> None:
        r = validate_cdm_structure("not-a-dict", run_schema=False, run_rosetta=False, supplementary=False)
        self.assertFalse(r.structure_ok)
        self.assertEqual(r.issues[0].code, CdmStructureIssueCode.ENVELOPE_NOT_OBJECT)

    def test_missing_trade_key(self) -> None:
        r = validate_cdm_structure({}, run_schema=False, run_rosetta=False, supplementary=False)
        self.assertFalse(r.structure_ok)
        self.assertEqual(r.issues[0].code, CdmStructureIssueCode.ENVELOPE_MISSING_KEY)

    def test_schema_layer_empty_trade(self) -> None:
        r = validate_cdm_structure({"trade": {}}, run_rosetta=False, supplementary=False)
        self.assertFalse(r.structure_ok)
        self.assertTrue(any(i.layer == "json_schema" for i in r.issues))


class InfraBlockedHelperTests(unittest.TestCase):
    def test_infra_blocked_codes(self) -> None:
        self.assertTrue(infra_blocked([CdmStructureIssueCode.INFRA_BLOCKED_NO_JAVA.value]))
        self.assertTrue(infra_blocked([CdmStructureIssueCode.INFRA_BLOCKED_NO_JAR.value]))
        self.assertFalse(infra_blocked([CdmStructureIssueCode.JSON_SCHEMA_FAILED.value]))


class RosettaIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(_rosetta_ready(), "Rosetta JAR + Java required")
    def test_fixture_passes_with_real_jar(self) -> None:
        with open(CDM_FIXTURE, "r", encoding="utf-8") as f:
            data = json.load(f)
        r = validate_cdm_structure(data, rosetta_timeout_seconds=120)
        self.assertTrue(r.structure_ok, msg=[(i.layer, str(i.code), i.message) for i in r.issues])
        self.assertTrue(r.rosetta.ran)
        self.assertTrue(r.rosetta.valid)


class RosettaMockTests(unittest.TestCase):
    def test_rosetta_invalid_from_mock(self) -> None:
        fake = RosettaValidationResult(
            valid=False,
            failures=[{"type": "X", "name": "Rule", "failureMessage": "bad", "path": "/trade"}],
            error=None,
            exit_code=1,
        )
        with patch("fpml_cdm.rosetta_validator.validate_cdm_rosetta", return_value=fake):
            with patch("fpml_cdm.rosetta_validator.find_jar", return_value=Path("dummy.jar")):
                with patch("fpml_cdm.rosetta_validator.java_available", return_value=True):
                    r = validate_cdm_structure({"trade": {}}, supplementary=False)
        self.assertFalse(r.structure_ok)
        self.assertTrue(any(i.layer == "rosetta" for i in r.issues))
        self.assertEqual(r.rosetta.failures, fake.failures)


class ContractTests(unittest.TestCase):
    def test_java_validate_output_matches_validator(self) -> None:
        from fpml_cdm.java_gen.tools import validate_output

        payload = '{"trade": {}}'
        with patch("fpml_cdm.rosetta_validator.validate_cdm_rosetta") as m_ros:
            m_ros.return_value = RosettaValidationResult(valid=True, failures=[], error=None, exit_code=0)
            with patch("fpml_cdm.rosetta_validator.find_jar", return_value=Path("dummy.jar")):
                with patch("fpml_cdm.rosetta_validator.java_available", return_value=True):
                    out = validate_output(payload)
                    direct = validate_cdm_structure(json.loads(payload), supplementary=False).to_dict()
        self.assertEqual(out.keys(), direct.keys())
        self.assertIn("structure_ok", out)
        self.assertIn("rosetta", out)


if __name__ == "__main__":
    unittest.main()
