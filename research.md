# Java generator agent (`fpml_cdm/java_gen`) — research notes

This document summarizes how the CDM Java code-generation **ReAct-style agent loop** works: what it optimizes for, how tools and prompts interact, and edge behaviors that are easy to miss from a quick skim.

---

## 1. Purpose

The agent takes a **CDM trade JSON** file path and drives an LLM with **OpenAI-compatible tool calling** (`chat.completions.create` with `tools`, `tool_choice="auto"`). The model must produce **one self-contained Java class** under `rosetta-validator/generated/` that:

- Compiles against the **shaded CDM** classpath (`rosetta-validator/target/rosetta-validator-1.0.0.jar` + `generated/`).
- When run, prints **valid JSON** to stdout with a `trade` wrapper (same general shape as input).

Success is normally signaled by the **`finish`** tool. The loop also has **fallback success** when the run ends without `finish` but a **`run_java` exit 0** appears in the trace (see §8).

---

## 2. Module layout

| File | Role |
|------|------|
| `agent.py` | Main loop, tool dispatch, context limits, trace, nudges, deterministic `run_java` injection |
| `tools.py` | All tool implementations, global Java target, in-memory payload store, `inspect_cdm_json`, compile/run |
| `prompt_blocks.py` | `build_system_prompt(preflight)` — composable instruction blocks |
| `tools.json` | LLM-facing function schemas (15 tools including `finish`) |
| `schema_index.py` | Lazy index over `schemas/jsonschema/*.schema.json` — type ↔ file, Java FQN from filename, enums |
| `openrouter_client.py` | HTTP client mimicking `openai`’s `chat.completions.create` for OpenRouter |

Public API (`java_gen/__init__.py`): `run_agent`, `AgentConfig`, `AgentResult`, plus `SchemaIndex` re-export from `schema_index` in the package’s historical surface.

---

## 3. Core loop (`_run_agent_impl`)

1. **Java target** is already set by `run_agent` via `set_java_generation_target(cdm_json_path, java_class_name)`:
   - Class/file name defaults from **CDM JSON filename stem** → PascalCase (`json_stem_to_java_class_name`), unless `java_class_name` is passed (e.g. FpML pipeline uses FpML stem).
   - Tools resolve `filename=None` / legacy `CdmTradeBuilder` names to the **active** target.

2. **Preflight**: `inspect_cdm_json(cdm_json_path, detail="full")` runs **before** the first LLM call (wrapped in try/except; on failure, `PREFLIGHT_ALL_BLOCKS` is used so prompts still load).

3. **Scaling**: `total_nodes` from inspect drives `scale_java_gen_config_for_node_count` — raises `max_iterations`, `max_tool_calls`, `timeout_seconds` for trades with many tree nodes (>150 / >400 thresholds).

4. **System prompt**: `build_system_prompt(preflight)` — not a static string; optional blocks depend on reference counts, location warnings, date/FX heuristics, `preflight_large_trade` (see §6).

5. **Bootstrap write**: Before iteration 0, the agent **writes `get_java_template()`’s template** to the active file via `write_java_file`. This ensures **placeholders** exist so `patch_java_file` strategies work; failures are swallowed so the model can still `write_java_file` a full file.

6. **Initial user message** fixes: CDM path, class/file name, `rosetta-validator/generated/`, compile/run expectations.

7. **Each iteration**:
   - Enforce **prompt budget** (`_presend_compact_messages`) if `FPML_JAVA_GEN_MAX_PROMPT_CHARS > 0` — replaces largest tool messages with stored stubs (details §7.3).
   - Call LLM with full message history + `tools` from `load_tool_specs()`.
   - If **no tool calls**: append short trace (`text`), increment **text-only** handling; on first occurrence append a **nudge** user message demanding at least one tool call; `continue`.
   - For each tool call: increment `total_tool_calls`, append trace (`tool_call`), handle **`finish`** early return, else `_execute_tool` → JSON string → **`prepare_tool_result_for_llm`** (per-tool size cap, inspect tree split; §7.2 / §7.10) → append `tool` message.
   - **Loop detection**: same tool + same serialized args **3 times** → wrap result with a warning (repeat counter reset).
   - **Single-patch loop**: if `patch_java_file` with **one** patch in a row **≥3** times, inject a **batch patch** warning (once per run, flag `patch_loop_nudge_sent`).
   - **`compile_java` success** → **deterministic** synthetic assistant `tool_calls` + `tool` result for **`run_java`** (active class, timeout 30) — does **not** wait for the model to call `run_java`.
   - If **`run_java` succeeded this iteration** (parsed from trace): append user message telling the model to **`finish`** with success and `java_file`.
   - Timeout / max tool calls / max iterations → `_agent_result_exhausted` (§8).

