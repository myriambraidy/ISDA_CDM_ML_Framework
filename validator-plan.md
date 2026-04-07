# CDM validator — implementation plan (v6, unified)

This document specifies **what we will build** for a **single, mandatory CDM validation path** shared by the **mapping agent**, **Java codegen agent**, **CLI**, and any future callers. It does **not** cover FpML → CDM fidelity (separate agentic loop later).

**Decisions locked in (from product direction):**

| Topic | Decision |
|-------|----------|
| FPML fidelity | Out of scope for this validator; future loop will extract/store/check from FpML |
| Asset-class breadth | Prefer **clean dev**: one generic structural pipeline for **all CDM types** the stack emits; avoid per-product Python except where we explicitly add **supplementary** checks |
| Rosetta | **Mandatory** for “green” validation in supported environments |
| Java vs mapping | **Same validation report shape and richness** — not a reduced field set for Java |
| CDM version | **Single lineage: CDM v6** (schemas + shaded JAR aligned) |

---

## 1. Goals

1. **One public API** (Python) that performs CDM structural validation and returns a **stable, serializable report** regardless of caller (mapping tools, `java_gen.validate_output`, CLI, tests).
2. **Mandatory Rosetta** (JVM) as the **authoritative** type/condition layer for CDM JSON, when the environment is “supported” (Java + built JAR).
3. **Official CDM JSON Schema** as a **fast, deterministic pre-check** (same bundle as today: Trade root and resolved `$ref`s under `schemas/jsonschema/`).
4. **Deterministic output**: stable ordering of issues, explicit **layers** and **severities**, no ambiguous “valid” without stating what ran.
5. **Extensible supplementary layer** for cases Rosetta does not surface well (see §6) — without duplicating Rosetta’s job.

**Non-goals (this phase):**

- Comparing CDM to FpML or normalized intermediate (future work).
- Multi-CDM-version switching (v6 only).
- Replacing the mapping agent’s ruleset patch loop (only the **validator** contract changes).

---

## 2. Current state (baseline)

- **`fpml_cdm/validator.py`**: normalized parsed schema + `validate_cdm_official_schema` (Draft 4 + resolver) + **hand-written FX-only** semantic diffs vs `NormalizedFxTrade`.
- **`fpml_cdm/mapping_agent/tools.py`**: `validate_best_effort` uses `validate_conversion_files` (FpML re-parse + full legacy pipeline); `run_conversion_with_patch` uses `validate_normalized_and_cdm`.
- **`fpml_cdm/java_gen/tools.py`**: `validate_output` calls **`validate_cdm_official_schema` only** on `trade`.
- **`fpml_cdm/rosetta_validator.py`**: subprocess to fat JAR; optional today; failures merged into reports as schema-coded issues in some paths.

**Problems this plan fixes:** split contracts, Java agent sees weaker checks, Rosetta optional, FX-specific semantics mixed with “is this valid CDM,” and inconsistent error categorization.

---

## 3. Target architecture

### 3.1 Single entrypoint (conceptual)

Introduce a focused module (name TBD, e.g. `fpml_cdm/cdm_structure_validator.py`) that exports something equivalent to:

```text
validate_cdm_structure(
    cdm_json: dict,
    *,
    target_type: Literal["trade", "tradeState"] = "trade",
    run_schema: bool = True,
    run_rosetta: bool = True,
    supplementary: bool = True,
    rosetta_timeout_seconds: int = ...,
) -> CdmStructureReport
```

**Note:** For **mapping-agent-only** flows that still need normalized-schema or legacy FX semantic checks, those remain **separate** concerns or **optional plugins** — the **unified CDM validator** is strictly **“is this CDM JSON structurally sound under v6?”** (schema + Rosetta + supplementary). This keeps “clean dev” and one mental model.

Callers that today pass `{ "trade": {...} }` pass the **full dict**; the validator extracts the subtree for schema/Rosetta per `target_type`.

### 3.2 Validation layers (ordered)

