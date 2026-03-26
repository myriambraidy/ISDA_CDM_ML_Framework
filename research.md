## Scaling the FpML → ISDA CDM Pipeline Beyond FX Forwards

This document captures a deep-dive on the current architecture, external ecosystem (FpML, ISDA CDM, Rosetta), and concrete directions to scale this repo from “FX forwards only” to a broader FX family, with a deterministic core plus an agentic / LLM-assisted mapping loop.

---

## 1. Current Architecture Recap

### 1.1 Core flow and pivot type

The pipeline today is:

```text
FpML XML  →  NormalizedFxForward (Python) → CDM v6 Trade JSON → Validation
```

The *only* internal pivot type is `NormalizedFxForward` in `fpml_cdm/types.py`. Everything else hangs off it:

- `parser.py` produces `NormalizedFxForward` from FpML.
- `transformer.py` maps `NormalizedFxForward` → `{"trade": {...}}` CDM JSON.
- `validator.py` re-parses the same FpML, and semantically validates *that* CDM JSON against the normalized model and schemas.
- `pipeline.py` orchestrates the whole flow and returns `ConversionResult`.

Key types:

```python
class ErrorCode(str, Enum):
    UNSUPPORTED_PRODUCT = "UNSUPPORTED_PRODUCT"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_VALUE = "INVALID_VALUE"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    SEMANTIC_VALIDATION_FAILED = "SEMANTIC_VALIDATION_FAILED"

@dataclass
class NormalizedFxForward:
    tradeDate: str
    valueDate: str
    currency1: str
    currency2: str
    amount1: float
    amount2: float
    tradeIdentifiers: List[Dict[str, str]] = field(default_factory=list)
    parties: List[Dict[str, Optional[str]]] = field(default_factory=list)
    exchangeRate: Optional[float] = None
    settlementType: str = "PHYSICAL"
    settlementCurrency: Optional[str] = None
    buyerPartyReference: Optional[str] = None
    sellerPartyReference: Optional[str] = None
    sourceProduct: str = "fxForward"
    sourceNamespace: Optional[str] = None
    sourceVersion: Optional[str] = None
    llm_recovered_fields: List[str] = field(default_factory=list)
```

`ConversionResult` is the public wrapper:

```python
@dataclass
class ConversionResult:
    ok: bool
    normalized: Optional[NormalizedFxForward] = None
    cdm: Optional[Dict[str, Any]] = None
    validation: Optional[ValidationReport] = None
    errors: List[ValidationIssue] = field(default_factory=list)
```

### 1.2 Parser: hard-wired FX forward semantics

`fpml_cdm/parser.py` is *very* opinionated:

- Hard-coded product set:

```python
SUPPORTED_PRODUCTS = {"fxForward", "fxSingleLeg"}
```

- `_detect_supported_product` looks at direct children of `<trade>`:
  - If it finds one of `fxForward` or `fxSingleLeg`, that becomes `sourceProduct`.
  - If it finds anything else, it raises `ParserError` with `UNSUPPORTED_PRODUCT`.
  - If it finds nothing supported, it raises `UNSUPPORTED_PRODUCT` again.

- It then extracts a forward-shaped economic footprint:
  - `exchangedCurrency1/paymentAmount/{currency,amount}`
  - `exchangedCurrency2/paymentAmount/{currency,amount}`
  - Optional `exchangeRate/rate`
  - `valueDate`
  - Optional `nonDeliverableSettlement` / `nonDeliverableForward` → sets `settlementType="CASH"` + `settlementCurrency`.
  - `buyerPartyReference/@href`, `sellerPartyReference/@href`.
  - `party` list (`id` + `partyName` / `partyId`).

All extraction is local-name based (namespace-agnostic) and uses strict validators:

```python
def _parse_date(value: Optional[str], path: str, issues: List[ValidationIssue]) -> Optional[str]:
    # Normalizes ISO date, records MISSING_REQUIRED_FIELD or INVALID_VALUE

def _parse_amount(value: Optional[str], path: str, issues: List[ValidationIssue]) -> Optional[float]:
    # Parses float, records MISSING_REQUIRED_FIELD or INVALID_VALUE

def _parse_currency(value: Optional[str], path: str, issues: List[ValidationIssue]) -> Optional[str]:
    # Requires 3-letter ISO code, records MISSING_REQUIRED_FIELD or INVALID_VALUE
```

