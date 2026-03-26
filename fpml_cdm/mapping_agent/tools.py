from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import xml.etree.ElementTree as ET


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_descendant_local(node: ET.Element, local_name: str) -> Optional[ET.Element]:
    for elem in node.iter():
        if _local_name(elem.tag) == local_name:
            return elem
    return None


def _find_child_local(node: Optional[ET.Element], local_name: str) -> Optional[ET.Element]:
    if node is None:
        return None
    for child in list(node):
        if _local_name(child.tag) == local_name:
            return child
    return None


def inspect_fpml_trade(fpml_path: str) -> Dict[str, Any]:
    """
    Walk the XML envelope and summarize what product subtrees exist under `<trade>`.

    Output is designed to be small but useful for the mapping agent.
    """
    xml_file = Path(fpml_path)
    if not xml_file.exists():
        return {"error": f"File not found: {xml_file}"}

    root = ET.parse(xml_file).getroot()
    trade = _find_descendant_local(root, "trade")
    if trade is None:
        return {"error": "Missing required element: trade"}

    product_candidates: List[Dict[str, Any]] = []
    trade_header = _find_child_local(trade, "tradeHeader")
    trade_date = None
    if trade_header is not None:
        td = _find_child_local(trade_header, "tradeDate")
        trade_date = (td.text or "").strip() or None

    # Under our fixtures, the product is a direct child of `<trade>` (besides tradeHeader).
    for child in list(trade):
        lname = _local_name(child.tag)
        if lname == "tradeHeader":
            continue
        # Summarize presence of a few relevant local names.
        found = {k: 0 for k in [
            "valueDate",
            "nearLeg",
            "farLeg",
            "exchangedCurrency1",
            "exchangedCurrency2",
            "exchangeRate",
            "nonDeliverableSettlement",
            "nonDeliverableForward",
            "buyerPartyReference",
            "sellerPartyReference",
            "europeanExercise",
            "americanExercise",
            "bermudaExercise",
            "putCurrencyAmount",
            "callCurrencyAmount",
            "strike",
        ]}
        # Count occurrences within this subtree.
        for elem in child.iter():
            l = _local_name(elem.tag)
            if l in found:
                found[l] += 1
        product_candidates.append(
            {
                "adapter_id": lname,
                "present": True,
                "counts": found,
            }
        )

    return {
        "tradeDate": trade_date,
        "product_candidates": product_candidates,
    }


def list_supported_fx_adapters() -> Dict[str, Any]:
    """Return registered FX adapter ids, priorities, and normalized_kind (for ruleset / transform dispatch)."""
    from fpml_cdm.adapters.registry import describe_fx_adapter_registry

    return {"adapters": describe_fx_adapter_registry()}


def get_active_ruleset_summary(adapter_id: str) -> Dict[str, Any]:
    """
    Return a compact summary of which XML candidate paths are tried for
    key normalized fields.
    """
    from fpml_cdm.rulesets import get_base_ruleset

    ruleset = get_base_ruleset(adapter_id)
    fields = ruleset.get("fields", {})
    out_fields: Dict[str, Any] = {}
    for field_name, field_def in fields.items():
        out_fields[field_name] = {
            "required": bool(field_def.get("required", False)),
            "parser": field_def.get("parser"),
            "candidates": list(field_def.get("candidates", [])),
        }

    return {
        "adapter_id": adapter_id,
        "derived": ruleset.get("derived", {}),
        "fields": out_fields,
    }


