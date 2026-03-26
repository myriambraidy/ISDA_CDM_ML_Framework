"""
Diff-and-fix: post-transform repair using validation errors + optional LLM callback.

Deterministic rules handle common schema messages; unresolved errors are returned.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..types import ValidationIssue

# Optional LLM: given prompt, return JSON patch dict or full CDM fragment
LlmFixFn = Callable[[str], str]


def _deep_set(root: Dict[str, Any], path_parts: List[str], value: Any) -> None:
    cur: Any = root
    for p in path_parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[path_parts[-1]] = value


def apply_deterministic_fixes(
    cdm: Dict[str, Any],
    errors: List[ValidationIssue],
) -> Tuple[Dict[str, Any], List[ValidationIssue]]:
    """
    Copy ``cdm`` and apply a small set of keyword-driven fixes. Returns (new_cdm, remaining_errors).
    """
    out = copy.deepcopy(cdm)
    remaining: List[ValidationIssue] = []
    fixed_messages: set[str] = set()

    for err in errors:
        msg = (err.message or "").lower()
        path = err.path or ""

        applied = False

        # Example: ensure trade has meta if schema expects it (no-op if present)
        if "taxonomy" in msg and "product" in path.lower():
            trade = out.setdefault("trade", {})
            prod = trade.setdefault("product", {})
            if not prod.get("taxonomy"):
                prod["taxonomy"] = [
                    {"source": "ISDA", "productQualifier": "ForeignExchange_Spot_Forward"}
                ]
                applied = True

        if applied:
            fixed_messages.add(err.message)
        else:
            remaining.append(err)

    return out, remaining


def run_diff_fix_agent(
    cdm: Dict[str, Any],
    errors: List[ValidationIssue],
    *,
    llm_fix: Optional[LlmFixFn] = None,
    max_rounds: int = 1,
) -> Tuple[Dict[str, Any], List[ValidationIssue], List[str]]:
    """
    Run deterministic fixes, then optional LLM round.

    Returns ``(cdm_out, remaining_errors, trace)`` where ``trace`` describes actions.
    """
    trace: List[str] = []
    current, rest = apply_deterministic_fixes(cdm, errors)
    if len(rest) < len(errors):
        trace.append(f"deterministic_fixes: {len(errors) - len(rest)} issue(s) cleared")

    if llm_fix and rest and max_rounds > 0:
        prompt = (
            "CDM Trade JSON has schema validation errors. Return ONLY a JSON object:\n"
            '{"patch": [ {"op": "set", "path": "$.trade...", "value": ...} ] }\n'
            "Use JSON Pointer-style paths with $ root, or return {\"cdm\": {...}} full document.\n\n"
            f"Errors:\n{json.dumps([e.to_dict() for e in rest], indent=2)}\n\n"
            f"Current CDM (abbreviated):\n{json.dumps(current, indent=2)[:12000]}\n"
        )
        try:
            text = llm_fix(prompt)
        except Exception as exc:
            trace.append(f"llm_fix: failed ({exc})")
            return current, rest, trace

        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            trace.append("llm_fix: no JSON object in response")
            return current, rest, trace
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            trace.append("llm_fix: JSON parse error")
            return current, rest, trace

        if isinstance(data.get("cdm"), dict):
            current = copy.deepcopy(data["cdm"])
            trace.append("llm_fix: replaced full cdm")
        elif isinstance(data.get("patch"), list):
            # Minimal patch interpreter
            for p in data["patch"]:
                if not isinstance(p, dict):
                    continue
                if p.get("op") == "set" and isinstance(p.get("path"), str) and "value" in p:
                    path_str = p["path"].lstrip("$").lstrip(".")
                    parts = [x for x in path_str.split(".") if x]
                    if parts:
                        _deep_set(current, parts, p["value"])
            trace.append(f'llm_fix: applied {len(data["patch"])} patch(es)')

    return current, rest, trace
