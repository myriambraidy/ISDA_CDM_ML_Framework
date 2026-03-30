# Implementation plan: repository restructure, CLI cleanup, agent separation, dead-code removal

**Status:** planning only — do not treat this file as executed work.  
**Companion:** `research.md` (current-state inventory).  
**Decision locked:** keep `data/` and `schemas/` (including `schemas/jsonschema/`).

---

## 1. Goals


| Goal                       | Success criteria                                                                                                                                                |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CLI**                    | Few top-level commands; shared LLM/Rosetta options in one place (config file + env); no triple-copy of `--model` / `--max-iterations` across three subcommands. |
| **Separation of concerns** | Deterministic **core** vs **agents** (mapping, java_codegen, enrichment) with clear public APIs; orchestration in one thin **pipelines** layer.                 |
| **Java / Rosetta**         | Single canonical tree for Maven JAR + generated Java; all Python path logic goes through one **repo root** resolver (no `parent.parent.parent` drift).          |
| **Tests & fixtures**       | Agent tests colocated or mirror package tree; integration tests explicit about Java/JAR availability.                                                           |
| **Dead / legacy code**     | Removed or implemented; duplicate `generated/` root eliminated; broken imports fixed.                                                                           |


---

## 2. Research summary (findings that drive the plan)

### 2.1 Path coupling today (must centralize)

These locations hard-code paths relative to `fpml_cdm` or repo root:

- `fpml_cdm/validator.py` — `SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "schemas"`
- `fpml_cdm/cdm_official_schema.py` — `schemas/jsonschema`, Trade schema file
- `fpml_cdm/java_gen/schema_index.py` — `schemas/jsonschema`
- `fpml_cdm/java_gen/tools.py` — `PROJECT_ROOT`, `GENERATED_DIR`, `JAR_PATH` under `rosetta-validator/`
- `fpml_cdm/rosetta_validator.py` — JAR search under `rosetta-validator/target/`
- `fpml_cdm/transformers/cdm_common.py` — `data/lei/bic_to_lei.json`
- `fpml_cdm/agents/lei_resolver.py` — same LEI path
- `scripts/compile_generated.ps1`, `scripts/java_env_check.ps1` — `rosetta-validator` paths

**Risk:** Moving `rosetta-validator` → `java/rosetta-validator` without updating all of these breaks validation and Java codegen.

### 2.2 Duplicate / inconsistent “generated” Java output

- **Authoritative write path:** `fpml_cdm/java_gen/tools.py` uses `PROJECT_ROOT / "rosetta-validator" / "generated"`.
- **Repo root `generated/`:** listed in `.gitignore`; may still contain stale copies; confuses humans.
- **Test bug:** `tests/test_java_gen/test_agent.py` `RealLLMIntegrationTests` uses `GENERATED_DIR = Path("generated")` while production code writes to `rosetta-validator/generated/`. Assertions use `GENERATED_DIR / f"{CDM_FIXTURE_JAVA_CLASS}.java"` — **wrong directory** unless something copies files; this should be aligned in the rewrite.

### 2.3 Broken or unused code


| Item                                                                       | Evidence                                                                                                                    | Plan                                                                                           |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `fpml_cdm.parser_enrichment`                                               | `scripts/run_fpml_mapping_to_java.py` imports `ParserEnrichmentConfig`, `run_parser_enrichment` — **module does not exist** | Implement minimal module **or** remove `--enrich-parser` and all references until designed.    |
| `data/fx_product_matrix.json`                                              | No Python references (only historical docs)                                                                                 | Wire into tests/docs **or** delete / move to `docs/assets/`.                                   |
| `scripts/run_fpml_mapping_to_java.py` vs `cli.cmd_generate_java_from_fpml` | Overlapping pipeline; script has extra flags (`--rosetta` default off, `--enrich-parser`)                                   | Single internal API `pipelines.run_fpml_to_java(...)`; script becomes thin wrapper or removed. |


### 2.4 `.gitignore` and `.agent/`

- `.gitignore` currently ignores `**.agent/`** — Cursor skills under `.agent/skills/` may be untracked in some clones. Decide policy: track skills, or document that they live elsewhere.

### 2.5 `schemas/jsonschema/` size





