# Plan: Real LLM Integration Test (Mapping Agent Only)

## Goal
Verify that `fpml_cdm/mapping_agent/agent.py::run_mapping_agent()` works end-to-end with a **real** OpenRouter/OpenAI-compatible provider using **tool calling**:
- LLM receives tool specs from the mapping-agent tool registry
- LLM returns tool calls
- mapping agent dispatches tool calls deterministically
- mapping agent updates **best-so-far** and returns a non-empty result
- test produces useful trace artifacts for debugging

This test does **not** validate Java codegen and does **not** require the Rosetta JAR.

## What the mapping agent loop does (intricacies to account for)
Key code: `run_mapping_agent()` in `fpml_cdm/mapping_agent/agent.py`.

### 1) Deterministic seeding happens before any LLM call
Before the LLM loop, the agent:
1. Detects supported adapter IDs via `inspect_fpml_trade(fpml_path)` in `_detect_supported_adapter_candidates()`.
2. For each candidate adapter (`fxForward`, `fxSingleLeg`), runs:
   - `parse_fpml_fx_with_ruleset(... strict=False, recovery_mode=True)`
   - `transform_to_cdm_v6(normalized)`
   - `validate_normalized_and_cdm(normalized, cdm)`
3. Computes `(schema_error_count, semantic_error_count)` for each adapter.
4. Seeds:
   - `best_cdm_json`
   - `best_validation_report_dict`
   - `best_schema_err`, `best_sem_err`

So the test can always assert `result.best_cdm_json` is non-empty even if tool calling fails later.

### 2) The LLM loop always starts for `max_iterations > 0`
Even if the seeded best is already perfect (`schema=0` and `semantic=0`), the code still enters:
```python
for iteration in range(config.max_iterations):
    response = llm_client.chat.completions.create(...)
    ...
```
and only stops *after* processing at least one tool call in the model response:
```python
if int(best_schema_err) == 0 and int(best_sem_err) == 0:
    break
```

Implication: the first LLM call is unavoidable in an integration test unless you set `max_iterations=0` (which would defeat the purpose of testing real tool calling).

### 3) Text-only responses trigger a “nudge” and retry
If the model response has `not message.tool_calls`, the agent:
1. appends a trace entry of type `text`
2. increments `consecutive_text_only`
3. on the first occurrence, appends an extra user nudge:
   “You must call at least one tool ... Do not respond with only text.”

Implication: use `max_iterations >= 2` so the nudge has a chance to work.

### 4) Tool execution + scoring update is gated on `run_conversion_with_patch`
The agent updates best-so-far **only** when:
- `fn_name == "run_conversion_with_patch"`
- `result_dict` is a dict and contains `validation_summary`
- it produces an improved `(schema_err, semantic_err)` via `_score_from_validation_summary`

Other tool calls (like `inspect_fpml_trade`) won’t affect best scoring.

### 5) Stopping conditions
The outer loop breaks when any is true:
- both `best_schema_err == 0` and `best_sem_err == 0` after tool processing
- `no_improve_iters >= config.semantic_no_improve_limit`
- elapsed time > `config.timeout_seconds`
- `total_tool_calls >= config.max_tool_calls`

### 6) Returned `iterations` field is not the actual number of LLM iterations
At return time, the code sets:
```python
iterations=config.max_iterations
```
So tests should not assert exact loop counts based on this field.

### 7) Rosetta is optional but can be triggered accidentally by the LLM
The mapping agent registers a tool:
`validate_best_effort(fpml_path, cdm_json, enable_rosetta?: bool, ...)`

The tool supports Rosetta only if LLM passes `enable_rosetta=true`.
To keep this test focused on tool-calling mechanics (and avoid the Java/JAR requirement), the test should monkeypatch `validate_best_effort` to always run with `enable_rosetta=False`.

## Test plan structure
Create a new integration test file:
`tests/test_mapping_agent_real_llm_integration.py`

Use environment gating:
- Skip unless `OPENROUTER_API_KEY` is set
- Optional env overrides:
  - `FPML_CDM_OPENROUTER_MODEL` (default `minimax/minimax-m2.5`)
  - `FPML_CDM_OPENROUTER_TIMEOUT` (default `60`)

Run tests manually:
```powershell
python -m unittest tests.test_mapping_agent_real_llm_integration -v
```

## Test cases

### Test Case A: “Happy path” fixture (`fx_forward.xml`)
Purpose:
- Confirm tool calling happens with a real LLM.
- Confirm agent returns a valid best CDM JSON.
- Since deterministic baseline should already be perfect for this fixture, we primarily assert:
  - trace has at least one tool_call
  - `best_schema_error_count == 0`
  - `best_semantic_error_count == 0`

Assertions:
- `result.best_cdm_json["trade"]` exists
- `result.best_schema_error_count == 0`
- `result.best_semantic_error_count == 0`
- trace contains at least one `tool_call` and at least one `tool_result`

Why this is safe:
If the seeded best is already perfect, the loop is expected to stop quickly after it receives a tool call (post-nudge).

### Test Case B: “Degraded input” fixture (`missing_value_date.xml`)
Purpose:
- Confirm the tool-calling loop still works when deterministic validation is failing.
- Confirm at least one `run_conversion_with_patch` is attempted (ideally).

Assertions:
- `result.best_cdm_json["trade"]` exists
- trace contains `tool_call` entries (at least one)
- optionally assert that at least one tool_call is `run_conversion_with_patch`
  - if the LLM chooses a different tool first, don’t fail hard; log the tool names for debugging

Expected outcomes:
- best error counts likely remain > 0 (we’re not forcing an improvement)
- test passes as long as the agent successfully executes tool calls and returns.