Strictness modes:

- `strict=True` (default): *any* issue → `ParserError`.
- `strict=False`: issues collected, but only `level == "error"` cause failure.
- `recovery_mode=True`: always returns `(model, issues)`, even with missing values (model filled with empty/zero defaults).

So right now, **“deterministic” = “one fixed path per field, FX-forward-shaped model, fail fast on anything else”**.

### 1.3 Transformer: single CDM trade shape

`fpml_cdm/transformer.py` is a pure function:

```python
def transform_to_cdm_v6(model: NormalizedFxForward) -> Dict[str, object]:
    ...
    return {"trade": trade}
```

Important aspects:

- Settlement type mapping:

```python
SETTLEMENT_TYPE_MAP = {
    "PHYSICAL": "Physical",
    "CASH": "Cash",
    "REGULAR": "Physical",
}
```

- Constructs:
  - `trade.tradeDate.value`
  - `trade.tradeIdentifier[]` (from `tradeIdentifiers`).
  - `party[]` and `counterparty[]` based on `parties`.
  - `partyRole[]` based on buyer/seller refs (`Buyer` / `Seller` roles).
  - `product.economicTerms.payout[0].SettlementPayout` with:
    - `payerReceiver.payer/receiver` mapped to `Party1` / `Party2`.
    - `settlementTerms` with `settlementDate.valueDate`, `settlementType`, optional `settlementCurrency`.
    - `underlier.Observable.Asset.Cash.identifier` for `currency1`.
  - `tradeLot[0].priceQuantity[0]` with:
    - `price[0]` for the FX rate (if present).
    - `quantity[0/1]` for `(currency1, amount1)` and `(currency2, amount2)`.

This is **hard-coded to the “simple FX forward leg” CDM pattern**. There’s no abstraction for other product shapes (options, swaps, multi-leg, etc.).

### 1.4 Validation: forward-specific semantic checks

`fpml_cdm/validator.py` does:

1. JSON Schema validation:
   - `fpml_fx_forward_parsed.schema.json` for `NormalizedFxForward.to_dict()`.
   - Official FINOS CDM Trade schema (`cdm-event-common-Trade.schema.json`) via `cdm_official_schema.get_trade_schema_validator`.
2. Semantic validation:

```python
def _semantic_validation(
    model: NormalizedFxForward,
    cdm_data: Dict[str, Any],
) -> Tuple[List[ValidationIssue], MappingScore]:
    # Checks tradeDate, valueDate, settlementType
    # Checks quantity[0/1] currencies and amounts
    # Checks price[0] FX rate + base/quote currencies
    # Checks settlementCurrency for NDFs
    # Checks buyer/seller vs payer/receiver roles
```

So semantic checks are also **FX-forward-shaped**. Any new product type would require a different semantic contract.

### 1.5 LLM enrichment: value-level recovery only

`fpml_cdm/llm_enricher.py` wraps an `LLMProvider` and tries to fix *values* for fields that the parser flagged as `MISSING_REQUIRED_FIELD` or `INVALID_VALUE`:

- Builds a prompt with:
  - `LLM_RECOVERY_RULES.md`
  - Raw FpML XML.
  - List of issues (paths + messages).
- Expects back a flat JSON like:

```json
{
  "valueDate": "2024-09-25",
  "exchangeRate": "1.36"
}
```

- Each candidate value is re-validated with the same deterministic helpers:

```python
_normalize_date_only
_parse_amount
_parse_currency
```

- On success, it:
  - Sets the attribute on `NormalizedFxForward`.
  - Adds the field name to `llm_recovered_fields`.
  - Downgrades the matching `ValidationIssue` from `error` → `warning` with `LLM-recovered:` prefix.

Critically, **the LLM cannot change structure**:

- It can’t decide that `valueDate` lives under some alternate FpML node.
- It can’t decide to use an entirely different product mapping.

It’s a deterministic parser with “LLM as value-repair fallback”.

### 1.6 Java + Rosetta integration

Two integration points:

1. **RosettaTypeValidator** via `fpml_cdm/rosetta_validator.py`:
   - Wraps a shaded `rosetta-validator-1.0.0.jar` and calls it via subprocess.
   - Returns a `RosettaValidationResult` which can be turned into `ValidationIssue`s.
