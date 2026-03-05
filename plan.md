
## Research: Agent Timeout and Failure Analysis

**Source**: Run with `python -m fpml_cdm generate-java tmp/my_cdm.json --trace-output tmp/trace.json` (OpenRouter / deepseek-chat-v3-0324). Outcome: **Timeout after 300s**, reported as FAILURE with match 0%.

### What Actually Happened (Trace Summary)

| Iteration | Tool calls | What happened |
|-----------|------------|----------------|
| 0 | 1 | `inspect_cdm_json("tmp/my_cdm.json")` — got tree, types, enums. |
| 1 | 25 | `get_java_template` + 12× `lookup_cdm_schema` (Trade, TradeIdentifier, Party, Counterparty, PartyRole, NonTransferableProduct, EconomicTerms, Payout, SettlementPayout, PayerReceiver, SettlementTerms, SettlementDate, Underlier, ReferenceWithMetaObservable, TradeLot, PriceQuantity, PriceSchedule, UnitType, NonNegativeQuantitySchedule) + 4× `list_enum_values` (CounterpartyRoleEnum, PartyRoleEnum, SettlementTypeEnum, PriceTypeEnum). |
| 2 | 1 | `write_java_file` — full 176-line Java with FieldWithMetaDate, Date.of(), underlier set to `ReferenceWithMetaObservable.builder().setValue(null)`. |
| 3 | 1 | `compile_java` — **success**. |
| 4 | 1 | `run_java` — **success**, JSON printed. |
| 5 | 1 | `diff_json(actual_json=<run output>, expected_json_path=tmp/my_cdm.json)` — **93.1% match**, 27/29 leaves, **1 difference**. |
| 6 | — | **Never executed**: at start of iteration 6 the loop checked `elapsed > 300` and returned timeout. |

**Total**: 6 iterations, 29 tool calls, **417.59s** wall time. Timeout is 300s, so the timeout fired **after** all of iteration 5’s work (including the diff_json result) but **before** the next LLM request for iteration 6.

### Root Cause 1: Timeout Too Low

- **Observation**: 417s for 6 iterations ⇒ ~70s per iteration (LLM latency + tool execution). With default `timeout_seconds=300`, the agent is cut off before it can start the next turn.
- **Effect**: The agent never got to (1) read the diff result, (2) patch the underlier, (3) recompile, (4) run, (5) diff again, (6) call `finish`. So the run is reported as failure with `match_percentage=0.0` and `java_file=null` even though we have a compilable, runnable Java file and **93.1% match**.
- **Conclusion**: For real CDM files and OpenRouter/DeepSeek, 300s is too low. The agent needs at least one more full iteration after the first diff to fix the remaining difference and call `finish`.

### Root Cause 2: No Partial Success on Timeout

- **Observation**: On timeout, `agent.py` returns `AgentResult(success=False, match_percentage=0.0, java_file=None, summary="Timeout after 300s")`. It does not inspect the trace for the last `diff_json` result or the path from `write_java_file` / `compile_java`.
- **Effect**: User sees “FAILURE” and 0% match even when the run was one iteration away from success (93.1% match, one clear diff).
- **Conclusion**: On timeout (and optionally on max-iterations), we could derive a **partial result**: e.g. last `diff_json` match_percentage, last written Java file path from trace, and set `summary` to e.g. “Timeout after 300s (last diff: 93.1% match; fix underlier.Observable then re-run).”

### Root Cause 3: Single Semantic Difference (Underlier)

- **diff_json output**:  
  `"path": "$.trade.product.economicTerms.payout[0].SettlementPayout.underlier.Observable.value"`  
  `"expected": {"Asset": {"Cash": {"identifier": [{"identifier": {"value": "GBP"}, "identifierType": "CurrencyCode"}]}}}`  
  `"actual": null`  
  `"type": "missing_in_actual"`

- **Cause**: The generated code set underlier to  
  `Underlier.builder().setObservable(ReferenceWithMetaObservable.builder().setValue(null).build()).build()`  
  but the input CDM has an FX forward underlier: **Asset.Cash** with **GBP** identifier. The agent had not populated that structure.

- **Fix path**: One more iteration: use `patch_java_file` (or a small rewrite) to replace the underlier with the correct Cash/GBP builder chain (or the equivalent type from schema lookup), then compile → run → diff → `finish`. This was not attempted because of the timeout.

### Root Cause 4: Large Context (Slowness)

- **Observation**: Iteration 1 has **25 tool calls** in one turn. The next LLM request carries: system prompt + user message + assistant message with 25 tool calls + 25 tool results (each can be hundreds of characters). That makes the request large and slow.
- **Effect**: Each round-trip is ~70s; more iterations and/or larger payloads increase total time and make timeouts more likely.
- **Conclusion**: Reducing context size (e.g. summarising old tool results, or encouraging the agent to use `inspect_cdm_json`’s type_registry and do fewer `lookup_cdm_schema` calls in one go) could reduce latency and token usage. System prompt already says to “use type_registry” and “batch lookups”; the agent still did 12 lookups + 4 enums in one turn.

### Implementation Plan (Recommendations)

1. **Increase default timeout**  
   - In `AgentConfig`, set `timeout_seconds` to **600** (or make it configurable via CLI `--timeout` with default 600).  
   - Ensures at least a few full iterations after the first diff (e.g. patch → compile → run → diff → finish).

