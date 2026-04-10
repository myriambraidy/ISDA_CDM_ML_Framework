# Plan: FpML → CDM mapping agent (skills, LLM-led, ruleset patches)

> **Note:** This file is the **mapping-agent** roadmap. The repository already contains [`plan.md`](plan.md) for an unrelated initiative (Java codegen `compact_context`). Keep both; do not merge without intent.

**Status:** **P0a, P0b, P1, P2 implemented.** P3–P4 remain open for future phases.

---

## 1. Executive summary

Build an **evolved** [`fpml_cdm/mapping_agent`](fpml_cdm/mapping_agent) that:

1. **Always runs** when converting FpML → CDM in production flows that use an LLM client (deterministic parse/transform remain in-repo as **oracle**, tests, and **tool implementation**, not as the user-facing default path).
2. Stays **product-agnostic** at the **orchestration** layer (one loop, one tool philosophy), like the Java codegen agent consumes CDM regardless of product.
3. Uses **Agent Skills** (modular prompts, progressive disclosure) selected after **document classification**, grounded in **official FpML** and **CDM v6** documentation and schemas.
4. Restricts the model's **effects** to **structured ruleset patches** applied by code; the LLM plans and proposes patches — it does **not** emit raw CDM JSON as the authority.
5. Optimizes for **coverage**: every materially relevant FpML field under agreed scope must be **accounted for** (mapped, derived, or explicitly ignored with reason) in addition to schema/semantic/Rosetta-style validation already in the stack.
6. Exposes a **standalone CLI command** to run **only** the mapping agent (FpML in → CDM JSON + full trace out) for development and CI, **without** invoking the Java codegen agent — parity with how `generate-java` is used to test the Java agent in isolation.

---

## 2. Goals and success criteria

| Dimension | Target |
|-----------|--------|
| **Validity** | CDM output passes project validators (`validate_*`, `cdm_structure_validator`, optional Rosetta) for the chosen CDM v6 alignment. |
| **Coverage** | No unclassified FpML leaves in scope: each path is mapped, derived, or explicitly ignored per policy (see §6). |
| **Auditability** | Trace includes: classifier output, `skill_id` + version, each patch, validation summaries, final scores. Standalone CLI writes trace JSON to disk (see §7.4). |
| **Separation** | Mapping agent remains **independent** from [`fpml_cdm/java_gen`](fpml_cdm/java_gen); Java agent still consumes CDM JSON only. |
| **Skills** | Skills are **authored from official FpML + CDM v6 sources** (§8); repo-local schemas (§8.3) are supporting artifacts, not a substitute for specs. |

**Non-goals (initial phases):**

- Removing [`parser.py`](fpml_cdm/parser.py) / [`transformer.py`](fpml_cdm/transformer.py).
- Letting the LLM freely author CDM JSON without tool-enforced structure.
- Mandating a single commercial LLM vendor (OpenRouter is the default; OpenAI-compatible clients are also supported via the same `chat.completions.create` interface).

---

## 3. Design principles

### 3.1 LLM-led strategy, deterministic execution

- **LLM:** classification, skill choice, patch design, iteration strategy, interpretation of validation/coverage feedback.
- **Code:** `apply_ruleset_patch` → parse → transform → validate → coverage report. Same boundary as today's [`run_conversion_with_patch`](fpml_cdm/mapping_agent/tools.py).

### 3.2 Progressive disclosure for skills

Follow the **Agent Skills** pattern (metadata first, full body on demand):

- **Always in context:** short base system prompt + **skill catalog** (id + one-line description per skill).
- **After routing:** inject the selected skill body into the system context (appended as a user or system message before the first LLM turn). This is the **P0 approach** — simple, auditable, no extra tool call needed.
- **Future (evaluate in P1):** if skills grow large enough that injection wastes tokens, add a `load_mapping_skill(skill_id, section?)` tool for on-demand progressive loading. Until then, tool-based loading is not implemented.

