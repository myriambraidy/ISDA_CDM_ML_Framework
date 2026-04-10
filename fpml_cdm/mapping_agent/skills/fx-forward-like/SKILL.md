---
name: fx-forward-like
description: Use when trade contains fxSingleLeg, fxForward, or NDF-style structures under FpML confirmation view.
adapter_ids: ["fxForward", "fxSingleLeg"]
cdm_target: "6.x"
fpml_profile: "5.13+ confirmation"
version: "0.3.1"
---

# FX Forward-Like Mapping Skill

## When to use

Classifier cues: the FpML `<trade>` element contains a direct child with local name `fxSingleLeg` or `fxForward`.
This includes deliverable forwards, spots, and non-deliverable forwards (NDFs).

Conflicts: if `fxSwap` or `fxOption` is also present, prefer those skills.

## FpML scope

Key elements under the product subtree:

- `exchangedCurrency1`, `exchangedCurrency2` — the two payment amounts (each has `payerPartyReference`, `receiverPartyReference`, `paymentAmount`)
- `exchangeRate` — quotedCurrencyPair, rate, **spotRate**, **forwardPoints**
- `valueDate` — settlement date
- `nonDeliverableSettlement` or `nonDeliverableForward` — NDF settlement terms
- `tradeHeader/tradeDate` — trade date
- `tradeHeader/partyTradeIdentifier` — party references and trade ids (one per party)

## CDM v6 JSON conventions (Rosetta serialization)

CDM v6 uses **Rosetta JSON serialization**. Every primitive value is wrapped:
- Dates: `{"value": "2001-12-21"}` — never a bare string
- Strings: `{"value": "EUR"}` — always wrapped
- Numbers: placed inside nested value objects

Choice types use a **discriminator key** — e.g. `{"SettlementPayout": {...}}`.

### Trade root — align with CDM `Trade` (no `partyRole`)

The JSON under `{"trade": ...}` must match the **CDM `Trade`** shape used for product confirmation: economics live under `product` and `tradeLot`; parties under `party` and `counterparty`.

**Do not include `trade.partyRole`.** Do not add Buyer/Seller (or any `PartyRole`) arrays on Trade. FpML payer/receiver and buyer/seller semantics are expressed only via:

- `counterparty` (mapping Party1 / Party2 to `party` via `partyReference`), and  
- the product payout (`SettlementPayout.payerReceiver`, quantities, prices).

Omit other Trade fields unless the source FpML clearly requires them (e.g. `ancillaryParty`, `executionDetails`). The minimal template below is the intended surface area.

---

## CRITICAL MAPPING RULES

### 1. Counterparty roles — Party1 = payer of exchangedCurrency1

The CDM `counterparty` array assigns abstract roles `"Party1"` and `"Party2"`.
The assignment is determined by **who pays `exchangedCurrency1`**:

- Read `exchangedCurrency1/payerPartyReference/@href` — this FpML party becomes **Party1**.
- Read `exchangedCurrency1/receiverPartyReference/@href` — this FpML party becomes **Party2**.

Example: if `exchangedCurrency1/payerPartyReference href="party2"`, then:
```json
"counterparty": [
  {"role": "Party1", "partyReference": {"externalReference": "party2"}},
  {"role": "Party2", "partyReference": {"externalReference": "party1"}}
]
```

The `payerReceiver` in the `SettlementPayout` then uses:
```json
"payerReceiver": {"payer": "Party1", "receiver": "Party2"}
```

**Do NOT assume Party1 = party1.** Read the FpML payer/receiver references.

### 2. Settlement type

For standard FX (deliverable forwards/spots), CDM uses `"Cash"` — not `"Physical"`:
- FpML `standardSettlementStyle = "Standard"` → CDM `settlementType = "Cash"`
- Only use `"Physical"` for non-standard physical delivery

### 3. Trade identifiers — each party gets its own

Each FpML `<partyTradeIdentifier>` maps to **two** CDM `tradeIdentifier` entries:
1. One with `issuerReference` pointing to the party's reference
2. One with `issuer.value` containing the party's reference ID

Example from FpML:
```xml
<partyTradeIdentifier>
  <partyReference href="party1"/>
  <tradeId tradeIdScheme="http://www.abn-amro.com/fx/trade-id">ABN1234</tradeId>
</partyTradeIdentifier>
<partyTradeIdentifier>
  <partyReference href="party2"/>
  <tradeId tradeIdScheme="http://www.db.com/fx/trade-id">DB5678</tradeId>
</partyTradeIdentifier>
```