**Note:** `AgentConfig.match_threshold` exists (default 95.0) but is **not read** inside `agent.py`; matching semantics are whatever the model puts in `finish` or trace-based fallbacks.

---

## 4. Tool surface vs implementation

- **`tools.json`** defines **15** tools for the LLM (same names as in tests’ ordered list: introspection, file I/O, compile/run, validate, payload paging, **`finish`**).
- **`TOOL_DISPATCH`** lists **15** names (including **`finish`**). For **`finish`**, the loop **short-circuits** before `_execute_tool`: it never invokes the `finish()` Python function for the LLM turn, and instead builds **`AgentResult`** from the tool arguments (`status`, `summary`, `java_file`, `match_percentage`). The other **14** tools go through `_execute_tool` → JSON string to the model.
- **`diff_json`** in `tools.py` is **not** exposed in `tools.json` or `TOOL_DISPATCH` — programmatic / future use only (e.g. comparing expected vs actual JSON outside the agent).

---

## 5. Key tools (behavioral specifics)

### `inspect_cdm_json`

- Walks `trade` (or root) with schema-aware recursion from **Trade** schema.
- Emits a **lossless** `tree` list (every node: `json_path`, `cdm_type`, `schema_ref`, `java_class`, leaves with values, enums).
- Builds **`type_registry`**: every unique `schema_ref` → `import_statement`, `builder_entry`, `is_enum`.
- **`reference_patterns_sample`** (capped) + **`reference_pattern_total`**: classifies `address` / `globalReference` / `externalReference` with **recommended builder calls** (e.g. `setGlobalReference` for DOCUMENT-scope address).
- **`location_array_warnings`**: MetaFields `.location` paths — **Key** vs **MetaFields** trap.
- **`java_type_warnings`**: `JAVA_TYPE_OVERRIDES` for known JSON-vs-Java date/string mismatches.
- `detail` is **ignored for data omission** — comment says compaction is agent-layer only.

### `lookup_cdm_schema` / `resolve_java_type` / `list_enum_values`

- Backed by `SchemaIndex`: filename → Java FQN via **`_java_class_from_filename`** convention.
- Property-level **`setter_hint`** / **`setter_note`**: critical for **Reference** schema — `address` → `setGlobalReference`, not nested `Reference.builder()`.

### File tools

- **`write_java_file`**: `rosetta-validator/generated/<name>.java`.
- **`read_java_file`**: full content (can be huge — subject to oversize stubbing).
- **`patch_java_file`**: single `old_text`/`new_text` or **`patches` array**; exact replace first, then **normalized whitespace** match (strip trailing per line); ambiguous normalized matches error; may return **`suggested_old_text`**.

### `compile_java` / `run_java`

- **`javac`** with classpath `JAR;generated` (Windows `;`, else `:`).
- Errors parsed into structured list; **repeated_error_patterns** / **batch_fix_required** when the same normalized message repeats.
- **`run_java`**: `java -cp ... <class>`; stdout truncated in result; **`stdout_is_valid_json`** flag.

### `validate_output`

- Delegates to `fpml_cdm.validator.validate_cdm_official_schema` on the `trade` object.

### Payload tools

- **`store_large_payload`**, **`fetch_payload`**, **`compact_context`**: in-memory handle store and **character-offset** paging (default `limit` 16_000). Full behavioral spec and failure modes: **§7**.

---

## 6. System prompt (`prompt_blocks.py`)

- **Always**: CORE (imports from **only** `well_known_imports`, `type_registry`, `list_enum_values`), PATCH, STRATEGY, CONVENTIONS, NESTED_BUILDERS, EFFICIENCY, RESPONSE, RULES_END.
- **Conditional**:
  - **`LARGE_TRADE_ALERT`** if `preflight_large_trade` (driven by node count vs `FPML_JAVA_GEN_FULL_INSPECT_MAX_NODES`, default 200).
  - **`LOCATIONS_KEY`** if `location_array_warnings` non-empty.
  - **`REFS_DOC`** if `reference_pattern_total > 0`.
  - **`DATES`** if java type warnings or certain `type_summary` keys.
  - **`UNDERLIER_FX`** if Settlement/Forward/Fx-related types or registry heuristics.

This ties **static CDM/Rosetta traps** (ReferenceWithMeta packages, MetaFields vs Key, dates) to **actual JSON shape** when possible.

