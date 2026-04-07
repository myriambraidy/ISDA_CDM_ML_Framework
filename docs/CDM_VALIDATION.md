# CDM v6 structural validation

This repository pins **one CDM v6 lineage** for JSON Schema bundles and the **Rosetta** JVM validator.

## Artifacts

| Artifact | Location |
|----------|----------|
| Official FINOS-style JSON Schemas (Draft 04) | `schemas/jsonschema/` |
| Rosetta type validator (shaded JAR) | `rosetta-validator/target/rosetta-validator-1.0.0.jar` (build with `cd rosetta-validator && mvn package -q`) |

Keep the schema tree and the JAR built from the **same CDM release** where possible. If you upgrade CDM, refresh both.

## Unified API

- **Python:** `fpml_cdm.cdm_structure_validator.validate_cdm_structure`
- **CLI:** `python -m fpml_cdm.cli validate-cdm-structure <file.json>`
- **Java codegen tool:** `validate_output` (returns the full report dict)

Layers: **envelope** → **json_schema** (Draft 04 Trade / TradeState) → **Rosetta** → **supplementary** (registry, optional checks).

## Mandatory Rosetta

Full validation expects **Java on PATH** and the **built JAR**. If either is missing, `structure_ok` is false and issues include `INFRA_BLOCKED_NO_JAVA` or `INFRA_BLOCKED_NO_JAR`.

For local development only, you may set **`FPML_CDM_ALLOW_NO_ROSETTA=1`** (unsafe): Rosetta is skipped and an error issue `ROSETTA_SKIPPED_ALLOW_ENV` is recorded — **do not** use this for release gates.

## CI

Integration tests that call the real JAR are skipped when the JAR is absent (`unittest.skipUnless`). Ensure `mvn package` runs before tests that must enforce Rosetta on CI, or rely on agents that build the JAR first.
