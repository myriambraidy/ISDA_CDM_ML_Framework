# Technical report: CDM Java codegen agent, context handling, and CDM validation stack

**Scope.** This report documents behavior observed in `fpml_cdm/java_gen` (the ReAct-style LLM loop that generates Java from CDM JSON), the compaction and payload mechanisms in `agent.py` / `tools.py`, and how CDM validity is assessed in `fpml_cdm/validator.py` and related modules—including how the **mapping agent** tools connect to those validators. It is written for engineers who need operational detail without reading every branch in source.

**Sources.** Code and tests under `fpml_cdm/java_gen`, `fpml_cdm/mapping_agent`, `fpml_cdm/validator.py`, `fpml_cdm/cdm_structure_validator.py`; tests in `tests/test_java_gen/` and `tests/test_validator.py`.

---

## 1. Executive summary

The **Java generator agent** drives an LLM with **OpenAI-compatible tool calling** (`chat.completions.create`, `tools`, `tool_choice="auto"`) to produce **one Java class** under `rosetta-validator/generated/` that compiles against the shaded CDM classpath and, when executed, prints **JSON** to stdout with a `trade` wrapper aligned to the input file.

- **Success** is normally declared via the **`finish`** tool; the implementation also treats certain **exhausted** runs as success if **`run_java`** completed with exit code 0 (see §8).
- **Large outputs** are managed through an **in-memory handle store**, **per-tool size caps**, and optional **pre-send prompt budget** stubbing—mechanisms that are easy to conflate; §7 disentangles them and notes how stubbing correlates with **hallucination risk** when the model does not re-fetch payloads.
- **CDM “validity”** in this repo is **layered**: normalized JSON Schema, official CDM Trade JSON Schema (Draft 4), hand-written **semantic** checks against the normalized FX model, and optionally **Rosetta** via `validate_cdm_structure`. The mapping agent’s **scoring** counts only a subset of error codes; **two different parse paths** (ruleset vs strict legacy) can make **`validate_best_effort`** disagree with **`run_conversion_with_patch`** semantics—§13.

---

## 2. Java generator: purpose and success criteria

The agent receives a **CDM trade JSON** path. It must emit Java that:

1. **Compiles** with `rosetta-validator/target/rosetta-validator-1.0.0.jar` and `generated/` on the classpath.
2. **Runs** and prints **valid JSON** to stdout, preserving the general `trade` shape of the input.

The **`finish`** tool is the primary stop signal. A **fallback success path** exists when the loop hits limits but the trace shows **`run_java`** success with exit 0 (§8).

---

## 3. Module layout (`fpml_cdm/java_gen`)

| Component | Responsibility |
|-----------|------------------|
| `agent.py` | Main loop, tool dispatch, trace, nudges, deterministic `run_java` injection after successful compile, presend compaction |
| `tools.py` | Tool implementations, active Java target, `_PAYLOAD_STORE`, `inspect_cdm_json`, compile/run, patch/read/write |
| `prompt_blocks.py` | `build_system_prompt(preflight)` — composable static and conditional blocks |
| `tools.json` | LLM function schemas (**15** tools including `finish`) |
| `schema_index.py` | Lazy index of `schemas/jsonschema/*.schema.json` — types, Java FQNs from filenames, enums |
| `openrouter_client.py` | HTTP client exposing `chat.completions.create`-compatible calls for OpenRouter |

The package’s public surface (`java_gen/__init__.py`) exposes `run_agent`, `AgentConfig`, `AgentResult`, and historically re-exports `SchemaIndex`.

---

## 4. Core agent loop (`_run_agent_impl`)

1. **Target resolution.** `run_agent` calls `set_java_generation_target(cdm_json_path, java_class_name)`. The class/file name defaults from the **CDM JSON filename stem** (`json_stem_to_java_class_name`) unless the FpML pipeline passes an explicit name. Tools treat `filename=None` and legacy `CdmTradeBuilder` as the **active** target.

2. **Preflight.** Before the first LLM turn, `inspect_cdm_json(..., detail="full")` runs inside try/except. On failure, `PREFLIGHT_ALL_BLOCKS` still allows the system prompt to load.