## Tooling / mocking choices (important)
To avoid Rosetta:
- monkeypatch `fpml_cdm.mapping_agent.tools.validate_best_effort` so `enable_rosetta=False` always.

This keeps integration focused on:
- LLM tool calling behavior
- registry dispatch
- deterministic parse/transform/validate wiring

## Recommended config for cost control
Use small budgets:
- `max_iterations`: 2 (so the nudge can happen)
- `max_tool_calls`: 15-25
- `timeout_seconds`: 60-120
- `semantic_no_improve_limit`: 1-2 (optional)

This limits LLM calls to at most 2 iterations per test case in the common path.

## Implementation sketch (code snippets)

### 1) Test file skeleton
```python
import os
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fpml_cdm.mapping_agent.agent import run_mapping_agent, MappingAgentConfig
from fpml_cdm.java_gen.openrouter_client import OpenRouterClient
from fpml_cdm.mapping_agent import tools as mapping_tools
```

### 2) Environment gating + client setup
```python
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
MODEL = os.environ.get("FPML_CDM_OPENROUTER_MODEL", "minimax/minimax-m2.5")
TIMEOUT = float(os.environ.get("FPML_CDM_OPENROUTER_TIMEOUT", "60"))

def make_client() -> OpenRouterClient:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is required for real integration tests")
    return OpenRouterClient(api_key=OPENROUTER_API_KEY, timeout=TIMEOUT)
```

### 3) Fixtures
```python
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fpml"
FX_FORWARD = FIXTURES / "fx_forward.xml"
MISSING_VALUE_DATE = FIXTURES / "missing_value_date.xml"
```

### 4) Rosetta avoidance monkeypatch
```python
original_validate_best_effort = mapping_tools.validate_best_effort

def validate_best_effort_no_rosetta(fpml_path: str, cdm_json: object, **kwargs):
    # Force enable_rosetta=False no matter what the LLM requests.
    return original_validate_best_effort(
        fpml_path=fpml_path,
        cdm_json=cdm_json,
        enable_rosetta=False,
        **{k: v for k, v in kwargs.items() if k != "enable_rosetta"},
    )
```

### 5) Test Case A (Happy path)
```python
@unittest.skipUnless(OPENROUTER_API_KEY, "Requires OPENROUTER_API_KEY for real OpenRouter tool calling")
def test_mapping_agent_fx_forward_real_llm(self):
    llm_client = make_client()
    cfg = MappingAgentConfig(
        max_iterations=2,
        max_tool_calls=25,
        timeout_seconds=120,
        semantic_no_improve_limit=1,
    )

    with patch("fpml_cdm.mapping_agent.tools.validate_best_effort", side_effect=validate_best_effort_no_rosetta):
        result = run_mapping_agent(
            fpml_path=str(FX_FORWARD),
            llm_client=llm_client,
            model=MODEL,
            config=cfg,
            log_progress=False,
        )

    self.assertIn("trade", result.best_cdm_json)
    self.assertEqual(result.best_schema_error_count, 0)
    self.assertEqual(result.best_semantic_error_count, 0)

    tool_calls = [t for t in result.trace if t.get("type") == "tool_call"]
    tool_results = [t for t in result.trace if t.get("type") == "tool_result"]
    self.assertGreaterEqual(len(tool_calls), 1)
    self.assertGreaterEqual(len(tool_results), 1)
```

### 6) Test Case B (Degraded input)
```python
@unittest.skipUnless(OPENROUTER_API_KEY, "Requires OPENROUTER_API_KEY for real OpenRouter tool calling")
def test_mapping_agent_missing_value_date_real_llm(self):
    llm_client = make_client()
    cfg = MappingAgentConfig(
        max_iterations=2,
        max_tool_calls=25,
        timeout_seconds=120,
        semantic_no_improve_limit=1,
    )

    with patch("fpml_cdm.mapping_agent.tools.validate_best_effort", side_effect=validate_best_effort_no_rosetta):
        result = run_mapping_agent(
            fpml_path=str(MISSING_VALUE_DATE),
            llm_client=llm_client,
            model=MODEL,
            config=cfg,
            log_progress=False,
        )

    self.assertIn("trade", result.best_cdm_json)

    tool_calls = [t for t in result.trace if t.get("type") == "tool_call"]
    self.assertGreaterEqual(len(tool_calls), 1)

    tool_names = {t.get("tool") for t in tool_calls}
    # Don’t hard fail if the LLM chooses a different tool first.
    # But log: if you’re debugging, you’ll see what it picked.
    self.assertTrue(any(name in tool_names for name in ["inspect_fpml_trade", "get_active_ruleset_summary", "run_conversion_with_patch"]))
```

## Trace artifact output (recommended)
For real integrations, write trace to disk to debug tool-calling failures:
```python
out_dir = Path("tmp") / "mapping_agent_real_llm"
out_dir.mkdir(parents=True, exist_ok=True)
trace_path = out_dir / f"trace_{FIXTURE_NAME}_{int(time.time())}.json"
trace_path.write_text(json.dumps({
    "adapter_id": result.adapter_id,
    "best_schema_error_count": result.best_schema_error_count,
    "best_semantic_error_count": result.best_semantic_error_count,
    "trace": result.trace,
}, indent=2), encoding="utf-8")
```

## How to run
1. Set your OpenRouter key:
   ```powershell
   $env:OPENROUTER_API_KEY="..."
   ```
2. Run only the mapping-agent integration test:
   ```powershell
   python -m unittest tests.test_mapping_agent_real_llm_integration -v
   ```

## Acceptance criteria
The integration tests pass when:
- Tool calling works (trace shows tool_call/tool_result)
- Agent returns a non-empty CDM JSON with `trade` present
- Rosetta is never invoked (due to the monkeypatch)
- Failures are trace-diagnosable (trace artifacts written)

