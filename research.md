# Project research: `fpml_isdacm`

This document is a snapshot of the repository as explored on **2026-03-28**. It describes purpose, layout, the several “agent” concepts, data flows, tooling, and known rough edges (including things that look incomplete or duplicated).

---

## 1. What this repo is for

The core product is a **Python library and CLI** (`fpml_cdm`) that converts **FpML XML** (today heavily **FX**: forwards, single-leg, swaps, vanilla options) into **ISDA CDM v6-shaped JSON**, validates it (JSON Schema, semantic checks against FpML, optional **Rosetta** type validation via Java), and can drive **LLM tool-calling loops** to:

1. **Refine mappings** by proposing **structured patches** to declarative **rulesets** (not free-form CDM hallucination).
2. **Generate Java** that builds the same CDM structure, with compile/run feedback in the loop.

There is also a **Cursor Agent Skill** under `.agent/skills/fpml-to-cdm-fx-forward/` (documentation, sample assets, thin scripts) that parallels the real implementation in `fpml_cdm/`—useful for human/IDE workflows but not the runtime package.

---

## 2. Top-level layout (messy bits called out)

| Path | Role |
|------|------|
| `fpml_cdm/` | Main Python package: parser, rulesets, transformers, validators, mapping agent, Java-gen agent, LLM adapters |
| `tests/` | `unittest` suite (`test_*.py`), fixtures under `tests/fixtures/` |
| `schemas/` | JSON Schema artifacts for normalized trades and CDM (and many `cdm-regulation-*` schema files) |
| `data/` | **FpML corpus** (`corpus/fpml_official/…`), **reference CDM JSON** (`corpus/isda_cdm_official/…`), `lei/bic_to_lei.json`, `fx_product_matrix.json`, corpus **reports** (`corpus/reports/*.json`) |
| `rosetta-validator/` | Maven project: CDM **6.7.0** Java deps, fat JAR `rosetta-validator-1.0.0.jar`, **`generated/`** subtree used as the **primary output directory** for Java codegen tools |
| `generated/` (repo root) | **Also** contains generated `.java` files—**duplicate / legacy** relative to `rosetta-validator/generated/` (easy to confuse which is authoritative) |
| `tmp/` | Traces, intermediate JSON, experiment outputs |
| `scripts/` | Operational runners (corpus check, FPML→Java pipeline); some overlap with `python -m fpml_cdm …` |
| `.agent/` | Cursor skill: FPML→CDM FX forward guidance and samples |
| `Makefile`, `make.ps1`, `make.sh`, `make.bat` | Test/corpus/rosetta/java-gen convenience targets |
| `requirements.txt` | Minimal runtime: `jsonschema`, `requests`, `python-dotenv`; OpenAI/Gemini commented as optional |

**Packaging:** There is **no** `pyproject.toml` / `setup.py` in tree; the project is used as a **source tree on `PYTHONPATH`** (see `scripts/run_fpml_mapping_to_java.py` inserting repo root) or via a local venv (`.venv` present).

---

## 3. End-to-end data flow

### 3.1 Deterministic core

1. **Adapter detection** (`fpml_cdm/adapters/registry.py`): under `<trade>`, picks registered product by **local name** + **priority** (`fxForward`, `fxSingleLeg`, `fxOption`, `fxSwap`).
2. **Parse** (`parser.py` + `ruleset_engine.py`): maps XML → normalized dataclasses (`NormalizedFxForward`, `NormalizedFxSwap`, `NormalizedFxOption` in `types.py`), with optional **recovery mode** and ruleset-driven candidate paths.
3. **Rulesets** (`rulesets.py`): per-`adapter_id` dicts of field → **candidate XPath-like paths**, parsers, derived flags; patches applied via `apply_ruleset_patch`.
4. **Transform** (`transformer.py` + `transformers/*`): normalized model → CDM JSON (`transform_to_cdm_v6` dispatches on `normalizedKind`).
5. **Validate** (`validator.py`): schema + semantic comparison to source FpML; produces `ValidationReport` / `ValidationIssue` codes (`SCHEMA_VALIDATION_FAILED`, `SEMANTIC_VALIDATION_FAILED`, etc.).

### 3.2 Optional LLM on parse (not the mapping agent)