s the directory; CDM Trade validation uses `RefResolver` with local `$ref`. **Keep as-is** unless a later phase introduces a versioned tarball download — out of scope for first rewrite pass.

---

## 3. Target layout (end state)

Prefer **minimal churn** on import strings: keep top-level package name `fpml_cdm` for import stability unless you explicitly rebrand.

```text
repo/
├── requirements.txt
├── Makefile, make.ps1, make.sh
├── config/
│   └── examples/
│       ├── default.yaml              # LLM + agent limits + paths
│       └── ci-minimal.yaml
│
├── fpml_cdm/                         # package root (unchanged name) OR src/fpml_cdm if you adopt src layout
│   ├── __init__.py                   # public re-exports + deprecation shims (temporary)
│   ├── __main__.py                   # delegates to cli.main
│   ├── paths.py                      # NEW: repo_root(), schemas_dir(), java_dir(), data_dir()
│   │
│   ├── core/                         # deterministic stack
│   │   ├── parser.py
│   │   ├── rulesets.py
│   │   ├── ruleset_engine.py
│   │   ├── transformer.py
│   │   ├── transformers/
│   │   ├── validator.py
│   │   ├── types.py
│   │   ├── adapters/
│   │   ├── xml_utils.py
│   │   ├── cdm_official_schema.py
│   │   └── rosetta_bridge.py         # renamed from rosetta_validator.py (optional; see §6)
│   │
│   ├── llm/                          # shared providers (unchanged conceptually)
│   │   ├── base.py
│   │   ├── openai_compatible.py
│   │   ├── gemini_provider.py
│   │   └── ...
│   ├── llm_enricher.py               # OR move to core/llm_enricher.py
│   │
│   ├── agents/
│   │   ├── mapping/
│   │   │   ├── agent.py
│   │   │   ├── tools.py
│   │   │   ├── registry.py
│   │   │   └── prompts.py            # extracted SYSTEM_PROMPT strings
│   │   ├── java_codegen/
│   │   │   ├── agent.py
│   │   │   ├── tools.py
│   │   │   ├── tools.json
│   │   │   ├── schema_index.py
│   │   │   ├── openrouter_client.py
│   │   │   └── java_templates/
│   │   └── enrichment/               # former fpml_cdm/agents/*
│   │       ├── lei_resolver.py
│   │       ├── taxonomy.py
│   │       └── ...
│   │
│   ├── pipelines/
│   │   ├── fpml_to_cdm.py            # former pipeline.py
│   │   └── fpml_to_java.py           # former fpml_to_cdm_java.py
│   │
│   └── cli/
│       ├── main.py
│       └── commands/
│           ├── validate.py
│           ├── convert.py
│           └── agents.py             # mapping / java / full-pipeline subcommands
│
├── java/                             # OPTIONAL rename from rosetta-validator at repo root
│   ├── rosetta-validator/            # Maven module (pom.xml unchanged inside)
│   │   ├── generated/                # sole canonical Java output
│   │   └── target/
│   └── env/
│       ├── README.md                 # JDK 11+, mvn package
│       └── scripts/                  # compile_generated.ps1, java_env_check.ps1 moved from scripts/
│
├── schemas/                          # KEEP (contract + jsonschema tree)
├── data/                             # KEEP (corpus, lei, isda reference, reports)
├── tests/
│   ├── core/
│   ├── agents/
│   │   ├── mapping/
│   │   └── java_codegen/
│   └── integration/
├── scripts/                          # THIN: corpus-import, corpus-check calling fpml_cdm APIs
└── tmp/                              # gitignored
```

**Note:** If renaming `rosetta-validator/` → `java/rosetta-validator/`, update Maven docs, Makefile, PS1, and `paths.java_home()` in one commit.

---

## 4. Central path resolution (`fpml_cdm/paths.py`)

**Purpose:** one place for repo root so moving `fpml_cdm` under `src/` or renaming `java/` does not require editing 8 files.

**Proposed implementation sketch:**

