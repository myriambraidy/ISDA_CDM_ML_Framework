"""Tests for the agent loop with a mock LLM client + real LLM integration."""

import json
import os
import shutil
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from fpml_cdm.java_gen.agent import (
    AgentConfig,
    AgentResult,
    load_tool_specs,
    run_agent,
    TOOL_DISPATCH,
    SYSTEM_PROMPT,
)


# ── tools.json validation ────────────────────────────────────────────

class ToolsJsonTests(unittest.TestCase):

    def test_loads_all_13_tools(self) -> None:
        specs = load_tool_specs()
        self.assertEqual(len(specs), 13)

    def test_each_spec_has_function_key(self) -> None:
        specs = load_tool_specs()
        for spec in specs:
            self.assertEqual(spec["type"], "function")
            self.assertIn("name", spec["function"])
            self.assertIn("parameters", spec["function"])

    def test_all_tools_in_dispatch(self) -> None:
        specs = load_tool_specs()
        spec_names = {s["function"]["name"] for s in specs}
        dispatch_names = set(TOOL_DISPATCH.keys())
        dispatch_names.add("finish")
        self.assertTrue(
            spec_names.issubset(dispatch_names),
            f"Missing from dispatch: {spec_names - dispatch_names}",
        )

    def test_tool_names_match(self) -> None:
        specs = load_tool_specs()
        names = [s["function"]["name"] for s in specs]
        expected = [
            "inspect_cdm_json",
            "lookup_cdm_schema",
            "resolve_java_type",
            "list_enum_values",
            "get_java_template",
            "write_java_file",
            "read_java_file",
            "patch_java_file",
            "compile_java",
            "run_java",
            "diff_json",
            "validate_output",
            "finish",
        ]
        self.assertEqual(names, expected)


# ── Mock LLM client ──────────────────────────────────────────────────

@dataclass
class MockFunctionCall:
    name: str
    arguments: str


@dataclass
class MockToolCall:
    id: str
    function: MockFunctionCall
    type: str = "function"


@dataclass
class MockMessage:
    content: Optional[str] = None
    tool_calls: Optional[List[MockToolCall]] = None
    role: str = "assistant"


@dataclass
class MockChoice:
    message: MockMessage


@dataclass
class MockResponse:
    choices: List[MockChoice]


