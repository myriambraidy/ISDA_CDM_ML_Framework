from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from dotenv import load_dotenv

from fpml_cdm.mapping_agent.agent import MappingAgentConfig, run_mapping_agent
from fpml_cdm.mapping_agent import tools as mapping_tools
from fpml_cdm.java_gen.openrouter_client import OpenRouterClient


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "fpml"

load_dotenv()


def _trace_tool_names(trace: List[Dict[str, Any]]) -> List[str]:
    return [t.get("tool") for t in trace if t.get("type") == "tool_call"]


def _has_tool_result(trace: List[Dict[str, Any]]) -> bool:
    return any(t.get("type") == "tool_result" for t in trace)


def _make_client() -> OpenRouterClient:
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for real integration tests")
    timeout = float(os.environ.get("FPML_CDM_OPENROUTER_TIMEOUT", "120"))
    return OpenRouterClient(api_key=api_key, timeout=timeout)


# Don’t invoke Rosetta in these integration tests (avoid needing JAR).
_ORIGINAL_VALIDATE_BEST_EFFORT = mapping_tools.validate_best_effort


def _validate_best_effort_force_no_rosetta(fpml_path: str, cdm_json: object, **kwargs: object):
    # Force enable_rosetta=False regardless of what the LLM passes.
    kwargs.pop("enable_rosetta", None)
    kwargs.pop("rosetta_timeout_seconds", None)
    return _ORIGINAL_VALIDATE_BEST_EFFORT(  # type: ignore[misc]
        fpml_path=fpml_path,
        cdm_json=cdm_json,
        enable_rosetta=False,
        rosetta_timeout_seconds=60,
        **kwargs,
    )


class MappingAgentRealLLMIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("OPENROUTER_API_KEY"), "Requires OPENROUTER_API_KEY for real tool-calling")
    def test_mapping_agent_real_llm_fx_forward(self) -> None:
        out_dir = ROOT / "tmp" / "mapping_agent_real_llm"
        out_dir.mkdir(parents=True, exist_ok=True)
        trace_path = out_dir / f"trace_fx_forward_{int(time.time())}.json"

        llm_client = _make_client()
        cfg = MappingAgentConfig(
            max_iterations=int(os.environ.get("FPML_CDM_MAPPING_MAX_ITERATIONS", "2")),
            max_tool_calls=int(os.environ.get("FPML_CDM_MAPPING_MAX_TOOL_CALLS", "25")),
            timeout_seconds=int(os.environ.get("FPML_CDM_MAPPING_TIMEOUT", "120")),
            semantic_no_improve_limit=int(os.environ.get("FPML_CDM_MAPPING_NO_IMPROVE", "1")),
        )

        fpml_path = str(FIXTURES / "fx_forward.xml")
        with patch("fpml_cdm.mapping_agent.tools.validate_best_effort", side_effect=_validate_best_effort_force_no_rosetta):
            result = run_mapping_agent(
                fpml_path=fpml_path,
                llm_client=llm_client,
                model=os.environ.get("FPML_CDM_OPENROUTER_MODEL", "minimax/minimax-m2.5"),
                config=cfg,
                log_progress=False,
            )

        self.assertIn("trade", result.best_cdm_json)
        self.assertIsInstance(result.best_schema_error_count, int)
        self.assertIsInstance(result.best_semantic_error_count, int)
        self.assertIsInstance(result.adapter_id, str)

        tool_names = _trace_tool_names(result.trace)
        self.assertGreaterEqual(len(tool_names), 1, f"trace had no tool_call: {result.trace!r}")
        self.assertTrue(_has_tool_result(result.trace), f"trace had no tool_result: {result.trace!r}")

        trace_path.write_text(
            __import__("json").dumps(
                {
                    "adapter_id": result.adapter_id,
                    "best_schema_error_count": result.best_schema_error_count,
                    "best_semantic_error_count": result.best_semantic_error_count,
                    "trace": result.trace,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @unittest.skipUnless(os.environ.get("OPENROUTER_API_KEY"), "Requires OPENROUTER_API_KEY for real tool-calling")
    def test_mapping_agent_real_llm_missing_value_date(self) -> None:
        out_dir = ROOT / "tmp" / "mapping_agent_real_llm"
        out_dir.mkdir(parents=True, exist_ok=True)
        trace_path = out_dir / f"trace_missing_value_date_{int(time.time())}.json"

        llm_client = _make_client()
        cfg = MappingAgentConfig(
            max_iterations=int(os.environ.get("FPML_CDM_MAPPING_MAX_ITERATIONS", "2")),
            max_tool_calls=int(os.environ.get("FPML_CDM_MAPPING_MAX_TOOL_CALLS", "25")),
            timeout_seconds=int(os.environ.get("FPML_CDM_MAPPING_TIMEOUT", "120")),
            semantic_no_improve_limit=int(os.environ.get("FPML_CDM_MAPPING_NO_IMPROVE", "1")),
        )

        fpml_path = str(FIXTURES / "missing_value_date.xml")
        with patch("fpml_cdm.mapping_agent.tools.validate_best_effort", side_effect=_validate_best_effort_force_no_rosetta):
            result = run_mapping_agent(
                fpml_path=fpml_path,
                llm_client=llm_client,
                model=os.environ.get("FPML_CDM_OPENROUTER_MODEL", "minimax/minimax-m2.5"),
                config=cfg,
                log_progress=False,
            )

        self.assertIn("trade", result.best_cdm_json)
        self.assertIsInstance(result.best_schema_error_count, int)
        self.assertIsInstance(result.best_semantic_error_count, int)

        tool_names = _trace_tool_names(result.trace)
        self.assertGreaterEqual(len(tool_names), 1, f"trace had no tool_call: {result.trace!r}")
        self.assertTrue(_has_tool_result(result.trace), f"trace had no tool_result: {result.trace!r}")

        trace_path.write_text(
            __import__("json").dumps(
                {
                    "adapter_id": result.adapter_id,
                    "best_schema_error_count": result.best_schema_error_count,
                    "best_semantic_error_count": result.best_semantic_error_count,
                    "trace": result.trace,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @unittest.skipUnless(os.environ.get("OPENROUTER_API_KEY"), "Requires OPENROUTER_API_KEY for real tool-calling")
    def test_mapping_agent_real_llm_fx_single_leg(self) -> None:
        out_dir = ROOT / "tmp" / "mapping_agent_real_llm"
        out_dir.mkdir(parents=True, exist_ok=True)
        trace_path = out_dir / f"trace_fx_single_leg_{int(time.time())}.json"

        llm_client = _make_client()
        cfg = MappingAgentConfig(
            max_iterations=int(os.environ.get("FPML_CDM_MAPPING_MAX_ITERATIONS", "2")),
            max_tool_calls=int(os.environ.get("FPML_CDM_MAPPING_MAX_TOOL_CALLS", "25")),
            timeout_seconds=int(os.environ.get("FPML_CDM_MAPPING_TIMEOUT", "120")),
            semantic_no_improve_limit=int(os.environ.get("FPML_CDM_MAPPING_NO_IMPROVE", "1")),
        )

        fpml_path = str(FIXTURES / "fx_single_leg.xml")
        with patch("fpml_cdm.mapping_agent.tools.validate_best_effort", side_effect=_validate_best_effort_force_no_rosetta):
            result = run_mapping_agent(
                fpml_path=fpml_path,
                llm_client=llm_client,
                model=os.environ.get("FPML_CDM_OPENROUTER_MODEL", "minimax/minimax-m2.5"),
                config=cfg,
                log_progress=False,
            )

        self.assertIn("trade", result.best_cdm_json)
        self.assertIsInstance(result.best_schema_error_count, int)
        self.assertIsInstance(result.best_semantic_error_count, int)

        tool_names = _trace_tool_names(result.trace)
        self.assertGreaterEqual(len(tool_names), 1, f"trace had no tool_call: {result.trace!r}")
        self.assertTrue(_has_tool_result(result.trace), f"trace had no tool_result: {result.trace!r}")

        trace_path.write_text(
            __import__("json").dumps(
                {
                    "adapter_id": result.adapter_id,
                    "best_schema_error_count": result.best_schema_error_count,
                    "best_semantic_error_count": result.best_semantic_error_count,
                    "trace": result.trace,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()

