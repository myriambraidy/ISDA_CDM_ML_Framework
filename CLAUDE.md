# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Deterministic FpML FX Forward conversion pipeline: `FpML XML → normalized JSON → CDM v6 JSON`

Supported products: `fxForward`, `fxSingleLeg` (including NDF via `nonDeliverableSettlement`). All other products are rejected with structured `UNSUPPORTED_PRODUCT` errors.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Commands

```bash
# Run tests (quality gate)
make check

# Run tests directly
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -q

# Run a single test
.venv/bin/python -m unittest tests.test_pipeline.PipelineTests.test_convert_pipeline_success

# CLI subcommands
python3 -m fpml_cdm parse <fpml.xml>       # FpML XML → normalized JSON
python3 -m fpml_cdm transform <parsed.json> # normalized JSON → CDM JSON
python3 -m fpml_cdm validate --fpml <f.xml> --cdm <cdm.json>
python3 -m fpml_cdm convert <fpml.xml>     # end-to-end

# Corpus operations
make corpus-import      # downloads official FpML examples to data/corpus/fpml_official/
make corpus-check       # batch conversion check, output → data/corpus/reports/latest.json
make corpus-check-fx    # FX derivatives only, output → data/corpus/reports/latest_fx.json
```

## Architecture

The pipeline has four distinct stages, each in its own module:

1. **`fpml_cdm/parser.py`** — Parses FpML XML (namespace-agnostic via local-name traversal) into `NormalizedFxForward`. Collects `ValidationIssue` objects; raises `ParserError` on failure. `strict=True` (default) raises on any error; `strict=False` only raises on error-level issues.

2. **`fpml_cdm/transformer.py`** — Pure function `transform_to_cdm_v6(model)` that maps `NormalizedFxForward` → CDM v6 `{"trade": {...}}` dict. No I/O, fully deterministic.

3. **`fpml_cdm/validator.py`** — Validates the CDM output in two ways:
   - **Schema validation**: uses `jsonschema` (Draft 2020-12) against `schemas/fpml_fx_forward_parsed.schema.json` and `schemas/cdm_fx_forward.schema.json`
   - **Semantic validation**: field-by-field cross-check of `NormalizedFxForward` vs the CDM dict; produces `MappingScore` with accuracy percentage

4. **`fpml_cdm/pipeline.py`** — `convert_fpml_to_cdm(path)` orchestrates parse → transform → validate, returning `ConversionResult`.

**Types** (`fpml_cdm/types.py`): `NormalizedFxForward`, `ConversionResult`, `ValidationReport`, `ValidationIssue`, `MappingScore`, `ParserError`, `ErrorCode`.

**Public API** (exported from `fpml_cdm/__init__.py`): `parse_fpml_fx`, `parse_fpml_xml`, `transform_to_cdm_v6`, `validate_transformation`, `validate_schema_data`, `validate_conversion_files`, `convert_fpml_to_cdm`, `EnrichmentConfig`.

### Optional agent-style enrichment (Phase 3)

`convert_fpml_to_cdm(fpml_path, enrichment=EnrichmentConfig(...))` — see `fpml_cdm/agents/`:

- **LEI**: `LocalBicLeiTable` / `GleifLeiResolver` / `ChainedLeiResolver` — BIC-like `party.name` → `party.lei` + CDM LEI `partyId` in transformer.
- **Taxonomy**: `taxonomy_mode` — `deterministic` (default qualifier only), `rules_ndf` (NDF heuristic → `ForeignExchange_NDF`), `agent` (+ `taxonomy_llm`).
- **Addresses**: `apply_document_addresses=True` runs `apply_document_address_pattern` (tradeLot `location` + `observable`; conservative vs strict CDM schema).
- **Diff-and-fix**: `run_diff_fix=True`, optional `diff_fix_llm` — deterministic fixes + optional LLM JSON patch hook.

Trace: `ConversionResult.enrichment_trace`.

## Test Fixtures

- `tests/fixtures/fpml/` — input FpML XML files
- `tests/fixtures/expected/` — expected normalized and CDM JSON outputs
- Test modules: `test_parser.py`, `test_transformer.py`, `test_validator.py`, `test_pipeline.py`

## Error Taxonomy

`ErrorCode` enum values: `UNSUPPORTED_PRODUCT`, `MISSING_REQUIRED_FIELD`, `INVALID_VALUE`, `SCHEMA_VALIDATION_FAILED`, `SEMANTIC_VALIDATION_FAILED`