References: [Anthropic Agent Skills overview](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills/overview), [Equipping agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills), [Cursor Agent Skills](https://www.cursor.com/docs/context/skills).

### 3.3 Product-agnostic orchestration, specialist skills — and the FX-only execution layer

**Orchestration** (agent loop, tool dispatch, scoring, CLI) is product-agnostic: one loop, one tool API.

**Execution** is currently FX-only:

- `NormalizedFxTrade = Union[NormalizedFxForward, NormalizedFxSwap, NormalizedFxOption]` in [`types.py`](fpml_cdm/types.py).
- `FX_ADAPTER_REGISTRY` in [`adapters/registry.py`](fpml_cdm/adapters/registry.py) — four FX adapters.
- `parse_fpml_fx_with_ruleset`, `get_base_ruleset`, `transform_to_cdm_v6` — all dispatch on FX adapter ids and FX normalized kinds.

**Extension seam for non-FX products (P3):** When a new asset class is added:

1. Register new adapters in a **generalized** adapter registry (extend or sibling of `FX_ADAPTER_REGISTRY`).
2. Add normalized model(s) (e.g. `NormalizedIrdSwap`) and transformer(s).
3. Add base rulesets keyed by the new adapter ids.
4. `run_conversion_with_patch` already delegates to `parse_fpml_fx_with_ruleset` → `transform_to_cdm_v6` — replace these calls with a **dispatch function** that selects parser + transformer by adapter spec, not by hardcoded FX function names.

**For P0–P2, FX is the only supported execution path.** The orchestration layer must **not** hardcode FX assumptions (e.g. the agent loop should not import FX-specific types); the skill's `adapter_ids` frontmatter tells the classifier which adapters it covers.

**What happens when `run_conversion_with_patch` cannot run** (no adapter/ruleset/transformer for the detected product): the tool returns `{"error": "No adapter registered for <product>", "unsupported": true}`. The agent loop treats this as a hard stop — logs it to trace, returns a result with `best_cdm_json: {}` and the error. The standalone CLI exits non-zero with a clear message.

**Router:**

- **Classification is pre-loop** (deterministic code, not an LLM tool call): run before the agent loop starts, output `skill_id` + `confidence` into the trace, then inject the resolved skill body into the bootstrap messages. The classifier is a Python function, not an agent tool.
- Cheap signals first (XML local names under `trade`, namespaces, optional `productType` / message type), then optional small LLM or embedding classifier for ambiguous cases.
- Support **abstain** + single clarification or fallback skill (`generic-derivatives` / `unknown-product`).

**Skills:** one skill per **domain** (e.g. FX spot/forward/NDF, FX swap, FX option, IRD, equity, credit, …) as coverage grows — not one monolithic prompt.

Routing patterns align with common practice: hybrid rule + semantic routing, coarse→fine, logging and shadow evaluation ([semantic routing discussion](https://proagenticworkflows.ai/harnessing-semantic-routing-for-llm-agents-in-ai-agentic-workflows), [dynamic tool/context selection](https://getathenic.com/blog/ai-agent-tool-selection-dynamic-routing-systems-that-scale)).

### 3.4 Tool budget

Avoid registering dozens of tools in every turn; keep **core** tools universal and optionally **register a subset** per product route if the implementation supports it without fragmenting the codebase.

### 3.5 Context window / prompt budget

Large validation reports, FpML trees, and coverage reports can overflow the context window. Plan for:

- **Compact summaries** by default in tool results (error counts + first N errors, not full reports).
- **Payload externalization** when a tool result exceeds a configurable size cap — store full JSON, return stub with handle + byte count (same pattern as Java agent's `store_large_payload` / `fetch_payload`).
- **Pre-send budget** (optional, off by default in P0): before each LLM call, check total message size and replace oldest large tool results with stubs if needed (mirrors `_presend_compact_messages` in `java_gen/agent.py`).

---

## 4. Architecture (target)

```
FpML path
    → [Classifier (pre-loop, deterministic)] → skill_id (+ confidence)
    → [Inject skill body into bootstrap messages]
    → [Agent loop]
           ↔ tools: inspect_*, list_*, run_conversion_with_patch, validate_*, coverage_*, finish, …
    → best_cdm_json + trace + compliance metadata
```

### 4.1 Components to add or extend

| Component | Responsibility |
|-----------|------------------|
| **Skill store** | Versioned directories (e.g. `fpml_cdm/mapping_agent/skills/<skill_id>/SKILL.md`) or `resources/`; YAML frontmatter (`name`, `description`, `cdm_version`, `fpml_versions`, `adapter_ids`, `tags`). |
| **Classifier** | Pre-loop Python function (not an LLM tool): rules → optional LLM for ambiguous; output schema stable for trace. |
| **Prompt builder** | Composes base system prompt + injected skill body; optional preflight summary (like Java agent preflight). |
| **Coverage engine** | Deterministic FpML inventory vs mapping rules; returns gaps and ignores. |
| **Agent loop** | Extend [`agent.py`](fpml_cdm/mapping_agent/agent.py): skill injection, scoring vector, stop conditions (including `finish` tool), same trace discipline as [`java_gen/agent.py`](fpml_cdm/java_gen/agent.py) where useful. |
| **Pipeline / CLI** | Always invoke mapping agent when LLM configured; document behavior when LLM absent (see §7); provide **standalone** mapping-only command (see §7.4). |

### 4.2 Scoring (best-so-far)

Define a **lexicographic** ordering:

1. Schema error count.
2. Semantic error count.
3. Rosetta / structure failures (if enabled).
4. Coverage gap count (unmapped non-ignored paths) — **enters in P1** when coverage engine ships.

**P0 scoring** uses the existing `(schema_err, semantic_err, rosetta_fail)` triple from today's mapping agent. Coverage gap count is added to the vector in P1 once `fpml_coverage_report` is implemented. Until then, coverage is informational (logged in trace but not used for best-so-far comparison).

Ruleset-only patches mean "improvement" is always measurable from tool JSON.

---

## 5. Tool surface (planned)

**Existing (keep / generalize):**

- `inspect_fpml_trade` — generalize beyond FX-specific tag counts; product hints.
- `list_supported_*` / adapter or ruleset discovery — evolve to non-FX adapters as added.
- `get_active_ruleset_summary` — parameterize by `adapter_id` / product.
- `run_conversion_with_patch` — core execution channel.
- `validate_best_effort` — re-validate arbitrary CDM JSON vs source where applicable.

**New (planned):**

- `finish(status, summary)` — explicit stop signal from the LLM (like Java agent). The loop short-circuits on `finish`; the agent can call it when satisfied or when it determines no further patches will improve the result.
- `fpml_coverage_report(fpml_path, adapter_id, ruleset_hash?)` — unmapped paths, ignored paths with reasons, statistics. **(P1)**
- `list_mapping_skills()` — returns catalog entries for the LLM (informational; routing is pre-loop, but the model can see what skills exist).

**Deferred (evaluate after P0):**

- `load_mapping_skill(skill_id, section?)` — progressive disclosure tool; only if skill bodies exceed reasonable injection size.
- `lookup_cdm_schema_fragment` — thin wrapper around existing JSON Schema index (reuse patterns from `java_gen/schema_index.py`) for CDM v6 shapes relevant to the active skill.

---

## 6. Coverage model

### 6.1 Definition

**Coverage** means: for every **in-scope** FpML node (configurable depth: e.g. leaf text nodes and selected attributes under `trade`), the system records one of:

- **Mapped** → normalized field key and/or CDM JSON pointer.
- **Derived** → computed from other nodes (document rule id).
- **Ignored** → explicit policy entry (`reason`, `fpml_pattern`); must be allowed by governance.

Out-of-scope: envelope noise, duplicate representations where the skill says "canonical path is X".

**Scope is defined per skill** (§6.2), not globally. Each skill's `SKILL.md` lists which FpML subtrees and attributes are in scope. The coverage engine reads that list.

### 6.2 Skill content

Each skill MUST document:

- Which FpML subtrees are in scope.
- Canonical paths vs deprecated alternates.
- Default ignore list (if any) and when to escalate to a new patch vs accept ignore.

### 6.3 Official sources for coverage rules

Author ignore/map tables from **FpML data dictionary / schema** and **CDM product docs**, not only from internal code (§8).

---

## 7. Pipeline and CLI behavior

### 7.1 When LLM is configured

- **`convert_fpml_to_cdm`** (and any CLI entry that implies "full" conversion): **always** run `run_mapping_agent` (or a renamed successor) — not only on deterministic failure.
- Deterministic path may still run **inside** the first tool call as baseline for diff/coverage, or as a parallel oracle — implementation choice, but **user-visible default** is agent output.

### 7.2 When LLM is absent

Explicit policy (pick one during implementation and document):

- **Fail** with clear error ("mapping agent requires LLM"), or
- **Degraded** deterministic-only mode with warnings and different compliance flags.

### 7.3 Compliance object

Extend [`compliance`](fpml_cdm/pipeline.py) (or equivalent) to include: `skill_id`, `skill_version`, `coverage_summary`, `classifier_confidence`.

**Interaction with `_apply_mapping_compliance_stage`:** That function in `pipeline.py` currently wraps `run_mapping_agent` with Rosetta re-validation and scoring. When the mapping agent is "always on," this wrapper must not **double-run** the agent. Resolve by: (a) the wrapper calls the agent once and uses its result, or (b) refactor the compliance stage to accept a pre-computed `MappingAgentResult` when the caller already ran the agent. Pick during implementation.

### 7.4 Standalone command: mapping agent only (no Java)

**Purpose:** Run the **full mapping agent loop** (LLM + tools + skills when implemented) on a single FpML file, emit **CDM JSON** and a **machine-readable trace**, and print a short human summary — **without** `generate-java` / `generate-java-from-fpml` and without requiring Java.

**Why:** Developers need the same ergonomics as testing [`java_gen`](fpml_cdm/java_gen): iterate on prompts, tools, and skills without paying for or debugging the downstream Java codegen step.

**Planned behavior (align with existing CLI patterns):**

| Aspect | Plan |
|--------|------|
| **Invocation** | New subcommand on `fpml_cdm` CLI (e.g. `run-mapping-agent` or `map-fpml-to-cdm` — final name TBD); optional thin wrapper in [`scripts/`](scripts/) mirroring [`scripts/run_fpml_mapping_to_java.py`](scripts/run_fpml_mapping_to_java.py) if desired. |
| **Input** | Path to FpML XML (required). |
| **LLM** | **OpenRouter** is the default provider (same as `generate-java`). Uses the existing [`openrouter_client.py`](fpml_cdm/java_gen/openrouter_client.py) HTTP client (shared or copied to a common location). Flags: `--provider` (openrouter / openai), `--api-key` (or `OPENROUTER_API_KEY` env), `--model`. |
| **Agent config** | `MappingAgentConfig` knobs exposed: `--max-iterations`, `--max-tool-calls`, `--timeout`, `--no-improve-limit`, Rosetta toggles (see §13). |
| **Outputs** | **CDM JSON** (default path under `--output-dir`, e.g. `mapping_output_cdm.json` or user-specified `--output-cdm`); optional **normalized** JSON if `--write-normalized` is set; **trace JSON** via `--trace-output` (same shape as today: envelope with `trace` + `result` / `mapping_result.to_dict()`), so tooling can diff traces across runs. |
| **Stderr** | Progress lines (optional `--quiet`); final line summary: adapter/skill id, error counts, output paths, exit code non-zero on hard failure. |
| **Exit code** | 0 when run completes and agent returns a result object; non-zero on infra/parse/CLI errors; optionally non-zero if validation/coverage thresholds fail (flag-gated). |
| **Baseline comparison** | Optional `--baseline` flag: also run deterministic parse→transform, write its CDM JSON alongside agent output, and log a diff summary (error count delta). Useful for regression / oracle comparison without requiring a separate `convert` invocation. |

**Parity with Java agent UX (`generate-java`):**

- `--trace-output` file with full agent trace for debugging and regression tests.
- Optional verbosity aligned with [`cmd_generate_java`](fpml_cdm/cli.py) / `generate-java-from-fpml` conventions so muscle memory transfers between commands.

**Explicit non-goals for this command:**

- Does **not** compile or run Java.
- Does **not** invoke `_apply_mapping_compliance_stage` — this is a raw agent run; compliance wrapper is for pipeline use.

**Open decisions (CLI):**

1. **Command name:** `run-mapping-agent` vs `map-fpml` vs `generate-cdm` — pick one namespace; avoid clashing with existing `convert`/`generate-java-from-fpml`.
2. **Default output directory:** e.g. `tmp/` vs cwd — match `generate-java-from-fpml` `--output-dir` default.
3. **Trace envelope:** whether to mirror `generate-java` exactly (`{"trace": ..., "result": ...}`) or nest under `mapping` for consistency with existing `mapping_trace.json` written by `generate-java-from-fpml` — document in implementation for test stability.

---

## 8. Official documentation and schemas (skill authoring)

Skills MUST cite and be aligned to authoritative sources. Primary references:

### 8.1 FpML

- **FpML home and standard:** [https://www.fpml.org/](https://www.fpml.org/)
- **Current / recent specifications:** [https://www.fpml.org/the_standard/current/](https://www.fpml.org/the_standard/current/)
- **Specific release pages** (e.g. 5.13 REC, 5.14 WD): under `https://www.fpml.org/spec/...` (some pages require login for full HTML docs — plan for downloadable REC packages for offline authoring).
- **Coding schemes / reference data:** [https://www.fpml.org/reference-data/](https://www.fpml.org/reference-data/) and machine-readable catalogs as published by FpML.

Authoring workflow: for each skill, maintain a **"Sources"** subsection listing FpML version, view (confirmation / pretrade / etc.), and schema modules that define the product subtree.

### 8.2 CDM v6 (ISDA / FINOS)

- **CDM documentation site:** [https://cdm.finos.org/](https://cdm.finos.org/)
- **Repository (releases, samples, Rosetta source):** [https://github.com/finos/common-domain-model](https://github.com/finos/common-domain-model) — pin **v6.x** tag matching this project's CDM alignment (e.g. [6.0.0 release notes](https://github.com/finos/common-domain-model/releases/tag/6.0.0) for v6 baseline narrative).
- **ISDA CDM overview (governance / licensing context):** [https://www.isda.org/isda-solutions-infohub/cdm/](https://www.isda.org/isda-solutions-infohub/cdm/)
- **Rosetta Design** (optional for contributors exploring model): [https://rosetta-technology.io/design](https://rosetta-technology.io/design)

Skills should reference **CDM v6** JSON serialization rules (e.g. choice types / capitalization changes noted in CDM 6.0 release materials) where they affect mapping examples.

### 8.3 Repo-local CDM JSON Schema

This project already vendors generated JSON Schema under [`schemas/jsonschema/`](schemas/jsonschema/). Use for:

- Concrete `lookup_*` tools,
- CI validation,
- examples in skills.

Do **not** treat the vendored copy as the legal "spec"; **version-pin** it to the same CDM release as Rosetta / validator JAR.

### 8.4 FpML ↔ CDM mapping hints in CDM

The FINOS CDM distribution includes **synonym / FpML-related** modelling work (see CDM release notes for "FpML mappings" items). Skills should cross-check those when proposing ruleset candidate paths.

---

## 9. Skill file standard (authoring checklist)

Each `SKILL.md`:

```yaml
---
name: fx-forward-like
description: Use when trade contains fxSingleLeg, forward, or NDF-style structures under FpML confirmation view.
adapter_ids: ["fxForward", "fxSingleLeg"]
cdm_target: "6.x"
fpml_profile: "5.13+ confirmation (example)"
version: "0.1.0"
---
```

Body sections (recommended):

1. **When to use** — classifier cues; conflicts with other skills.
2. **FpML scope** — elements/attributes in scope; canonical paths.
3. **CDM target shape** — primary `trade` / `product` qualifiers; pointers to CDM doc sections.
4. **Ruleset strategy** — typical patch patterns, ordering of candidates, dangerous patches to avoid.
5. **Coverage & ignores** — default ignore table + escalation rules.
6. **Validation interpretation** — how to read schema vs semantic errors from this codebase.
7. **Sources** — links to FpML spec pages, CDM doc pages, release tags.
8. **Examples** — minimal anonymized fragments only; no proprietary data.

Optional: `references/` subfolder for pasted excerpts **with attribution** (respect FpML/FINOS copyright and license terms).

---

## 10. Phased delivery

| Phase | Deliverable |
|-------|-------------|
| **P0a** ✅ | **Standalone CLI** `run-mapping-agent` with `--trace-output`, CDM + trace artifacts, OpenRouter client, `finish` tool, `MappingAgentConfig` flags, 7-tool registry (incl. `fpml_coverage_report`). |
| **P0b** ✅ | Skill store (`mapping_agent/skills/`): 3 FX skills. Classifier v1 (rules-based XML local names, pre-loop). Prompt builder (base system + injected skill body). Trace: `skill_id`, `skill_version`, `classifier_result`. |
| **P1** ✅ | Coverage engine (`coverage.py`) + `fpml_coverage_report` tool. Post-loop coverage computation. `best_coverage_gaps` in `MappingAgentResult`. |
| **P2** ✅ | Pipeline compliance object extended: `coverage_gaps`, `skill_id`, `skill_version`. Always-on when LLM configured. |
| **P3** | Additional product skills + adapters/rulesets (IRD, equity, …) prioritized by fixture backlog; generalize `run_conversion_with_patch` dispatch (§3.3). |
| **P4** | Optional schema fragment lookup tool shared with java_gen index patterns; evaluation harness, golden traces, prompt budget management. |

---

## 11. Testing and evaluation

- **Unit tests:** skill loader, classifier, coverage engine, patch application (no network).
- **Integration tests:** mocked LLM with scripted tool calls (pattern from [`tests/test_java_gen/test_agent.py`](tests/test_java_gen/test_agent.py)); **CLI smoke** for the standalone mapping command (trace file written, exit code) without Java.
- **Regression:** compare agent CDM vs deterministic CDM on fixtures where deterministic succeeds — not as gate for shipping, but as **drift detector**. Log differences in error counts and coverage for trend monitoring.
- **Metrics:** coverage %, error counts, iterations, token estimates, skill routing accuracy (confusion matrix).

---

## 12. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Wrong skill routed | Abstain threshold; generic fallback skill; trace confidence; human-readable classifier output. |
| Token overflow | Progressive skill loading (future); compact tool result summaries; payload externalization for oversized results; optional pre-send budget (§3.5). |
| License on spec excerpts | Link out + short quotes; full copies only where license permits; prefer org-internal mirrors. |
| "Always agent" cost | Caching classifier results; cap iterations; optional fast path flag for dev only (documented). |
| Drift vs deterministic baseline | Agent may produce valid-but-different CDM from deterministic path on cases where deterministic already succeeds. Regression tests (§11) log diffs; `--baseline` flag on CLI (§7.4) enables side-by-side comparison. Not a shipping gate, but monitored. |

---

## 13. Open decisions (status)

1. **Ignored FpML nodes:** require explicit ruleset `ignore` entries vs blocking completion. *(Open — P3+)*
2. **Single vs multi-skill:** allow composing e.g. `base` + `ird-swaption` or strictly one skill per run. *(Open — currently single skill per run)*
3. **Rosetta:** always on in production compliance or configurable per environment. *(Resolved: configurable via `--no-rosetta` flag; on by default in pipeline compliance)*
4. **FpML version:** single supported FpML REC for v1 skills vs version matrix in frontmatter. *(Open — P3+)*
5. **Standalone command name** (§7.4): *(Resolved: `run-mapping-agent`; trace envelope: `{"trace": ..., "result": ...}` matching `generate-java`)*
6. **OpenRouter client location:** *(Resolved: imported from `java_gen/openrouter_client.py` — single source of truth; move to common deferred)*

---

## 14. References (external)

- FpML: [https://www.fpml.org/](https://www.fpml.org/)
- FpML current standard index: [https://www.fpml.org/the_standard/current/](https://www.fpml.org/the_standard/current/)
- CDM docs: [https://cdm.finos.org/](https://cdm.finos.org/)
- CDM GitHub: [https://github.com/finos/common-domain-model](https://github.com/finos/common-domain-model)
- ISDA CDM InfoHub: [https://www.isda.org/isda-solutions-infohub/cdm/](https://www.isda.org/isda-solutions-infohub/cdm/)
- Agent Skills (Anthropic): [https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills/overview](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills/overview)
- Cursor Agent Skills: [https://www.cursor.com/docs/context/skills](https://www.cursor.com/docs/context/skills)

---

*Document version: 2.0 — April 2026 — P0a/P0b/P1/P2 implemented.*

*Changelog:*
- *2.0 — Implementation complete for P0a, P0b, P1, P2. New files: `skill_store.py`, `classifier.py`, `prompt_builder.py`, `coverage.py`, 3 FX skill SKILL.md files, `run-mapping-agent` CLI subcommand. Agent loop rewritten with finish tool, skill injection, coverage computation, 7-tool registry. Pipeline compliance extended with coverage_gaps/skill_id/skill_version. 42 new tests (all passing). Open decisions §5 and §6 resolved.*
- *1.2 — Review fixes: (1) §3.3 documents FX-only execution layer + extension seam + unsupported product behavior; (2) §3.2 picks injection as P0 skill loading, defers tool-based loading; (3) §3.3 clarifies classifier is pre-loop, not a tool; (4) §3.5 adds context window / prompt budget plan; (5) §4 architecture diagram + classifier updated; (6) §4.2 scoring reordered — coverage deferred to P1, P0 uses existing triple; (7) §5 adds `finish` tool, defers `load_mapping_skill`; (8) §7.3 addresses `_apply_mapping_compliance_stage` double-run risk; (9) §7.4 adds `--baseline` flag, OpenRouter as default, client location; (10) §9 adds `adapter_ids` to SKILL.md frontmatter; (11) §10 splits P0 into P0a/P0b; (12) §12 adds drift risk; (13) §13 adds OpenRouter client location decision.*
- *1.1 — Added §7.4 standalone mapping-agent CLI (mapping-only, trace + CDM output, Java parity); P0 and testing updates; open decision on command name.*
