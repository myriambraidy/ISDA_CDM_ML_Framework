# Java CDM code generator — implementation plan for tooling fixes

**Implementation status: completed** (2026-03-28). Fixes F1–F6 and F2 phase 2a+2b are in `fpml_cdm/java_gen/`; tests in `tests/test_java_gen/`.

This document describes the **current pipeline**, the **defects** we agreed to fix, and a **step-by-step implementation plan** with code sketches. It is intended for engineers implementing changes in `fpml_cdm/java_gen/` and related tests.

---

## 1. Pipeline architecture (as implemented today)

### 1.1 Entry points

| Entry | Role |
|--------|------|
| `fpml_cdm.java_gen.agent.run_agent()` | Main ReAct loop: OpenAI-style `chat.completions` + tool calls until `finish`, timeout, max iterations, or max tool calls. |
| `fpml_cdm.fpml_to_cdm_java.generate_java_from_fpml()` | FpML → CDM JSON (deterministic + optional mapping agent) → writes `generated_expected_cdm.json` → `run_agent()` on that path. |
| `scripts/run_fpml_mapping_to_java.py` | CLI orchestration; optional `--java-trace-output`. |
| `fpml_cdm.cli` | Subcommands that eventually call the same agent. |

`run_agent()` always calls `set_java_generation_target(cdm_json_path=..., class_name=...)` first, then `_run_agent_impl()`.

### 1.2 Agent loop (`fpml_cdm/java_gen/agent.py`)

1. **Config** — `AgentConfig(max_iterations=20, max_tool_calls=50, timeout_seconds=600, match_threshold=95.0)`.
2. **Template priming** — `get_java_template()` then `write_java_file(template)` so patch-based flows see placeholders (`IMPORTS_PLACEHOLDER`, `BUILDER_CODE_PLACEHOLDER`).
3. **Messages** — `SYSTEM_PROMPT` + user message with CDM path, class name, output path rules.
4. **Per iteration** — Enforce `timeout_seconds` and `max_tool_calls` *before* waiting on the LLM. LLM returns `tool_calls`; each is executed via `_execute_tool()` → JSON string appended as tool role content. Trace stores truncated previews (500 chars).
5. **Deterministic hooks** — After a successful `compile_java`, the loop injects `run_java` without LLM. If `run_java` exits 0 in that iteration, a user nudge asks the model to call `finish`.
6. **Termination** — `finish` tool → `AgentResult`; else exhaustion via timeout / max tool calls / max iterations → `_agent_result_exhausted()` (may still mark success if trace shows successful `run_java`).

**Scaling:** `_run_agent_impl` runs a pre-flight `inspect_cdm_json`, then `scale_java_gen_config_for_node_count` raises `max_iterations` / `max_tool_calls` / `timeout_seconds` for large `total_nodes` (using `dataclasses.replace`, preserving `match_threshold` and higher user limits).

### 1.3 Tool dispatch (`TOOL_DISPATCH` in `agent.py`)

All tools live in `fpml_cdm/java_gen/tools.py` and return **plain `dict`s** serialized to JSON for the LLM.

| Tool | Implementation | Notes |
|------|----------------|--------|
| `inspect_cdm_json` | Walks `trade` subtree, resolves child types via parent schema `$ref` | Also `well_known_imports`, `reference_pattern_total` / `reference_patterns_sample` / `reference_api_note` |
| `lookup_cdm_schema` | Loads schema by type name | `setter_hint` + `setter_note`; `address`→`setReference`; `builder_reference_note` on ReferenceWithMeta* |
| `list_enum_values` | `enum_json_value_java_identifier` (oneOf titles + sanitized fallback) | `enum_constant_warning` when JSON values have punctuation |
| `get_java_template` / `write_java_file` / `read_java_file` / `patch_java_file` | File I/O under `rosetta-validator/generated/` | |
| `compile_java` / `run_java` | `javac` / `java` subprocess vs shaded JAR | |
| `validate_output` / `finish` | Schema / terminal | |

### 1.4 Schema index (`fpml_cdm/java_gen/schema_index.py`)

- Indexes `schemas/jsonschema/*.schema.json` by `title` → file.
- **Enums:** `type == "string"` and top-level `"enum": [...]` → `enum_values()` returns raw JSON strings.
- **Java FQCN:** Derived from schema file naming convention (`java_class_parts`, `schema_ref_to_java_class`).
- **`_camel_to_screaming_snake`:** Sanitizes non-alphanumeric characters, then camel→snake; used as fallback when `oneOf` titles are absent.
- **`enum_json_value_java_identifier`:** Prefers `oneOf` branch `title` (via `_camel_to_screaming_snake(title)`), else sanitized JSON value.

