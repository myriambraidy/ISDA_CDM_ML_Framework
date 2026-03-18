from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .types import ErrorCode, NormalizedFxForward, ParserError, ValidationIssue
from .xml_utils import (
    _find_child_local,
    _find_descendant_local,
    _iter_descendants_local,
    _local_name,
    _namespace,
    _parse_amount,
    _parse_currency,
    _parse_date,
    _text,
)


def apply_ruleset_patch(base_ruleset: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply structured, deterministic modifications to a ruleset.

    Supported patch shapes (all optional):
      - {"fields": {<field>: {"candidates_order": [...], "candidates_add": [...], "required": bool}}}
      - {"derived": {<field>: {"enabled": bool}}}
    """
    ruleset = copy.deepcopy(base_ruleset)
    patch = patch or {}

    fields_patch = patch.get("fields") or {}
    for field_name, field_changes in fields_patch.items():
        if field_name not in ruleset.get("fields", {}):
            continue
        if not isinstance(field_changes, dict):
            continue
        fdef = ruleset["fields"][field_name]

        if "candidates_order" in field_changes and isinstance(field_changes["candidates_order"], list):
            fdef["candidates"] = list(field_changes["candidates_order"])
        if "candidates" in field_changes and isinstance(field_changes["candidates"], list):
            # Alias for candidates_order.
            fdef["candidates"] = list(field_changes["candidates"])
        if "candidates_add" in field_changes and isinstance(field_changes["candidates_add"], list):
            add_list = [str(x) for x in field_changes["candidates_add"]]
            cur = list(fdef.get("candidates", []))
            for x in add_list:
                if x not in cur:
                    cur.append(x)
            fdef["candidates"] = cur
        if "required" in field_changes and isinstance(field_changes["required"], bool):
            fdef["required"] = field_changes["required"]

    derived_patch = patch.get("derived") or {}
    for derived_name, derived_changes in derived_patch.items():
        if not isinstance(derived_changes, dict):
            continue
        if derived_name not in ruleset.get("derived", {}):
            continue
        if "enabled" in derived_changes and isinstance(derived_changes["enabled"], bool):
            ruleset["derived"][derived_name]["enabled"] = derived_changes["enabled"]

    # Small compatibility: allow {"exchangeRate": {"enabled": ...}} inside fields.
    if isinstance(patch.get("exchangeRate"), dict) and "enabled" in patch["exchangeRate"]:
        enabled = patch["exchangeRate"]["enabled"]
        if isinstance(enabled, bool) and "exchangeRate" in ruleset.get("derived", {}):
            ruleset["derived"]["exchangeRate"]["enabled"] = enabled

    return ruleset


def _resolve_element_path(root: ET.Element, local_path: str) -> Optional[ET.Element]:
    """
    Resolve a local-name path like `exchangedCurrency1/paymentAmount`.
    Attributes are not supported here (use `_resolve_value_path`).
    """
    if not local_path:
        return None
    segments = [seg for seg in local_path.split("/") if seg]
    cur: Optional[ET.Element] = root
    for seg in segments:
        if seg.startswith("@"):
            return None
        if cur is None:
            return None
        cur = _find_child_local(cur, seg)
    return cur


def _resolve_value_path(root: ET.Element, local_path: str) -> Optional[str]:
    """
    Resolve a local-name path to either:
      - element text (default)
      - element attribute (if path ends with `/@attrName`)
    """
    if not local_path:
        return None
    segments = [seg for seg in local_path.split("/") if seg]
    attr_name: Optional[str] = None
    if segments and segments[-1].startswith("@"):
        attr_name = segments[-1][1:]
        segments = segments[:-1]

    cur: Optional[ET.Element] = root
    for seg in segments:
        if cur is None:
            return None
        cur = _find_child_local(cur, seg)

    if cur is None:
        return None
    if attr_name:
        raw = cur.get(attr_name)
        if raw is None:
            return None
        raw = raw.strip()
        return raw or None
    return _text(cur)


def _parse_field_value(
    *,
    parser: str,
    raw: str,
    issue_path: str,
    issues: List[ValidationIssue],
) -> Optional[Any]:
    if parser == "date_only":
        return _parse_date(raw, issue_path, issues)
    if parser == "amount":
        return _parse_amount(raw, issue_path, issues)
    if parser == "currency3":
        return _parse_currency(raw, issue_path, issues)
    if parser == "href":
        # Raw value already comes from attribute extraction.
        val = raw.strip()
        if not val:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                    message=f"Missing required href at {issue_path}",
                    path=issue_path,
                )
            )
            return None
        return val
    raise ValueError(f"Unknown parser type in ruleset: {parser}")


def extract_fx_product_fields(
    product_node: Optional[ET.Element],
    adapter_id: str,
    ruleset: Dict[str, Any],
    issues: List[ValidationIssue],
) -> Dict[str, Any]:
    if product_node is None:
        return {
            "valueDate": None,
            "currency1": None,
            "amount1": None,
            "currency2": None,
            "amount2": None,
            "exchangeRate": None,
            "settlementType": "PHYSICAL",
            "settlementCurrency": None,
            "buyerPartyReference": None,
            "sellerPartyReference": None,
        }

    out: Dict[str, Any] = {}
    fields = ruleset.get("fields", {})

    # 1) settlementType needs presence semantics and feeds settlementCurrency.
    if "settlementType" in fields and fields["settlementType"].get("parser") == "settlement_type_from_ndf_presence":
        st_def = fields["settlementType"]
        ndf_candidates = st_def.get("ndf_candidates") or []
        cash_value = st_def.get("cash_value", "CASH")
        physical_value = st_def.get("physical_value", "PHYSICAL")

        found_ndf = False
        for cand in ndf_candidates:
            if _resolve_element_path(product_node, str(cand)) is not None:
                found_ndf = True
                break
        out["settlementType"] = cash_value if found_ndf else physical_value
    else:
        out["settlementType"] = "PHYSICAL"

    # 2) regular scalar fields by candidate evaluation.
    for field_name, field_def in fields.items():
        if field_name == "settlementType":
            continue
        parser = field_def.get("parser")
        required = bool(field_def.get("required", False))
        candidates: List[str] = list(field_def.get("candidates") or [])
        parsed_val: Optional[Any] = None
        parse_errors: List[List[ValidationIssue]] = []

        for cand in candidates:
            raw = _resolve_value_path(product_node, str(cand))
            if raw is None:
                continue

            tmp_issues: List[ValidationIssue] = []
            val = _parse_field_value(parser=parser, raw=raw, issue_path=f"trade/{adapter_id}/{field_name}/{cand}", issues=tmp_issues)
            if val is not None:
                parsed_val = val
                break
            parse_errors.append(tmp_issues)

        if parsed_val is not None:
            out[field_name] = parsed_val
            continue

        # No candidate succeeded.
        if required:
            if parse_errors:
                # Use the first candidate's error set deterministically.
                issues.extend(parse_errors[0])
            else:
                # Force a missing-required issue at the first candidate path.
                fallback_path = candidates[0] if candidates else field_name
                # Route based on parser type.
                tmp_issues = []
                # For date/amount/currency we can reuse the parse helpers by passing None,
                # but they require Optional[str]. We'll just call a field-specific helper.
                if parser == "date_only":
                    _parse_date(None, f"trade/{adapter_id}/{field_name}/{fallback_path}", issues)
                elif parser == "amount":
                    _parse_amount(None, f"trade/{adapter_id}/{field_name}/{fallback_path}", issues)
                elif parser == "currency3":
                    _parse_currency(None, f"trade/{adapter_id}/{field_name}/{fallback_path}", issues)
                elif parser == "href":
                    issues.append(
                        ValidationIssue(
                            code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                            message=f"Missing required href at {field_name}",
                            path=f"trade/{adapter_id}/{field_name}/{fallback_path}",
                        )
                    )
                else:
                    raise ValueError(f"Unknown parser type in ruleset: {parser}")

        out[field_name] = parsed_val if parsed_val is not None else None

    # 3) If settlementCurrency is present but settlementType is physical,
    # leave it as-is; validation will decide.
    return out


def parse_fpml_fx_with_ruleset(
    *,
    fpml_path: str,
    adapter_id: str,
    ruleset: Dict[str, Any],
    strict: bool = True,
    recovery_mode: bool = False,
) -> Tuple[NormalizedFxForward, List[ValidationIssue]] | NormalizedFxForward:
    """
    Parse an FpML file using a provided adapter ruleset to extract
    the FX product economic fields deterministically.
    """
    xml_file = Path(fpml_path)
    if not xml_file.exists():
        issues = [
            ValidationIssue(
                code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                message=f"Input file not found: {xml_file}",
                path="input",
            )
        ]
        if strict and not recovery_mode:
            raise ParserError(issues)
        model = NormalizedFxForward(
            tradeDate="",
            valueDate="",
            currency1="",
            currency2="",
            amount1=0.0,
            amount2=0.0,
            tradeIdentifiers=[],
            parties=[],
            exchangeRate=None,
            settlementType="PHYSICAL",
            settlementCurrency=None,
            buyerPartyReference=None,
            sellerPartyReference=None,
            sourceProduct=adapter_id,
            sourceNamespace=None,
            sourceVersion=None,
        )
        return (model, issues) if recovery_mode else model

    try:
        root = ET.parse(xml_file).getroot()
    except ET.ParseError as exc:
        issues = [
            ValidationIssue(
                code=ErrorCode.INVALID_VALUE.value,
                message=f"Invalid XML format: {exc}",
                path="xml",
            )
        ]
        if strict and not recovery_mode:
            raise ParserError(issues)
        model = NormalizedFxForward(
            tradeDate="",
            valueDate="",
            currency1="",
            currency2="",
            amount1=0.0,
            amount2=0.0,
            tradeIdentifiers=[],
            parties=[],
            exchangeRate=None,
            settlementType="PHYSICAL",
            settlementCurrency=None,
            buyerPartyReference=None,
            sellerPartyReference=None,
            sourceProduct=adapter_id,
            sourceNamespace=None,
            sourceVersion=None,
        )
        return (model, issues) if recovery_mode else model

    issues: List[ValidationIssue] = []
    source_namespace = _namespace(root.tag)
    source_version = root.get("fpmlVersion") or root.get("version")

    trade = _find_descendant_local(root, "trade")
    if trade is None:
        issues.append(
            ValidationIssue(
                code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                message="Missing required element: trade",
                path="trade",
            )
        )
        model = NormalizedFxForward(
            tradeDate="",
            valueDate="",
            currency1="",
            currency2="",
            amount1=0.0,
            amount2=0.0,
            tradeIdentifiers=[],
            parties=[],
            exchangeRate=None,
            settlementType="PHYSICAL",
            settlementCurrency=None,
            buyerPartyReference=None,
            sellerPartyReference=None,
            sourceProduct=adapter_id,
            sourceNamespace=None,
            sourceVersion=source_version,
        )
        if strict and not recovery_mode and issues:
            raise ParserError(issues)
        return (model, issues) if recovery_mode else model

    product_node: Optional[ET.Element] = None
    for child in list(trade):
        if _local_name(child.tag) == adapter_id:
            product_node = child
            break

    if product_node is None:
        issues.append(
            ValidationIssue(
                code=ErrorCode.UNSUPPORTED_PRODUCT.value,
                message=f"Unsupported product type: {adapter_id}",
                path=f"trade/{adapter_id}",
            )
        )

    trade_header = _find_child_local(trade, "tradeHeader")
    if trade_header is None:
        issues.append(
            ValidationIssue(
                code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                message="Missing required element: tradeHeader",
                path="trade/tradeHeader",
            )
        )

    trade_date_raw = _text(_find_child_local(trade_header, "tradeDate")) if trade_header is not None else None
    trade_date = _parse_date(trade_date_raw, "trade/tradeHeader/tradeDate", issues)

    trade_identifiers: List[Dict[str, str]] = []
    if trade_header is not None:
        for pti in list(trade_header):
            if _local_name(pti.tag) != "partyTradeIdentifier":
                continue
            trade_id = _text(_find_child_local(pti, "tradeId"))
            if trade_id:
                trade_identifiers.append({"tradeId": trade_id})

    parties: List[Dict[str, Optional[str]]] = []
    for party in _iter_descendants_local(root, "party"):
        party_id = party.get("id", "")
        party_name = _text(_find_child_local(party, "partyName"))
        if party_name is None:
            party_name = _text(_find_child_local(party, "partyId"))
        parties.append({"id": party_id, "name": party_name})

    product_fields = extract_fx_product_fields(
        product_node=product_node,
        adapter_id=adapter_id,
        ruleset=ruleset,
        issues=issues,
    )

    # Optional derived values: currently only exchangeRate.
    derived_cfg = (ruleset.get("derived") or {}).get("exchangeRate") or {}
    derived_enabled = bool(derived_cfg.get("enabled", False))
    if derived_enabled and product_fields.get("exchangeRate") is None:
        strategy = derived_cfg.get("strategy")
        if strategy == "amount_ratio":
            a1 = product_fields.get("amount1")
            a2 = product_fields.get("amount2")
            if isinstance(a1, (int, float)) and isinstance(a2, (int, float)) and a1 != 0:
                product_fields["exchangeRate"] = a2 / a1

    settlement_type = product_fields.get("settlementType", "PHYSICAL") or "PHYSICAL"
    model = NormalizedFxForward(
        tradeDate=trade_date or "",
        valueDate=product_fields.get("valueDate") or "",
        currency1=product_fields.get("currency1") or "",
        currency2=product_fields.get("currency2") or "",
        amount1=float(product_fields.get("amount1") or 0.0),
        amount2=float(product_fields.get("amount2") or 0.0),
        tradeIdentifiers=trade_identifiers,
        parties=parties,
        exchangeRate=product_fields.get("exchangeRate"),
        settlementType=settlement_type,
        settlementCurrency=product_fields.get("settlementCurrency"),
        buyerPartyReference=product_fields.get("buyerPartyReference"),
        sellerPartyReference=product_fields.get("sellerPartyReference"),
        sourceProduct=adapter_id,
        sourceNamespace=source_namespace,
        sourceVersion=source_version,
    )

    if not recovery_mode:
        if strict and issues:
            raise ParserError(issues)
        if not strict:
            if any(i.level == "error" for i in issues):
                raise ParserError(issues)
    return (model, issues) if recovery_mode else model