```python
# fpml_cdm/paths.py
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=1)
def repo_root() -> Path:
    env = os.environ.get("FPML_CDM_REPO_ROOT")
    if env:
        return Path(env).resolve()
    # fpml_cdm/paths.py -> fpml_cdm -> repo root (adjust if using src layout: parents[2])
    return Path(__file__).resolve().parent.parent

def schemas_dir() -> Path:
    return repo_root() / "schemas"

def schemas_jsonschema_dir() -> Path:
    return schemas_dir() / "jsonschema"

def data_dir() -> Path:
    return repo_root() / "data"

def java_module_dir() -> Path:
    """Directory containing the Maven rosetta-validator project."""
    return repo_root() / "java" / "rosetta-validator"  # or repo_root() / "rosetta-validator" during migration

def rosetta_jar_path() -> Path:
    return java_module_dir() / "target" / "rosetta-validator-1.0.0.jar"

def java_generated_dir() -> Path:
    return java_module_dir() / "generated"
```

**Migration tactic:** introduce `paths.py` first while keeping **physical** `rosetta-validator/` at old location; `java_module_dir()` returns `repo_root() / "rosetta-validator"` behind a feature flag or second search path:

```python
def java_module_dir() -> Path:
    root = repo_root()
    preferred = root / "java" / "rosetta-validator"
    legacy = root / "rosetta-validator"
    if preferred.joinpath("pom.xml").is_file():
        return preferred
    return legacy
```

After the directory move, delete the legacy branch.

---

## 5. CLI redesign

### 5.1 Problems today

`cli.py` (~700 lines) registers overlapping arguments for:

- `convert` — mapping provider, mapping model, many mapping limits, LLM field recovery, outputs
- `generate-java` — provider, model, iterations, tool calls, timeout, trace, java class
- `generate-java-from-fpml` — duplicates most of the above + mapping-specific knobs

### 5.2 Target surface (example)

**Tier 1 — always needed**

```text
fpml-cdm validate [--rosetta] INPUT.json|INPUT.xml
fpml-cdm convert INPUT.xml [-o result.json] [--config FILE]
```

**Tier 2 — agents**

```text
fpml-cdm agents mapping run INPUT.xml [--config FILE]
fpml-cdm agents java run INPUT.cdm.json [--config FILE]
fpml-cdm agents pipeline fpml-to-java INPUT.xml --out-dir DIR [--config FILE]
```

**Tier 3 — low-level (optional, for debugging)**

```text
fpml-cdm debug parse|transform|validate-schema ...
```

### 5.3 Shared config (YAML example)

```yaml
# config/examples/default.yaml
llm:
  provider: openrouter          # openrouter | openai
  model: minimax/minimax-m2.5
  # api_key from env: OPENROUTER_API_KEY / OPENAI_API_KEY

mapping_agent:
  max_iterations: 10
  max_tool_calls: 80
  timeout_seconds: 300
  semantic_no_improve_limit: 3
  enable_rosetta: true
  rosetta_timeout_seconds: 60

java_codegen:
  max_iterations: 20
  max_tool_calls: 50
  timeout_seconds: 600

paths:
  # optional overrides; default via fpml_cdm.paths.repo_root()
  # java_module: java/rosetta-validator
```

### 5.4 Argparse composition sketch

```python
# fpml_cdm/cli/main.py
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fpml-cdm")
    p.add_argument("--config", type=Path, help="YAML agent/LLM defaults")
    sub = p.add_subparsers(dest="cmd", required=True)

    convert_p = sub.add_parser("convert")
    convert_p.add_argument("input", type=Path)
    convert_p.add_argument("-o", "--output", type=Path)
    # Only overrides; load config first then argparse on top
    convert_p.set_defaults(func=run_convert)
    ...
    return p


def run_convert(args: argparse.Namespace) -> int:
    cfg = load_merged_config(args.config, args)  # deep-merge: CLI wins
    ...
```

**Library:** use `PyYAML` only if you add `pyyaml` to requirements; otherwise TOML via `tomllib` (stdlib 3.11+) is an option for `config.toml`.

### 5.5 Backward compatibility

For one release cycle, support legacy invocations:

```text
python -m fpml_cdm generate-java-from-fpml ...
```

Map to `fpml-cdm agents pipeline fpml-to-java` internally and emit `DeprecationWarning` on stderr.

---

## 6. Package moves and import shims