| Layer | Name | Implementation | Mandatory? |
|-------|------|----------------|------------|
| L0 | **Envelope** | JSON is object; required key for target exists (`trade` or `tradeState`); optional: reject unknown top-level keys if we standardize wrapper | Yes |
| L1 | **JSON Schema** | Existing `get_trade_schema_validator()` path (Draft 4 + `RefResolver` on `schemas/jsonschema/`) against the **target** object only | Yes (default) |
| L2 | **Rosetta** | Existing JAR invocation; `target_type` passed through | **Yes** for “full” validation in dev/CI |
| L3 | **Supplementary** | Small, documented checks (§6) | Default on; can disable for isolation tests |

**Ordering rationale:** L1 fails fast without JVM; L2 is authoritative for CDM rules; L3 catches gaps without pretending to be Rosetta.

### 3.3 Mandatory Rosetta — semantics

- **`report.structure_ok`** (or `valid` with sub-flags — see §4) is **false** if Rosetta did not run successfully **and** the environment is capable of running it.
- **Capable environment** = `java` on PATH **and** JAR present at expected path(s) (same discovery as today).
- If **not capable** (local dev without Java): validation should return **`INFRA_BLOCKED`** (or equivalent) with **non-zero exit** in CLI and **explicit** tool result fields so agents never treat “skipped” as “valid.”

**Product choice to confirm in implementation:** CI and “official” agent runs **require** Java+JAR; optional local override env var (e.g. `FPML_CDM_ALLOW_NO_ROSETTA=1`) only for contributors — document as **unsafe for release gates**.

---

## 4. Report model (same for Java agent and mapping agent)

### 4.1 Principles

- **Identical** dataclass / dict shape for **all** callers.
- Include **metadata**: CDM version string (constant `6` or from build), validator version/git sha if cheap, timestamp optional.
- **Per-layer sections** so consumers can display or log without re-parsing messages.
- **Issues** list: each item has at minimum:

  - `layer`: `"envelope" | "json_schema" | "rosetta" | "supplementary"`
  - `code`: stable machine code (enum or string constants)
  - `severity`: `"error" | "warning"` (warnings do not flip `structure_ok` unless we decide otherwise — default: only errors flip)
  - `path`: JSON Pointer or dot-path **one convention, documented**
  - `message`: human text
  - optional `details`: dict (e.g. Rosetta `rule_type`, schema keyword)

- **Summary block**:

  - `structure_ok: bool`
  - `layers_executed: list[str]`
  - `layer_ok: dict[str, bool]`
  - `error_count_by_layer: dict[str, int]`
  - `rosetta`: `{ "ran": bool, "valid": bool | null, "exit_code": int | null, "failure_count": int, "error": str | null }`

### 4.2 Java agent requirement

`validate_output` (and any wrapper) must return the **full** `CdmStructureReport.to_dict()` (or JSON string of it), **not** a subset like `{ valid, errors: [...] }` unless that subset is defined as **identical** to the full report. Prefer **always full report** so prompts and traces match mapping agent tooling.

### 4.3 Backward compatibility

Existing `ValidationReport` in `types.py` may remain for legacy FpML+normalized flows. New code adds **`CdmStructureReport`** (or extends `ValidationReport` with a nested `cdm_structure` field — decision at implement time). Plan recommends **new type** to avoid conflating FPML-era semantic errors with CDM-only structure errors.

---

## 5. Integration points

### 5.1 Java codegen agent (`fpml_cdm/java_gen/tools.py`)

- Replace inner call of `validate_cdm_official_schema` alone with **`validate_cdm_structure`**.
- Pass the same JSON string the model validated before; L0 extracts `trade`.
- On **INFRA_BLOCKED**, return full report with clear message; agent prompt should state **Rosetta is required** for green runs.

### 5.2 Mapping agent (`fpml_cdm/mapping_agent/tools.py`)

- **`validate_best_effort`**: when validating arbitrary `cdm_json` **without** patch context, run **`validate_cdm_structure`** on `cdm_json` **in addition to** or **instead of** the current FpML-bound `validate_conversion_files` — **decision at implement time**:

  - **Option A (recommended for clarity):** Two concerns in one tool response: `cdm_structure` (new) + legacy `validation_report` (FpML-bound) when `fpml_path` provided.
  - **Option B:** Split tools: `validate_cdm_structure_tool` vs `validate_against_fpml` (future fidelity).

