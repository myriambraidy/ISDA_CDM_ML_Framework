# FX product matrix

Authoritative backlog for FpML → normalized → CDM adapters. Rows are added **before** implementation work on a new product.

| adapter_id (FpML) | Priority | normalized_kind | CDM target (intent) | Status | Example corpus / notes |
|-------------------|----------|-----------------|---------------------|--------|------------------------|
| fxForward | P0 | fx_spot_forward_like | ForeignExchange spot/forward, SettlementPayout | Done | `tests/fixtures/fpml/fx_forward.xml` |
| fxSingleLeg | P0 | fx_spot_forward_like | Same as forward | Done | `tests/fixtures/fpml/fx_single_leg.xml` |
| fxSwap | P1 | fx_swap | Multi-leg / multiple settlement dates | Done | `tests/fixtures/fpml/fx_swap.xml`, `tests/fixtures/fpml/fx_swap_alt_dates.xml` |
| fxSingleLegOption | P1 | TBD (fx_option) | OptionPayout + underlier | Planned | |
| Barrier / NDO options | P1–P2 | TBD | Option + feature | Planned | Matrix must specify FpML shape vs composite detection |

**Priority:** P0 = supported today; P1/P2 = next waves per business and corpus frequency.

**Rates (out of scope for FX matrix):** use separate registry namespace and `fpml_cdm.models.rates` — see [rates README](../fpml_cdm/models/rates/README.md) (repository root-relative: `fpml_cdm/models/rates/README.md`).
