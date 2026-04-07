"""
Unified CDM v6 **structural** validation (envelope + JSON Schema + Rosetta + supplementary).

Paths in :class:`CdmStructureIssue` use **JSON Pointer** syntax (RFC 6901), e.g. ``/trade/tradeDate``.
Empty pointer ``""`` means the root of the validated subtree.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Protocol, Tuple

from .cdm_official_schema import get_trade_schema_validator, get_trade_state_schema_validator

# ---------------------------------------------------------------------------
# Issue codes (stable API)
# ---------------------------------------------------------------------------


class CdmStructureIssueCode(str, Enum):
    ENVELOPE_NOT_OBJECT = "ENVELOPE_NOT_OBJECT"
    ENVELOPE_MISSING_KEY = "ENVELOPE_MISSING_KEY"
    JSON_SCHEMA_FAILED = "JSON_SCHEMA_FAILED"
    JSON_SCHEMA_LOAD_FAILED = "JSON_SCHEMA_LOAD_FAILED"
    ROSETTA_FAILED = "ROSETTA_FAILED"
    ROSETTA_RUNTIME_ERROR = "ROSETTA_RUNTIME_ERROR"
    SUPPLEMENTARY_FAILED = "SUPPLEMENTARY_FAILED"
    INFRA_BLOCKED_NO_JAVA = "INFRA_BLOCKED_NO_JAVA"
    INFRA_BLOCKED_NO_JAR = "INFRA_BLOCKED_NO_JAR"
    ROSETTA_SKIPPED_ALLOW_ENV = "ROSETTA_SKIPPED_ALLOW_ENV"


LayerName = Literal["envelope", "json_schema", "rosetta", "supplementary"]


@dataclass
class CdmStructureIssue:
    layer: LayerName
    code: str
    severity: Literal["error", "warning"]
    path: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "layer": self.layer,
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class RosettaBlock:
    ran: bool
    valid: Optional[bool]
    exit_code: Optional[int]
    failure_count: int
    error: Optional[str]
    failures: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ran": self.ran,
            "valid": self.valid,
            "exit_code": self.exit_code,
            "failure_count": self.failure_count,
            "error": self.error,
            "failures": self.failures,
        }


@dataclass
class CdmStructureReport:
    """Result of :func:`validate_cdm_structure`."""

    structure_ok: bool
    layers_executed: List[str]
    layer_ok: Dict[str, bool]
    error_count_by_layer: Dict[str, int]
    issues: List[CdmStructureIssue]
    rosetta: RosettaBlock
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-friendly dict with deterministic key ordering at each level."""
        out = {
            "structure_ok": self.structure_ok,
            "metadata": _sort_keys_deep(self.metadata),
            "layers_executed": list(self.layers_executed),
            "layer_ok": _sort_keys_deep(self.layer_ok),
            "error_count_by_layer": _sort_keys_deep(self.error_count_by_layer),
            "issues": [_sort_keys_deep(i.to_dict()) for i in self.issues],
            "rosetta": _sort_keys_deep(self.rosetta.to_dict()),
        }
        return {k: out[k] for k in sorted(out.keys())}


