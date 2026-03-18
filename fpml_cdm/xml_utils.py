from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from typing import Iterable, Optional, Tuple

from .types import ErrorCode, ParserError, ValidationIssue


def _split_tag(tag: str) -> Tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _local_name(tag: str) -> str:
    return _split_tag(tag)[1]


def _namespace(tag: str) -> Optional[str]:
    ns, _ = _split_tag(tag)
    return ns or None


def _text(node: Optional[ET.Element]) -> Optional[str]:
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value if value else None


def _iter_children_local(node: ET.Element, local_name: str) -> Iterable[ET.Element]:
    for child in list(node):
        if _local_name(child.tag) == local_name:
            yield child


def _find_child_local(node: Optional[ET.Element], local_name: str) -> Optional[ET.Element]:
    if node is None:
        return None
    for child in _iter_children_local(node, local_name):
        return child
    return None


def _iter_descendants_local(node: ET.Element, local_name: str) -> Iterable[ET.Element]:
    for elem in node.iter():
        if _local_name(elem.tag) == local_name:
            yield elem


def _find_descendant_local(node: ET.Element, local_name: str) -> Optional[ET.Element]:
    for elem in _iter_descendants_local(node, local_name):
        return elem
    return None


def _normalize_date_only(value: str) -> Optional[str]:
    raw = value.strip()
    candidates = [raw]
    if raw.endswith(("Z", "z")):
        candidates.insert(0, raw[:-1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            dt.date.fromisoformat(candidate)
            return candidate
        except ValueError:
            continue
    return None


def _parse_date(value: Optional[str], path: str, issues: list[ValidationIssue]) -> Optional[str]:
    if not value:
        issues.append(
            ValidationIssue(
                code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                message=f"Missing required date at {path}",
                path=path,
            )
        )
        return None

    normalized = _normalize_date_only(value)
    if normalized is None:
        issues.append(
            ValidationIssue(
                code=ErrorCode.INVALID_VALUE.value,
                message=f"Invalid ISO date at {path}: {value}",
                path=path,
            )
        )
        return None
    return normalized


def _parse_amount(value: Optional[str], path: str, issues: list[ValidationIssue]) -> Optional[float]:
    if value is None:
        issues.append(
            ValidationIssue(
                code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                message=f"Missing required numeric field at {path}",
                path=path,
            )
        )
        return None
    try:
        return float(value)
    except ValueError:
        issues.append(
            ValidationIssue(
                code=ErrorCode.INVALID_VALUE.value,
                message=f"Invalid numeric value at {path}: {value}",
                path=path,
            )
        )
        return None


def _parse_currency(value: Optional[str], path: str, issues: list[ValidationIssue]) -> Optional[str]:
    if not value:
        issues.append(
            ValidationIssue(
                code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                message=f"Missing required currency at {path}",
                path=path,
            )
        )
        return None
    if len(value) != 3:
        issues.append(
            ValidationIssue(
                code=ErrorCode.INVALID_VALUE.value,
                message=f"Currency must be 3-letter ISO code at {path}: {value}",
                path=path,
            )
        )
        return None
    return value