2. **Java generation agent** in `fpml_cdm/java_gen/*`:
   - `schema_index.py` indexes the *full* CDM JSON schema directory (`schemas/jsonschema`).
   - `tools.py` implements a bunch of structured tools:
     - `inspect_cdm_json`, `lookup_cdm_schema`, `list_enum_values`, `resolve_java_type`, `diff_json`, `validate_output`, etc.
   - `agent.py` runs a ReAct-style loop against an LLM + those tools to synthesize `CdmTradeBuilder.java` that reproduces a target CDM JSON instance.

The Java agent side is **already agentic and schema-driven**; the Python conversion pipeline is not.

---

## 2. External Ecosystem Findings (Rosetta, CDM, FpML)

### 2.1 ISDA CDM Java distribution and validation

From FINOS CDM Java docs:

- CDM Java is distributed via Maven (`org.finos.cdm:cdm-java`).
- Uses builder patterns for all model objects.
- Ships JSON↔Java serializers (`RosettaObjectMapper`).
- Validation is done with `RosettaTypeValidator` bound via Guice:

```java
public class GenericModule extends AbstractModule {
  @Override
  protected void configure() {
    bind(ModelObjectValidator.class).to(RosettaTypeValidator.class);
    bind(QualifyFunctionFactory.class).to(QualifyFunctionFactory.Default.class);
  }
}

Injector injector = Guice.createInjector(new GenericModule());

RosettaTypeValidator validator = injector.getInstance(RosettaTypeValidator.class);
ValidationReport report = validator.runProcessStep(cdmInstance.getClass(), cdmInstance.toBuilder());
```

Implications for this repo:

- Our current `fpml_cdm/rosetta_validator.py` is consistent: we already rely on a shaded JAR with Rosetta CDM and its validator.
- Rosetta is the *authoritative* source of truth for CDM structural correctness, beyond JSON Schema; we should lean on it for scaling.

### 2.2 Rosetta Ingest, synonyms, and mapping layer

Rosetta Ingest docs emphasise:

- The main mapping artefact is the **Translation Dictionary** built from *synonym sources*.
- Synonym sources are **namespaces that define translations** from external XML/JSON formats into Rosetta model types.
- Those “mappings” (synonyms) are:
  - Expressed in Rune DSL (“mapping component / synonym”).
  - Distributed in machine-readable `.rosetta` files (e.g. `cdm.mapping.*` namespaces).
  - Meant to be **code-generated into executable translation code**, just like CDM codegen.

High-level ingestion flow (hosted or on-prem):

- XML/JSON input → Ingestion API → Rosetta Ingest → CDM model instance plus:
  - Mapping coverage stats.
  - Validation results (data rules, cardinality, etc.).

Key takeaway:

- In the Rosetta world, *scalable mapping* is:
  - Specified declaratively via synonyms.
  - Code-generated.
  - Measured with coverage/diagnostics.

We’re currently doing a small, hand-coded subset of that in Python; there’s an opportunity to mimic the same principles (even if we don’t directly consume `.rosetta` synonyms yet).

### 2.3 FpML FX structures (fxSingleLeg and forward semantics)

From FpML 5.11 FX docs:

- `fxSingleLeg` is a global element of type `FxSingleLeg` and may substitute for `product`.
- Content includes:
  - `exchangedCurrency1`, `exchangedCurrency2`
  - `dealtCurrency`
  - `tenorName` / `tenorPeriod`
  - Either:
    - a single `valueDate`, or
    - `currency1ValueDate` + `currency2ValueDate`
  - `exchangeRate`
  - Optional `nonDeliverableSettlement`
  - Optional `disruption` terms.
- The FpML FX architecture notes that `fxSingleLeg` can represent:
  - Spot.
  - Forward.
  - Swaps (when embedded in multi-leg structures).

`nonDeliverableSettlement`:

- Adds:
  - `settlementCurrency`
  - `referenceCurrency` (optional)
  - `notionalAmount` (optional)
  - `fixing` details
  - `settlementDate` (optional)

So:

- Our current parser already aligns with the *core subset* of `fxSingleLeg`/`fxForward`:
  - `exchangedCurrency1/2` → amounts + currencies.
  - `exchangeRate`.
  - `valueDate`.
  - `nonDeliverableSettlement/settlementCurrency`.
- But FpML FX has **more dimensions**:
  - Tenors.
  - Currency-specific value dates.
  - Dealt currency.
  - Disruption clauses.
  - Multi-leg compositions and options.

---

## 3. Pain Points and Limitations

### 3.1 Product coverage is binary and hard-coded

- `SUPPORTED_PRODUCTS = {"fxForward", "fxSingleLeg"}` in `parser.py`.
- Anything else under `<trade>` gets `UNSUPPORTED_PRODUCT`.
- Scaling to the rest of the FX family (spot, swaps, options, NDO, barriers, strategies) with this strategy would turn `parser.py` into an unmaintainable `if/elif` zoo.

### 3.2 Single pivot model (`NormalizedFxForward`) couples everything

- Parser, transformer, validator, and semantic checks all assume:
  - Exactly 2 notionals.
  - Exactly 1 rate.
  - Single `valueDate`.
  - One settlement payout.
- To support, say, an FX swap, we’d need:
  - Multiple legs.
  - Different date patterns.
  - Possibly different CDM types (e.g. multi-payout structures).

Right now, there’s no place to put “swap semantics” other than forking `NormalizedFxForward` into something else, which cascades into all modules.

### 3.3 “Too deterministic” means “too brittle”

Determinism is good; brittleness is not:

- If the field lives somewhere unexpected but still *semantically* obvious, the parser doesn’t try to adapt:
  - e.g. alternate `valueDate` placement, wrappers, or vendor-specific extensions.
- The LLM enricher only fixes value-level problems; it can’t suggest which *path* to read from.
- Schema-driven or Rosetta-driven hints are not used to adapt field mappings automatically.

### 3.4 No product-adapter abstraction

- There’s no notion of per-product adapters with:
  - Independent parsers.
  - Independent transformers.
  - Independent semantic validators.
- Everything FX goes through the same `NormalizedFxForward` lens, even though FpML already separates `fxSingleLeg` vs. other FX nodes.

### 3.5 Agentic / tool-driven intelligence only exists on the Java-gen side

- `fpml_cdm/java_gen/agent.py` + `tools.py` show a rich pattern:
  - Agent loop with tool calls (`inspect_cdm_json`, `lookup_cdm_schema`, `list_enum_values`, `diff_json`, `validate_output`, etc.).
  - Schema-index-driven introspection (`SchemaIndex`).
  - `diff_json`-based feedback loop.
- The FpML→CDM pipeline doesn’t use any of that. Semantics are frozen in hand-written Python.

---

## 4. Design Goals for Scaling

We want to:

1. **Cover the full FX family first** (fxForward, fxSingleLeg variants, spot, swaps, FX options, NDFs/NDOs), with a path to non-FX later.
2. **Keep a deterministic core**:
   - Same input + same configuration → same output.
   - No hidden randomness in the main line.
3. **Use LLMs in an “agentic, tool-constrained” way**:
   - LLM proposes *structured mapping decisions* and *value recoveries*.
   - Tools and validators enforce correctness.
   - Changes are traceable and auditable.
4. **Leverage CDM and Rosetta as ground truth**:
   - JSON Schemas (what we already do).
   - RosettaTypeValidator (we already have a bridge).
   - Potentially, ingestion / synonyms as long-term mapping spec.
5. **Avoid rewriting the world**:
   - Incrementally refactor: current FX forward path becomes one adapter among many.
   - Reuse `SchemaIndex`, `diff_json`, and Rosetta validator where possible.

---

## 5. Proposed Scalable Architecture

### 5.1 Product adapters and a registry

Introduce a small, explicit `ProductAdapter` interface:

```python
class ProductAdapter(Protocol):
    product_id: str  # e.g. "fx_forward", "fx_single_leg", "fx_swap", "fx_option"

    def detect(self, trade_element: ET.Element) -> float:
        """Return confidence [0,1] that this adapter applies to the given <trade>."""

    def parse_normalized(
        self,
        root: ET.Element,
        strict: bool,
        recovery_mode: bool,
    ) -> Tuple[BaseNormalizedTrade, List[ValidationIssue]]:
        ...

    def transform_to_cdm(
        self,
        normalized: BaseNormalizedTrade,
    ) -> Dict[str, Any]:
        ...

    def semantic_validate(
        self,
        normalized: BaseNormalizedTrade,
        cdm_obj: Dict[str, Any],
    ) -> Tuple[List[ValidationIssue], MappingScore]:
        ...
```

