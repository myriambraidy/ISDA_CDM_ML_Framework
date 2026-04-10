---
name: fx-swap
description: Use when trade contains fxSwap with nearLeg and farLeg structures.
adapter_ids: ["fxSwap"]
cdm_target: "6.x"
fpml_profile: "5.13+ confirmation"
version: "0.3.1"
---

# FX Swap Mapping Skill

## When to use

Classifier cues: the FpML `<trade>` element contains a direct child with local name `fxSwap`.
An FX swap has `nearLeg` and `farLeg` children.

## FpML scope

Key elements under `fxSwap`:

- `nearLeg` — first settlement (exchangedCurrency1, exchangedCurrency2, exchangeRate, valueDate)
- `farLeg` — second settlement (same structure)
- Each leg contains `exchangedCurrency1`, `exchangedCurrency2`, `exchangeRate`
- `tradeHeader/tradeDate`

## CDM v6 target shape (critical)

CDM v6 uses **Rosetta JSON serialization** conventions — see fx-forward-like skill for full details.
**All shared rules from fx-forward-like apply here**, specifically:

### Trade root

**Do not include `trade.partyRole`.** Parties and economics use `party`, `counterparty`, and each leg’s payout only (see fx-forward-like).

### Counterparty roles (same logic as forward)

`Party1` = the party that pays `exchangedCurrency1` in the **near leg**.
Read `nearLeg/exchangedCurrency1/payerPartyReference/@href` to determine Party1.

### Settlement type

Use `"Cash"` for standard FX settlement (not `"Physical"`).

### Trade identifiers

Each FpML `partyTradeIdentifier` produces **two** CDM `tradeIdentifier` entries (one with `issuerReference`, one with `issuer`). Each must point to **its own party**. Preserve `tradeIdScheme` as `meta.scheme`.

### Party identification

Use `<partyId>` text content for `partyId[].identifier.value`, NOT the XML `id` attribute. XML `id` goes to `meta.externalKey`.

### Key structural rules for FX swaps

1. `tradeDate` is `{"value": "YYYY-MM-DD"}`, **not** a bare string.
2. **Two payout entries** in `product.economicTerms.payout[]`, each using `SettlementPayout` discriminator.
3. Near leg = `payout[0]`, far leg = `payout[1]`.
4. Each `SettlementPayout.settlementTerms.settlementDate.valueDate` is the leg's value date.
5. `settlementTerms.settlementType = "Cash"` for both legs.
6. **Two priceQuantity entries** in `tradeLot[0].priceQuantity[]` — one per leg.
7. Quantities wrapped as `{"value": {"value": N, "unit": {"currency": {"value": "CCY"}}}}`.
8. If `spotRate` and `forwardPoints` present on a leg, include `composite` on the price (see fx-forward-like skill).
9. Observable asset must include `"assetType": "Cash"`.
10. Cross-reference addresses link payout → tradeLot entries via `meta.location`.

## Coverage & ignores

Both legs must be fully mapped. All fields under `nearLeg` and `farLeg` are in scope.

## Common mistakes

1. **Party1 ≠ party1** — Party1 is the near-leg exchangedCurrency1 payer.
2. **settlementType "Physical"** — use `"Cash"`.
3. **tradeIdentifier all pointing to same party** — each must reference its own party.
4. **Using XML id for partyId** — use `<partyId>` text content.
5. **Missing composite** — include when spotRate+forwardPoints exist.
6. **`partyRole` on Trade** — omit entirely.

## Sources

- FpML 5.13: FX Swap product schema
- CDM v6: ForeignExchange swap representation
