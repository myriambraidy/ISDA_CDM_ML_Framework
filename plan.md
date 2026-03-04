# Fix Plan: 3 Remaining Rosetta Validator Failures

## Status Quo

The pipeline produces CDM v6 JSON that passes Python schema + semantic validation, but the **RosettaTypeValidator** (authoritative CDM Java validator) reports **3 DATA_RULE failures**:

| # | Rule Name | Type | Error Summary |
|---|-----------|------|---------------|
| 1 | `TradeSettlementPayout` | DATA_RULE | Missing `partyRole` with `Buyer` / `Seller` on `Trade` |
| 2 | `SettlementDateBusinessDays` | DATA_RULE | `settlementDate` uses non-existent field `adjustableOrAdjustedDate` |
| 3 | `UnderlierChoice` | DATA_RULE | `underlier` is `{}` — must have exactly one of `Observable` or `Product` |

All three originate from **`transformer.py`**. Fixes cascade into `validator.py` (semantic validation paths), `tests/fixtures/expected/fx_forward_cdm.json`, and test files.

---

## Files to Change

| File | Change Type |
|------|-------------|
| `fpml_cdm/transformer.py` | **Primary** — all 3 fixes |
| `fpml_cdm/validator.py` | Path update for settlement date access |
| `tests/fixtures/expected/fx_forward_cdm.json` | Fixture update to match new output |
| `tests/test_transformer.py` | No code change expected (driven by fixture) |
| `tests/test_validator.py` | Path update for semantic mismatch test |

---

## Fix 1: Add `partyRole` Array (TradeSettlementPayout)

### Root Cause

The Rosetta rule `TradeSettlementPayout` states:

```
if SettlementPayoutOnlyExists(product -> economicTerms -> payout)
then partyRole -> role contains PartyRoleEnum -> Buyer
 and partyRole -> role contains PartyRoleEnum -> Seller
 and tradeLot -> priceQuantity -> price exists
```

Our `Trade` object has `counterparty` (which maps Party1/Party2 to actual parties) but lacks `partyRole` (which assigns semantic roles like Buyer/Seller). These are **different concepts** in CDM v6:

- `counterparty` = abstract role assignments (Party1, Party2) — already present
- `partyRole` = business-level roles (Buyer, Seller, ClearingFirm, etc.) — missing

### CDM Schema Reference

From `cdm-event-common-Trade.schema.json`:

```json
"partyRole": {
  "type": "array",
  "items": { "$ref": "cdm-base-staticdata-party-PartyRole.schema.json" },
  "minItems": 0
}
```

From `cdm-base-staticdata-party-PartyRole.schema.json`:

```json
{
  "properties": {
    "partyReference": { "$ref": "...ReferenceWithMetaParty.schema.json" },
    "role": { "$ref": "cdm-base-staticdata-party-PartyRoleEnum.schema.json" }
  },
  "required": ["partyReference", "role"]
}
```

Valid `PartyRoleEnum` values include `"Buyer"`, `"Seller"`, `"Counterparty"`, etc.

### Change in `transformer.py`

The `NormalizedFxForward` model already carries `buyerPartyReference` and `sellerPartyReference` (parsed from `<buyerPartyReference href="party1"/>` in FpML). We map these to `partyRole` entries.

**Current code** (lines 28-41) — `trade` dict initialization:

```python
trade: Dict = {
    "tradeDate": {"value": model.tradeDate},
    "tradeIdentifier": [],
    "party": [],
    "counterparty": [],
    "product": {
        "economicTerms": {
            "payout": []
        }
    },
    "tradeLot": [{
        "priceQuantity": []
    }],
}
```

**New code** — add `partyRole` key:

```python
trade: Dict = {
    "tradeDate": {"value": model.tradeDate},
    "tradeIdentifier": [],
    "party": [],
    "counterparty": [],
    "partyRole": [],          # <-- NEW
    "product": {
        "economicTerms": {
            "payout": []
        }
    },
    "tradeLot": [{
        "priceQuantity": []
    }],
}
```