---

## 7. Context compaction — how it really works (deep dive)

The word **“compact”** in this codebase names two different things: the **`compact_context` tool** (paging a stored payload) and **pre-send “compaction”** (`_presend_compact_messages`, which replaces whole tool messages with stubs). They share the same **in-memory payload store** (`_PAYLOAD_STORE` in `tools.py`) but are triggered by **different rules**. Neither path **summarizes** or **compresses** JSON semantically — only **externalizes** full strings behind handles or drops them from the visible transcript in favor of a stub.

### 7.1 Data flow (where each hook runs)

On **every** LLM request, **before** `chat.completions.create`:

1. **`_presend_compact_messages(messages)`** runs if `FPML_JAVA_GEN_MAX_PROMPT_CHARS > 0` (default **0 = disabled**). It may rewrite **historical** `role: "tool"` messages in place.

On **each tool result** returned to the model (right after `_execute_tool`):

2. **`prepare_tool_result_for_llm(fn_name, raw_result_str)`** runs (public surface includes **`maybe_store_oversized_tool_result_for_llm`**, which returns only the string). See **§7.10** for inspect **envelope + tree split** and **`FPML_JAVA_GEN_MAX_TOOL_BYTES`**.

Optional **auto first tree chunk:** if **`FPML_JAVA_GEN_AUTO_TREE_CHUNK_CHARS` > 0** and inspect used a tree split, the loop injects one synthetic **`compact_context`** round-trip in the same turn.

Otherwise there is **no** automatic paging: recovery is up to the model calling **`compact_context` / `fetch_payload`**.

### 7.2 Mechanism A — per-tool cap (`prepare_tool_result_for_llm`)

**Location:** `agent.py` (`prepare_tool_result_for_llm`, `maybe_store_oversized_tool_result_for_llm`, alias `compact_tool_result_for_llm`).

**Threshold:** If **`FPML_JAVA_GEN_MAX_TOOL_BYTES` > 0**, compare **UTF-8 byte length** of `result_str` to that limit. Otherwise use **`FPML_JAVA_GEN_MAX_TOOL_CHARS`** (default **120_000**) as **Python `len(str)`** (character count).

**When under cap:** The exact `result_str` is returned; `_append_tool_io_log` logs raw and sent as identical.

**When over cap:**

1. `store_large_payload(kind=f"{fn_name}:oversize", payload_json=result_str)` copies the **entire** tool output string into `_PAYLOAD_STORE` under a new **handle** (`{kind}:{ms_since_epoch}:{sha256_16}`).
2. The model receives a **different** JSON object, roughly:
   - `stored: true`, `handle`, `sha256`, `bytes` (see §7.4 for the `bytes` naming quirk), `tool`, `next_step` instructing `compact_context` or `fetch_payload`.

**Same-turn NOTICE (tool path):** After tool execution, the loop may append a **user** `NOTICE` that depends on outcome: **tree split** (inspect envelope inline, tree externalized), **full oversize stub**, or generic **`stored: true`**. **Presend** is handled separately: if presend stubbed before the LLM call, a **distinct** one-line NOTICE is appended then (prompt budget), not conflated with tool-cap wording.

### 7.3 Mechanism B — pre-send prompt budget (`_presend_compact_messages`)

**Location:** `agent.py` (`_presend_compact_messages`, `_stub_tool_content_for_prompt_budget`, `_message_list_utf8_bytes`).

**Activation:** Only if `FPML_JAVA_GEN_MAX_PROMPT_CHARS` is set to a **positive** integer (default **0** → function returns immediately, no changes).

**Budget metric:** `_message_list_utf8_bytes` sums **UTF-8 byte lengths** of string fields on each message: `content`, `name`, `tool_call_id`, `role`, plus `json.dumps(tool_calls)`. So the budget is **bytes**, not Python `len(str)`.

**Algorithm:** While total UTF-8 bytes `> max(0, max_prompt_chars - headroom)`:

1. Among **stub-eligible** tool messages (see **§7.10**: protect last **N** with a carve-out so a single tool message can still be stubbed), pick a **victim**: prefer **`read_java_file`**-shaped JSON (`path` + `content` + `lines`) and **`compile_java` failure** JSON (`success: false` + `errors`) over other tools when choosing what to stub first; among that pool (or all eligible if none match), pick the **largest UTF-8** `content`. Replace with `_stub_tool_content_for_prompt_budget`.