`convert_fpml_to_cdm(..., llm_provider=…)` uses `LLMFieldEnricher` (`llm_enricher.py`) when parse leaves **recoverable** issues (`MISSING_REQUIRED_FIELD`, `INVALID_VALUE`). Providers come from `llm/base.py`: `none`, `gemini`, `openai_compat` (e.g. Ollama). This is **text completion**, not OpenAI-style tool calling.

### 3.3 Mapping compliance stage (on `convert`)

`pipeline.py::_apply_mapping_compliance_stage`:

- Scores **deterministic** CDM: schema errors, semantic errors, **Rosetta** failures (if JAR/Java available).
- If `mapping_llm_client` + `mapping_model` are set, runs **`run_mapping_agent`** and replaces output CDM with agent’s **best-so-far** JSON while keeping original normalized model from deterministic parse for reference.
- Fills `ConversionResult.compliance` (deterministic vs agent scores, `rosetta_report`) and optional `review_ticket` for manual triage.

### 3.4 FpML → Java (library)

`fpml_to_cdm_java.generate_java_from_fpml`:

- `convert_fpml_to_cdm` (strict, no parse LLM).
- If compliant: use that CDM; else if mapping enabled: **`run_mapping_agent`**; else skip mapping and use best-effort deterministic CDM.
- Writes `generated_expected_cdm.json` under `output_dir` (default `tmp`).
- Runs **`java_gen.agent.run_agent`** on that file.

CLI mirrors this: `generate-java-from-fpml`.

---

## 4. “Agents” in this repo (four different concepts)

### 4.1 Mapping agent (`fpml_cdm/mapping_agent/`)

- **Entry:** `run_mapping_agent(fpml_path, llm_client, model, config=MappingAgentConfig, …)`.
- **Mechanism:** OpenAI-compatible **`chat.completions.create` with `tools`** (same pattern as Java agent). Client must expose `.chat.completions.create` (OpenRouter wrapper in `java_gen/openrouter_client.py` or OpenAI SDK).
- **Tools** (`mapping_agent/tools.py` + `registry.py`): `inspect_fpml_trade`, `list_supported_fx_adapters`, `get_active_ruleset_summary`, **`run_conversion_with_patch`**, `validate_best_effort`.
- **Seeding:** Before any LLM call, evaluates **all supported adapter candidates** with **base rulesets**, picks best tuple `(schema_err, semantic_err, [rosetta_fail])`.
- **Loop:** LLM proposes **patches** to rulesets; execution is **fully deterministic** after the patch. Best CDM updated only when score improves. Stops on zero errors (including Rosetta when enabled), timeout, max tool calls, or `semantic_no_improve_limit`.
- **Config:** `MappingAgentConfig`: `max_iterations`, `max_tool_calls`, `timeout_seconds`, `semantic_no_improve_limit`, `enable_rosetta`, `rosetta_timeout_seconds`.

### 4.2 Java generation agent (`fpml_cdm/java_gen/`)

- **Entry:** `run_agent(cdm_json_path, llm_client, model, config=AgentConfig, java_class_name=…)`.
- **Tools** (`tools.py`, specs in `tools.json`): inspect CDM JSON, schema lookup, template read/write, **patch** Java, **compile_java**, **run_java**, **validate_output** vs expected JSON, `finish`.
- **Output path:** **`rosetta-validator/generated/<ClassName>.java`** (`GENERATED_DIR` in `tools.py`). Class name from CDM JSON stem or FpML stem when passed from pipeline.
- **Config scaling:** `scale_java_gen_config_for_node_count` bumps iterations/tool calls/timeouts for large CDM trees.
- **Success:** Explicit `finish` tool or heuristic success if `run_java` exited 0 (see `_agent_result_exhausted`).

### 4.3 Enrichment “agents” (`fpml_cdm/agents/`)

These are **not** a unified orchestrator; they are **composable steps** gated by `EnrichmentConfig` in `agents/enrichment.py`:

- **LEI:** `lei_resolver.py` + `data/lei/bic_to_lei.json`
- **Taxonomy:** `taxonomy.py` (deterministic, NDF rules, optional LLM)
- **CDM address indirection:** `cdm_address_refactor.py`
- **Diff/fix:** `cdm_diff_fix.py` (`run_diff_fix_agent` with optional LLM)

`convert_fpml_to_cdm(..., enrichment=EnrichmentConfig(...))` wires parse-time and post-transform enrichment (only exercised when callers pass `enrichment`; CLI `convert` does not expose all of this today).