### 6.1 Mechanical moves (files)


| Current                          | New                                              |
| -------------------------------- | ------------------------------------------------ |
| `fpml_cdm/pipeline.py`           | `fpml_cdm/pipelines/fpml_to_cdm.py`              |
| `fpml_cdm/fpml_to_cdm_java.py`   | `fpml_cdm/pipelines/fpml_to_java.py`             |
| `fpml_cdm/mapping_agent/*`       | `fpml_cdm/agents/mapping/*`                      |
| `fpml_cdm/java_gen/*`            | `fpml_cdm/agents/java_codegen/*`                 |
| `fpml_cdm/agents/*` (enrichment) | `fpml_cdm/agents/enrichment/*`                   |
| `fpml_cdm/rosetta_validator.py`  | `fpml_cdm/core/rosetta_bridge.py` (name clarity) |


### 6.2 Temporary compatibility layer (`fpml_cdm/pipeline.py` stub)

```python
# fpml_cdm/pipeline.py — DEPRECATED: remove in v2
"""Backward-compatible imports; use fpml_cdm.pipelines.fpml_to_cdm."""
from warnings import warn
from fpml_cdm.pipelines.fpml_to_cdm import convert_fpml_to_cdm

warn("fpml_cdm.pipeline is deprecated; use fpml_cdm.pipelines.fpml_to_cdm", DeprecationWarning, stacklevel=2)
__all__ = ["convert_fpml_to_cdm"]
```

Same pattern for `fpml_cdm.mapping_agent` → re-export from `fpml_cdm.agents.mapping`.

### 6.3 Update `fpml_cdm/__init__.py`

Keep exporting stable names (`parse_fpml_fx`, `convert_fpml_to_cdm`, `transform_to_cdm_v6`, …) so external callers and tests need minimal changes. Internally, import from `core` / `pipelines`.

---

## 7. Pipelines: single orchestration API

**Goal:** `scripts/run_fpml_mapping_to_java.py` and CLI both call one function.

```python
# fpml_cdm/pipelines/fpml_to_java.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

@dataclass
class FpmlToJavaConfig:
    mapping: Any   # MappingAgentConfig
    java: Any      # AgentConfig
    mapping_model: str
    java_model: str
    mapping_enabled: bool = True
    output_dir: Path = Path("tmp")
    java_class_name: Optional[str] = None
    # parser_enrichment: Optional[ParserEnrichmentConfig] = None  # when implemented

def run_fpml_to_java(
    fpml_path: str | Path,
    *,
    llm_client: object,
    cfg: FpmlToJavaConfig,
    log_progress: bool | None = None,
) -> Tuple[Any, Any, Path]:
    """Returns (java_agent_result, mapping_agent_result|None, cdm_json_path)."""
    ...
```

CLI `agents pipeline` and Makefile targets call `run_fpml_to_java` only.

---

## 8. Parser enrichment decision

**Option A — Remove:** Delete `--enrich-parser` from `run_fpml_mapping_to_java.py` and all summary fields until product owner defines behavior.

**Option B — Implement minimal module:** `fpml_cdm/agents/parser_enrichment/` with:

- `ParserEnrichmentConfig` (max_attempts, rosetta flags)
- `run_parser_enrichment(fpml_path, llm_client, model, config) -> ParserEnrichmentResult`

Implementation can wrap existing `parse_fpml_fx(..., recovery_mode=True)` + `LLMFieldEnricher` or a small tool loop — **spec separately** before coding.

**Recommendation:** Option A for first merge (unblocks script import); Option B as a tracked follow-up issue.

---

## 9. Dead code and legacy removal checklist


| Action                                                        | Detail                                                             |
| ------------------------------------------------------------- | ------------------------------------------------------------------ |
| Remove or fix `parser_enrichment` import                      | See §8                                                             |
| Delete root `generated/` after confirming no CI depends on it | Keep only `java/.../generated/` or `rosetta-validator/generated/`  |
| Align `RealLLMIntegrationTests`                               | Use `java_gen.tools.GENERATED_DIR` or `paths.java_generated_dir()` |
| Deduplicate `run_fpml_mapping_to_java.py`                     | Thin wrapper ≤ 30 lines calling `run_fpml_to_java` or delete       |
| `data/fx_product_matrix.json`                                 | Document, test, or remove                                          |
| Audit `.agent/skills` scripts                                 | Point to `python -m fpml_cdm` or delete duplicates to avoid drift  |
| `vulture` / `ruff check --select F401`                        | Optional sweep after moves                                         |