3. **Scaling.** `total_nodes` from inspect feeds `scale_java_gen_config_for_node_count`, raising iteration, tool-call, and timeout limits for large trees (thresholds near 150 and 400 nodes).

4. **System prompt.** `build_system_prompt(preflight)` is dynamic: optional blocks depend on references, location warnings, date/FX heuristics, and large-trade alerts (§6).

5. **Bootstrap file.** Before iteration 0, the agent writes `get_java_template()` via `write_java_file` so placeholders exist for `patch_java_file`. Failures are swallowed so a full `write_java_file` remains possible.

6. **Initial user message.** States CDM path, class/file name, `rosetta-validator/generated/`, and compile/run expectations.

7. **Per-iteration behavior.**
   - If `FPML_JAVA_GEN_MAX_PROMPT_CHARS > 0`, **`_presend_compact_messages`** may replace historical tool message bodies with stubs (§7.3).
   - The LLM is called with full history and `load_tool_specs()`.
   - **No tool calls:** trace records `text`; a **nudge** user message may demand at least one tool call on the first such event.
   - **Each tool call:** trace records `tool_call`; **`finish`** short-circuits; otherwise `_execute_tool` → JSON string → **`prepare_tool_result_for_llm`** (§7.2, §7.10) → `tool` role message.
   - **Repeat detection:** identical tool name + serialized arguments **three** times yields a warning wrapper (counter reset).
   - **Patch loop:** three consecutive **`patch_java_file`** calls with a **single** patch each triggers a one-time **batch patch** nudge (`patch_loop_nudge_sent`).
   - **`compile_java` success** triggers **synthetic** assistant `tool_calls` and a `tool` result for **`run_java`** (timeout 30s) without waiting for the model.
   - If **`run_java`** succeeded in that iteration, a user message prompts **`finish`** with success and `java_file`.
   - Limits → `_agent_result_exhausted` (§8).

**Note.** `AgentConfig.match_threshold` (default 95.0) is **not** consumed inside `agent.py`; match semantics come from `finish` arguments or trace fallbacks.

---

## 5. Tool surface vs. dispatch

- **`tools.json`** and **`TOOL_DISPATCH`** both list **15** tools including **`finish`**.
- For **`finish`**, the loop **does not** call the Python `finish()` helper; it builds **`AgentResult`** from arguments (`status`, `summary`, `java_file`, `match_percentage`).
- The other **14** tools flow through `_execute_tool` and return JSON strings to the model.
- **`diff_json`** exists in `tools.py` but is **not** exposed to the LLM—intended for programmatic or future use.

---

## 6. Notable tool behaviors

### `inspect_cdm_json`

Schema-aware walk from the Trade schema; **lossless** `tree` nodes (`json_path`, `cdm_type`, `schema_ref`, `java_class`, values, enums). Builds **`type_registry`** (`import_statement`, `builder_entry`, `is_enum`). **`reference_patterns_sample`** / totals guide `address` vs `globalReference` vs `externalReference`. **`location_array_warnings`** flag MetaFields `.location` pitfalls. **`java_type_warnings`** apply `JAVA_TYPE_OVERRIDES`. The `detail` parameter does **not** shrink tool output—compaction is agent-layer.

### Schema helpers

`lookup_cdm_schema`, `resolve_java_type`, `list_enum_values` use **`SchemaIndex`** and filename→Java FQN conventions. Property **`setter_hint`** / **`setter_note`** matter for Reference shapes (e.g. `address` → `setGlobalReference`).

### File and build tools

- **`write_java_file`** / **`read_java_file`** / **`patch_java_file`** target `rosetta-validator/generated/`. Patching: exact replace, then normalized whitespace; ambiguous matches error; may return **`suggested_old_text`**.
- **`compile_java`** / **`run_java`**: `javac` and `java` with OS-appropriate classpath separators; structured compile errors; **`stdout_is_valid_json`** on run output.

### `validate_output`

Delegates to `fpml_cdm.validator.validate_cdm_official_schema` on the `trade` object (see §13 for the fuller stack).