### 4.4 External orchestration scripts

- **`scripts/run_fpml_mapping_to_java.py`**: intended **3-phase** runner (optional parser enrichment → mapping agent → Java agent), OpenRouter/OpenAI, optional Rosetta patch to force-disable in tools when `--rosetta` not passed.
- **Bug / gap:** it imports **`fpml_cdm.parser_enrichment`** (`ParserEnrichmentConfig`, `run_parser_enrichment`), but **that module does not exist** in the tree. Using `--enrich-parser` would fail at import or runtime until implemented.

---

## 5. CLI surface (`python -m fpml_cdm`)

| Command | Purpose |
|---------|---------|
| `parse` | FpML → normalized JSON |
| `transform` | normalized JSON → CDM JSON |
| `validate` | FpML + CDM semantic/schema report |
| `validate-schema` | JSON-only schema check |
| `validate-rosetta` | CDM vs Rosetta (needs JAR + Java) |
| `convert` | Full convert + optional parse LLM + optional **mapping** provider (`--mapping-provider` openrouter/openai) + compliance/review outputs |
| `generate-java` | CDM JSON → Java agent |
| `generate-java-from-fpml` | Mapping + Java agent; writes `mapping_trace.json` under `--output-dir` |

Default LLM for mapping/Java in argparse is often **`minimax/minimax-m2.5`** on OpenRouter; keys from `.env` (`OPENROUTER_API_KEY`).

---

## 6. Rosetta validator bridge

- **`fpml_cdm/rosetta_validator.py`**: subprocess to JAR, stdin/temp file JSON, parses failure list.
- **Maven:** `rosetta-validator/pom.xml`, `cdm-java` **6.7.0**, Java **11**.
- **Makefile:** `make rosetta-build`, `validate-rosetta-sample`, `generate-java` (writes trace to `tmp/trace.json`).

---

## 7. Tests (high level)

- `tests/test_parser.py`, `test_transformer.py`, `test_pipeline.py`, `test_validator.py`
- `tests/test_agents.py` — LEI, taxonomy, addresses, diff-fix
- `tests/test_adapters.py`
- `tests/test_java_gen/` — agent, tools, schema index, OpenRouter client
- `tests/test_mapping_agent_real_llm_integration.py` — real API integration (documented plan in `tests/mapping_agent_real_llm_integration_plan.md`)
- `tests/test_rosetta_validator.py`, `test_fpml_to_java_from_fpml.py`, etc.

Run: `make test` or `python -m unittest discover -s tests -p "test_*.py"`.

---

## 8. Git / docs state (observed)

- Branch naming in status: **`ft/mapping-agent`**.
- Several markdown files were **deleted** in the working tree (`README.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `plan.md`, `docs/*`, `trace.json` per initial snapshot)—this research file is a replacement inventory for navigation.

---

## 9. Summary: orchestrator reality vs aspiration

- There is **no single central “orchestrator” class** that registers all agents. Orchestration is **procedural**: `cli.py` → `pipeline.py` / `fpml_to_cdm_java.py` / `run_fpml_mapping_to_java.py`, with two prominent **ReAct+tools** loops (mapping, Java) and a separate **enrichment** subsystem.
- **Strengths:** Clear split between **deterministic** conversion and **LLM-constrained** search (ruleset patches only for mapping); Java loop grounded in **compile/run**.
- **Pain points:** **Duplicate generated Java locations**, **tmp** and **script/CLI** overlap, **missing `parser_enrichment` module** referenced by scripts, large **corpus/schema** trees mixed with code, and **`.agent` skill** content potentially drifting from `fpml_cdm` behavior if not updated together.

---

## 10. Quick reference: main entry points

| Goal | Where to start |
|------|----------------|
| Library convert | `fpml_cdm.pipeline.convert_fpml_to_cdm` |
| Mapping loop | `fpml_cdm.mapping_agent.agent.run_mapping_agent` |
| Java loop | `fpml_cdm.java_gen.agent.run_agent` |
| FpML→CDM→Java | `fpml_cdm.fpml_to_cdm_java.generate_java_from_fpml` or CLI `generate-java-from-fpml` |
| Rosetta | `fpml_cdm.rosetta_validator.validate_cdm_rosetta` or CLI `validate-rosetta` |