def run_conversion_with_patch(
    fpml_path: str,
    patch: Dict[str, Any],
    adapter_id: str,
    *,
    enable_rosetta: bool = False,
    rosetta_timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Deterministically apply a structured patch to the ruleset, then run:
      parse (ruleset) → transform → validate

    Returns the CDM JSON (best-effort even if validation fails) plus
    a schema/semantic error summary for the agent loop.
    """
    from fpml_cdm.rulesets import get_base_ruleset
    from fpml_cdm.ruleset_engine import apply_ruleset_patch, parse_fpml_fx_with_ruleset
    from fpml_cdm.transformer import transform_to_cdm_v6
    from fpml_cdm.validator import validate_normalized_and_cdm

    base = get_base_ruleset(adapter_id)
    patched = apply_ruleset_patch(base, patch or {})

    normalized, parse_issues = parse_fpml_fx_with_ruleset(
        fpml_path=fpml_path,
        adapter_id=adapter_id,
        ruleset=patched,
        strict=False,
        recovery_mode=True,
    )
    cdm = transform_to_cdm_v6(normalized)
    report = validate_normalized_and_cdm(normalized, cdm)

    schema_error_count = sum(1 for e in report.errors if e.code == "SCHEMA_VALIDATION_FAILED")
    semantic_error_count = sum(1 for e in report.errors if e.code == "SEMANTIC_VALIDATION_FAILED")
    rosetta_failure_count = 0
    rosetta_report = None
    if enable_rosetta:
        from fpml_cdm.rosetta_validator import validate_cdm_rosetta_with_retry

        ros = validate_cdm_rosetta_with_retry(
            cdm,
            timeout_seconds=rosetta_timeout_seconds,
            max_attempts=2,
        )
        rosetta_report = ros.to_dict()
        rosetta_failure_count = 0 if ros.valid else max(1, len(ros.failures))

    return {
        "adapter_id": adapter_id,
        "patch": patch,
        "normalized": normalized.to_dict(),
        "parse_issues": [i.to_dict() for i in parse_issues],
        "cdm_json": cdm,
        "validation_report": report.to_dict(),
        "validation_summary": {
            "schema_error_count": schema_error_count,
            "semantic_error_count": semantic_error_count,
            "rosetta_failure_count": rosetta_failure_count,
            "error_count_total": len(report.errors),
        },
        "rosetta_report": rosetta_report,
    }


def validate_best_effort(
    fpml_path: str,
    cdm_json: Dict[str, Any],
    *,
    enable_rosetta: bool = False,
    rosetta_timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Validate `cdm_json` for the given `fpml_path`.

    Notes:
      - this tool is deterministic
      - Rosetta is best-effort and optional
    """
    from fpml_cdm.validator import validate_conversion_files
    from fpml_cdm.rosetta_validator import validate_cdm_rosetta_with_retry

    # Re-parse normalized from the source, since validate_conversion_files is
    # source-bound. For patch-based validation, the agent should prefer
    # `run_conversion_with_patch`, which uses the patched normalized model.
    report = validate_conversion_files(fpml_path, _write_tmp_cdm_json(cdm_json))

    rosetta_report = None
    rosetta_errors = []
    if enable_rosetta:
        try:
            ros = validate_cdm_rosetta_with_retry(
                cdm_json,
                timeout_seconds=rosetta_timeout_seconds,
                max_attempts=2,
            )
            rosetta_report = ros.to_dict()
            rosetta_errors = ros.to_issues()
        except Exception as exc:
            rosetta_report = {"error": f"{type(exc).__name__}: {exc}"}

    if enable_rosetta and rosetta_errors:
        # Merge rosetta failures into the report errors for visibility.
        report.errors.extend(rosetta_errors)
        report.valid = len(report.errors) == 0

    out: Dict[str, Any] = report.to_dict()
    out["rosetta_report"] = rosetta_report
    return out


def _write_tmp_cdm_json(cdm_json: Dict[str, Any]) -> str:
    """
    validate_conversion_files expects a JSON path; write to a temp file.
    """
    import tempfile
    import os

    _fd, path = tempfile.mkstemp(prefix="cdm_", suffix=".json")
    os.close(_fd)
    # Avoid depending on tempfile.NamedTemporaryFile behavior on Windows.
    Path(path).write_text(json.dumps(cdm_json), encoding="utf-8")
    return path

