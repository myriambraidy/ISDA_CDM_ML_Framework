"""Tests for mapping_agent.agent with mocked LLM."""

import json
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import patch

from fpml_cdm.mapping_agent.agent import (
    MappingAgentConfig,
    MappingAgentResult,
    run_mapping_agent,
    _build_registry,
)


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "fpml"


# --- Mock LLM infrastructure ---

@dataclass
class MockFunctionCall:
    name: str
    arguments: str


@dataclass
class MockToolCall:
    id: str
    function: MockFunctionCall


@dataclass
class MockMessage:
    role: str = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[List[MockToolCall]] = None


@dataclass
class MockChoice:
    message: MockMessage
    index: int = 0
    finish_reason: Optional[str] = None


@dataclass
class MockResponse:
    choices: List[MockChoice]


class MockCompletions:
    def __init__(self, responses: List[MockResponse]):
        self._responses = list(responses)
        self._call_count = 0

    def create(self, **kwargs) -> MockResponse:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
        else:
            resp = self._responses[-1]
        self._call_count += 1
        return resp


class MockChat:
    def __init__(self, completions: MockCompletions):
        self.completions = completions


class MockLLMClient:
    def __init__(self, responses: List[MockResponse]):
        self.chat = MockChat(MockCompletions(responses))


def _make_tool_call(tool_id: str, name: str, args: dict) -> MockToolCall:
    return MockToolCall(id=tool_id, function=MockFunctionCall(name=name, arguments=json.dumps(args)))


def _make_finish_response(status: str = "success", summary: str = "done") -> MockResponse:
    return MockResponse(choices=[MockChoice(message=MockMessage(
        tool_calls=[_make_tool_call("tc_finish", "finish", {"status": status, "summary": summary})]
    ))])


class TestToolRegistry(unittest.TestCase):
    def test_registry_has_finish(self):
        reg = _build_registry("ruleset")
        self.assertIn("finish", reg.tools)

    def test_registry_has_core_tools_ruleset(self):
        reg = _build_registry("ruleset")
        expected = {"inspect_fpml_trade", "get_active_ruleset_summary",
                    "list_supported_fx_adapters", "run_conversion_with_patch",
                    "validate_best_effort", "fpml_coverage_report", "finish"}
        self.assertEqual(set(reg.tools.keys()), expected)

    def test_registry_llm_native_has_submit_not_patch(self):
        reg = _build_registry("llm_native")
        names = set(reg.tools.keys())
        self.assertIn("submit_llm_cdm", names)
        self.assertNotIn("run_conversion_with_patch", names)
        self.assertNotIn("get_active_ruleset_summary", names)

    def test_tool_definitions_for_llm(self):
        reg = _build_registry("ruleset")
        specs = reg.tool_definitions_for_llm()
        self.assertEqual(len(specs), 7)
        names = {s["function"]["name"] for s in specs}
        self.assertIn("finish", names)
        self.assertIn("fpml_coverage_report", names)