def _sort_keys_deep(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sort_keys_deep(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_sort_keys_deep(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# Supplementary checkers (registry)
# ---------------------------------------------------------------------------


class SupplementaryChecker(Protocol):
    """Optional extra checks when Rosetta/schema are insufficient (see validator-plan §6)."""

    def __call__(
        self,
        cdm_json: Dict[str, Any],
        *,
        target_type: Literal["trade", "tradeState"],
        rosetta_payload: Dict[str, Any],
    ) -> List[CdmStructureIssue]:
        ...


SUPPLEMENTARY_CHECKERS: List[SupplementaryChecker] = []


def register_supplementary_checker(checker: SupplementaryChecker) -> None:
    """Register an extra supplementary checker (tests or product extensions)."""
    SUPPLEMENTARY_CHECKERS.append(checker)


def _json_pointer_from_schema_path(path: Tuple[Any, ...]) -> str:
    if not path:
        return ""
    out: List[str] = []
    for p in path:
        if isinstance(p, int):
            out.append(str(p))
        else:
            out.append(str(p).replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(out)


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------

_ENV_ALLOW_NO_ROSETTA = "FPML_CDM_ALLOW_NO_ROSETTA"


def _read_allow_no_rosetta(allow_no_rosetta: Optional[bool]) -> bool:
    if allow_no_rosetta is not None:
        return bool(allow_no_rosetta)
    v = os.environ.get(_ENV_ALLOW_NO_ROSETTA, "").strip().lower()
    return v in ("1", "true", "yes", "on")


def validate_cdm_structure(
    cdm_json: Any,
    *,
    target_type: Literal["trade", "tradeState"] = "trade",
    run_schema: bool = True,
    run_rosetta: bool = True,
    supplementary: bool = True,
    rosetta_timeout_seconds: int = 60,
    allow_no_rosetta: Optional[bool] = None,
) -> CdmStructureReport:
    """
    Validate CDM JSON for structural conformance (v6): envelope, JSON Schema, Rosetta, supplementary.

    Top-level input must be a JSON object with a ``trade`` or ``tradeState`` key matching ``target_type``.

    **Paths** in issues are JSON Pointers relative to the document root (the input object).

    :param allow_no_rosetta: If True, skip Rosetta when Java/JAR missing (unsafe; for local dev only).
        When ``None``, reads :envvar:`FPML_CDM_ALLOW_NO_ROSETTA`.
    """
    issues: List[CdmStructureIssue] = []
    layers_executed: List[str] = []
    layer_ok: Dict[str, bool] = {}
    allow_skip = _read_allow_no_rosetta(allow_no_rosetta)

    meta: Dict[str, Any] = {"cdm_version": "6", "validator": "fpml_cdm.cdm_structure_validator"}

    key = "trade" if target_type == "trade" else "tradeState"

    # --- L0 envelope ---
    layers_executed.append("envelope")
    envelope_ok = True
    if not isinstance(cdm_json, dict):
        issues.append(
            CdmStructureIssue(
                layer="envelope",
                code=CdmStructureIssueCode.ENVELOPE_NOT_OBJECT,
                severity="error",
                path="",
                message="CDM JSON root must be a JSON object",
            )
        )
        envelope_ok = False
    elif key not in cdm_json:
        issues.append(
            CdmStructureIssue(
                layer="envelope",
                code=CdmStructureIssueCode.ENVELOPE_MISSING_KEY,
                severity="error",
                path="",
                message=f"Missing required top-level key {key!r} for target_type={target_type!r}",
                details={"expected_key": key, "target_type": target_type},
            )
        )
        envelope_ok = False

    layer_ok["envelope"] = envelope_ok

    rosetta_payload: Dict[str, Any] = {}
    subtree: Any = None
    if envelope_ok and isinstance(cdm_json, dict):
        rosetta_payload = dict(cdm_json)
        subtree = cdm_json.get(key)

    # --- L1 JSON Schema ---
    schema_layer_ok = True
    if run_schema and envelope_ok and isinstance(cdm_json, dict):
        layers_executed.append("json_schema")
        try:
            if target_type == "trade":
                validator = get_trade_schema_validator()
                if not isinstance(subtree, dict):
                    issues.append(
                        CdmStructureIssue(
                            layer="json_schema",
                            code=CdmStructureIssueCode.JSON_SCHEMA_FAILED,
                            severity="error",
                            path=f"/{key}",
                            message=f"Value at {key!r} must be an object",
                        )
                    )
                    schema_layer_ok = False
                else:
                    for err in sorted(validator.iter_errors(subtree), key=lambda e: list(e.path)):
                        ptr = f"/{key}" + _json_pointer_from_schema_path(tuple(err.path))
                        issues.append(
                            CdmStructureIssue(
                                layer="json_schema",
                                code=CdmStructureIssueCode.JSON_SCHEMA_FAILED,
                                severity="error",
                                path=ptr,
                                message=err.message,
                                details={"schema_path": list(err.path)},
                            )
                        )
                    schema_layer_ok = len([i for i in issues if i.layer == "json_schema"]) == 0
            else:
                validator = get_trade_state_schema_validator()
                if not isinstance(subtree, dict):
                    issues.append(
                        CdmStructureIssue(
                            layer="json_schema",
                            code=CdmStructureIssueCode.JSON_SCHEMA_FAILED,
                            severity="error",
                            path=f"/{key}",
                            message=f"Value at {key!r} must be an object",
                        )
                    )
                    schema_layer_ok = False
                else:
                    for err in sorted(validator.iter_errors(subtree), key=lambda e: list(e.path)):
                        ptr = f"/{key}" + _json_pointer_from_schema_path(tuple(err.path))
                        issues.append(
                            CdmStructureIssue(
                                layer="json_schema",
                                code=CdmStructureIssueCode.JSON_SCHEMA_FAILED,
                                severity="error",
                                path=ptr,
                                message=err.message,
                                details={"schema_path": list(err.path)},
                            )
                        )
                    schema_layer_ok = len([i for i in issues if i.layer == "json_schema"]) == 0
        except Exception as exc:
            issues.append(
                CdmStructureIssue(
                    layer="json_schema",
                    code=CdmStructureIssueCode.JSON_SCHEMA_LOAD_FAILED,
                    severity="error",
                    path="",
                    message=f"Failed to load or run JSON Schema validation: {exc}",
                    details={"exception_type": type(exc).__name__},
                )
            )
            schema_layer_ok = False
        layer_ok["json_schema"] = schema_layer_ok
    else:
        layer_ok["json_schema"] = True  # not executed

    # --- L2 Rosetta ---
    rosetta_block = RosettaBlock(ran=False, valid=None, exit_code=None, failure_count=0, error=None, failures=[])
    if run_rosetta and envelope_ok and isinstance(cdm_json, dict):
        layers_executed.append("rosetta")
        from . import rosetta_validator as rv

        java_ok = rv.java_available()
        jar_path = rv.find_jar()
        rosetta_layer_ok = True

        if not java_ok:
            msg = "Java not found on PATH. Install JDK 11+ to run Rosetta validation."
            if allow_skip:
                issues.append(
                    CdmStructureIssue(
                        layer="rosetta",
                        code=CdmStructureIssueCode.ROSETTA_SKIPPED_ALLOW_ENV,
                        severity="error",
                        path="",
                        message=msg + f" (unsafe skip: {_ENV_ALLOW_NO_ROSETTA} is set)",
                    )
                )
                rosetta_block = RosettaBlock(ran=False, valid=None, exit_code=None, failure_count=0, error=msg, failures=[])
                rosetta_layer_ok = False
            else:
                issues.append(
                    CdmStructureIssue(
                        layer="rosetta",
                        code=CdmStructureIssueCode.INFRA_BLOCKED_NO_JAVA,
                        severity="error",
                        path="",
                        message=msg,
                    )
                )
                rosetta_block = RosettaBlock(ran=False, valid=False, exit_code=None, failure_count=0, error=msg, failures=[])
                rosetta_layer_ok = False
        elif jar_path is None:
            msg = "Rosetta validator JAR not found. Build with: cd rosetta-validator && mvn package -q"
            if allow_skip:
                issues.append(
                    CdmStructureIssue(
                        layer="rosetta",
                        code=CdmStructureIssueCode.ROSETTA_SKIPPED_ALLOW_ENV,
                        severity="error",
                        path="",
                        message=msg + f" (unsafe skip: {_ENV_ALLOW_NO_ROSETTA} is set)",
                    )
                )
                rosetta_block = RosettaBlock(ran=False, valid=None, exit_code=None, failure_count=0, error=msg, failures=[])
                rosetta_layer_ok = False
            else:
                issues.append(
                    CdmStructureIssue(
                        layer="rosetta",
                        code=CdmStructureIssueCode.INFRA_BLOCKED_NO_JAR,
                        severity="error",
                        path="",
                        message=msg,
                    )
                )
                rosetta_block = RosettaBlock(ran=False, valid=False, exit_code=None, failure_count=0, error=msg, failures=[])
                rosetta_layer_ok = False
        else:
            result = rv.validate_cdm_rosetta(
                rosetta_payload,
                target_type=target_type,
                timeout_seconds=rosetta_timeout_seconds,
            )
            rosetta_block = RosettaBlock(
                ran=True,
                valid=result.valid,
                exit_code=result.exit_code,
                failure_count=len(result.failures),
                error=result.error,
                failures=list(result.failures),
            )
            if result.error:
                issues.append(
                    CdmStructureIssue(
                        layer="rosetta",
                        code=CdmStructureIssueCode.ROSETTA_RUNTIME_ERROR,
                        severity="error",
                        path="",
                        message=result.error,
                        details={"exit_code": result.exit_code},
                    )
                )
                rosetta_layer_ok = False
            elif not result.valid:
                for f in result.failures:
                    rule_type = f.get("type", "UNKNOWN")
                    name = f.get("name", "")
                    msg = f.get("failureMessage", "") or f.get("definition", "")
                    fpath = f.get("path", "") or ""
                    issues.append(
                        CdmStructureIssue(
                            layer="rosetta",
                            code=CdmStructureIssueCode.ROSETTA_FAILED,
                            severity="error",
                            path=fpath if fpath.startswith("/") else f"/{fpath}".replace("//", "/") if fpath else "",
                            message=f"[{rule_type}] {name}: {msg}".strip(),
                            details=dict(f),
                        )
                    )
                rosetta_layer_ok = False
            else:
                rosetta_layer_ok = True

        layer_ok["rosetta"] = rosetta_layer_ok
    else:
        layer_ok["rosetta"] = True

    # --- L3 supplementary ---
    sup_ok = True
    if supplementary and envelope_ok and isinstance(cdm_json, dict) and SUPPLEMENTARY_CHECKERS:
        layers_executed.append("supplementary")
        for checker in SUPPLEMENTARY_CHECKERS:
            try:
                extra = checker(
                    cdm_json,
                    target_type=target_type,
                    rosetta_payload=rosetta_payload,
                )
                issues.extend(extra)
            except Exception as exc:  # pragma: no cover - defensive
                issues.append(
                    CdmStructureIssue(
                        layer="supplementary",
                        code=CdmStructureIssueCode.SUPPLEMENTARY_FAILED,
                        severity="error",
                        path="",
                        message=f"Supplementary checker raised: {exc}",
                        details={"exception_type": type(exc).__name__},
                    )
                )
        sup_ok = len([i for i in issues if i.layer == "supplementary" and i.severity == "error"]) == 0
        layer_ok["supplementary"] = sup_ok
    else:
        layer_ok["supplementary"] = True

    # Sort issues for determinism
    layer_order = {"envelope": 0, "json_schema": 1, "rosetta": 2, "supplementary": 3}

    def _issue_sort_key(issue: CdmStructureIssue) -> Tuple[int, str, str, str]:
        return (layer_order.get(issue.layer, 99), issue.path, str(issue.code), issue.message)

    issues.sort(key=_issue_sort_key)

    # Count errors by layer
    error_count_by_layer: Dict[str, int] = {}
    for lyr in ("envelope", "json_schema", "rosetta", "supplementary"):
        error_count_by_layer[lyr] = len(
            [i for i in issues if i.layer == lyr and i.severity == "error"]
        )

    has_error = any(i.severity == "error" for i in issues)
    structure_ok = not has_error

    return CdmStructureReport(
        structure_ok=structure_ok,
        layers_executed=layers_executed,
        layer_ok={k: layer_ok[k] for k in sorted(layer_ok.keys())},
        error_count_by_layer=error_count_by_layer,
        issues=issues,
        rosetta=rosetta_block,
        metadata=meta,
    )


def infra_blocked(issue_codes: List[str]) -> bool:
    """True if any issue indicates missing Java/JAR (not skipped via allow env)."""
    blocked = {
        CdmStructureIssueCode.INFRA_BLOCKED_NO_JAVA,
        CdmStructureIssueCode.INFRA_BLOCKED_NO_JAR,
    }
    return bool(set(issue_codes) & {b.value for b in blocked})