Then, a registry:

```python
PRODUCT_ADAPTERS: List[ProductAdapter] = [
    FxForwardAdapter(),
    FxSingleLegAdapter(),
    # FxSwapAdapter(), FxOptionAdapter(), ...
]
```

`parse_fpml_root` becomes:

1. Find `<trade>`.
2. Run `detect()` on each adapter.
3. Pick the highest-confidence adapter above a threshold; if tie or 0.0, produce `UNSUPPORTED_PRODUCT`.
4. Delegate to that adapter’s `parse_normalized()`.

This decouples **“which product”** from **“how to parse/transform/validate it”**, and gives us a place to add new FX products incrementally.

### 5.2 Generalize the normalized model

Option A (minimal surgery):

- Keep `NormalizedFxForward` as-is but introduce:

```python
class BaseNormalizedTrade(Protocol):
    product_type: str
    source_namespace: Optional[str]
    source_version: Optional[str]
    llm_recovered_fields: List[str]

    def to_dict(self) -> Dict[str, Any]: ...
```

And make `NormalizedFxForward` implement that protocol (it already does, functionally).

Option B (longer-term, more flexible):

- Introduce a generic:

```python
@dataclass
class NormalizedTrade:
    productType: str               # e.g. "FX_FORWARD", "FX_SWAP", "FX_OPTION"
    legs: List[NormalizedLeg]      # 1..n legs, each with amounts, currencies, dates
    payouts: List[NormalizedPayout]
    parties: ...
    # plus extension slots / metadata
```

- Each `ProductAdapter`:
  - Emits `NormalizedTrade` with its own productType and leg structures.
  - Supplies its own transformer and semantic validator that know how to interpret those legs into CDM.

Short-term recommendation:

- Start with Option A, so we can plug **FX-only adapters** without breaking existing tests.
- Design Option B in parallel for future, but don’t break the existing `NormalizedFxForward` API yet.

### 5.3 Deterministic but multi-path extraction rules

Right now each field has a single path. For FX scaling we can:

For each adapter, define per-field extraction rules:

```python
@dataclass
class FieldRule:
    name: str  # "valueDate", "currency1", ...
    candidates: List[str]  # ordered list of XPath-like local-name paths
    required: bool = True
```

Example snippet for an `FxSingleLegAdapter`:

```python
FX_SINGLE_LEG_RULES = [
    FieldRule(
        name="valueDate",
        candidates=[
            "trade/fxSingleLeg/valueDate",
            # Currency-specific fallbacks:
            "trade/fxSingleLeg/currency1ValueDate",
        ],
    ),
    FieldRule(
        name="currency1",
        candidates=[
            "trade/fxSingleLeg/exchangedCurrency1/paymentAmount/currency",
        ],
    ),
    # ...
]
```

The adapter then implements:

1. Deterministic evaluation of candidates *in order*.
2. Accumulation of `ValidationIssue`s when none of the candidates succeed.
3. Clear provenance: each candidate is logged in the issue path (for debugging and LLM use).

This keeps determinism, but allows *structured fallback logic* that can be tuned per product.

### 5.4 Agentic mapping loop driven by validation

Target: an “agent pipeline” on top of the deterministic core, **not in place of it**.

#### 5.4.1 Position in the pipeline

New orchestration:

```text
parse (adapter) → normalized
→ deterministic transform → cdm
→ schema + semantic validate
→ [if valid enough] done
→ [if not] agent loop (tools + LLM)
    → propose mapping tweaks / value fixes
    → re-run transform + validation
    → converge or give up with structured report
```

#### 5.4.2 Tools we already have and can reuse

From `fpml_cdm/java_gen/tools.py` (and friends), we already know how to:

- Index CDM schemas (`SchemaIndex`).
- Inspect JSON trees (`inspect_cdm_json`).
- Resolve Java/CDM types from schema refs (`schema_ref_to_java_class`).
- Deep-compare CDM JSON (`diff_json`).
- Validate against CDM schema (`validate_output` → uses `validate_cdm_official_schema`).

