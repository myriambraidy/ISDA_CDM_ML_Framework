
---

# Plan: Scale Parser & Transformer — All FX Products (Extensible to Non-FX)

**Status:** planning only — **do not implement** until this plan is accepted.

---

## Executive summary

This plan describes **how to scale** the deterministic FpML → normalized → CDM pipeline from the current **two adapters** (`fxForward`, `fxSingleLeg`) to **full FX derivative coverage** supported by FpML and your business needs, without rewriting the orchestration for every new product. The strategy rests on five pillars: **inventory-driven scope**, **adapter registry**, **ruleset-first parsing with escape hatches**, **per-product CDM transforms**, and **per-adapter validation + corpus gates**. Non-FX asset classes are **out of scope for implementation** here but **in scope for naming and module layout** so a later phase can plug in rates/credit/equity without a fork.

---

## 1. Goals

1. Support **all FX derivative product shapes** that the team commits to from FpML (under `<trade>`), not only forward / single-leg spot-forward style trades.
2. Preserve **one pipeline**: parse → normalize → transform → validate → optional mapping agent → compliance (schema + semantic + Rosetta as configured).
3. Make **adding a new FX product** a repeatable recipe: matrix row → ruleset + optional extractor → normalized type → transformer → tests → corpus line.
4. **Non-FX later:** same registry and dispatch ideas; separate normalized packages and transformer modules per asset class.

---

## 2. What “all FX products” means (scope contract)

**In scope for this plan**

- FpML elements that represent **FX cash, forwards, swaps, options, and common variants** (NDF, flexible dates, etc.) where the economic terms map to **CDM v6 FX-related product / payout** structures.
- Products that appear in **your corpora** (official FpML examples + internal files): the **product matrix** is the source of truth for what “all” means in practice.

**Explicitly out of scope until a separate decision**

- **Non-FX** (rates, credit, equity, commodities as non-cash underliers) — only **hooks** and naming here.
- **Exotic / structured FX** (e.g. correlation, variance swaps, complex multi-underlier structures) unless the matrix marks them P0/P1 and CDM mapping is agreed with a quant/CDM owner.
- **FpML versions** older than those you officially support — handle via schema/corpus filters, not infinite backward compatibility in one pass.

**Deliverable that locks scope:** `docs/fx_product_matrix.md` (or `data/fx_product_matrix.json`) — **no adapter work starts** for a product until it has a matrix row with priority and CDM target.

---

## 3. Current baseline (constraints to remove)

| Area | Today | Limitation |
|------|--------|------------|
| Detection | Two hard-coded product types + rulesets | Ignores other `<trade>` children |
| Normalized | `NormalizedFxForward` — one shape | Cannot represent swaps/options without hacks |
| Parser | Ruleset-driven extraction in `ruleset_engine` | One extractor shape (`extract_fx_product_fields`) |
| Transformer | Mostly one FX spot/forward CDM pattern | Other products need different `economicTerms` / payouts |
| Validator | Semantic checks tuned to forward-like output | Must branch per product family |
| Mapping agent | Patches **rulesets only** | Cannot add new CDM subgraphs; new products need correct transformer first |

---

## 4. Strategy overview — five pillars

### Pillar A — Inventory-driven scope (don’t guess)

1. **Scan corpus** (`data/corpus/fpml_official/`, internal drops): list every **local name** of direct children of `<trade>` (excluding `tradeHeader`) that appear under `fx-derivatives` / FX paths.
2. **Cross-check FpML XSD / documentation** for element names and expected children (optional script: extract element names from relevant schema fragments).
3. **Build the product matrix** (Section 5): each row = one **adapter_id** (usually = FpML element local name), priority, example paths, CDM product qualifier / payout intent, notes.
4. **Triage:** P0 = already needed by business; P1 = high frequency in corpus; P2 = rare; **Deferred** = exotics or CDM mapping unclear.

### Pillar B — Adapter registry (single front door)

Introduce a **registry** (data structure + optional `fpml_cdm/adapters/registry.py`) that maps:

- `adapter_id` (string, e.g. `fxSwap`, `fxSingleLegOption`)
- **Parser profile:** ruleset key or reference to `get_base_ruleset(adapter_id)`
- **Normalizer factory:** `FpML + issues → NormalizedFx*` (or union)
- **Transformer key:** which function builds CDM
- **Validator key:** which semantic validator runs
- **Feature flags:** e.g. `requires_custom_extractor: bool`

**Detection strategy**

1. List candidate product nodes under `<trade>` (namespace-agnostic local names).
2. **Intersect** with registry keys; order by **priority** in registry (not XML order), so behavior is deterministic.
3. If **multiple** candidates could apply (ambiguous XML), use **scoring**: e.g. prefer element that has required economic children present; tie-break by registry order.
4. If **none** match → `UNSUPPORTED_PRODUCT` with path `trade/<localName>`.

### Pillar C — Ruleset-first parsing, extractor second