### 1.5 LLM tool specs (`fpml_cdm/java_gen/tools.json`)

OpenAPI-style descriptions for each tool. **Updating** descriptions when return shapes change (`well_known_imports`, `reference_patterns`, warnings) reduces model confusion.

### 1.6 Tests

- `tests/test_java_gen/test_tools.py` — `inspect_cdm_json`, `lookup_cdm_schema`, `list_enum_values`, etc.
- `tests/test_java_gen/test_agent.py` — agent wiring, tool lists.

---

## 2. Problem → fix mapping

| ID | Problem | Primary fix location | Status |
|----|---------|----------------------|--------|
| F1 | JSON `address` → wrong `setter_hint` (`setAddress`); Rosetta builders use `setReference` / `setGlobalReference` / `setExternalReference` | `tools.py` — `lookup_cdm_schema` | Done |
| F2 | Enum JSON values like `ACT/360` → invalid Java identifiers in `list_enum_values` | `schema_index.py` + `tools.py` (`list_enum_values` warnings); oneOf title + sanitize fallback | Done |
| F3 | LLM omits intermediate `.build()` in nested builders | `agent.py` — `SYSTEM_PROMPT` | Done |
| F4 | Easy to miss imports for common CDM / JDK types | `tools.py` — `inspect_cdm_json` return payload | Done |
| F5 | Large trades exhaust iteration/time budget | `agent.py` — pre-flight scaling + `dataclasses.replace` | Done |
| F6 | Instance-level reference fields (`globalReference`, …) need explicit API reminder | `tools.py` — `inspect_cdm_json` (total + capped sample) | Done |

---

## 3. Fix F1 — `lookup_cdm_schema` setter hints for references

### 3.1 Root cause

Current logic (approx. lines 340–343 in `tools.py`):

```python
if is_array:
    setter = f"add{prop_name[0].upper()}{prop_name[1:]}"
else:
    setter = f"set{prop_name[0].upper()}{prop_name[1:]}"
```

For property `address` with `$ref` → `com-rosetta-model-lib-meta-Reference.schema.json`, this yields **`setAddress`**, but generated builders expose **`setReference(com.rosetta.model.lib.meta.Reference)`** (verified via `javap` on `rosetta-validator-1.0.0.jar`).

### 3.2 Recommended behaviour

1. **`address`** when `ref == "com-rosetta-model-lib-meta-Reference.schema.json"` → `setter_hint`: **`setReference`**, plus a **short** `setter_note` on that property: e.g. *“JSON property `address` maps to Java `setReference(Reference)`; build with `Reference.builder().setScope(...).setValue(...).build()`.”*
2. **`globalReference`** → `setGlobalReference` (mechanical already matches; explicit override is fine for clarity).
3. **`externalReference`** → `setExternalReference`.

Optional **`builder_reference_note`** on the top-level `lookup_cdm_schema` response when `type_name` or schema `title` contains **`ReferenceWithMeta`**: one sentence that **there is no `setAddress`** and to use the three methods above.

### 3.3 Implementation sketch

Add a small helper in `tools.py` (module level or near `lookup_cdm_schema`):

```python
_REFERENCE_SCHEMA = "com-rosetta-model-lib-meta-Reference.schema.json"

def _setter_hint_for_property(
    prop_name: str,
    *,
    is_array: bool,
    ref: Optional[str],
) -> tuple[str, Optional[str]]:
    """Return (setter_hint, optional setter_note)."""
    if not is_array:
        if prop_name == "address" and ref == _REFERENCE_SCHEMA:
            return (
                "setReference",
                "JSON 'address' → Java setReference(com.rosetta.model.lib.meta.Reference); "
                "not setAddress (does not exist).",
            )
        if prop_name == "globalReference":
            return ("setGlobalReference", None)
        if prop_name == "externalReference":
            return ("setExternalReference", None)
    if is_array:
        return (f"add{prop_name[0].upper()}{prop_name[1:]}", None)
    return (f"set{prop_name[0].upper()}{prop_name[1:]}", None)
```

In the property loop, extend each `properties[prop_name]` dict with optional `"setter_note"` when non-`None`.

After the loop, if `"ReferenceWithMeta"` in `schema.get("title", "")` or in `type_name`:

```python
result["builder_reference_note"] = (
    "ReferenceWithMeta* builders: use setGlobalReference, setExternalReference, "
    "or setReference(Reference) for JSON 'address'. There is no setAddress()."
)
```

