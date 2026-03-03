# fpml_isdacdm (Deterministic JSON-First POC)

Deterministic FpML FX Forward conversion pipeline:

`FpML XML -> normalized JSON -> CDM v6 JSON`

## Scope

- Supported products: `fxForward`, `fxSingleLeg`
- NDF support via `nonDeliverableSettlement`
- Unsupported products (for example `fxDigitalOption`) are rejected with structured `UNSUPPORTED_PRODUCT` errors.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## CLI

```bash
python3 -m fpml_cdm parse tests/fixtures/fpml/fx_forward.xml
python3 -m fpml_cdm transform tests/fixtures/expected/fx_forward_parsed.json
python3 -m fpml_cdm validate --fpml tests/fixtures/fpml/fx_forward.xml --cdm tests/fixtures/expected/fx_forward_cdm.json
python3 -m fpml_cdm convert tests/fixtures/fpml/fx_forward.xml
```

### Subcommands

- `parse`: FpML XML -> normalized JSON
- `transform`: normalized JSON -> CDM JSON
- `validate`: validate CDM JSON against source FpML with schema + semantic checks
- `convert`: end-to-end parse + transform + validate

## Python API

```python
from fpml_cdm import parse_fpml_fx, transform_to_cdm_v6, validate_transformation, convert_fpml_to_cdm

model = parse_fpml_fx("tests/fixtures/fpml/fx_forward.xml")
cdm = transform_to_cdm_v6(model)
report = validate_transformation("tests/fixtures/fpml/fx_forward.xml", cdm)
result = convert_fpml_to_cdm("tests/fixtures/fpml/fx_forward.xml")
```

## Error Taxonomy

- `UNSUPPORTED_PRODUCT`
- `MISSING_REQUIRED_FIELD`
- `INVALID_VALUE`
- `SCHEMA_VALIDATION_FAILED`
- `SEMANTIC_VALIDATION_FAILED`

## Quality Gates

```bash
make check
```

`make check` runs the deterministic regression test suite.
`make check` runs the deterministic regression test suite.

## Local Corpus Import and Batch Checks

Import official FpML example corpora into a local folder for repeatable offline testing:

```bash
make corpus-import
```

This downloads to `data/corpus/fpml_official` and writes an import manifest to `data/corpus/fpml_official/manifest.json`.

Run conversion checks across the full local corpus:

```bash
make corpus-check
```

Run checks only for FX derivatives paths:

```bash
make corpus-check-fx
```

Batch reports are written to `data/corpus/reports/latest.json`.
FX-only batch reports are written to `data/corpus/reports/latest_fx.json`.

You can also run scripts directly:

```bash
.venv/bin/python scripts/import_fpml_corpus.py --dest data/corpus/fpml_official
.venv/bin/python scripts/run_local_corpus_check.py --corpus data/corpus/fpml_official --output data/corpus/reports/latest.json
```