class MockLLMClient:
    """Mock OpenAI client that returns a predefined sequence of responses."""

    def __init__(self, responses: List[MockMessage]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.chat = self

    @property
    def completions(self) -> "MockLLMClient":
        return self

    def create(self, **kwargs: object) -> MockResponse:
        if self._call_count >= len(self._responses):
            msg = MockMessage(
                tool_calls=[
                    MockToolCall(
                        id=f"call_end",
                        function=MockFunctionCall(
                            name="finish",
                            arguments='{"status": "failure", "summary": "No more mock responses"}',
                        ),
                    )
                ]
            )
        else:
            msg = self._responses[self._call_count]
        self._call_count += 1
        return MockResponse(choices=[MockChoice(message=msg)])


# ── Agent loop tests ─────────────────────────────────────────────────

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CDM_FIXTURE = FIXTURES / "expected" / "fx_forward_cdm.json"


class AgentLoopTests(unittest.TestCase):

    def test_finish_immediately(self) -> None:
        """Agent calls finish on first turn → loop exits."""
        responses = [
            MockMessage(tool_calls=[
                MockToolCall(
                    id="call_1",
                    function=MockFunctionCall(
                        name="finish",
                        arguments='{"status": "success", "summary": "Done", "match_percentage": 100.0}',
                    ),
                )
            ])
        ]
        client = MockLLMClient(responses)
        config = AgentConfig(max_iterations=5, timeout_seconds=30)
        result = run_agent(str(CDM_FIXTURE), llm_client=client, config=config)
        self.assertTrue(result.success)
        self.assertEqual(result.match_percentage, 100.0)
        self.assertEqual(result.iterations, 1)

    def test_timeout_returns_failure(self) -> None:
        """Agent with negative timeout fails immediately."""
        responses = [
            MockMessage(content="thinking..."),
            MockMessage(content="still thinking..."),
        ]
        client = MockLLMClient(responses)
        config = AgentConfig(max_iterations=5, timeout_seconds=-1)
        result = run_agent(str(CDM_FIXTURE), llm_client=client, config=config)
        self.assertFalse(result.success)
        self.assertIn("Timeout", result.summary)

    def test_max_iterations_returns_failure(self) -> None:
        """Agent that never finishes hits max iterations."""
        responses = [MockMessage(content=f"thinking {i}...") for i in range(5)]
        client = MockLLMClient(responses)
        config = AgentConfig(max_iterations=3, timeout_seconds=60)
        result = run_agent(str(CDM_FIXTURE), llm_client=client, config=config)
        self.assertFalse(result.success)
        self.assertIn("Max iterations", result.summary)

    def test_tool_call_then_finish(self) -> None:
        """Agent calls a tool, then finishes."""
        responses = [
            MockMessage(tool_calls=[
                MockToolCall(
                    id="call_1",
                    function=MockFunctionCall(
                        name="get_java_template",
                        arguments="{}",
                    ),
                )
            ]),
            MockMessage(tool_calls=[
                MockToolCall(
                    id="call_2",
                    function=MockFunctionCall(
                        name="finish",
                        arguments='{"status": "success", "summary": "Generated template"}',
                    ),
                )
            ]),
        ]
        client = MockLLMClient(responses)
        config = AgentConfig(max_iterations=10, timeout_seconds=30)
        result = run_agent(str(CDM_FIXTURE), llm_client=client, config=config)
        self.assertTrue(result.success)
        self.assertEqual(result.total_tool_calls, 2)
        self.assertEqual(result.iterations, 2)

    def test_max_tool_calls_limit(self) -> None:
        """Agent hits max_tool_calls limit."""
        responses = [
            MockMessage(tool_calls=[
                MockToolCall(
                    id=f"call_{i}",
                    function=MockFunctionCall(
                        name="get_java_template",
                        arguments="{}",
                    ),
                )
            ])
            for i in range(10)
        ]
        client = MockLLMClient(responses)
        config = AgentConfig(max_iterations=20, max_tool_calls=3, timeout_seconds=30)
        result = run_agent(str(CDM_FIXTURE), llm_client=client, config=config)
        self.assertFalse(result.success)
        self.assertIn("Max tool calls", result.summary)

    def test_trace_captures_events(self) -> None:
        """Trace records tool calls and results."""
        responses = [
            MockMessage(tool_calls=[
                MockToolCall(
                    id="call_1",
                    function=MockFunctionCall(
                        name="get_java_template",
                        arguments="{}",
                    ),
                )
            ]),
            MockMessage(tool_calls=[
                MockToolCall(
                    id="call_2",
                    function=MockFunctionCall(
                        name="finish",
                        arguments='{"status": "success", "summary": "ok"}',
                    ),
                )
            ]),
        ]
        client = MockLLMClient(responses)
        result = run_agent(str(CDM_FIXTURE), llm_client=client)
        tool_calls = [t for t in result.trace if t["type"] == "tool_call"]
        tool_results = [t for t in result.trace if t["type"] == "tool_result"]
        self.assertEqual(len(tool_calls), 2)
        self.assertEqual(len(tool_results), 1)

    def test_unknown_tool_returns_error(self) -> None:
        """Unknown tool name produces error in result, loop continues."""
        responses = [
            MockMessage(tool_calls=[
                MockToolCall(
                    id="call_1",
                    function=MockFunctionCall(
                        name="nonexistent_tool",
                        arguments="{}",
                    ),
                )
            ]),
            MockMessage(tool_calls=[
                MockToolCall(
                    id="call_2",
                    function=MockFunctionCall(
                        name="finish",
                        arguments='{"status": "failure", "summary": "bad tool"}',
                    ),
                )
            ]),
        ]
        client = MockLLMClient(responses)
        result = run_agent(str(CDM_FIXTURE), llm_client=client)
        error_results = [
            t for t in result.trace
            if t["type"] == "tool_result" and "Unknown tool" in str(t.get("result_preview", ""))
        ]
        self.assertEqual(len(error_results), 1)

    def test_system_prompt_not_empty(self) -> None:
        self.assertGreater(len(SYSTEM_PROMPT), 100)
        self.assertIn("CDM", SYSTEM_PROMPT)

    def test_agent_result_to_dict(self) -> None:
        r = AgentResult(success=True, summary="done", match_percentage=99.5, iterations=5, total_tool_calls=12, duration_seconds=45.678)
        d = r.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["match_percentage"], 99.5)
        self.assertEqual(d["duration_seconds"], 45.68)


