"""Coverage engine: walk FpML tree and report unmapped / ignored / mapped paths."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


@dataclass
class CoverageEntry:
    fpml_path: str
    status: str  # "mapped" | "ignored" | "unmapped"
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"fpml_path": self.fpml_path, "status": self.status, "reason": self.reason}


@dataclass
class CoverageReport:
    total_paths: int
    mapped_count: int
    ignored_count: int
    unmapped_count: int
    unmapped_paths: List[str]
    entries: List[CoverageEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_paths": self.total_paths,
            "mapped_count": self.mapped_count,
            "ignored_count": self.ignored_count,
            "unmapped_count": self.unmapped_count,
            "unmapped_paths": self.unmapped_paths,
            "coverage_pct": round(100 * (self.mapped_count + self.ignored_count) / max(self.total_paths, 1), 1),
        }


# Default envelope / metadata paths that are typically ignored
DEFAULT_IGNORE_PATTERNS: Set[str] = {
    "tradeHeader/partyTradeIdentifier/versionedTradeId",
    "tradeHeader/partyTradeIdentifier/linkId",
}


def _walk_fpml_paths(element: ET.Element, prefix: str = "") -> List[str]:
    """Recursively walk XML tree, returning list of local-name paths for leaf text nodes."""
    paths: List[str] = []
    lname = _local_name(element.tag)
    current = f"{prefix}/{lname}" if prefix else lname

    has_children = False
    for child in list(element):
        has_children = True
        paths.extend(_walk_fpml_paths(child, current))

    if not has_children and (element.text and element.text.strip()):
        paths.append(current)

    return paths


def compute_coverage(
    fpml_path: str,
    cdm_json: Dict[str, Any],
    ignore_patterns: Optional[Set[str]] = None,
) -> CoverageReport:
    """Compute coverage of FpML leaf paths in CDM output.

    Simple heuristic: flatten CDM JSON keys and check if each FpML leaf path's
    local name appears somewhere in CDM. Real coverage (with ruleset mapping tables)
    would be more precise but this gives a useful signal.
    """
    path_obj = Path(fpml_path)
    if not path_obj.is_file():
        return CoverageReport(0, 0, 0, 0, [])

    root = ET.parse(path_obj).getroot()
    trade = None
    for elem in root.iter():
        if _local_name(elem.tag) == "trade":
            trade = elem
            break
    if trade is None:
        return CoverageReport(0, 0, 0, 0, [])

    fpml_paths = _walk_fpml_paths(trade)
    if ignore_patterns is None:
        ignore_patterns = DEFAULT_IGNORE_PATTERNS

    cdm_keys = _flatten_cdm_keys(cdm_json)

    entries: List[CoverageEntry] = []
    mapped = 0
    ignored = 0
    unmapped_list: List[str] = []

    for fp in fpml_paths:
        if _is_ignored(fp, ignore_patterns):
            entries.append(CoverageEntry(fp, "ignored", "matches ignore pattern"))
            ignored += 1
        elif _is_mapped(fp, cdm_keys):
            entries.append(CoverageEntry(fp, "mapped"))
            mapped += 1
        else:
            entries.append(CoverageEntry(fp, "unmapped"))
            unmapped_list.append(fp)

    return CoverageReport(
        total_paths=len(fpml_paths),
        mapped_count=mapped,
        ignored_count=ignored,
        unmapped_count=len(unmapped_list),
        unmapped_paths=unmapped_list,
        entries=entries,
    )


def _flatten_cdm_keys(obj: Any, prefix: str = "") -> Set[str]:
    """Flatten CDM JSON into a set of key paths (for lookup)."""
    keys: Set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            keys.add(k.lower())
            keys.add(full.lower())
            keys.update(_flatten_cdm_keys(v, full))
    elif isinstance(obj, list):
        for item in obj:
            keys.update(_flatten_cdm_keys(item, prefix))
    return keys


def _is_ignored(fpml_path: str, patterns: Set[str]) -> bool:
    for pat in patterns:
        if pat in fpml_path:
            return True
    return False


def _is_mapped(fpml_path: str, cdm_keys: Set[str]) -> bool:
    """Check if the leaf name of the FpML path appears somewhere in CDM keys."""
    parts = fpml_path.split("/")
    leaf = parts[-1] if parts else ""
    return leaf.lower() in cdm_keys


def fpml_coverage_report(
    fpml_path: str,
    cdm_json: Dict[str, Any],
) -> Dict[str, Any]:
    """Tool-compatible wrapper for compute_coverage."""
    report = compute_coverage(fpml_path, cdm_json)
    return report.to_dict()