### 3.4 Tests (`tests/test_java_gen/test_tools.py`)

- `lookup_cdm_schema("ReferenceWithMetaNonNegativeQuantitySchedule")` (or another `ReferenceWithMeta*` type from schema):
  - `properties["address"]["setter_hint"] == "setReference"`
  - assert `setter_note` present or `builder_reference_note` present on result.

---

## 4. Fix F2 — enum values → valid Java identifiers

### 4.1 Phase 2a — sanitize + leading digit (minimal)

In `schema_index.py`, extend `_camel_to_screaming_snake`:

```python
def _camel_to_screaming_snake(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if not sanitized:
        return ""
    result = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", sanitized)
    result = re.sub(r"([a-z])([A-Z])", r"\1_\2", result)
    result = re.sub(r"([A-Za-z])(\d)", r"\1_\2", result)
    result = re.sub(r"(\d)([A-Za-z])", r"\1_\2", result)
    result = result.upper()
    if result[0].isdigit():
        result = "_" + result
    return result
```

**Verify** against real Rosetta names:

- `"ACT/360"` → `ACT_360` → `DayCountFractionEnum.ACT_360`
- `"30/360"` → `_30_360`

### 4.2 `list_enum_values` warnings (`tools.py`)

After building `values`:

```python
import re

has_special = any(re.search(r"[^a-zA-Z0-9]", v["json_value"]) for v in values)
result: Dict[str, object] = { ... }
if has_special:
    result["enum_constant_warning"] = (
        "Some JSON enum values contain / or . etc.; constants were derived via sanitization. "
        "If compilation fails, verify against the shaded CDM JAR (javap) or Rosetta enum source."
    )
```

(Optional: also set when `_camel_to_screaming_snake(json_value)` contained characters before sanitize — if you split helpers.)

### 4.3 Phase 2b — `oneOf[].title` (recommended follow-up)

`DayCountFractionEnum.schema.json` has `enum: ["ACT/360", ...]` **and** `oneOf` branches with `"title": "ACT_360"` per value. **Authoritative** Java names match those titles.

**Plan:**

1. Add `SchemaIndex.enum_value_to_java_constant(schema_file: str) -> Dict[str, str]` that:
   - Parses `oneOf` entries with `enum: [single]` and `title` → map `json_value` → `title` (already valid SCREAMING_SNAKE).
   - Falls back to `_camel_to_screaming_snake(json_value)` for values not in the map.
2. `list_enum_values` uses that map to fill `java_constant`.

### 4.4 Tests

- New test: `list_enum_values("DayCountFractionEnum")` contains an entry for JSON `"ACT/360"` with `java_constant` ending in `.ACT_360` (after phase 2a or 2b).
- Ensure existing `CounterpartyRoleEnum` tests still pass (no slashes).

---

## 5. Fix F3 — builder nesting in `SYSTEM_PROMPT`

### 5.1 Location

`fpml_cdm/java_gen/agent.py`, inside `SYSTEM_PROMPT` string (after “CDM Date Types” or “Underlier” section).

### 5.2 Content sketch

```text
## CRITICAL: Nested builders — close each .builder() with .build()

Wrong (PartyIdentifier not closed before Party.setMeta — javac: ')' expected):
Party.builder()
    .addPartyId(PartyIdentifier.builder()
        .setIdentifier(FieldWithMetaString.builder().setValue("x").build())
    .setMeta(...)   // ERROR: still inside addPartyId(

Right:
Party.builder()
    .addPartyId(
        PartyIdentifier.builder()
            .setIdentifier(FieldWithMetaString.builder().setValue("x").build())
            .build()
    )
    .setMeta(...)
    .build()

Rule: after every nested Type.builder() chain passed to add*(...), call .build() on that nested builder before the parent’s next setter.
```

No Python logic change required beyond editing the string.

---

## 6. Fix F4 — `well_known_imports` in `inspect_cdm_json`

### 6.1 Goal

Expose a **curated** `simple_name → fully_qualified_name` map for types that appear constantly in generated Java but are easy to forget, including **rates** types implicated in IRS traces.

### 6.2 Implementation sketch (`tools.py`)

