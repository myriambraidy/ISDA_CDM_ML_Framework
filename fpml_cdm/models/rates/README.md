# Rates / IRS (planned)

This package is reserved for **non-FX** FpML products (e.g. `swap` / `swapStream`, FRAs, IRS).

## Conventions (do not mix with FX)

- **Registry keys:** use a distinct prefix or asset-class namespace so they never collide with FX adapters (e.g. `rates:InterestRateSwap` or a dedicated `rates_registry` parallel to `fpml_cdm.adapters.registry`).
- **Normalized models:** define new dataclasses here — **do not** extend `NormalizedFxForward` with rates fields.
- **Transformers:** add `fpml_cdm/transformers/rates_*.py` (or `irs.py`) and register dispatch in `transform_to_cdm_v6` via `normalized_kind`.
- **Schemas:** add a new JSON Schema branch under `schemas/` and wire `validator.normalized_parsed_schema_for_kind`.

No IRS implementation lives here until the FX scaling phases stabilize; see `docs/fx_product_matrix.md` and repository `plan.md`.