Maps to:
```json
"tradeIdentifier": [
  {
    "issuerReference": {"externalReference": "party1"},
    "assignedIdentifier": [{"identifier": {"value": "ABN1234", "meta": {"scheme": "http://www.abn-amro.com/fx/trade-id"}}}]
  },
  {
    "assignedIdentifier": [{"identifier": {"value": "ABN1234", "meta": {"scheme": "http://www.abn-amro.com/fx/trade-id"}}}]
  },
  {
    "issuerReference": {"externalReference": "party2"},
    "assignedIdentifier": [{"identifier": {"value": "DB5678", "meta": {"scheme": "http://www.db.com/fx/trade-id"}}}]
  },
  {
    "assignedIdentifier": [{"identifier": {"value": "DB5678", "meta": {"scheme": "http://www.db.com/fx/trade-id"}}}]
  }
]
```

**Key**: Each trade ID's `issuerReference` must point to **its own party**, and preserve the `tradeIdScheme` as `meta.scheme`.

### 4. Party identification

Map `<party id="party1"><partyId>ABNANL2A</partyId></party>` as:
```json
{
  "partyId": [{"identifier": {"value": "ABNANL2A"}}],
  "meta": {"externalKey": "party1"}
}
```

Use the **`<partyId>` text content** (e.g. "ABNANL2A"), NOT the XML `id` attribute (e.g. "party1"). The XML `id` goes to `meta.externalKey`.

If `<partyId>` has a `partyIdScheme` attribute, include it:
```json
"partyId": [{
  "identifier": {"value": "BFXS5XCH7N0Y05NIXW11", "meta": {"scheme": "http://www.fpml.org/coding-scheme/external/iso17442"}},
  "identifierType": "LEI"
}]
```

### 5. Price composite for forwards (spotRate + forwardPoints)

When FpML has `<spotRate>` and `<forwardPoints>` under `<exchangeRate>`, include a `composite` object inside the price value:

```json
"price": [{
  "value": {
    "value": 0.9175,
    "unit": {"currency": {"value": "USD"}},
    "perUnitOf": {"currency": {"value": "EUR"}},
    "priceType": "ExchangeRate",
    "composite": {
      "baseValue": 0.9130,
      "operand": 0.0045,
      "arithmeticOperator": "Add",
      "operandType": "ForwardPoint"
    }
  }
}]
```

- `composite.baseValue` = FpML `<spotRate>`
- `composite.operand` = FpML `<forwardPoints>`
- `composite.arithmeticOperator` = `"Add"`
- `composite.operandType` = `"ForwardPoint"`

### 6. Observable asset type

The `observable` in priceQuantity must include `"assetType": "Cash"`:
```json
"observable": {
  "value": {
    "Asset": {
      "Cash": {
        "identifier": [{"identifier": {"value": "EUR"}, "identifierType": "CurrencyCode"}],
        "assetType": "Cash"
      }
    }
  }
}
```

### 7. Address-based cross-references

Payout `priceQuantity` uses address-based links to `tradeLot` entries:
- `quantitySchedule.address.value = "quantity-1"` links to `tradeLot[0].priceQuantity[0].quantity[0].meta.location[0].value = "quantity-1"`
- `priceSchedule[0].address.value = "price-1"` links to the price entry
- `underlier.Observable.address.value = "observable-1"` links to the observable entry

Include `meta.location` on quantity, price, and observable entries in `tradeLot`.

---

## Minimal valid FX forward CDM trade (template)