class TestAgentWithMockedLLM(unittest.TestCase):
    def _fixture(self, name: str) -> str:
        p = FIXTURES_DIR / name
        if not p.exists():
            self.skipTest(f"Fixture not found: {p}")
        return str(p)

    def test_immediate_finish(self):
        """LLM calls finish on the first turn."""
        client = MockLLMClient([_make_finish_response("success", "Mapping looks good")])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_forward.xml"),
            llm_client=client,
            model="test-model",
            config=MappingAgentConfig(max_iterations=5, max_tool_calls=20, timeout_seconds=30),
        )
        self.assertIsInstance(result, MappingAgentResult)
        self.assertEqual(result.finish_summary, "Mapping looks good")
        self.assertGreater(result.iterations, 0)
        self.assertIsNotNone(result.skill_id)
        self.assertEqual(result.skill_id, "fx-forward-like")

    def test_text_only_then_finish(self):
        """LLM returns text-only first, nudged, then finishes."""
        text_resp = MockResponse(choices=[MockChoice(message=MockMessage(content="Let me think..."))])
        client = MockLLMClient([text_resp, _make_finish_response()])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_forward.xml"),
            llm_client=client,
            model="test-model",
            config=MappingAgentConfig(max_iterations=5, max_tool_calls=20, timeout_seconds=30),
        )
        self.assertIsNotNone(result.finish_summary)

    def test_inspect_then_finish(self):
        """LLM calls inspect_fpml_trade then finish."""
        fpml_path = self._fixture("fx_forward.xml")
        inspect_resp = MockResponse(choices=[MockChoice(message=MockMessage(
            tool_calls=[_make_tool_call("tc1", "inspect_fpml_trade", {"fpml_path": fpml_path})]
        ))])
        client = MockLLMClient([inspect_resp, _make_finish_response()])
        result = run_mapping_agent(
            fpml_path=fpml_path,
            llm_client=client,
            model="test-model",
            config=MappingAgentConfig(max_iterations=5, max_tool_calls=20, timeout_seconds=30),
        )
        self.assertIsNotNone(result)
        # At least 1 tool call (inspect); may stop early if deterministic baseline already 0 errors
        self.assertGreaterEqual(result.total_tool_calls, 1)

    def test_trace_contains_classifier(self):
        client = MockLLMClient([_make_finish_response()])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_forward.xml"),
            llm_client=client,
            model="test-model",
            config=MappingAgentConfig(max_iterations=3),
        )
        classifier_entries = [t for t in result.trace if t.get("type") == "classifier"]
        self.assertEqual(len(classifier_entries), 1)
        self.assertEqual(classifier_entries[0]["skill_id"], "fx-forward-like")

    def test_trace_contains_initial_best(self):
        client = MockLLMClient([_make_finish_response()])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_forward.xml"),
            llm_client=client,
            model="test-model",
            config=MappingAgentConfig(max_iterations=3),
        )
        init = [t for t in result.trace if t.get("type") == "initial_best"]
        self.assertEqual(len(init), 1)
        self.assertIn("schema_errors", init[0])

    def test_result_has_skill_fields(self):
        client = MockLLMClient([_make_finish_response()])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_forward.xml"),
            llm_client=client,
            model="test-model",
        )
        self.assertIsNotNone(result.skill_id)
        self.assertIsNotNone(result.skill_version)
        self.assertIsNotNone(result.classifier_result)

    def test_result_to_dict(self):
        client = MockLLMClient([_make_finish_response()])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_forward.xml"),
            llm_client=client,
            model="test-model",
        )
        d = result.to_dict()
        self.assertIn("skill_id", d)
        self.assertIn("skill_version", d)
        self.assertIn("classifier_result", d)
        self.assertIn("finish_summary", d)

    def test_fx_swap_classification(self):
        client = MockLLMClient([_make_finish_response()])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_swap.xml"),
            llm_client=client,
            model="test-model",
        )
        self.assertEqual(result.skill_id, "fx-swap")

    def test_fx_option_classification(self):
        client = MockLLMClient([_make_finish_response()])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_option.xml"),
            llm_client=client,
            model="test-model",
        )
        self.assertEqual(result.skill_id, "fx-option")

    def test_max_iterations_respected(self):
        """Agent stops at max_iterations even if LLM never calls finish."""
        inspect_resp = MockResponse(choices=[MockChoice(message=MockMessage(
            tool_calls=[_make_tool_call("tc1", "list_supported_fx_adapters", {})]
        ))])
        client = MockLLMClient([inspect_resp] * 20)
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_forward.xml"),
            llm_client=client,
            model="test-model",
            config=MappingAgentConfig(max_iterations=3, max_tool_calls=50, timeout_seconds=60),
        )
        self.assertLessEqual(result.iterations, 3)
        self.assertIsNone(result.finish_summary)

    def test_llm_native_mode_no_baseline_until_submit(self):
        client = MockLLMClient([_make_finish_response()])
        result = run_mapping_agent(
            fpml_path=self._fixture("fx_forward.xml"),
            llm_client=client,
            model="test-model",
            config=MappingAgentConfig(
                mapping_mode="llm_native",
                max_iterations=3,
                max_tool_calls=20,
                timeout_seconds=60,
                enable_rosetta=False,
            ),
        )
        self.assertEqual(result.mapping_mode, "llm_native")
        self.assertEqual(result.best_cdm_json, {})
        self.assertGreater(result.best_schema_error_count, 0)


if __name__ == "__main__":
    unittest.main()