We can design a **Python-side toolset** for FpML mapping tuning, analogous in spirit:

- `inspect_fpml_trade`:
  - Walks the XML tree under `<trade>`, annotating local names, attributes, and namespaces.
  - Highlights candidate nodes for each economic concept (dates, amounts, currencies, settlement, optional extra fields).
- `list_product_candidates`:
  - Runs `detect()` on all adapters and reports their confidences and reasons.
- `simulate_ruleset`:
  - Given:
    - An adapter id.
    - A variant ruleset (or patch).
  - Runs parse → transform → validate and returns a detailed results object:
    - Normalized model snippet.
    - CDM snippet.
    - Schema errors.
    - Semantic errors.
    - Rosetta errors (if enabled).
- `patch_ruleset`:
  - Edits adapter rule config (e.g., reorder candidate paths, add fallback, change required flags) in a config file rather than Python code.

Then an LLM-backed agent (similar to `java_gen/agent.py`) can:

1. Run `inspect_fpml_trade` to understand the XML envelope.
2. Call `list_product_candidates` to choose/confirm an adapter.
3. Call `simulate_ruleset` with slightly modified rulesets to see if errors vanish.
4. Iterate until:
   - Validation passes and diff vs expected CDM shape is acceptable (for regression cases).
   - Or a maximum iteration/tool budget is hit.

All outputs (rule patches, chosen adapter, validations) are structured and loggable.

#### 5.4.3 LLM value recovery integration

The existing `LLMFieldEnricher` can remain almost unchanged, but we can:

- Move it under the adapter world:
  - Each adapter can supply its own `Normalized*` → field handlers.
  - Agent can call the enricher for specific groups of fields (e.g. “recover missing FX rate”, “recover settlementCurrency”).
- Use validation signals to *constrain* which fields the LLM is allowed to touch.

---

## 6. Rosetta / CDM Integration Strategy

### 6.1 Keep JSON Schemas as first-line structural checks

We should continue to:

- Validate normalized models against local schemas:

```python
validate_schema_data("fpml_fx_forward_parsed.schema.json", normalized.to_dict())
```

- Validate CDM trades against the official `cdm-event-common-Trade.schema.json`:

```python
trade_dict = cdm_obj.get("trade", {})
cdm_schema_errors = validate_cdm_official_schema(trade_dict)
```

For new products:

- Introduce per-product normalized schemas (e.g. `fpml_fx_option_parsed.schema.json`).
- Optionally introduce product-specific CDM schemas if we want to be stricter about product substructures.

### 6.2 Use RosettaTypeValidator in CI / corpus flows

We already have `fpml_cdm/rosetta_validator.py`, which:

- Locates the shaded JAR.
- Writes a temp JSON file.
- Runs:

```bash
java -jar rosetta-validator-1.0.0.jar <tmp.json> --type trade
```

- Parses the JSON result into `RosettaValidationResult`.

We should:

- Make Rosetta validation part of **corpus checks** (`make corpus-check[-fx]`).
- Expose a high-level CLI or API knob:
  - `--with-rosetta` or `ROSETTA_VALIDATION=1` to turn it on for select runs.
- Keep it out of the hot path for latency-sensitive usage, but always run it in QA / regression.

### 6.3 Long-term: Rosetta synonyms as the ground truth mapping spec

Long-term, rather than encoding FpML→CDM mappings in Python:

- Express them as synonyms in a Rosetta model workspace (`cdm.mapping.*` namespaces).
- Use Rosetta’s code generation to produce:
  - Java translation code (for ingestion).
  - Potentially, JSON or “mapping manifests” we can consume from Python.

For now this is aspirational; the short-term value is to **mirror** Rosetta’s approach (synonym spec + codegen + coverage) in our own architecture, so the mental model lines up.

---

## 7. Incremental Roadmap

### Phase 0: Document + codify current behavior (this repo is already close)

- `ARCHITECTURE.md` already documents a lot; keep it in sync.
- Add small tests that make **intended** FX forward / single-leg semantics explicit (especially around:
  - NDF handling.
  - Buyer/seller vs payer/receiver mapping.
  - Edge cases like missing `exchangeRate`).