### Payload tools

**`store_large_payload`**, **`fetch_payload`**, **`compact_context`** implement the handle store and character-offset paging (default chunk size on the order of 16k characters). Full behavioral detail is in §7.

---

## 7. System prompt (`prompt_blocks.py`)

Always-on blocks include CORE (import rules from **`well_known_imports`**, **`type_registry`**, **`list_enum_values`** only), PATCH, STRATEGY, CONVENTIONS, NESTED_BUILDERS, EFFICIENCY, RESPONSE, RULES_END.

Conditional blocks attach when preflight indicates:

- **`LARGE_TRADE_ALERT`** — node count vs `FPML_JAVA_GEN_FULL_INSPECT_MAX_NODES` (default 200).
- **`LOCATIONS_KEY`** — non-empty `location_array_warnings`.
- **`REFS_DOC`** — `reference_pattern_total > 0`.
- **`DATES`** — java type warnings or certain `type_summary` keys.
- **`UNDERLIER_FX`** — settlement/forward/FX-related registry signals.

This ties **static Rosetta/CDM traps** to **instance-specific** JSON shape where possible.

---

## 8. Context handling: compaction, caps, and payload store (detailed)

The codebase uses **“compact”** for two different mechanisms: the **`compact_context`** tool (paging stored text) and **`_presend_compact_messages`** (rewriting past tool messages before the next API call). Both can use **`_PAYLOAD_STORE`** but are triggered under **different rules**. Neither path performs **semantic summarization** of JSON—only **externalization** behind handles or **removal** of full text from the visible transcript in favor of stubs.

### 8.1 Ordering of hooks

**Before each `chat.completions.create`:** if `FPML_JAVA_GEN_MAX_PROMPT_CHARS > 0`, **`_presend_compact_messages`** may rewrite older `role: "tool"` messages.

**After each tool execution:** **`prepare_tool_result_for_llm`** (and related helpers) may stub or split **`inspect_cdm_json`** (§8.10). Optional **`FPML_JAVA_GEN_AUTO_TREE_CHUNK_CHARS`** can inject a same-turn synthetic **`compact_context`** round-trip for the first tree slice.

Otherwise, **no** automatic paging: the model must call **`compact_context`** / **`fetch_payload`**.

### 8.2 Per-tool cap (`prepare_tool_result_for_llm`)

If **`FPML_JAVA_GEN_MAX_TOOL_BYTES` > 0**, the cap is **UTF-8 byte length** of the serialized result. Else **`FPML_JAVA_GEN_MAX_TOOL_CHARS`** (default 120_000) uses **Python character length** of the string.

Under cap: the full string is returned. Over cap: the full string is stored via **`store_large_payload`**, and the model receives a stub with `stored`, `handle`, `sha256`, `bytes`, `tool`, and **`next_step`** pointing to **`compact_context`** or **`fetch_payload`**.

Separate **NOTICE** user messages may follow for tree split, full oversize, presend stubbing, etc., depending on path (tool vs presend).

### 8.3 Pre-send prompt budget (`_presend_compact_messages`)

Active only when **`FPML_JAVA_GEN_MAX_PROMPT_CHARS`** is a **positive** integer (default **0** disables).

**Budget:** `_message_list_utf8_bytes` sums UTF-8 sizes of message string fields and serialized `tool_calls`.

**Algorithm:** While over budget (with headroom), select a **stub-eligible** tool message (subject to **§8.10** carve-outs for the last N tools), preferring **`read_java_file`-shaped** JSON and **`compile_java` failure** payloads when choosing victims, then the **largest UTF-8** `content`. Replace via **`_stub_tool_content_for_prompt_budget`**. Repeat until under budget or a guard aborts (optional **`FPML_JAVA_GEN_LOG_PRESEND_ABORT`**).

**Consequence:** On turn *t* the model may see a full tool result; on turn *t+1* presend may **replace** that message with a stub, so the model must **re-fetch** to see the text again.

### 8.4 Payload store semantics

**Scope:** In-process only; tests use **`reset_payload_store()`**.

**Handles:** Include time and hash fragments to reduce collisions.

