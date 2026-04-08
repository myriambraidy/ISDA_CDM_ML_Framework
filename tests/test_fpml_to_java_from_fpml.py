from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from fpml_cdm import convert_fpml_to_cdm
from fpml_cdm.fpml_to_cdm_java import generate_java_from_fpml
from fpml_cdm.java_gen.agent import AgentResult
from fpml_cdm.java_gen.tools import json_stem_to_java_class_name
from fpml_cdm.mapping_agent.agent import MappingAgentConfig, MappingAgentResult
from fpml_cdm.rosetta_validator import RosettaValidationResult


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "fpml"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class GenerateJavaFromFpmlTests(unittest.TestCase):
    def test_generate_java_from_fpml_skips_mapping_when_deterministic_valid(self) -> None:
        # Fixtures that already pass the deterministic conversion pipeline.
        fixtures = [FIXTURES / "fx_forward.xml", FIXTURES / "fx_single_leg.xml"]

        for fpml_path in fixtures:
            with self.subTest(fpml_path=str(fpml_path)):
                out_dir = ROOT / "tmp" / "unittest_fpml_to_java"
                cdm_json_path_expected = out_dir / "generated_expected_cdm.json"
                if out_dir.exists():
                    # Best-effort cleanup; if it fails, tests will just re-write the file.
                    for p in out_dir.glob("*.json"):
                        p.unlink(missing_ok=True)

                with patch(
                    "fpml_cdm.pipeline.validate_cdm_rosetta_with_retry",
                    return_value=RosettaValidationResult(valid=True, failures=[]),
                ):
                    expected_cdm = convert_fpml_to_cdm(str(fpml_path), strict=True).cdm
                assert expected_cdm is not None

                mapping_result = MappingAgentResult(
                    best_cdm_json=expected_cdm,
                    best_normalized={},
                    best_validation_report={"valid": True, "errors": [], "warnings": [], "mapping_score": {"total_fields": 0, "matched_fields": 0, "accuracy_percent": 100.0}},
                    best_schema_error_count=0,
                    best_semantic_error_count=0,
                    best_rosetta_failure_count=0,
                    adapter_id="fxForward",
                    iterations=1,
                    total_tool_calls=1,
                    duration_seconds=0.01,
                    trace=[],
                )
                with patch("fpml_cdm.fpml_to_cdm_java.run_mapping_agent", return_value=mapping_result) as mock_mapping, patch(
                    "fpml_cdm.java_gen.agent.run_agent"
                ) as mock_java:

                    def _mock_java_run(
                        *,
                        cdm_json_path: str,
                        llm_client: object,
                        model: str,
                        config: object,
                        log_progress: object = None,
                        java_class_name: object = None,
                        artifacts_dir: object = None,
                        enable_fixups: object = None,
                    ) -> AgentResult:
                        cdm_json_path_p = Path(cdm_json_path)
                        self.assertTrue(cdm_json_path_p.exists())
                        self.assertEqual(_load_json(cdm_json_path_p), expected_cdm)
                        jc = json_stem_to_java_class_name(fpml_path.stem)
                        self.assertEqual(java_class_name, jc)
                        return AgentResult(
                            success=True,
                            java_file=f"generated/{jc}.java",
                            match_percentage=100.0,
                            iterations=1,
                            total_tool_calls=0,
                            duration_seconds=0.0,
                            summary="mock",
                            trace=[],
                        )

                    mock_java.side_effect = _mock_java_run

                    java_result, mapping_result, cdm_json_path = generate_java_from_fpml(
                        str(fpml_path),
                        llm_client=object(),
                        mapping_model="mapping",
                        java_model="java",
                        mapping_enabled=True,
                        mapping_config=MappingAgentConfig(max_iterations=0),
                        java_config=None,
                        log_progress=False,
                        output_dir=str(out_dir),
                    )

                    self.assertTrue(java_result.success)
                    self.assertIsNone(mapping_result)
                    self.assertEqual(Path(cdm_json_path), cdm_json_path_expected)
                    mock_mapping.assert_not_called()

    def test_generate_java_from_fpml_runs_mapping_when_deterministic_invalid(self) -> None:
        fpml_path = FIXTURES / "missing_value_date.xml"
        out_dir = ROOT / "tmp" / "unittest_fpml_to_java"

        best_cdm_json = {"trade": {"tradeDate": {"value": "2024-06-01"}}}
        best_normalized = {
            "tradeDate": "2024-06-01",
            "valueDate": "2024-06-01",
            "currency1": "USD",
            "currency2": "CAD",
            "amount1": 1000000.0,
            "amount2": 1360000.0,
            "exchangeRate": None,
            "settlementType": "PHYSICAL",
            "parties": [],
            "tradeIdentifiers": [],
            "sourceProduct": "fxForward",
        }
        best_validation_report = {
            "valid": False,
            "mapping_score": {"total_fields": 0, "matched_fields": 0, "accuracy_percent": 0.0},
            "errors": [],
            "warnings": [],
        }
        mapping_result = MappingAgentResult(
            best_cdm_json=best_cdm_json,
            best_normalized=best_normalized,
            best_validation_report=best_validation_report,
            best_schema_error_count=1,
            best_semantic_error_count=0,
            adapter_id="fxForward",
            iterations=1,
            total_tool_calls=1,
            duration_seconds=0.01,
            trace=[{"iteration": 0, "type": "tool_call", "tool": "run_conversion_with_patch"}],
        )

        with patch("fpml_cdm.fpml_to_cdm_java.run_mapping_agent", return_value=mapping_result) as mock_mapping, patch(
            "fpml_cdm.java_gen.agent.run_agent"
        ) as mock_java:

            jc = json_stem_to_java_class_name(fpml_path.stem)
            mock_java.return_value = AgentResult(
                success=True,
                java_file=f"generated/{jc}.java",
                match_percentage=100.0,
                iterations=1,
                total_tool_calls=0,
                duration_seconds=0.0,
                summary="mock",
                trace=[],
            )

            java_result, mapping_out, cdm_json_path = generate_java_from_fpml(
                str(fpml_path),
                llm_client=object(),
                mapping_model="mapping",
                java_model="java",
                mapping_enabled=True,
                mapping_config=MappingAgentConfig(max_iterations=1),
                java_config=None,
                log_progress=False,
                output_dir=str(out_dir),
            )

            self.assertTrue(java_result.success)
            self.assertIsNotNone(mapping_out)
            self.assertEqual(mapping_out.best_cdm_json, best_cdm_json)
            self.assertEqual(_load_json(Path(cdm_json_path)), best_cdm_json)
            mock_mapping.assert_called_once()


if __name__ == "__main__":
    unittest.main()