JAR_PATH = Path("rosetta-validator/target/rosetta-validator-1.0.0.jar")
GENERATED_DIR = Path("generated")


def _has_openai_key() -> bool:
    if os.environ.get("OPENAI_API_KEY"):
        return True
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("OPENAI_API_KEY") and "=" in line:
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and not val.startswith("YOUR"):
                    return True
    return False


def _load_openai_client() -> object:
    """Load an OpenAI client, reading .env if needed."""
    from dotenv import load_dotenv
    load_dotenv()
    import openai
    return openai.OpenAI()


@unittest.skipUnless(
    _has_openai_key() and JAR_PATH.exists(),
    "Requires OPENAI_API_KEY and rosetta-validator JAR",
)
class RealLLMIntegrationTests(unittest.TestCase):
    """Integration tests that call the real OpenAI API.

    These are slow (~60-120s) and cost money. Run explicitly:
        python -m unittest tests.test_java_gen.test_agent.RealLLMIntegrationTests -v
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = _load_openai_client()
        if GENERATED_DIR.exists():
            shutil.rmtree(GENERATED_DIR)

    @classmethod
    def tearDownClass(cls) -> None:
        if GENERATED_DIR.exists():
            shutil.rmtree(GENERATED_DIR)

    def test_agent_generates_java_for_fx_forward(self) -> None:
        """Full agent run against the fx_forward CDM fixture.

        Asserts:
        - Agent runs without crashing
        - Multiple tools were called
        - Agent follows the expected strategy (inspect → schemas → write → compile)
        - A Java file is produced
        """
        config = AgentConfig(
            max_iterations=25,
            max_tool_calls=80,
            timeout_seconds=300,
        )

        result = run_agent(
            cdm_json_path=str(CDM_FIXTURE),
            llm_client=self.client,
            model="gpt-4o",
            config=config,
        )

        self.assertIsInstance(result, AgentResult)
        self.assertGreater(result.iterations, 0, "Agent did at least one iteration")
        self.assertGreater(result.total_tool_calls, 3, "Agent called multiple tools")
        self.assertGreater(len(result.trace), 0, "Trace is non-empty")

        tool_names_used = {
            t["tool"] for t in result.trace if t["type"] == "tool_call"
        }
        self.assertIn("inspect_cdm_json", tool_names_used,
                       "Agent should inspect the CDM JSON")
        self.assertIn("write_java_file", tool_names_used,
                       "Agent should write a Java file")
        self.assertIn("compile_java", tool_names_used,
                       "Agent should attempt compilation")

        java_file = GENERATED_DIR / "CdmTradeBuilder.java"
        self.assertTrue(
            java_file.exists() or result.java_file,
            "A Java file should be produced",
        )

        print(f"\n{'='*60}")
        print(f"Integration test result:")
        print(f"  Success:      {result.success}")
        print(f"  Iterations:   {result.iterations}")
        print(f"  Tool calls:   {result.total_tool_calls}")
        print(f"  Duration:     {result.duration_seconds:.1f}s")
        print(f"  Match:        {result.match_percentage}%")
        print(f"  Summary:      {result.summary}")
        print(f"  Tools used:   {sorted(tool_names_used)}")
        print(f"{'='*60}")

    def test_agent_calls_schema_lookup_tools(self) -> None:
        """Verify agent uses schema introspection (not just blind code gen).

        Uses a tighter budget — we only care that the agent strategy
        starts correctly (inspect → schema lookups → template).
        """
        config = AgentConfig(
            max_iterations=6,
            max_tool_calls=15,
            timeout_seconds=90,
        )

        result = run_agent(
            cdm_json_path=str(CDM_FIXTURE),
            llm_client=self.client,
            model="gpt-4o",
            config=config,
        )

        tool_names_used = {
            t["tool"] for t in result.trace if t["type"] == "tool_call"
        }

        introspection_tools = {
            "inspect_cdm_json", "lookup_cdm_schema",
            "resolve_java_type", "list_enum_values",
        }
        used_introspection = tool_names_used & introspection_tools
        self.assertGreaterEqual(
            len(used_introspection), 1,
            f"Agent should use at least 1 introspection tool, used: {tool_names_used}",
        )

        print(f"\n  Strategy test: {sorted(tool_names_used)} in {result.iterations} iters")


if __name__ == "__main__":
    unittest.main()
