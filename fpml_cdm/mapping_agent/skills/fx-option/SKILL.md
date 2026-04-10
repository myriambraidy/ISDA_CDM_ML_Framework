---
name: fx-option
description: Use when trade contains fxOption with exercise style and strike information.
adapter_ids: ["fxOption"]
cdm_target: "6.x"
fpml_profile: "5.13+ confirmation"
version: "0.3.1"
---

# FX Option Mapping Skill

## When to use

Classifier cues: the FpML `<trade>` element contains a direct child with local name `fxOption`.
Subtree contains exercise style (European/American/Bermuda), put/call amounts, and strike.

## FpML scope

Key elements under `fxOption`:

- `putCurrencyAmount`, `callCurrencyAmount` — option amounts
- `strike` — strike price
- `europeanExercise` / `americanExercise` / `bermudaExercise` — exercise terms
- `exchangeRate` — spot/forward rate reference
- `buyerPartyReference`, `sellerPartyReference`
- `expiryDate`, `expiryTime`, `valueDate`
- `premium` — option premium details

## CDM v6 target shape (critical)

CDM v6 uses **Rosetta JSON serialization** conventions — see fx-forward-like skill for full details.
**All shared rules from fx-forward-like apply here**, specifically:

### Trade root

**Do not include `trade.partyRole`.** Buyer/seller on the option is modeled in `OptionPayout.buyerSeller` (Party1/Party2), not as a separate `partyRole` list on Trade.

### Counterparty roles

For FX options, `Party1` = `buyerPartyReference/@href`, `Party2` = `sellerPartyReference/@href`.

### Settlement type

Use `"Cash"` for standard FX settlement (not `"Physical"`).

### Trade identifiers

Each FpML `partyTradeIdentifier` produces **two** CDM `tradeIdentifier` entries (one with `issuerReference`, one with `issuer`). Each must point to **its own party**. Preserve `tradeIdScheme` as `meta.scheme`.

### Party identification

Use `<partyId>` text content for `partyId[].identifier.value`, NOT the XML `id` attribute. XML `id` goes to `meta.externalKey`.

### Key structural rules for FX options

1. `tradeDate` is `{"value": "YYYY-MM-DD"}`, **not** a bare string.
2. Payout uses **`OptionPayout`** (capital O) as discriminator key in `product.economicTerms.payout[0]`.
3. `OptionPayout.exerciseTerms.expirationDate` is an array of adjustable dates.
4. `OptionPayout.optionType` is e.g. `"Put"` or `"Call"`.
5. `OptionPayout.strike.strikePrice.value` is the numeric strike rate.
6. `OptionPayout.buyerSeller.buyer` / `.seller` use role labels like `"Party1"`.
7. Quantities wrapped as `{"value": {"value": N, "unit": {"currency": {"value": "CCY"}}}}`.
8. `settlementTerms.settlementType = "Cash"`.
9. Observable asset must include `"assetType": "Cash"`.
10. Cross-reference addresses link payout → tradeLot entries via `meta.location`.

## Coverage & ignores

All option economics (amounts, strike, exercise, premium) must be mapped.
Expiry date/time and value date are required.

## Common mistakes

1. **Party1 ≠ party1** — Party1 is the buyer, not necessarily the first party in the XML.
2. **settlementType "Physical"** — use `"Cash"`.
3. **tradeIdentifier all pointing to same party** — each must reference its own party.
4. **Using XML id for partyId** — use `<partyId>` text content.
5. **Missing identifier schemes** — preserve tradeIdScheme as meta.scheme.
6. **`partyRole` on Trade** — omit entirely.

## Sources

- FpML 5.13: FX Option product schema
- CDM v6: Option payout and exercise representation