2. Repeat until under budget or **guard** aborts. If **`FPML_JAVA_GEN_LOG_PRESEND_ABORT`** is set, log when the guard stops or the list is still over budget after stubbing.

**Important:** This **mutates** `messages` in place across iterations. On turn *t* the model may see full tool output; on turn *t+1*, `_presend_compact_messages` may **replace** that tool message with a stub **before** the next API call. The **next** request then no longer contains the full JSON — only the handle — so the model must **re-fetch** via tools to have the text in context again.

### 7.4 Payload store (`store_large_payload` / `fetch_payload` / `compact_context`)

**Location:** `tools.py` (`_PAYLOAD_STORE`, `store_large_payload`, `fetch_payload`, `compact_context`).

- **Scope:** Single Python process, **in-memory** only. Handles are meaningless after process exit; tests call `reset_payload_store()` for isolation.
- **Stored value:** The full string `payload_json` (tool results are JSON text; presend stores the exact prior tool `content` string).
- **Handle:** Includes timestamp to reduce collisions for equal prefixes.
- **Return field `bytes`:** In `store_large_payload`, `bytes` is `len(payload_json.encode("utf-8"))` — UTF-8 size of the **whole** payload. It is **not** the length of the current chunk.
- **Paging axes:** `fetch_payload` and `compact_context` slice with `payload[offset : offset + limit]` — **Python string indices** (0-based **characters**, not UTF-8 bytes). The response includes `total_chars: len(payload)` (accurate name). Provenance flags named `bytes_omitted_before_this_chunk` / `bytes_omitted_after_this_chunk` are really **“content omitted”** in **characters**, not bytes — naming is misleading for non-ASCII.

**`done` flag:** `done = (offset + limit >= len(payload))`. Last chunk may be shorter than `limit`.

**`compact_context`:** Thin wrapper over `fetch_payload` that adds `provenance` and `next_step`. The suggested continuation is `offset + len(chunk)` (character-accurate). Using a fixed `offset += limit` is equivalent **whenever** `not done` implies `len(chunk) == limit` (true for interior chunks).

### 7.5 Recursive stubbing (large `compact_context` results)

`prepare_tool_result_for_llm` wraps **every** tool, including **`compact_context`** and **`fetch_payload`**. A single chunk (~16k chars) plus JSON wrapper can still exceed `MAX_TOOL_CHARS` if you raise limits or add huge metadata. In that case the **chunk itself** is stored again under a **new** handle, and the model sees a stub pointing to **another** handle. Nothing in code prevents this chain; the model must chase handles.

### 7.6 Why hallucinations spike when compaction is on (code-aligned)

1. **Truth is removed from the prompt:** After stubbing, the model’s **input** for the next call no longer contains the tree/schema text — only a handle and short instructions. Correct behavior requires **calling `compact_context` / `fetch_payload` enough times** to reconstruct needed facts. If the model skips that and writes Java anyway, it will **invent** paths, types, or imports — the system prompt forbids that (`prompt_blocks.py` “Truth boundary”), but that is **policy**, not enforcement.

2. **No structured “plan” of what to page:** The stub does not list json_paths or type names; the model must guess **which** offsets to read or read **sequentially** through a huge JSON — expensive in tool calls and easy to stop early.

3. **`inspect_cdm_json` is especially large:** The lossless `tree` dominates size. **Tree split** (§7.10) keeps **envelope** fields inline when only the tree exceeds the cap; hallucination risk is lower than a **full** inspect stub.

4. **Presend victim selection** now uses **UTF-8 size** for the largest-message tie-break, aligned with the byte budget.

5. **Budget loop may give up:** If stubbing four-ish passes per message count still leaves the list over budget, the code **breaks** and sends an oversized request anyway.

6. **NOTICE wording:** Tool-path and presend NOTICEs are **split** in current code (see §7.10).

7. **Trace vs chat:** `trace` only keeps `result_preview` (**first 500 characters**) of each tool result — debugging from trace alone **hides** stubbed payloads entirely if the stub is short and the rest only in `messages`.

### 7.7 Prompt / tool spec (patch nudge)

The patch-loop nudge in `agent.py` tells the model to call **`read_java_file`** for the **full** generated file (no line-range API on that tool).

### 7.8 Telemetry and OpenRouter

- **`tool_io.jsonl`** (repo root): every tool result logs **raw** vs **sent** strings and lengths (`_append_tool_io_log`). Useful to confirm whether the model saw a stub.
- **`FPML_JAVA_GEN_LOG_TOOL_BYTES`:** stderr lines for raw vs outgoing sizes per tool.
- **OpenRouter:** Optional `FPML_OPENROUTER_LOG_REQUEST_BYTES`; retries via `FPML_OPENROUTER_*`.