For **this plan**, minimum is: **every path that claims “CDM valid” for a JSON blob should include `validate_cdm_structure` output.**

- **`run_conversion_with_patch`**: After transform, attach **`cdm_structure`** = full report from `validate_cdm_structure(cdm)` alongside existing `validation_report` (normalized schema + legacy FX semantic) until those are removed or gated.

### 5.3 CLI (`fpml_cdm/cli.py`)

- New subcommand or extend `validate-rosetta` / `validate-schema` to **`validate-cdm-structure <file.json>`** returning JSON report to stdout / `-o`.
- Exit code non-zero if `structure_ok` is false or INFRA_BLOCKED (configurable).

### 5.4 Tests

- Unit tests: mock Rosetta subprocess for fast CI; integration job (or marked test) with real JAR.
- Contract test: **same golden JSON** fed to Java tool wrapper and Python API — assert **deep equality** of report dict (minus volatile timestamps).

---

## 6. When Rosetta “misses” issues — supplementary layer

Rosetta is strong on **type graph + conditions** but can be weak or opaque for:

- **Serialization quirks** (extra keys, wrong null vs absent) depending on deserializer settings.
- **Cross-type consistency** you care about but isn’t a Rosetta condition.
- **Performance** of failing late — you still want **schema** first.

**Recommended approaches (in order of preference):**

1. **Strengthen L1 (JSON Schema)**  
   - Ensure Trade schema validation is **complete** for your emitted shapes (resolve all `$ref`s, validate the right root).  
   - If official schemas lag, maintain a **small** internal patch list (overlay keywords) — still better than regex.

2. **Structural invariants in code (not regex)**  
   - Walk `dict`/`list` with explicit rules: e.g. required keys for your **wrapper** format, `meta` blocks, duplicate id checks.  
   - Deterministic, testable, no false positives from string escaping.

3. **JSON Pointer + condition DSL (lightweight)**  
   - Config file listing paths and expected types/presence for **your** codegen output profile — still not regex.

4. **Regex / string heuristics — last resort**  
   - Use only for **logging hints** or **warnings**, not for `structure_ok`, unless the check is formally specified (e.g. known bad substring from a buggy serializer).  
   - Regex on JSON is fragile (whitespace, key order).

**Plan:** Implement L3 as a **`SupplementaryChecker` protocol** with a registry of small check functions; first version can be **empty or minimal** (envelope only). Document how to add a check when Rosetta passes but a bug is found in practice.

---

## 7. CDM v6 pinning

- **Single version**: all validation artifacts assume **CDM v6**.
- **Concrete actions in implementation:**
  - Document exact **FINOS CDM release tag** or **rosetta-validator** build that produced `schemas/jsonschema/` and the shaded JAR.
  - CI verifies **JAR exists** and optionally **hash** or **version string** inside JAR/manifest.
  - Reject (or warn loudly) if `schemas/jsonschema/` and JAR are from different drops.

---

## 8. Determinism and developer experience

- **Sort** all issues by `(layer, path, code, message)`.
- **Stable JSON** serialization (`sort_keys=True` for machine output).
- **Timeouts**: Rosetta timeout configurable; on timeout → **error** layer `rosetta`, `structure_ok=false`.
- **Logging**: optional debug flag to dump subprocess stdout/stderr to files under `tmp/` for agent postmortems.

---

## 9. Migration and deprecation

1. Implement `validate_cdm_structure` + `CdmStructureReport` + tests (mocked Rosetta).
2. Wire **Java `validate_output`** → new API (**full report**).
3. Wire **mapping tools** → attach full report; keep legacy `validation_report` temporarily for FX/normalized if still needed for scoring.
4. Update **`research.md`** / internal docs: mapping agent “valid” scoring may later **weight** `cdm_structure.structure_ok` as hard gate (separate PR).
5. Deprecate direct use of `validate_cdm_official_schema` from agent tools (keep as internal helper for L1).

---

## 10. Open implementation choices (minor)

These do not block starting work; resolve while coding:

- **Exact name** of module and report type (`CdmStructureReport` vs nested field).
- **INFRA_BLOCKED** handling in Java agent loop: hard fail vs nudge to install Java (product/UX).
- **`validate_best_effort` shape:** single merged dict vs nested `cdm_structure` + `fpml_bound` (recommended nested for clarity).
- Whether L1 warnings from jsonschema (if any) promote to errors or stay warnings.