### Phase 1: Introduce product adapters, keep `NormalizedFxForward`

1. Create a minimal `ProductAdapter` protocol and registry as described in 5.1.
2. Wrap the current FX logic into:
   - `FxForwardAdapter`.
   - `FxSingleLegAdapter` (even if they share 95% of logic at first).
3. Refactor `parse_fpml_fx` and `validate_transformation` to go through the adapter registry, but:
   - Keep the *external* API intact.
   - Keep `NormalizedFxForward` as the only normalized type initially.

Success criteria:

- All existing tests still pass.
- It’s now possible to plug a placeholder adapter for `fxSwap` / `fxOption` that just returns `UNSUPPORTED_PRODUCT` or a “not yet implemented” error.

### Phase 2: Add multi-path field rules for FX forwards/single-legs

1. For each FX adapter, factor extraction into `FieldRule` configs.
2. Implement deterministic resolution of candidates per field.
3. Add tests that:
   - Cover FpML variants where `valueDate` / currency-specific dates appear.
   - Show different FpML vendor quirks but same normalized result.

Success criteria:

- Parser becomes **less brittle** while staying deterministic.
- Issues include richer path information for failed candidates (good for LLM/agent use later).

### Phase 3: Build a Python-side mapping agent + tools

1. Add a `fpml_cdm/mapping_tools.py` module with:
   - `inspect_fpml_trade`
   - `list_product_candidates`
   - `simulate_ruleset`
   - `patch_ruleset`
2. Implement a small agent loop (similar to `java_gen/agent.py`) that:
   - Accepts an FpML file and optional target CDM sample.
   - Calls tools, proposes rule tweaks, and measures improvement via `validate_transformation` + optional `validate_cdm_rosetta`.
3. Start with **offline / corpus-only** usage:
   - Use this agent to auto-suggest mapping adjustments for tricky corpus examples.

Success criteria:

- The mapping agent can legitimately improve coverage on real FpML corpus without manual coding for every edge case.
- All changes it proposes are:
   - Diffable (rule patches in config files).
   - Reproducible.

### Phase 4: Introduce new FX products

Start with:

1. FX spot / same-day/next-day trades (still basically single legs).
2. FX swaps (two legs, potentially shared exchange rate or different).
3. Simple FX vanilla options (where CDM already has payout templates).

For each product:

- Implement an adapter.
- Implement product-specific:
  - normalized model (or re-use a more generic `NormalizedTrade` if ready).
  - transformer logic.
  - semantic validator.
- Extend the corpus and tests.
- Optionally, let the mapping agent help tune field paths for new products.

### Phase 5: Deep Rosetta alignment (optional but powerful)

Longer-term tasks:

- Define FpML→CDM synonyms in a Rosetta workspace.
- Use Rosetta Ingest APIs as an external “ground truth translator” to:
  - Cross-check Python mappings.
  - Possibly even replace some Python-side transforms with calls to a local Rosetta ingestion container.
- Explore DRR-style reporting / projection if regulatory outputs become a goal.

---

## 8. Summary

- The current pipeline is a **clean, deterministic FX forward converter** pivoting on `NormalizedFxForward`, with:
  - Strict parsing.
  - Fixed CDM trade shape.
  - FX-forward-specific semantic validation.
  - Optional value-level LLM recovery.
- This design is great for a POC but doesn’t scale well to the full FX family.
- The key scaling moves are:
  - Introduce **product adapters** + a registry.
  - Gradually generalize the normalized model.
  - Replace “single hard-coded path per field” with **deterministic, multi-path rule sets**.
  - Build a **tool-constrained agent** around those rules and the existing schema/Rosetta validators.
- This keeps the core **deterministic and auditable**, while giving us an extensible framework to support more FpML types and to use LLMs in a disciplined way for both mapping design and runtime recovery.

---

## Current Implementation Notes (FpML→CDM v6→Java)

This repo now has the concrete, working shape of the agentic pipeline described earlier:

### What is implemented end-to-end
1. Deterministic parsing of FX-family FpML trades into the normalized model (`NormalizedFxForward`), using rulesets instead of hard-coded paths.
2. Deterministic transformation into CDM v6 `{"trade": {...}}`.
3. Deterministic validation:
   - normalized schema (`fpml_fx_forward_parsed.schema.json`)
   - CDM official Trade schema (`cdm-event-common-Trade.schema.json`)
   - semantic cross-check against the normalized model