---

## 10. Test relocation plan


| Current                                | Target                                                                                                    |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `tests/test_java_gen/`*                | `tests/agents/java_codegen/` (or keep name `test_java_gen` for discovery)                                 |
| New                                    | `tests/agents/mapping/test_mapping_agent.py` (move mapping-specific tests out of integration blob if any) |
| `tests/test_fpml_to_java_from_fpml.py` | `tests/integration/test_fpml_to_java.py`                                                                  |
| `tests/test_rosetta_validator.py`      | `tests/integration/` or `tests/core/` depending on whether JAR is required                                |


**Discovery:** keep `python -m unittest discover -s tests -p "test_*.py"` working; if subfolders deepen, no change needed.

**Markers:** use `@unittest.skipUnless(jar_exists, ...)` consistently; consider `pytest` + markers in a later phase (optional).

---

## 11. Makefile / scripts changes

```makefile
# Makefile (conceptual)
REPO_ROOT := $(CURDIR)
JAVA_DIR := $(REPO_ROOT)/java/rosetta-validator
ROSETTA_JAR := $(JAVA_DIR)/target/rosetta-validator-1.0.0.jar

rosetta-build:
	cd $(JAVA_DIR) && mvn package -q -DskipTests

generate-java:
	$(PYTHON) -m fpml_cdm agents java run tests/fixtures/expected/fx_forward_cdm.json
```

`scripts/compile_generated.ps1` and `java_env_check.ps1` move under `java/env/scripts/` and parameterize `JAVA_DIR` via env var defaulting to repo-relative path.

---

## 12. Phased rollout (recommended order)


| Phase | Scope                                                                                                                                                       | Exit gate                                      |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| **0** | Add `fpml_cdm/paths.py`; switch `validator`, `cdm_official_schema`, `schema_index`, `java_gen/tools`, `rosetta_validator`, LEI paths to use it; tests green | No directory moves yet                         |
| **1** | Move Maven tree to `java/rosetta-validator` (optional) + update Makefile/PS1/paths                                                                          | `make rosetta-build`, `validate-rosetta` works |
| **2** | Introduce `pipelines/fpml_to_java.py` API; refactor CLI + script to use it; fix parser_enrichment (remove or implement)                                     | Script runs without ImportError                |
| **3** | Physical package restructure (`core/`, `agents/`, `pipelines/`) with shim modules                                                                           | Full unittest green                            |
| **4** | CLI split + config file; deprecate old subcommand names                                                                                                     | Manual smoke + update `research.md`            |
| **5** | Remove shims + dead files + root `generated/`; align integration tests                                                                                      | Clean `git status`                             |
| **6** | Optional: rebrand (package/CLI naming)                                                                                                                      | Separate release notes                         |


---

## 13. Risks and mitigations


| Risk                                                | Mitigation                                                                                   |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Broken imports for external consumers of `fpml_cdm` | Keep `__init__.py` exports and deprecation shims ≥1 release                                  |
| Windows path / Git Bash issues                      | Already documented in `cli.py`; centralize path resolution and document `FPML_CDM_REPO_ROOT` |
| Large diff                                          | Strict phase boundaries; run full test suite after each phase                                |
| Rosetta JAR path in CI                              | Document env vars; integration tests skip if JAR missing                                     |


---

## 14. Documentation updates (after implementation)

- Refresh `research.md` with final layout and CLI.
- Add `java/env/README.md` for JDK/Maven/JAR.
- Restore or add root `README.md` with install + one-liner examples.

---

## 15. Out of scope (explicit)

- Slimming `schemas/jsonschema/` to a subset (unless build times force it).
- Replacing JSON Schema validation with Rosetta-only (architectural change).
- Migrating from `unittest` to `pytest` (optional follow-up).
- Full product rebrand name (plan uses `fpml-cdm` CLI as example only).

---

*End of plan.*