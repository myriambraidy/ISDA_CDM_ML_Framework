# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Deterministic FpML FX Forward conversion pipeline: `FpML XML → normalized JSON → CDM v6 JSON`

Supported products: `fxForward`, `fxSingleLeg` (including NDF via `nonDeliverableSettlement`), `fxSwap`, `fxOption`, registered in [`fpml_cdm/adapters/registry.py`](fpml_cdm/adapters/registry.py). Backlog / future FX rows: [`docs/fx_product_matrix.md`](docs/fx_product_matrix.md). Rates/IRS hooks: [`fpml_cdm/models/rates/README.md`](fpml_cdm/models/rates/README.md). All unregistered trade products are rejected with structured `UNSUPPORTED_PRODUCT` errors.

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

## Java Environment (rosetta-validator/generated/)

`rosetta-validator/generated/*.java` compilation/execution uses:
- `rosetta-validator/target/rosetta-validator-1.0.0.jar`
- `rosetta-validator/generated/` on classpath

Windows quick checks:
```powershell
java -version
javac -version
mvn -version
```

Build required jar:
```powershell
cd rosetta-validator
mvn package -DskipTests
```

Compile + run one generated class manually:
```powershell
javac -cp "C:\path\to\fpml_isdacm\rosetta-validator\target\rosetta-validator-1.0.0.jar;C:\path\to\fpml_isdacm\rosetta-validator\generated" -d "C:\path\to\fpml_isdacm\rosetta-validator\generated" "C:\path\to\fpml_isdacm\rosetta-validator\generated\BondOptionOutput.java"
java -cp "C:\path\to\fpml_isdacm\rosetta-validator\target\rosetta-validator-1.0.0.jar;C:\path\to\fpml_isdacm\rosetta-validator\generated" BondOptionOutput
```

Troubleshooting:
- `javac not found`: JDK is missing (JRE alone is insufficient) or PATH is wrong.
- jar missing: build in `rosetta-validator` with Maven.
- wrong classpath separator: use `;` on Windows, `:` on Linux/macOS.
- `public class X` mismatch: filename must be `X.java`.
- Git Bash path escapes (`\r`, `\t`): use forward slashes or quote paths.

Helper scripts:
- `scripts/java_env_check.ps1` (env + optional jar build)
- `scripts/compile_generated.ps1` (compile/run `rosetta-validator/generated/<Class>.java`)

## Architecture

The pipeline has four distinct stages, each in its own module:

1. **`fpml_cdm/adapters/registry.py`** — FX adapter table (`adapter_id`, priority, `normalized_kind`) and `detect_fx_adapter_product` for `<trade>` child selection (deterministic; priority + economic-presence tie-break).

2. **`fpml_cdm/parser.py`** — Parses FpML XML (namespace-agnostic) into `NormalizedFxForward`, `NormalizedFxSwap`, or `NormalizedFxOption` (members of `NormalizedFxTrade` union). `SUPPORTED_PRODUCTS` mirrors the registry. Collects `ValidationIssue` objects; raises `ParserError` on failure. `strict=True` (default) raises on any error; `strict=False` only raises on error-level issues.

3. **`fpml_cdm/transformer.py`** — `transform_to_cdm_v6(model)` dispatches on `normalized_kind` (`fx_spot_forward_like` → `transformers/fx_spot_forward.py`, `fx_swap` → `transformers/fx_swap.py`, `fx_option` → `transformers/fx_option.py`). No I/O, fully deterministic.

4. **`fpml_cdm/validator.py`** — Schema + semantic validation:
   - **Normalized JSON**: `validate_normalized_parsed_dict` picks schema by `normalizedKind` (see `schemas/fpml_normalized_trade.schema.json` + per-kind schemas such as `fpml_fx_forward_parsed.schema.json`, `fpml_fx_swap_parsed.schema.json`, `fpml_fx_option_parsed.schema.json`).
   - **CDM**: official FINOS trade schema + semantic cross-check per `normalized_kind`.

5. **`fpml_cdm/pipeline.py`** — `convert_fpml_to_cdm(path)` orchestrates parse → transform → validate, returning `ConversionResult`.

**Types** (`fpml_cdm/types.py`): `NormalizedFxForward`, `NormalizedFxSwap`, `NormalizedFxOption`, `NormalizedFxTrade`, `NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE`, `NORMALIZED_KIND_FX_SWAP`, `NORMALIZED_KIND_FX_OPTION`, `ConversionResult`, `ValidationReport`, `ValidationIssue`, `MappingScore`, `ParserError`, `ErrorCode`.

**Public API** (exported from `fpml_cdm/__init__.py`): `parse_fpml_fx`, `parse_fpml_xml`, `transform_to_cdm_v6`, `validate_transformation`, `validate_normalized_parsed_dict`, `validate_schema_data`, `validate_conversion_files`, `convert_fpml_to_cdm`, `EnrichmentConfig`, adapter registry symbols as listed in `__init__.py`.

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