```python
WELL_KNOWN_IMPORTS: Dict[str, str] = {
    "MetaFields": "com.rosetta.model.metafields.MetaFields",
    "FieldWithMetaDate": "com.rosetta.model.metafields.FieldWithMetaDate",
    "FieldWithMetaString": "com.rosetta.model.metafields.FieldWithMetaString",
    "ReferenceWithMetaString": "com.rosetta.model.metafields.ReferenceWithMetaString",
    "Date": "com.rosetta.model.lib.records.Date",
    "Reference": "com.rosetta.model.lib.meta.Reference",
    "BigDecimal": "java.math.BigDecimal",
    "BusinessCenterEnum": "cdm.base.datetime.BusinessCenterEnum",
    "BusinessDayConventionEnum": "cdm.base.datetime.BusinessDayConventionEnum",
    "DayTypeEnum": "cdm.base.datetime.DayTypeEnum",
    "RollConventionEnum": "cdm.base.datetime.RollConventionEnum",
    "PeriodEnum": "cdm.base.datetime.PeriodEnum",
    "PeriodExtendedEnum": "cdm.base.datetime.PeriodExtendedEnum",
    "DayCountFractionEnum": "cdm.base.datetime.daycount.DayCountFractionEnum",
    "InterestRatePayout": "cdm.product.asset.InterestRatePayout",
    "RateSpecification": "cdm.product.asset.RateSpecification",
    "FloatingRateSpecification": "cdm.product.asset.FloatingRateSpecification",
    "FixedRateSpecification": "cdm.product.asset.FixedRateSpecification",
    "PriceTypeEnum": "cdm.observable.asset.PriceTypeEnum",
    "AssetClassEnum": "cdm.base.staticdata.asset.common.AssetClassEnum",
    "AssetIdTypeEnum": "cdm.base.staticdata.asset.common.AssetIdTypeEnum",
    "PartyIdentifierTypeEnum": "cdm.base.staticdata.party.PartyIdentifierTypeEnum",
    "FloatingRateIndexEnum": "cdm.base.staticdata.asset.rates.FloatingRateIndexEnum",
    "CounterpartyRoleEnum": "cdm.base.staticdata.party.CounterpartyRoleEnum",
}
```

Append to the return dict of `inspect_cdm_json`:

```python
"well_known_imports": WELL_KNOWN_IMPORTS,
"well_known_imports_note": (
    "Consider imports for these symbols when building trades; not all appear as explicit "
    "values in the JSON instance."
),
```

### 6.3 Documentation

Update `tools.json` description for `inspect_cdm_json` to mention `well_known_imports`.

### 6.4 Tests

- `inspect_cdm_json` on existing fixture: assert `"well_known_imports" in result` and `InterestRatePayout` key present with expected FQCN.

---

## 7. Fix F5 — complexity-based `AgentConfig` scaling

### 7.1 Constraint

`AgentConfig` is a `@dataclass` with **`match_threshold`** and possibly future fields. **Never** replace the whole config with a fresh `AgentConfig(...)` that omits user overrides — use **`dataclasses.replace`**.

### 7.2 Recommended approach: deterministic pre-flight inspect

**Why:** The LLM might call tools in suboptimal order; **first** iteration already spends tokens before `inspect_cdm_json`. Reading JSON and walking the tree is **cheap** compared to LLM latency.

**Where:** Start of `_run_agent_impl` in `agent.py`, immediately after `config = config or AgentConfig()`:

```python
from dataclasses import replace

def _scale_config_for_node_count(config: AgentConfig, total_nodes: int) -> AgentConfig:
    """Raise limits for large CDM instances; preserve match_threshold and any user tweaks."""
    if total_nodes > 400:
        return replace(
            config,
            max_iterations=max(config.max_iterations, 50),
            max_tool_calls=max(config.max_tool_calls, 150),
            timeout_seconds=max(config.timeout_seconds, 900),
        )
    if total_nodes > 150:
        return replace(
            config,
            max_iterations=max(config.max_iterations, 35),
            max_tool_calls=max(config.max_tool_calls, 100),
            timeout_seconds=max(config.timeout_seconds, 600),
        )
    return config

# Inside _run_agent_impl, after config = config or AgentConfig():
try:
    pre = inspect_cdm_json(cdm_json_path)
    nodes = int(pre.get("total_nodes", 0))
    config = _scale_config_for_node_count(config, nodes)
except Exception:
    pass  # keep original config if path unreadable
```

**Semantics:** `max(..., scaled_floor)` ensures a user who set **higher** limits is not **downgraded**; only bumps when their values are lower. If you prefer “always force IRS limits”, use `replace(config, max_iterations=50, ...)` only when `total_nodes > 400` and document that CLI overrides lose for huge files.

### 7.3 Optional stderr / user message

If `log_progress`, print one line: `scaled limits: iter=… timeout=… tools=… (nodes=N)`.

