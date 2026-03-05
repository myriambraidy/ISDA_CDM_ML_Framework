# Plan: Faster Java-Gen Agent Loop

## 1. Current performance issues

- **LLM-bound**: In a typical run (~~16 iterations, 22 tool calls), almost all time is spent waiting for the model (~~476s LLM vs ~4s tools). The bottleneck is inference, not tools or network.
- **Too many rounds**: Many turns contain a single tool call (e.g. get_java_template, one lookup, write_java_file, compile_java, run_java, diff_json each in their own round). Each round is a full request/response with full conversation history.
- **Large, growing context**: Every request resends system prompt + user message + all prior assistant messages and tool results. Early results (e.g. `inspect_cdm_json`, many schema lookups) are large and stay in context for the rest of the run, increasing tokens and latency on every subsequent call.
- **Model latency**: The current default (z-ai/glm-4.6) can take tens of seconds to over two minutes per response on large tool-calling turns; switching to a faster model reduces time per round.

**What we do not change**: The 13 tools, their signatures, or `tools.json`; the shape of `AgentResult` and `AgentConfig`; the public `run_agent(...)` signature and return contract; the trace structure (`tool_call` / `tool_result` with `result_preview`) used by `--trace-output` and `_partial_result_from_trace`; the CLI’s output format and exit codes; or the handling of `finish` (success, java_file, match_percentage, summary from tool args). All existing tests must keep passing.

---

## 2. Goals

- **Fewer LLM rounds**: Batch tool use where possible; run compile → run_java → diff_json deterministically after a successful compile so the model does not need separate turns for run and diff.
- **Smaller context**: Optionally summarize or truncate older tool results so we do not resend the full history every time.
- **Deterministic steps**: After `compile_java` succeeds, automatically execute `run_java` and `diff_json` and append their results to the conversation so the next LLM turn sees the diff and can call `finish` or `patch_java_file`.
- **Faster model**: Use `google/gemini-2.5-flash` as the default for the generate-java command (OpenRouter) to reduce latency per round.

---

## 3. Implementation phases

### Phase 1: Switch default model to google/gemini-2.5-flash

- **Where**: `fpml_cdm/cli.py` (generate-java parser), and optionally `.env.example` / docs if we mention the default.
- **Change**: Set the default for `--model` from `z-ai/glm-4.6` to `google/gemini-2.5-flash`. Keep `--model` overridable so behavior is unchanged except for the default.
- **Risk**: None for existing behavior; only default changes. If the new model behaves worse on some CDM files, users can pass `--model z-ai/glm-4.6` (or another model).
- **Tests**: Update any test or doc that asserts the default model string; existing mock-based agent tests do not depend on model name.

### Phase 2: Deterministic compile → run_java → diff_json

- **Intent**: After the agent executes a tool and that tool is `compile_java` with a successful result, we run `run_java` and `diff_json` ourselves and append their results to `messages` and `trace` so the next LLM turn sees the diff without needing two extra rounds.
- **Where**: `fpml_cdm/java_gen/agent.py` inside the main loop, after the `for tool_call in message.tool_calls` loop.
- **Logic**:
  1. After processing all tool calls in the current message, check whether the **last** tool executed was `compile_java` and its result JSON contains `"success": true`.
  2. If yes:
    - Call `run_java(class_name="CdmTradeBuilder", timeout=30)` (or same defaults as in `tools.py`). Use `cdm_json_path` (already in scope) for the diff.
    - From `run_java` result take `stdout` (the Java program output). If `success` is false or stdout empty, do not run diff; optionally append a single synthetic tool result for run_java so the model sees the failure.
    - If run_java succeeded and stdout is non-empty: call `diff_json(expected_json_path=cdm_json_path, actual_json=run_result["stdout"])`.
    - Append to `messages`: one assistant message with two tool_calls (synthetic ids, e.g. `call_det_run`, `call_det_diff`) with `function.name` and `function.arguments` for `run_java` and `diff_json`; then two tool messages with `role: "tool"`, `tool_call_id`, and `content` as the JSON result strings.
    - Append to `trace`: two `tool_call` entries and two `tool_result` entries (with `result_preview` as first 500 chars of each result).
    - Increment `total_tool_calls` by 2. Do **not** invoke the LLM again in the same iteration; the next iteration will see the new messages and decide finish or patch.
  3. If the last tool was not a successful compile_java, do nothing (current behavior).
- **Edge cases**:
  - Compile fails: no deterministic step; model gets compile error and can call patch_java_file or other tools as today.
  - run_java fails (no JAR, timeout, etc.): we can still append run_java result so the model sees the failure and can retry or report; only skip calling diff_json when run_java did not produce usable stdout.
  - Multiple tool calls in one turn including compile_java: only trigger deterministic step if the **last** tool in that turn was compile_java and it succeeded (so we don’t inject in the middle of a batch).
- **Compatibility**: The conversation shape stays valid (assistant with tool_calls, then tool results in order). The LLM still receives the same information it would have if it had asked for run_java and diff_json in two separate turns. `_partial_result_from_trace` and trace-output still see the same trace entry types. `finish` is only ever called by the model, not by the deterministic step.
- **Tests**: Existing tests use mocks that never return compile_java as the last tool with success; they keep passing. Add a unit test that, with a mock LLM that returns compile_java success once then finish, verifies that the trace contains run_java and diff_json entries and that the run completes (optional but recommended).