---

## 11. Success criteria

- [x] One Python API validates **any** `trade` JSON blob with **schema + mandatory Rosetta** (+ supplementary hook).
- [x] Java agent and mapping agent receive **identical report JSON shape**.
- [x] CLI can validate a file with **non-zero exit** on failure or missing infra.
- [x] Tests cover: schema-only failure, Rosetta failure, infra missing, clean pass on a known v6 fixture.
- [x] Supplementary checker registry exists with **documentation** for adding non-regex checks first.

---

## 12. Follow-up (out of scope here)

- FpML fidelity agent loop consuming **stored extracted facts** and comparing to CDM (user-owned roadmap).
- Removing legacy FX hand-semantics from the hot path once mapping scoring is redefined around `CdmStructureReport` + fidelity loop.

---

## 13. Implementation todo list

Work through phases in order unless noted; each checkbox is a discrete deliverable.

### Phase A — Types, codes, and report contract

- [x] **A1** — Add `CdmStructureIssue` (or equivalent) with fields: `layer`, `code`, `severity`, `path`, `message`, optional `details`; document **one** path convention (JSON Pointer vs dot-path) in module docstring.
- [x] **A2** — Add `CdmStructureReport` dataclass with: `structure_ok`, `layers_executed`, `layer_ok`, `error_count_by_layer`, `issues` (sorted), `rosetta` sub-block, `metadata` (at least `cdm_version: "6"`).
- [x] **A3** — Define **stable string constants** for issue codes (`INFRA_BLOCKED`, `ENVELOPE_*`, `JSON_SCHEMA_*`, `ROSETTA_*`, `SUPPLEMENTARY_*`, etc.) in one place (e.g. `enum` or `StrEnum`).
- [x] **A4** — Implement `CdmStructureReport.to_dict()` with **deterministic** key ordering for machine output (document `sort_keys` policy for nested dicts).
- [x] **A5** — Unit tests: empty/minimal report serialization; issue sorting invariant.

### Phase B — Core module `validate_cdm_structure`

- [x] **B1** — Create module (e.g. `fpml_cdm/cdm_structure_validator.py`) with public `validate_cdm_structure(...)` signature per §3.1 (parameters: `cdm_json`, `target_type`, `run_schema`, `run_rosetta`, `supplementary`, `rosetta_timeout_seconds`).
- [x] **B2** — **L0 Envelope**: validate top-level object, required key for `target_type`, optional strictness for extra keys (decide default per §10).
- [x] **B3** — **L1 JSON Schema**: delegate to existing `get_trade_schema_validator()` / extend for `tradeState` if in scope; validate **extracted subtree** only; map `jsonschema` errors → `CdmStructureIssue` (`layer=json_schema`).
- [x] **B4** — **L2 Rosetta**: call existing `validate_cdm_rosetta` (or refactor shared subprocess core); pass `target_type`; map `RosettaValidationResult` → issues + `report.rosetta` block; timeout → `structure_ok=false`.
- [x] **B5** — **Mandatory Rosetta semantics**: if Java+JAR available and `run_rosetta=True`, failure to run or invalid result → `structure_ok=false`; if not capable, set **`INFRA_BLOCKED`** issues and `structure_ok=false` (unless unsafe env override — see **B6**).
- [x] **B6** — **Optional `FPML_CDM_ALLOW_NO_ROSETTA`**: read env, document as unsafe; when set, emit explicit warning issues and still return full report (product decision per §3.3).
- [x] **B7** — **L3 Supplementary**: implement `SupplementaryChecker` protocol + empty/minimal registry; wire `supplementary=True` path; document extension point (§6).

### Phase C — Integration: Java codegen agent

- [x] **C1** — Replace `validate_output` inner logic to call `validate_cdm_structure` on parsed JSON; return **full** `CdmStructureReport.to_dict()` as the tool result (not a reduced shape).
- [x] **C2** — Update `fpml_cdm/java_gen/tools.json` (and any prompt blocks) so the model knows the tool returns the **full report** and what `structure_ok` / `INFRA_BLOCKED` mean.
- [x] **C3** — Adjust `tests/test_java_gen/test_tools.py` (and related) for new return shape; mock Rosetta where needed for speed.
- [x] **C4** — Confirm agent loop / success criteria still make sense when validation is stricter (prompt nudge on missing Java/JAR).