4. If validation fails, an LLM-driven mapping agent can propose *structured ruleset patches*.
5. The best CDM JSON produced by the mapping agent is fed into the existing Java codegen agent to emit:
   - `generated/CdmTradeBuilder.java`

### Key modules (by responsibility)
- Ruleset definitions: `fpml_cdm/rulesets.py`
- Ruleset application + ruleset-driven extraction: `fpml_cdm/ruleset_engine.py`
- Deterministic extraction entrypoint: `fpml_cdm/parser.py` (now ruleset-driven for supported FX adapters)
- Mapping tool registry + tool handlers: `fpml_cdm/mapping_agent/registry.py`, `fpml_cdm/mapping_agent/tools.py`
- Mapping agent loop (LLM + tools + best-so-far scoring): `fpml_cdm/mapping_agent/agent.py`
- End-to-end orchestration into Java codegen:
  - `fpml_cdm/fpml_to_cdm_java.py`
  - CLI entry: `fpml_cdm/cli.py::generate-java-from-fpml`

### Mapping agent tools (tool-constrained)
The mapping agent is tool-constrained: it may only call tools, and it may only “edit” mapping through structured patches passed to `run_conversion_with_patch`.

Tool contracts:
- `inspect_fpml_trade(fpml_path: str) -> { tradeDate, product_candidates }`
- `get_active_ruleset_summary(adapter_id: str) -> { adapter_id, fields, derived }`
- `run_conversion_with_patch(fpml_path: str, adapter_id: str, patch: object) -> { cdm_json, normalized, validation_report, validation_summary, ... }`
- `validate_best_effort(fpml_path: str, cdm_json: object, enable_rosetta?: bool, rosetta_timeout_seconds?: int) -> ValidationReport (+ best-effort rosetta failures)`

### Ruleset format + patch schema
Rulesets are plain Python dicts at runtime (`fpml_cdm/rulesets.py`), with a conceptual shape like:
```json
{
  "fields": {
    "<normalizedField>": {
      "required": true,
      "parser": "date_only|amount|currency3|href|settlement_type_from_ndf_presence",
      "candidates": ["<local-name candidate path>", "..."]
    }
  },
  "derived": {
    "exchangeRate": { "enabled": false, "strategy": "amount_ratio" }
  }
}
```

The mapping agent is allowed to change only this subset via deterministic patches:
```json
{
  "fields": {
    "<fieldName>": {
      "candidates_order": ["<candidatePath>", "..."],
      "candidates_add": ["<candidatePath>", "..."],
      "required": true
    }
  },
  "derived": {
    "<derivedField>": { "enabled": true }
  }
}
```

Patch notes:
- `fields.<field>.candidates` is accepted as an alias for `candidates_order`.
- Candidate resolution is deterministic: candidate paths are evaluated in order and parsed using the existing helpers.

### CLI usage examples
FX forward (deterministic pass):
```bash
python -m fpml_cdm generate-java-from-fpml tests/fixtures/fpml/fx_forward.xml --provider openrouter --model minimax/minimax-m2.5
```

When validation fails, mapping agent runs automatically unless disabled:
```bash
python -m fpml_cdm generate-java-from-fpml tests/fixtures/fpml/missing_value_date.xml --provider openrouter --model minimax/minimax-m2.5
```

Skip mapping agent (uses deterministic CDM even if validation fails):
```bash
python -m fpml_cdm generate-java-from-fpml tests/fixtures/fpml/missing_value_date.xml --provider openrouter --model minimax/minimax-m2.5 --no-mapping-agent
```

Mapping + Java traces / artifacts:
- Best CDM JSON: `--output-dir <dir>/generated_expected_cdm.json`
- Mapping trace (if mapping agent ran): `--output-dir <dir>/mapping_trace.json`
- Java agent trace (optional): `--trace-output <file>` (written by the Java agent wrapper)
- Java output always goes to `generated/CdmTradeBuilder.java`

### Output/exit-code contract
- Exit code is `0` if the Java codegen agent returns `success=True`, else `1`.
- Provider init failures return `2` (e.g., missing `OPENROUTER_API_KEY` for `openrouter`).