### Phase 3: Stronger batching prompt and (optional) shorter tool descriptions

- **Intent**: Encourage the model to do more in fewer turns (batch schema and enum lookups, avoid one-tool-per-turn where possible).
- **Where**: `fpml_cdm/java_gen/agent.py` (`SYSTEM_PROMPT`), and optionally `fpml_cdm/java_gen/tools.json` (shorten `description` strings).
- **Prompt changes**: Add or tighten a short “Efficiency” section, e.g.:
  - “In your first 1–2 turns after inspect_cdm_json and get_java_template, call ALL lookup_cdm_schema and list_enum_values you need in parallel in a single turn. Do not call write_java_file until you have gathered schema and enum info for every type you need.”
  - Keep existing rules (type_registry, batch patches, etc.) so behavior and correctness are unchanged.
- **Tool descriptions**: Optionally shorten long descriptions in `tools.json` to reduce input tokens; keep required parameters and semantics so the model still chooses and invokes tools correctly.
- **Risk**: Prompt changes might change model behavior (e.g. more batching); we do not remove or reorder required steps, so existing successful runs should remain possible. If a run regresses, we can revert the prompt text.
- **Tests**: No code contract change; mock tests still pass. Manual or integration tests can compare round count before/after.

---

## 4. Order of work and safeguards

- Implement in order: **Phase 1 → Phase 2 → Phase 3**.
- After each phase: run the full agent test suite (`tests.test_java_gen.test_agent`, `tests.test_java_gen.test_openrouter_client`) and ensure nothing is broken. For Phase 2, add a test that covers the deterministic path (mock LLM returns compile_java success then finish; assert trace contains run_java and diff_json and result is success).
- **Do not**: remove or rename tools; change `AgentResult` / `AgentConfig` fields; change the signature or return type of `run_agent`; change the trace entry shape used by `_partial_result_from_trace` or `--trace-output`; or change how `finish` is handled (success, java_file, match_percentage, summary from tool args).
- **Rollback**: Phase 1 = revert default model string. Phase 2 = remove the block that detects compile_java success and injects run_java/diff_json. Phase 3 = revert prompt/description edits.

---

## 5. Summary


| Phase | What                                                                               | Where                | Breaks?                      |
| ----- | ---------------------------------------------------------------------------------- | -------------------- | ---------------------------- |
| 1     | Default model → google/gemini-2.5-flash                                            | cli.py               | No                           |
| 2     | After compile_java success, auto run_java + diff_json and append to messages/trace | agent.py             | No; same API and trace shape |
| 3     | Stronger batching prompt; optional shorter tool descriptions                       | agent.py, tools.json | No; prompt only              |

All changes preserve existing working behavior, public API, and test compatibility while reducing rounds and per-round latency.

---

## 6. Todo list (implementation checklist)

Use this list when implementing; check off items as done. Do not implement Phase 4.

### Phase 1: Default model

- [x] **1.1** In `fpml_cdm/cli.py`, change the generate-java `--model` default from `z-ai/glm-4.6` to `google/gemini-2.5-flash`.
- [x] **1.2** Update any test that asserts the default model string (e.g. `tests.test_java_gen.test_openrouter_client` if it checks the default model in the request).
- [x] **1.3** Run `tests.test_java_gen.test_agent` and `tests.test_java_gen.test_openrouter_client`; all pass.

### Phase 2: Deterministic compile → run_java → diff_json

- [x] **2.1** In `agent.py`, after the `for tool_call in message.tool_calls` loop, add a check: last tool was `compile_java` and its result JSON has `"success": true`.
- [x] **2.2** If true: call `run_java(class_name="CdmTradeBuilder", timeout=30)`; get `stdout` from result.
- [x] **2.3** If run_java succeeded and stdout non-empty: call `diff_json(expected_json_path=cdm_json_path, actual_json=stdout)`. If run_java failed: optionally append only run_java result so the model sees the failure.
- [x] **2.4** Build synthetic assistant message (two tool_calls: run_java, diff_json with synthetic ids e.g. `call_det_run`, `call_det_diff`) and two tool messages; append to `messages`.
- [x] **2.5** Append two `tool_call` and two `tool_result` entries to `trace` (with `result_preview` first 500 chars); increment `total_tool_calls` by 2.
- [x] **2.6** Handle edge case: only trigger when the **last** tool in the current message was compile_java success (not if compile_java was one of several and not last).
- [x] **2.7** Add unit test: mock LLM returns one turn with compile_java success, next turn with finish; assert trace contains run_java and diff_json, result.success is True.
- [x] **2.8** Run full agent and openrouter client tests; all pass.

### Phase 3: Batching prompt and optional shorter tool descriptions

- [x] **3.1** In `agent.py`, add or tighten the “Efficiency” section in `SYSTEM_PROMPT`: e.g. first 1–2 turns after inspect and get_java_template, call ALL lookup_cdm_schema and list_enum_values in parallel; do not call write_java_file until schema/enum info is gathered.
- [x] **3.2** (Optional) In `tools.json`, shorten long `description` strings for tools; keep parameters and semantics unchanged. _(Skipped: left descriptions as-is to avoid any behavior change.)_
- [x] **3.3** Run full agent and openrouter client tests; all pass.