Then, **after the counterparty loop** (after line 82), add logic to populate `partyRole`:

```python
if model.buyerPartyReference:
    trade["partyRole"].append({
        "role": "Buyer",
        "partyReference": {"globalReference": model.buyerPartyReference},
    })
if model.sellerPartyReference:
    trade["partyRole"].append({
        "role": "Seller",
        "partyReference": {"globalReference": model.sellerPartyReference},
    })
```

### Why `buyerPartyReference` / `sellerPartyReference`?

In the FpML source (`fx_forward.xml`):

```xml
<buyerPartyReference href="party1"/>
<sellerPartyReference href="party2"/>
```

The parser extracts these into `NormalizedFxForward.buyerPartyReference = "party1"` and `sellerPartyReference = "party2"`. These map directly to CDM's `PartyRoleEnum.Buyer` and `PartyRoleEnum.Seller`.

### Edge Case: Missing Buyer/Seller References

Some FpML files don't have `buyerPartyReference`/`sellerPartyReference` (e.g. vanilla spot trades). In that case, the Rosetta rule only fires when a `SettlementPayout` exists AND `tradeLot.priceQuantity.price` exists. Since we always produce a SettlementPayout with prices, we should default to the first two parties if references are absent:

```python
buyer_ref = model.buyerPartyReference
seller_ref = model.sellerPartyReference
if not buyer_ref and len(model.parties) >= 1:
    buyer_ref = model.parties[0].get("id")
if not seller_ref and len(model.parties) >= 2:
    seller_ref = model.parties[1].get("id")

if buyer_ref:
    trade["partyRole"].append({
        "role": "Buyer",
        "partyReference": {"globalReference": buyer_ref},
    })
if seller_ref:
    trade["partyRole"].append({
        "role": "Seller",
        "partyReference": {"globalReference": seller_ref},
    })
```

---

## Fix 2: Rename `adjustableOrAdjustedDate` → `adjustableOrRelativeDate` (SettlementDateBusinessDays)

### Root Cause

The Rosetta rule `SettlementDateBusinessDays` states:

```
if cashSettlementBusinessDays exists
then cashSettlementBusinessDays >= 0
else adjustableOrRelativeDate exists
  or valueDate exists
  or adjustableDates exists
  or businessDateRange exists
```

Our transformer writes `adjustableOrAdjustedDate` — a **CDM v5 field name** that doesn't exist in the CDM v6 `SettlementDate` type.

### CDM Schema Reference

From `cdm-product-common-settlement-SettlementDate.schema.json`:

```json
{
  "properties": {
    "adjustableOrRelativeDate": { "$ref": "cdm-base-datetime-AdjustableOrAdjustedOrRelativeDate.schema.json" },
    "valueDate": { "type": "string" },
    "adjustableDates": { "$ref": "..." },
    "businessDateRange": { "$ref": "..." },
    "cashSettlementBusinessDays": { "type": "integer" },
    "paymentDelay": { "type": "boolean" }
  }
}
```

Note there is **no** `adjustableOrAdjustedDate` property. The valid options are:

1. `adjustableOrRelativeDate` — wraps `AdjustableOrAdjustedOrRelativeDate` (has `unadjustedDate` as plain string)
2. `valueDate` — a simple string date

### Two Options

**Option A** (recommended): Use `valueDate` directly — simplest for FX forwards where the date is a known fixed date:

```python
"settlementDate": {
    "valueDate": model.valueDate
}
```

**Option B**: Use `adjustableOrRelativeDate` with proper field name:

```python
"settlementDate": {
    "adjustableOrRelativeDate": {
        "unadjustedDate": model.valueDate  # plain string, NOT {"value": ...}
    }
}
```

Note: in the `AdjustableOrAdjustedOrRelativeDate` schema, `unadjustedDate` is `"type": "string"`, not a FieldWithMeta wrapper. So it's a **plain string**, unlike `tradeDate` which uses `{"value": "2024-06-01"}`.

