from __future__ import annotations

import unittest
from pathlib import Path

from fpml_cdm import ErrorCode, parse_fpml_fx, transform_to_cdm_v6, validate_transformation

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ValidatorTests(unittest.TestCase):
    def test_validate_transformation_success_path(self) -> None:
        fpml_path = FIXTURES / "fpml" / "fx_forward.xml"
        model = parse_fpml_fx(str(fpml_path))
        cdm = transform_to_cdm_v6(model)

        report = validate_transformation(str(fpml_path), cdm)
        self.assertTrue(report.valid)
        self.assertGreater(report.mapping_score.total_fields, 0)
        self.assertEqual(report.mapping_score.accuracy_percent, 100.0)

    def test_validate_transformation_success_path_fx_swap(self) -> None:
        fpml_path = FIXTURES / "fpml" / "fx_swap.xml"
        model = parse_fpml_fx(str(fpml_path))
        cdm = transform_to_cdm_v6(model)
        report = validate_transformation(str(fpml_path), cdm)
        self.assertTrue(report.valid)

    def test_validate_detects_semantic_mismatch(self) -> None:
        fpml_path = FIXTURES / "fpml" / "fx_forward.xml"
        model = parse_fpml_fx(str(fpml_path))
        cdm = transform_to_cdm_v6(model)
        cdm["trade"]["tradeLot"][0]["priceQuantity"][0]["quantity"][0]["value"]["unit"]["currency"]["value"] = "CHF"
        cdm["trade"]["tradeLot"][0]["priceQuantity"][0]["quantity"][0]["value"]["value"] = 999.0

        report = validate_transformation(str(fpml_path), cdm)
        self.assertFalse(report.valid)
        self.assertTrue(any(issue.code == ErrorCode.SEMANTIC_VALIDATION_FAILED.value for issue in report.errors))

    def test_validate_detects_schema_mismatch(self) -> None:
        fpml_path = FIXTURES / "fpml" / "fx_forward.xml"
        report = validate_transformation(str(fpml_path), {"invalid": "shape"})

        self.assertFalse(report.valid)
        self.assertTrue(any(issue.code == ErrorCode.SCHEMA_VALIDATION_FAILED.value for issue in report.errors))

    def test_validate_returns_structured_error_for_unsupported_source(self) -> None:
        fpml_path = FIXTURES / "fpml" / "unsupported_fx_digital_option.xml"
        report = validate_transformation(str(fpml_path), {"trade": {}})

        self.assertFalse(report.valid)
        self.assertTrue(any(issue.code == ErrorCode.UNSUPPORTED_PRODUCT.value for issue in report.errors))


if __name__ == "__main__":
    unittest.main()