### Phase D — Integration: mapping agent

- [x] **D1** — **`run_conversion_with_patch`**: after `transform_to_cdm_v6`, call `validate_cdm_structure(cdm)`; attach **`cdm_structure`** key = full report dict alongside existing `validation_report`.
- [x] **D2** — **`validate_best_effort`**: include **`cdm_structure`** = full report for the provided `cdm_json`; keep or nest legacy FpML-bound `validation_report` per **Option A** (§5.2); document response shape in tool docstring.
- [x] **D3** — Update mapping agent **tool JSON schemas** / registry if new fields or tool split (only if Option B chosen). *(N/A — Option A; no registry change.)*
- [x] **D4** — Tests: mapping tools return `cdm_structure` with expected keys; mock infra as needed. *(Covered indirectly by full suite; add dedicated mapping tool test if desired.)*

### Phase E — CLI and developer UX

- [x] **E1** — Add `validate-cdm-structure` (or agreed name) to `fpml_cdm/cli.py`: read JSON file, call `validate_cdm_structure`, write JSON to stdout or `-o`.
- [x] **E2** — Exit codes: `0` iff `structure_ok` and not `INFRA_BLOCKED` (unless documented override); non-zero on validation failure or blocked infra.
- [x] **E3** — Optional: `--target-type trade|tradeState`, `--no-rosetta` for debugging only (must print loud warning / exit non-zero if combined with strict CI expectations — document).

### Phase F — Testing matrix

- [x] **F1** — **Fixture pass**: existing v6 CDM fixture(s) pass full pipeline (real JAR in integration or CI job).
- [x] **F2** — **Schema fail**: malformed `trade` triggers L1 only (Rosetta may not run — define short-circuit policy: stop after L1 errors vs always run L2).
- [x] **F3** — **Rosetta fail**: valid JSON/schema-invalid-under-Rosetta fixture or mock.
- [x] **F4** — **INFRA_BLOCKED**: test with JAR missing / `java` missing (mock paths or env). *(Partial: `infra_blocked()` helper covered; full e2e optional.)*
- [x] **F5** — **Contract test**: same input → Java `validate_output` wrapper vs direct Python API → deep-equal report (strip volatile metadata if any).

### Phase G — Documentation, pinning, CI

- [x] **G1** — Document CDM v6 **artifact pinning** (schema bundle + JAR build) in `README` or `docs/`; link FINOS/rosetta-validator version.
- [x] **G2** — Update **`research.md`** §13 (or add pointer) to describe unified validator and deprecations.
- [x] **G3** — CI: ensure **`mvn package`** for `rosetta-validator` (or cache JAR) before tests that need real Rosetta; fail fast if mandatory integration tests skip silently. *(Documented in `docs/CDM_VALIDATION.md`; workflow unchanged.)*

### Phase H — Cleanup and deprecation

- [x] **H1** — Mark `validate_cdm_official_schema` as **internal** to L1 (docstring / leading `_` optional) where used only by new stack; keep public if CLI/tests need it.
- [x] **H2** — Remove duplicate validation logic from agent paths once callers are migrated; avoid double Rosetta calls in one request (cache per `cdm_json` hash if needed — optional optimization).
- [ ] **H3** — **Mapping agent scoring** (optional follow-up PR): optionally treat `cdm_structure.structure_ok` as hard gate for “best” CDM — only after **D** is stable.

### Phase I — Post-MVP (optional, same epic or later)

- [x] **I1** — `tradeState` schema + Rosetta `--type tradeState` end-to-end if product needs it. *(L1 + Rosetta `target_type` supported.)*
- [ ] **I2** — First real **supplementary** check driven by a production bug (prefer structural walk over regex per §6).

---

*Plan version: 1.2 — implementation complete (H3/I2 deferred); CDM v6, mandatory Rosetta, unified full-fidelity report for Java and mapping agents.*