**Field naming caveat:** `bytes` on store results is **UTF-8 length of the entire stored string**, not the chunk length. **`fetch_payload`** / **`compact_context`** slice with **Python string indices** (characters). Fields named `bytes_omitted_*` actually refer to **character** offsets omitted—misleading for non-ASCII.

**`done`:** `done = (offset + limit >= len(payload))`.

**`compact_context`:** Wraps `fetch_payload` with **`provenance`** and **`next_step`**; continuation offset should follow returned chunk length when interior chunks are full-sized.

### 8.5 Recursive stubbing

`prepare_tool_result_for_llm` applies to **`compact_context`** and **`fetch_payload`** too. A large chunk can be re-stored under a **new** handle, producing a **chain** of handles the model must follow.

### 8.6 Why hallucinations increase under aggressive compaction (aligned with code)

1. After stubbing, the **prompt** no longer contains tree/schema text—only handles—unless the model retrieves.
2. Stubs do not enumerate **`json_path`** or types; retrieval strategy is model-dependent.
3. **`inspect_cdm_json`** dominates size; **envelope + tree split** (§8.10) reduces risk versus stubbing the entire inspect output.
4. Presend victim selection uses UTF-8 size for tie-breaking.
5. The budget loop may **stop early** and still send an oversized request.
6. **Trace** keeps only **`result_preview`** (first 500 characters)—debugging from trace alone can miss stubbed content.

### 8.7 Patch-loop guidance

The agent nudge instructs the model to **`read_java_file`** for the full file (no line-range API on that tool).

### 8.8 Telemetry and OpenRouter

- **`tool_io.jsonl`**: logs raw vs sent tool strings (`_append_tool_io_log`).
- **`FPML_JAVA_GEN_LOG_TOOL_BYTES`**: stderr size diagnostics.
- **OpenRouter:** optional request logging and retry-related `FPML_OPENROUTER_*` variables.

### 8.9 Tests locking behavior

- **`test_compact_context.py`**: paging until `done`; presend reduces UTF-8 size and leaves `context_stub` handles.
- **`test_compact_envelope.py`**: inspect envelope/tree split, `MAX_TOOL_BYTES`, envelope-too-big fallback, presend deprioritization of `read_java_file`-shaped JSON.

### 8.10 Inspect tree split and related env vars (summary)

- Central sizing policy in **`prepare_tool_result_for_llm`** with metadata for `tree_split` vs `oversize_full`.
- **Tree split:** store `json.dumps(tree)` under an inspect-specific kind; LLM sees all inspect keys **except** `tree`, plus `tree_handle`, `tree_stored`, `storage_mode: inspect_tree_only`, shared `handle` for paging.
- **`FPML_JAVA_GEN_MAX_TOOL_BYTES`**: optional UTF-8 cap.
- **`FPML_JAVA_GEN_AUTO_TREE_CHUNK_CHARS`**: optional first automatic tree chunk.
- **Presend:** **`FPML_JAVA_GEN_PRESEND_PROTECT_LAST_TOOLS`** (default 3), victim preference rules, UTF-8 victim sizing, **`FPML_JAVA_GEN_LOG_PRESEND_ABORT`**.

---

## 9. Termination and `AgentResult`

| Outcome | `success` | Notes |
|---------|-----------|--------|
| `finish` with `status == "success"` | true | `java_file`, `match_percentage`, `summary` from tool args |
| Timeout / max iterations / max tool calls | typically false | `_agent_result_exhausted` |
| Exhausted but trace shows **`run_java`** success, exit 0 | true | Closing message; `java_file` from last successful `write_java_file` preview or default path under `generated/` |

Trace types include `preflight`, `text`, `tool_call`, `tool_result` (previews capped at 500 characters—full content lives in **`messages`**).

---

## 10. Integration points

- **`fpml_cdm/cli.py`** — `cmd_generate_java`: OpenRouter (default) or OpenAI SDK, `AgentConfig`, optional `--java-class`, trace output path.
- **`fpml_to_cdm_java.generate_java_from_fpml`** — writes CDM JSON (e.g. under `tmp/`), then **`run_agent`** with class name from FpML stem unless overridden.
- **`scripts/run_fpml_mapping_to_java.py`** — similar orchestration for end-to-end FpML experiments.

