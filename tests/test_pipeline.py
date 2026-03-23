from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fpml_cdm import ErrorCode, convert_fpml_to_cdm
from fpml_cdm.rosetta_validator import RosettaValidationResult

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _stable_hash(data) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class PipelineTests(unittest.TestCase):
    def test_convert_pipeline_success(self) -> None:
        with patch(
            "fpml_cdm.pipeline.validate_cdm_rosetta_with_retry",
            return_value=RosettaValidationResult(valid=True, failures=[]),
        ):
            result = convert_fpml_to_cdm(str(FIXTURES / "fpml" / "fx_forward.xml"))
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.normalized)
        self.assertIsNotNone(result.cdm)
        self.assertIsNotNone(result.validation)
        self.assertTrue(result.validation.valid)

    def test_convert_is_deterministic_across_runs(self) -> None:
        cdm_hashes = []
        normalized_hashes = []

        for _ in range(3):
            with patch(
                "fpml_cdm.pipeline.validate_cdm_rosetta_with_retry",
                return_value=RosettaValidationResult(valid=True, failures=[]),
            ):
                result = convert_fpml_to_cdm(str(FIXTURES / "fpml" / "fx_forward.xml"))
            self.assertTrue(result.ok)
            cdm_hashes.append(_stable_hash(result.cdm))
            normalized_hashes.append(_stable_hash(result.normalized.to_dict()))

        self.assertEqual(len(set(cdm_hashes)), 1)
        self.assertEqual(len(set(normalized_hashes)), 1)

    def test_convert_rejects_unsupported_product(self) -> None:
        result = convert_fpml_to_cdm(str(FIXTURES / "fpml" / "unsupported_fx_digital_option.xml"))
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.code == ErrorCode.UNSUPPORTED_PRODUCT.value for issue in result.errors))

    def test_convert_marks_review_required_when_rosetta_unavailable(self) -> None:
        with patch(
            "fpml_cdm.pipeline.validate_cdm_rosetta_with_retry",
            return_value=RosettaValidationResult(valid=False, failures=[], error="Java not found"),
        ):
            result = convert_fpml_to_cdm(str(FIXTURES / "fpml" / "fx_forward.xml"))
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.compliance)
        self.assertEqual(result.compliance["failure_reason"], "ROSETTA_INFRA_UNAVAILABLE")
        self.assertTrue(result.compliance["review_required"])
        self.assertIsNotNone(result.review_ticket)

    def test_cli_convert_command_passes(self) -> None:
        cmd = [
            sys.executable,
            "-m",
            "fpml_cdm",
            "convert",
            str(FIXTURES / "fpml" / "fx_forward.xml"),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