### Recommendation: Option A (`valueDate`)

- `valueDate` is described in the schema as: *"The settlement date for a forward settling product. For Foreign Exchange contracts, this represents a common settlement date between both currency legs."* — this is exactly our use case.
- Simpler structure, fewer nested objects.
- Directly mirrors the FpML `<valueDate>` element.

### Change in `transformer.py`

**Current code** (lines 84-93):

```python
settlement_terms: Dict = {
    "settlementDate": {
        "adjustableOrAdjustedDate": {
            "unadjustedDate": {
                "value": model.valueDate
            }
        }
    },
    "settlementType": settlement_type,
}
```

**New code**:

```python
settlement_terms: Dict = {
    "settlementDate": {
        "valueDate": model.valueDate
    },
    "settlementType": settlement_type,
}
```

### Impact on `validator.py`

The semantic validation in `_semantic_validation()` reads the settlement date at:

```python
cdm_settlement_date = (
    settlement_terms
    .get("settlementDate", {})
    .get("adjustableOrAdjustedDate", {})   # <-- this path changes
    .get("unadjustedDate", {})
    .get("value")
)
```

**New path**:

```python
cdm_settlement_date = (
    settlement_terms
    .get("settlementDate", {})
    .get("valueDate")                       # simple string now
)
```

And the `check()` path string should update from:

```
trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementDate.adjustableOrAdjustedDate.unadjustedDate.value
```

to:

```
trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementDate.valueDate
```

---

## Fix 3: Populate `underlier` with Observable (UnderlierChoice)

### Root Cause

The Rosetta rule `UnderlierChoice` states:

> One and only one field must be set of 'Observable', 'Product'. No fields are set.

Our transformer writes `"underlier": {}` — an empty object that satisfies neither branch.

### CDM Schema Chain

```
SettlementPayout.underlier  →  Underlier
    ├── Observable  →  ReferenceWithMetaObservable
    │       ├── globalReference: string
    │       ├── externalReference: string
    │       └── address: Reference
    └── Product  →  Product (NonTransferable or Transferable)
```

For FX forwards, `Observable` is the correct branch. The observable is the FX rate between the two currencies.

### What Goes Inside `Observable`?

The `ReferenceWithMetaObservable` schema is a Rosetta meta wrapper. It supports:
- **Inline value** via a `value` key (not shown in the simplified schema, but supported by `RosettaObjectMapper`)
- **Reference** via `globalReference` or `externalReference`

The simplest valid approach for our case: provide an inline Observable containing a Cash asset for each currency.

From the `Observable` schema:

```json
{
  "properties": {
    "Asset": { "$ref": "cdm-base-staticdata-asset-common-Asset.schema.json" },
    "Basket": { "$ref": "..." },
    "Index": { "$ref": "..." }
  }
}
```

The `Asset` schema has a `Cash` variant:

```json
{
  "Cash": {
    "identifier": [
      {
        "identifier": { "value": "USD" },
        "identifierType": "CurrencyCode"
      }
    ]
  }
}
```

### Proposed Structure

For an FX forward (e.g. USD/EUR), the underlier observable represents the quoted currency (currency1, the one being "bought"):

```python
"underlier": {
    "Observable": {
        "value": {
            "Asset": {
                "Cash": {
                    "identifier": [
                        {
                            "identifier": {"value": model.currency1},
                            "identifierType": "CurrencyCode"
                        }
                    ]
                }
            }
        }
    }
}
```

### Simpler Alternative: Reference-Only

If the inline approach causes issues with the Rosetta deserializer (which may not expect a `value` key inside `ReferenceWithMetaObservable`), use a reference:

```python
"underlier": {
    "Observable": {
        "externalReference": f"{model.currency1}-{model.currency2}"
    }
}
```

This is still enough to satisfy the choice rule (the `Observable` field is non-null).

### Recommendation

**Start with the inline value approach** — it carries more semantic information and aligns with CDM's intent. If the Rosetta deserializer rejects it, fall back to the reference-only approach.