---

## 11. Intentional design tensions

1. **Template bootstrap** vs. full rewrites each run—favors patching; may overwrite prior artifacts.
2. **Deterministic `run_java` after compile**—guarantees execution feedback and consumes tool-call budget.
3. **Lossless inspect** vs. **hard caps**—runtime does not fully auto-page; model discipline required (§8.6).
4. **Full preflight inspect** for huge trades—scaling raises limits rather than truncating inspect inside `tools.py`.

---

## 12. Related automated tests

| Area | Tests |
|------|--------|
| Agent loop | `tests/test_java_gen/test_agent.py` |
| Tool semantics | `tests/test_java_gen/test_tools.py` |
| Compaction / paging | `tests/test_java_gen/test_compact_context.py`, `test_compact_envelope.py` |
| OpenRouter client | `tests/test_java_gen/test_openrouter_client.py` |
| Validator | `tests/test_validator.py` |

---

## 13. Environment variables (quick pointer)

Authoritative lists: **`fpml_cdm/java_gen/ENV_VARS.md`** for Java-gen limits; **`openrouter_client.py`** and `FPML_OPENROUTER_*` for HTTP client behavior. **`OPENROUTER_API_KEY`** is required for the default CLI path to OpenRouter.

---

## 14. CDM JSON validation: mapping agent, `validator`, and `validate_cdm_structure`

This section explains what “valid” means for CDM trade JSON in the **FpML → CDM** context, how **`fpml_cdm.validator`** composes checks, how the **mapping agent** consumes reports, and how that differs from **Java `validate_output`**.

### 14.1 Definition of `ValidationReport.valid`

`valid` is **true only when `errors` is empty** (`ValidationReport` in `fpml_cdm/types.py`). The mapping agent’s **lexicographic score** for best CDM counts **only**:

- `SCHEMA_VALIDATION_FAILED`
- `SEMANTIC_VALIDATION_FAILED`

Other codes (e.g. `UNSUPPORTED_PRODUCT`, parse-time `MISSING_REQUIRED_FIELD`) still set `valid=False` but **do not** increment `best_schema_error_count` / `best_semantic_error_count` in `run_mapping_agent`.

### 14.2 Entry points (summary)

| Function | FpML | Normalized | Typical use |
|----------|------|------------|-------------|
| `validate_transformation(fpml_path, cdm_obj)` | Re-parse `parse_fpml_fx(..., strict=True)` | From that parse | CLI `validate`, fixtures |
| `validate_normalized_and_cdm(normalized, cdm_obj)` | None | Caller-supplied `NormalizedFxTrade` | `run_conversion_with_patch`, mapping agent scoring |
| `validate_conversion_files(fpml_path, cdm_json_path)` | Same as transformation path | Same | `validate_best_effort` (via temp file) |
| `validate_cdm_structure` | N/A | N/A | Unified CDM-only gate: envelope + JSON Schema + Rosetta + supplementary (`cdm_structure_validator.py`) |
| `validate_cdm_official_schema(trade_dict)` | N/A | N/A | L1 JSON Schema helper; used inside structure validation and legacy paths |

Normalized helpers include `validate_normalized_parsed_dict` keyed by `normalizedKind`. Artifact pinning is discussed in **`docs/CDM_VALIDATION.md`** where present.

### 14.3 Pipeline inside `validate_transformation` / `validate_normalized_and_cdm`

After a normalized model is available:

1. **Normalized JSON Schema** — `Draft202012Validator` on `fpml_fx_*_parsed.schema.json` selected by `normalizedKind`.
2. **CDM Trade JSON Schema** — `validate_cdm_official_schema` uses **Draft 4** with a **local RefResolver** under `schemas/jsonschema/` (no network).
3. **Semantic cross-check** — `_semantic_validation(normalized, cdm_obj)` walks CDM and compares selected fields to the normalized model; issues use `SEMANTIC_VALIDATION_FAILED`. A **`MappingScore`** reflects internal `check()` counters.