2. **Partial success on timeout / max-iterations**  
   - Before returning `AgentResult` for timeout (or max iterations), scan `trace` for the most recent `tool_result` where `tool == "diff_json"` and parse its `result_preview` (or store full result in trace) for `match_percentage`.  
   - Optionally scan for last `write_java_file` success to get `path`.  
   - Set `result.match_percentage` and `result.java_file` from that; set `result.summary` to e.g. `"Timeout after 300s (last diff: 93.1% match; 1 difference: underlier.Observable)"`.  
   - Keeps `success=False` but gives the user actionable information.

3. **System prompt nudge for underlier / dates**  
   - Add a short line: when the CDM has `underlier` (e.g. FX forward with Asset.Cash and currency), the agent must build the full underlier structure from schema (e.g. Underlier → Observable → Asset → Cash → identifier), not `setValue(null)`.  
   - Reinforce that date fields use CDM date types (FieldWithMetaDate, Date.of()) as already stated in the prompt.

4. **Optional: context reduction**  
   - In a later phase, consider summarising or truncating older tool results in `messages` (e.g. keep last N tool rounds in full, summarise the rest as “Previously: inspected CDM, looked up N types, wrote and compiled Java.”) to reduce payload size and latency.  
   - Or add a tool “summarise_so_far” that the agent can call to replace a long stretch of tool calls with one summary message (requires prompt and tool design).

5. **CLI**  
   - Document `--timeout` in help and in plan.md (e.g. “Default 600s for generate-java; increase for large CDM or slow models.”).

### Summary Table

| Issue | Cause | Recommendation |
|-------|--------|----------------|
| Run reported as failure | Timeout at 300s before iteration 6 | Raise default timeout to 600s (or configurable). |
| match_percentage 0% on timeout | Timeout path doesn’t use last diff_json | Derive partial result from trace (last diff %, java path). |
| Only 93.1% match | Underlier left as null instead of Cash/GBP | Prompt + one more iteration to fix underlier (blocked by timeout). |
| 417s for 6 iterations | Large context (25 tool calls in iter 1) + LLM latency | Optional: summarise/trim context; keep prompt guidance on batching. |

### Logging and per-tool timing (planned)

To let users see what the agent is doing and where time is spent, add **terminal logging** and **timing data** without changing agent behaviour.

**Terminal output to add**

- **Per tool call (before execution)**  
  Log each tool name and a short, readable summary of arguments (e.g. `inspect_cdm_json(json_path=tmp/my_cdm.json)`, `lookup_cdm_schema(type_name=Trade)`, `compile_java()`). Truncate or omit huge payloads (e.g. `actual_json` for `diff_json` → `diff_json(expected=tmp/my_cdm.json, actual=<len N chars>)`).

- **Per tool call (after execution)**  
  Log the **duration** of that tool execution (e.g. `  → 0.12s` or `  → 2.34s`). Optionally one-line success/failure (e.g. `  → 0.12s ok` or `  → 2.34s error`).

- **Per iteration (LLM round-trip)**  
  Before calling the LLM, log that we are starting iteration N and waiting for the model. After receiving the response, log the **LLM wait time** (e.g. `  [iter 2] LLM response in 68.3s, 25 tool calls`). This separates **tool execution time** from **LLM latency**, which is the main unknown.

- **Running totals (optional)**  
  At the end of each iteration or on finish: cumulative tool time vs cumulative LLM time (e.g. `  Total so far: 12.1s tools, 210.0s LLM`).

**Where to implement**

- **`agent.py`**  
  In the main loop: (1) record `t0 = time.perf_counter()` before `llm_client.chat.completions.create(...)`, and `t1` after; log iteration and `t1 - t0` as LLM time. (2) For each tool call, `t0` before `_execute_tool(...)`, `t1` after; log tool name, short args, and `t1 - t0`. Use `sys.stderr` so trace output and normal stdout are unchanged.

- **CLI**  
  No new flags required for the minimal version. Optional later: `--verbose` to enable per-tool logs, or `--quiet` to suppress them (default: log when stderr is a TTY).

**Example terminal output (target)**

```
[2/2] Running agent loop...
  [iter 1] waiting for LLM...
  [iter 1] LLM responded in 71.2s (1 tool call)
    inspect_cdm_json(json_path=tmp/my_cdm.json)  → 0.15s ok
  [iter 2] waiting for LLM...
  [iter 2] LLM responded in 68.1s (25 tool calls)
    get_java_template()  → 0.02s ok
    lookup_cdm_schema(type_name=Trade)  → 0.01s ok
    ...
  [iter 3] waiting for LLM...
  ...
```

This gives enough data to see whether the bottleneck is LLM latency (e.g. 70s per turn) or specific tools (e.g. `compile_java` or `run_java` taking seconds).

### Todo list

| # | Task | Owner | Status |
|---|------|--------|--------|
| 1 | **Logging**: Log each tool name + short args to stderr before execution | agent.py | Done |
| 2 | **Logging**: Log per-tool duration after each tool execution | agent.py | Done |
| 3 | **Logging**: Log "waiting for LLM" and LLM response time + tool count per iteration | agent.py | Done |
| 4 | **Optional**: Running totals (cumulative tool time vs LLM time) per iteration | agent.py | Done |
| 5 | **Optional**: `--verbose` / `--quiet` for logging level (default: on when stderr TTY) | cli.py | Done |
| 6 | Increase default timeout to 600s (or CLI `--timeout` default 600) | agent.py / cli.py | Done |
| 7 | Partial success on timeout: set match_percentage + java_file from last diff_json in trace | agent.py | Done |
| 8 | System prompt: underlier must be full structure (e.g. Cash/GBP), not setValue(null) | agent.py | Done |
| 9 | CLI: document `--timeout` in help | cli.py | Done |

---