- **Default:** each new FX product gets a **`_BASE_RULESETS[adapter_id]`** in [`fpml_cdm/rulesets.py`](fpml_cdm/rulesets.py): `fields` with ordered **candidate paths** (local-name paths), `derived` toggles, same patterns as today for dates/amounts/hrefs.
- **Generalize** [`ruleset_engine.py`](fpml_cdm/ruleset_engine.py): either
  - **Dispatch table:** `extract_product_fields(adapter_id, product_node, ruleset, issues)`, or
  - **Small strategy classes** per adapter inheriting shared `_parse_date`, `_parse_amount`, NDF anchor logic, etc.
- **Escape hatch:** when rulesets cannot express nesting (e.g. deeply optional legs), add **`adapter_id`-specific helper** in `ruleset_engine` or `parser.py` that fills a **typed dict** fragment, still called only from the adapter path (no global special cases in unrelated products).

### Pillar D — Normalized model strategy (discriminated union)

**Recommendation:** move from a single growing `NormalizedFxForward` to a **tagged union**:

- `NormalizedFxSpotForward` — current fields (rename or alias existing model).
- `NormalizedFxSwap` — near leg / far leg dates, notionals per leg, etc.
- `NormalizedFxOption` — strike, premium, exercise dates, put/call, barrier fields as needed.

Each type implements **`to_dict` / `from_dict`** and a stable **`source_product`** / `adapter_id` discriminator.  
`ConversionResult.normalized` becomes `Optional[NormalizedFxTradeUnion]` (typing alias).

**Migration:** keep `NormalizedFxForward` as **deprecated alias** or factory for spot-forward for one release; update CLI/tests gradually.

### Pillar E — Per-adapter transformer + Rosetta-canonical patterns

- **Dispatch:** `transform_to_cdm_v6(model)` inspects type/discriminator → calls `transform_fx_spot_forward_like`, `transform_fx_swap`, `transform_fx_option`, …
- **Shared building blocks:** party list, LEI/BIC resolution, `tradeIdentifier` duplication pattern, `tradeLot.priceQuantity` with **address/location** linking to `SettlementPayout.priceQuantity` schedules (already directionally in codebase).
- **Per-product CDM table** (maintain in docs or code comments): adapter_id → list of payout kinds (`SettlementPayout`, `OptionPayout`, …), underlier placement, taxonomy qualifier convention.

---

## 5. Product matrix (deliverable template)

Create `docs/fx_product_matrix.md` with **minimum columns**:

| Column | Description |
|--------|-------------|
| `adapter_id` | Stable id, usually FpML child local name |
| `fpml_element` | Same as adapter_id or XSD name |
| `priority` | P0 / P1 / P2 / Deferred |
| `cdm_product_qualifier_target` | e.g. `ForeignExchange_Spot_Forward`, or option taxonomy |
| `normalized_type` | e.g. `NormalizedFxSpotForward` |
| `payout_summary` | Short text: settlement vs option vs multi-leg |
| `example_fpml_paths` | 1+ corpus-relative paths |
| `status` | Planned / In progress / Done |
| `notes` | NDF, vendor quirks |

**Starter buckets** (illustrative — finalize against your corpus scan):

| Bucket | Typical FpML trade children | CDM complexity |
|--------|------------------------------|----------------|
| Spot / forward family | `fxSingleLeg`, `fxForward` | Lower — shared with current |
| Swaps | `fxSwap`, possibly `fxFlexibleForward` | Higher — multiple dates/legs |
| Options | `fxSingleLegOption`, barrier variants | Higher — option payout + underlier |
| NDF / EM | Often under single-leg/forward | Medium — ruleset paths |
| Exotics | Correlation, variance, … | Deferred unless P0 |

---

## 6. Parser scaling — detailed steps

### 6.1 Product detection (replace ad-hoc)

- Implement **registry-driven** detection as in Pillar B.
- **Logging (dev mode):** optional debug output listing all `<trade>` child local names when `UNSUPPORTED_PRODUCT` — speeds matrix updates.

### 6.2 Rulesets

- For each **P0/P1** matrix row, add `_BASE_RULESETS[adapter_id]`:
  - Reuse field names where semantics align with spot-forward (`valueDate`, `exchangedCurrency*`, etc.).
  - Add **new logical fields** only on the normalized type that needs them (e.g. `farValueDate` for swaps — reflected in ruleset + dataclass).

### 6.3 `partyTradeIdentifier` / parties

- Keep **one** party extraction pass at document level; per-adapter only changes **which hrefs** bind to normalized legs (buyer/seller, payer/receiver).

### 6.4 Mapping agent

- **`get_active_ruleset_summary`:** must list **all** field keys for the active adapter so the LLM can patch candidates.
- Optional tool **`list_supported_adapters`** returning registry keys + one-line description.
- **No change to rule:** agent still cannot patch transformer; only rulesets.

---

## 7. Transformer scaling — detailed steps

### 7.1 CDM mapping table (per adapter)

Maintain `docs/fx_cdm_mapping_table.md` or embedded dict:

- **Input:** normalized union instance.
- **Output:** which CDM nodes are populated (`product.economicTerms.payout[]` entries, `tradeLot`, etc.).
- **Rosetta constraints:** address/location keys (`price-1`, `quantity-1`, `observable-1`, …) consistent across payout and tradeLot.

### 7.2 Code layout (suggested)

- `fpml_cdm/transformer.py` — thin **dispatch** + shared helpers (globalKey, party refs, identifiers).
- `fpml_cdm/transformers/fx_spot_forward.py`, `fx_swap.py`, `fx_option.py` — **product-specific** `build_trade(...)`.

### 7.3 Party / LEI / identifiers

- **Single module** for: BIC → LEI (`data/lei/bic_to_lei.json`), `tradeIdentifier` with/without `issuerReference` + `issuer` object rows, `counterparty` / `partyRole` consistency rules.

---

## 8. Validation scaling

| Layer | Strategy |
|-------|----------|
| JSON Schema | `validate_cdm_official_schema` per output; add **adapter-specific** optional stricter tests if needed |
| Semantic | Register `_semantic_validation_*` by `adapter_id` or `isinstance`; **reuse** float tolerance patterns |
| Rosetta | CI job per adapter golden file; **failure signature** catalog (rule name → owner) |

---

## 9. Testing and corpus gates

1. **Unit:** per adapter — minimal XML → parse → normalize → transform → assert key CDM paths + semantic score.
2. **Golden:** `tests/fixtures/expected/<adapter>_cdm.json` for stable cases.
3. **Corpus:** extend Makefile target to output **JSON report**: `{ adapter_id: { ok: n, fail: n, errors: [...] } }`.
4. **Regression:** optional CDM hash for pinned files on `main`.

---

## 10. Rollout phases (detailed)

| Phase | Activities | Exit criteria |
|-------|--------------|---------------|
| **P0** | Build product matrix from corpus scan; add registry **stub** (keys only); ADR for normalized union; no new FpML products parsed yet | Matrix reviewed; registry API merged; CI green |
| **P1** | Implement **first** non-trivial FX product end-to-end (e.g. `fxSwap` **or** `fxSingleLegOption` — pick in open decisions); union type + ruleset + transformer + tests | Green tests + ≥1 corpus example passes |
| **P2** | Remaining P1/P2 matrix rows | Coverage % vs matrix; document gaps |
| **P3** | Mapping agent + ruleset patches validated on messy XML | Trace shows useful patches; no transformer hacks |
| **P4** | Non-FX: package layout (`fpml_cdm.models.fx`, …), namespace prefixes in registry, **no** full rates implementation unless scoped | ADR + empty stubs or interfaces |

---

## 11. Non-FX future (hooks only)

- Registry keys like `rates:Swap`, `credit:CreditDefaultSwap` (prefix prevents collision with `fxSwap` if needed).
- Separate normalized packages — **do not** put rates fields on FX union types.
- Pipeline entry unchanged: **detect → adapter → parse → transform → validate**.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| God-object normalized model | Discriminated union + small focused dataclasses |
| CDM/Rosetta drift | Golden files + Rosetta in CI |
| Unbounded scope (“all FX”) | **Matrix + priority**; Deferred bucket |
| Mapping agent misuse for CDM shape bugs | Document: fix transformer first; agent only for extraction |
| Maintenance | CODEOWNERS or doc owner per adapter family |

---

## 13. Open decisions (resolve before coding)

1. **P1 product:** `fxSwap` vs `fxSingleLegOption` first (quant + corpus frequency).
2. **Breaking API:** timeline for removing `NormalizedFxForward` as sole type.
3. **Compliance bar per product:** same schema+semantic+Rosetta for all adapters day-one, or **staged** (schema first, Rosetta later for new adapters).

---

## 14. File-level checklist (implementation)

- [ ] `docs/fx_product_matrix.md` — inventory + priorities
- [ ] `fpml_cdm/adapters/registry.py` (or equivalent) — adapter registry
- [ ] `fpml_cdm/rulesets.py` — new `_BASE_RULESETS` entries
- [ ] `fpml_cdm/ruleset_engine.py` — dispatch / extractors per adapter
- [ ] `fpml_cdm/parser.py` — registry-driven detection
- [ ] `fpml_cdm/types.py` — union normalized types + serialization
- [ ] `fpml_cdm/transformer.py` + `fpml_cdm/transformers/fx_*.py` — dispatch + per-product CDM
- [ ] `fpml_cdm/validator.py` — per-adapter semantic validation
- [ ] `schemas/` — split or extend if normalized JSON schema becomes multi-type
- [ ] `fpml_cdm/mapping_agent/*` — adapter list tool + field summaries for new adapters
- [ ] `tests/fixtures/` — per-adapter minimal XML + expected JSON
- [ ] `CLAUDE.md` / README — supported products table

---

## 15. Glossary

- **Adapter:** one supported FpML product type under `<trade>` with full parse→CDM path.
- **Ruleset:** declarative candidate paths + parsers for normalized fields.
- **Matrix:** authoritative list of what “all FX” means for this codebase.

---

*End of scaling plan section.*