Avoid injecting huge JSON into the first user message unless the model consistently ignores stderr.

### 7.4 Tests

- Unit test `_scale_config_for_node_count` with mock configs (small vs large node counts, user `max_iterations` already 60).
- Optional integration: patch `inspect_cdm_json` return value in agent test.

---

## 8. Fix F6 — `reference_patterns` / `reference_api_note` in `inspect_cdm_json`

### 8.1 Goal

When the **instance** contains `globalReference` / `externalReference`, remind the model of the **builder API** (overlaps F1 but reinforces with real paths).

### 8.2 Implementation sketch

Inside `inspect_cdm_json`, before `walk(...)`:

```python
ref_hits: List[Dict[str, object]] = []
REF_KEYS = frozenset({"globalReference", "externalReference"})
MAX_REF_SAMPLES = 40  # cap list size for LLM context
```

In `walk`, when `isinstance(node, dict)`:

```python
present = [k for k in REF_KEYS if k in node]
if present:
    if len(ref_hits) < MAX_REF_SAMPLES:
        ref_hits.append({"json_path": path, "keys": present})
```

Add to return dict:

```python
"reference_pattern_count": len(ref_hits),  # if you also count all dicts, use separate total
"reference_patterns_sample": ref_hits,
"reference_api_note": (
    "For ReferenceWithMeta* builders use setGlobalReference(String), setExternalReference(String), "
    "and setReference(Reference) for JSON 'address'. Do not use setAddress()."
)
    if ref_hits
    else None,
```

**Note:** The sample count is **capped**; optionally add `reference_pattern_total` with a second pass or increment on every hit (cheap) so the model sees “+ 120 more” without listing them.

### 8.3 Tests

- Fixture CDM JSON that contains at least one `globalReference`: assert `reference_patterns_sample` non-empty and `reference_api_note` is not `None`.

---

## 9. Tool / prompt documentation sync

**Done:** `tools.json` descriptions updated; `SYSTEM_PROMPT` extended (inspect bullets, lookup step, nested builders).

| File | Change |
|------|--------|
| `fpml_cdm/java_gen/tools.json` | Extend `inspect_cdm_json`, `lookup_cdm_schema`, `list_enum_values` descriptions to mention new keys (`well_known_imports`, `reference_api_note`, `setter_note`, warnings). |
| `SYSTEM_PROMPT` | F3 nesting section; optionally one line: “Trust `setter_hint` for references only after F1 — `address` → `setReference`.” (After F1 ships, this is accurate.) |

---

## 10. Implementation order (recommended)

1. ~~**F1** — `lookup_cdm_schema` + tests~~ **Done**
2. ~~**F2 phase 2a** — sanitize `_camel_to_screaming_snake` + `list_enum_values` warning + `DayCountFractionEnum` test~~ **Done**
3. ~~**F6** — `inspect_cdm_json` reference sampling + tests~~ **Done**
4. ~~**F4** — `well_known_imports` + test + `tools.json`~~ **Done**
5. ~~**F5** — pre-flight scaling with `dataclasses.replace` + unit tests~~ **Done**
6. ~~**F3** — `SYSTEM_PROMPT` edit~~ **Done**
7. ~~**F2 phase 2b** — `oneOf` title map (`_enum_oneof_json_to_java_title_map` + `enum_json_value_java_identifier`)~~ **Done**

---

## 11. Risks and follow-ups

| Risk | Mitigation |
|------|------------|
| Sanitized enum still ≠ Rosetta for edge cases | `enum_constant_warning` + phase 2b `oneOf` titles |
| `well_known_imports` rots across CDM versions | Regenerate from JAR or codegen; keep small |
| Pre-flight `inspect_cdm_json` doubles walk work | Negligible vs LLM cost; single read per run |
| User wanted *lower* timeout on huge files | Document `replace` semantics; offer env `JAVA_GEN_NO_SCALE=1` if needed |

---

## 12. Optional future work (out of scope for this plan)

- **Import closure:** Union of all types reachable from `Trade` in the schema graph → auto import list (heavy context).
- **compile_java:** Emit multiple errors (`-Xmaxerrs`) or parse full log for batch fixes.
- **Deterministic compile repair:** Map “cannot find symbol: class InterestRatePayout” → add import from table.

---

*Document version: 1.1 — implementation complete; see `fpml_cdm/java_gen/agent.py`, `tools.py`, `schema_index.py`, `tests/test_java_gen/test_tools.py`, `test_agent.py`, `test_schema_index.py`.*