`validate_transformation` **prepends** strict FpML parse; on `ParserError`, it returns early with parse issues only.

### 14.4 Semantic validators (FX, structure-specific)

Semantics are **not** a general CDM constraint solver; they encode expectations for **this repo’s transformer output**:

- **Forward-like:** first `tradeLot[0].priceQuantity[0]`, first payout `SettlementPayout`, dates, quantities, rates, payer/receiver vs counterparty (defaults `Party1`/`Party2` when refs missing). Tolerances: **0.01** amounts, **0.0001** rates.
- **Swap:** at least two payouts and two `priceQuantity` entries; near/far legs; distinct default party expectations where applicable.
- **Option:** `OptionPayout`, exercise, `optionType`, strike, buyer/seller, put/call currencies in first `priceQuantity`.

Unknown `normalized_kind` yields a single semantic error.

### 14.5 Mapping agent tools vs. best-state updates

- **`run_conversion_with_patch`** — ruleset patch → `parse_fpml_fx_with_ruleset` (non-strict, recovery) → `transform_to_cdm_v6` → **`validate_normalized_and_cdm`**. Returns `validation_report`, `validation_summary` (schema/semantic/Rosetta counts), `cdm_json`, and **`cdm_structure`**. **Only this tool** updates the loop’s **best** CDM when the score improves.

- **`validate_best_effort`** — writes CDM to a temp file → **`validate_conversion_files` → `validate_transformation`**, which uses **`parse_fpml_fx(..., strict=True)`** (legacy parser), **not** the ruleset engine. Therefore semantic comparison may target a **different** normalized instance than the patched ruleset path. Docstrings direct the model to prefer **`run_conversion_with_patch`** after patching.

**`validate_best_effort` does not** update `best_cdm_json` or the best key—it is advisory.

Both tools can attach **`cdm_structure`** from **`validate_cdm_structure`**. **`enable_rosetta`** on mapping tools maps to whether Rosetta runs inside that structure validator.

### 14.6 `validate_cdm_structure` (unified CDM gate)

Layers: envelope (`trade` / `tradeState`) → Draft 04 trade schema → Rosetta JVM (unless Java/JAR missing or `FPML_CDM_ALLOW_NO_ROSETTA`) → optional supplementary hooks. Reports include `structure_ok`, per-layer flags, Rosetta sub-report, `metadata.cdm_version` **"6"**.

The **FpML-bound** normalized + hand-semantic stack remains **orthogonal**; use **`cdm_structure`** for “is this CDM JSON structurally acceptable as v6?”

### 14.7 Java `validate_output`

Returns **`validate_cdm_structure(...).to_dict()`**—the same rich shape as mapping tools, not a minimal `{valid, errors}`.

### 14.8 CLI (validation-related)

- `validate --fpml … --cdm …` → `validate_conversion_files`.
- `validate-schema` → project schema by name.
- `validate-rosetta` → Rosetta-only diagnostic.
- `validate-cdm-structure` → full structure report; exit `2` on infra failure without `--allow-no-rosetta`.

### 14.9 Practical limitations

1. **Two parsers** — ruleset normalized model vs strict legacy parse → semantic confusion if tools are mixed without reading docstrings.
2. **Two JSON Schema drafts** — Draft 4 (CDM official) vs Draft 2020-12 (normalized)—subtle for dependency upgrades.
3. **Semantic rules are FX-only and order-sensitive** — valid CDM with different leg ordering may fail semantics while passing schema and Rosetta.
4. **Scoring blind spot** — non-counted error codes still invalidate `valid` but not headline agent counts.
5. **Temp files** for `validate_best_effort` — typical OS cleanup assumptions apply.

### 14.10 Validator tests

`tests/test_validator.py` covers happy paths, tampering, unsupported product, and related edge cases.

---

## 15. Document control

**Version.** April 2026, rewritten from `research.md` for report-style structure and cross-references.

**Audience.** Implementers and reviewers of the Java codegen agent, compaction behavior, and CDM validation / mapping-agent interaction.