### 7.9 Tests that lock behavior

- `tests/test_java_gen/test_compact_context.py`: round-trip paging with `compact_context` until `done`; presend budget reduces UTF-8 size and leaves a `context_stub` handle on a stubbed tool message.
- `tests/test_java_gen/test_compact_envelope.py`: inspect **envelope + tree** split, **MAX_TOOL_BYTES** path, envelope-still-too-big fallback, presend **deprioritize** `read_java_file`-shaped JSON.

### 7.10 Enhancements (implementation summary)

- **`prepare_tool_result_for_llm`:** Central entry for tool-size policy; returns `(string, meta)` with `tree_split` / `oversize_full`.
- **Inspect tree split:** Store `json.dumps(tree)` under `inspect_cdm_json:tree`; LLM sees all inspect keys **except** `tree`, plus `tree_handle`, `tree_stored`, `storage_mode: inspect_tree_only`, and shared `handle` for paging.
- **`FPML_JAVA_GEN_MAX_TOOL_BYTES`:** Optional UTF-8 byte cap; if unset, behavior matches legacy **`MAX_TOOL_CHARS`**.
- **`FPML_JAVA_GEN_AUTO_TREE_CHUNK_CHARS`:** Optional same-turn synthetic `compact_context` for the first tree slice.
- **Presend:** `FPML_JAVA_GEN_PRESEND_PROTECT_LAST_TOOLS` (default 3) with **at least one** stubbable tool when multiple exist; deprioritize read_java / compile-error JSON; victim size by UTF-8 bytes; `FPML_JAVA_GEN_LOG_PRESEND_ABORT` for diagnostics.
- **NOTICE:** Separate messages for presend (before LLM), tree split, full tool stub, and generic `stored`.

---

## 8. Termination and `AgentResult`

| Path | `success` | Notes |
|------|-----------|--------|
| `finish` tool | `fn_args["status"] == "success"` | `java_file`, `match_percentage`, `summary` from args |
| Timeout / max iterations / max tool calls | Usually **false** | `_agent_result_exhausted` |
| Exhausted **but** trace has **`run_java` success + exit 0** | **true** | “Closing as success because run_java…”; `java_file` from last successful `write_java_file` preview or `rosetta-validator/generated/<active>` |

Trace entries: `preflight`, `text`, `tool_call`, `tool_result` (with **`result_preview` first 500 chars only** — full tool content lives in `messages`, not trace).

---

## 9. Integration points

- **`fpml_cdm/cli.py`**: `cmd_generate_java` — OpenRouter (default) or OpenAI SDK, `AgentConfig` from CLI, optional `--java-class`, trace JSON output.
- **`fpml_to_cdm_java.generate_java_from_fpml`**: Writes CDM JSON to `tmp/generated_expected_cdm.json`, then `run_agent` with class name from FpML stem unless overridden.
- **`scripts/run_fpml_mapping_to_java.py`**: Similar orchestration.

---

## 10. Design tensions (intentional)

1. **Template overwrite at start** vs agent full rewrite — favors patch workflows; may clobber a previous good file at each run.
2. **Deterministic `run_java` after compile** — ensures execution feedback even if the model forgets; doubles as extra tool call count.
3. **Lossless inspect** + **hard caps / presend stubbing** — the runtime does not auto-page; see **§7.6** for why stubs correlate with hallucinated structure when the model skips retrieval.
4. **Preflight runs full inspect** — for huge trades, preflight itself can be large; scaling bumps limits rather than truncating inspect in `tools.py`.

---

## 11. Related tests

- `tests/test_java_gen/test_agent.py`: mock LLM, scale config, preflight prompt, oversize stub, deterministic `run_java`, tool list parity.
- `tests/test_java_gen/test_tools.py`: tool semantics, patch, compile, etc.
- `tests/test_java_gen/test_compact_context.py`: paging/provenance.
- `tests/test_java_gen/test_openrouter_client.py`: HTTP client behavior.

---

## 12. Quick reference — environment variables

See `fpml_cdm/java_gen/ENV_VARS.md` for Java-gen limits; OpenRouter has separate `FPML_OPENROUTER_*` variables in `openrouter_client.py`. `OPENROUTER_API_KEY` is required for default CLI path.

---

*Generated from codebase review of `fpml_cdm/java_gen` and related tests/CLI — April 2026.*