```json
{
  "trade": {
    "tradeDate": {"value": "2001-11-19"},
    "party": [
      {
        "partyId": [{"identifier": {"value": "ABNANL2A"}}],
        "meta": {"externalKey": "party1"}
      },
      {
        "partyId": [{"identifier": {"value": "DEUTDEFF"}}],
        "meta": {"externalKey": "party2"}
      }
    ],
    "counterparty": [
      {"role": "Party1", "partyReference": {"externalReference": "party2"}},
      {"role": "Party2", "partyReference": {"externalReference": "party1"}}
    ],
    "tradeIdentifier": [
      {
        "issuerReference": {"externalReference": "party1"},
        "assignedIdentifier": [{"identifier": {"value": "ABN1234", "meta": {"scheme": "http://www.abn-amro.com/fx/trade-id"}}}]
      },
      {
        "assignedIdentifier": [{"identifier": {"value": "ABN1234", "meta": {"scheme": "http://www.abn-amro.com/fx/trade-id"}}}]
      },
      {
        "issuerReference": {"externalReference": "party2"},
        "assignedIdentifier": [{"identifier": {"value": "DB5678", "meta": {"scheme": "http://www.db.com/fx/trade-id"}}}]
      },
      {
        "assignedIdentifier": [{"identifier": {"value": "DB5678", "meta": {"scheme": "http://www.db.com/fx/trade-id"}}}]
      }
    ],
    "tradeLot": [
      {
        "priceQuantity": [
          {
            "quantity": [
              {
                "value": {"value": 10000000, "unit": {"currency": {"value": "EUR"}}},
                "meta": {"location": [{"scope": "DOCUMENT", "value": "quantity-1"}]}
              },
              {
                "value": {"value": 9175000, "unit": {"currency": {"value": "USD"}}},
                "meta": {"location": [{"scope": "DOCUMENT", "value": "quantity-2"}]}
              }
            ],
            "price": [
              {
                "value": {
                  "value": 0.9175,
                  "unit": {"currency": {"value": "USD"}},
                  "perUnitOf": {"currency": {"value": "EUR"}},
                  "priceType": "ExchangeRate",
                  "composite": {
                    "baseValue": 0.9130,
                    "operand": 0.0045,
                    "arithmeticOperator": "Add",
                    "operandType": "ForwardPoint"
                  }
                },
                "meta": {"location": [{"scope": "DOCUMENT", "value": "price-1"}]}
              }
            ],
            "observable": {
              "value": {
                "Asset": {
                  "Cash": {
                    "identifier": [{"identifier": {"value": "EUR"}, "identifierType": "CurrencyCode"}],
                    "assetType": "Cash"
                  }
                }
              },
              "meta": {"location": [{"scope": "DOCUMENT", "value": "observable-1"}]}
            }
          }
        ]
      }
    ],
    "product": {
      "taxonomy": [{"source": "ISDA", "productQualifier": "ForeignExchange_Spot_Forward"}],
      "economicTerms": {
        "payout": [
          {
            "SettlementPayout": {
              "payerReceiver": {"payer": "Party1", "receiver": "Party2"},
              "priceQuantity": {
                "quantitySchedule": {"address": {"scope": "DOCUMENT", "value": "quantity-1"}},
                "priceSchedule": [{"address": {"scope": "DOCUMENT", "value": "price-1"}}]
              },
              "settlementTerms": {
                "settlementType": "Cash",
                "settlementDate": {"valueDate": "2001-12-21"}
              },
              "underlier": {
                "Observable": {"address": {"scope": "DOCUMENT", "value": "observable-1"}}
              }
            }
          }
        ]
      }
    }
  }
}
```

---

## Coverage & ignores

Default ignores (envelope/metadata not mapped to CDM economics):
- `tradeHeader/partyTradeIdentifier/versionedTradeId` (versioning metadata)
- XML namespace declarations and schema locations

Everything under `exchangedCurrency1`, `exchangedCurrency2`, `exchangeRate`, `valueDate`, and `nonDeliverableSettlement` MUST be accounted for.

## Validation interpretation

- Schema errors on `trade.tradeDate` usually mean it was sent as a bare string instead of `{"value": "..."}`.
- Semantic errors on currency mismatches indicate the `quantity[].value.unit.currency.value` path is wrong.
- If counterparty roles look swapped, re-check `exchangedCurrency1/payerPartyReference/@href`.

## Common mistakes

1. **Party1 ≠ party1** — Party1 is the exchangedCurrency1 payer, which may be party2 in FpML.
2. **settlementType "Physical"** — use `"Cash"` for standard FX deliverable forwards.
3. **tradeIdentifier all pointing to same party** — each partyTradeIdentifier must reference ITS OWN party.
4. **Using XML `id` for partyId** — use the `<partyId>` text content (BIC/LEI), not the XML `id` attribute.
5. **Missing price composite** — if spotRate and forwardPoints exist, include the composite object.
6. **Missing identifier schemes** — preserve `tradeIdScheme` as `meta.scheme` on identifiers.
7. **Including `partyRole` on Trade** — forbidden for this mapping; use `counterparty` + payout only.

## Sources

- FpML 5.13 Confirmation View: FX products (`fpml-fx-5-13.xsd`)
- CDM v6 ForeignExchange: [cdm.finos.org](https://cdm.finos.org/) product documentation
- CDM 6.0.0 release notes: JSON serialization changes for choice types