### Change in `transformer.py`

**Current code** (lines 101-108):

```python
settlement_payout: Dict = {
    "payerReceiver": {
        "payer": payer_role,
        "receiver": receiver_role,
    },
    "settlementTerms": settlement_terms,
    "underlier": {},
}
```

**New code**:

```python
settlement_payout: Dict = {
    "payerReceiver": {
        "payer": payer_role,
        "receiver": receiver_role,
    },
    "settlementTerms": settlement_terms,
    "underlier": _build_underlier(model),
}
```

With a new helper function:

```python
def _build_underlier(model: NormalizedFxForward) -> Dict:
    """Build the Underlier with an Observable for the FX currency pair."""
    return {
        "Observable": {
            "value": {
                "Asset": {
                    "Cash": {
                        "identifier": [
                            {
                                "identifier": {"value": model.currency1},
                                "identifierType": "CurrencyCode",
                            }
                        ]
                    }
                }
            }
        }
    }
```

---

## Cascade: Update Test Fixtures

### `tests/fixtures/expected/fx_forward_cdm.json`

Three sections change:

**1. Add `partyRole` after `counterparty`:**

```json
"partyRole": [
  {
    "role": "Buyer",
    "partyReference": { "globalReference": "party1" }
  },
  {
    "role": "Seller",
    "partyReference": { "globalReference": "party2" }
  }
],
```

**2. Replace `settlementDate` structure:**

```json
"settlementDate": {
  "valueDate": "2024-09-01"
}
```

Instead of the old nested `adjustableOrAdjustedDate.unadjustedDate.value`.

**3. Replace `underlier`:**

```json
"underlier": {
  "Observable": {
    "value": {
      "Asset": {
        "Cash": {
          "identifier": [
            {
              "identifier": { "value": "USD" },
              "identifierType": "CurrencyCode"
            }
          ]
        }
      }
    }
  }
}
```

Instead of `"underlier": {}`.

---

## Cascade: Update `validator.py`

Only the settlement date access path changes.

**Current** (lines 129-135):

```python
cdm_settlement_date = (
    settlement_terms
    .get("settlementDate", {})
    .get("adjustableOrAdjustedDate", {})
    .get("unadjustedDate", {})
    .get("value")
)
```

**New**:

```python
cdm_settlement_date = (
    settlement_terms
    .get("settlementDate", {})
    .get("valueDate")
)
```

And the path string in the `check()` call (line 139):

**Current**:
```
trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementDate.adjustableOrAdjustedDate.unadjustedDate.value
```

**New**:
```
trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementDate.valueDate
```

---

## Implementation Order

1. [x] **`transformer.py`** — all 3 fixes at once (they're independent)
2. [x] **`validator.py`** — update settlement date path
3. [x] **`tests/fixtures/expected/fx_forward_cdm.json`** — update expected output
4. [x] **`tests/test_rosetta_validator.py`** — update tests to expect valid=True (old tests expected failures)
5. [x] **Run `make check`** — all 35 Python unit tests pass
6. [x] Rosetta validator confirms 0 failures (inline Observable approach worked)

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Inline `Observable.value` rejected by `RosettaObjectMapper` | Medium | Fall back to `externalReference` approach |
| Missing `buyerPartyReference` in some FpML files | Low | Default to first two parties |
| `valueDate` not accepted by stricter Rosetta rules | Low | Switch to `adjustableOrRelativeDate` |
| NDF fixture also needs updates | Certain | NDF test checks `settlementTerms` — same path changes apply |

---

## Verification Checklist

- [x] `make check` passes (all 35 Python unit tests green)
- [x] `test_fixture_passes_rosetta_validation` — Rosetta validator returns `valid=True`, 0 failures
- [x] NDF test still passes (`test_transform_ndf_includes_settlement_currency`)
- [x] Missing exchange rate test still passes
- [x] Semantic validation test still passes with 100% accuracy
- [x] End-to-end pipeline test passes
