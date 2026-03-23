from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .types import ErrorCode, NormalizedFxForward, ParserError, ValidationIssue

SUPPORTED_PRODUCTS = {"fxForward", "fxSingleLeg"}


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


def _parse_date(value: Optional[str], path: str, issues: List[ValidationIssue]) -> Optional[str]:
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


def _parse_amount(value: Optional[str], path: str, issues: List[ValidationIssue]) -> Optional[float]:
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


def _parse_currency(value: Optional[str], path: str, issues: List[ValidationIssue]) -> Optional[str]:
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


def _detect_supported_product(trade: ET.Element) -> Tuple[str, ET.Element]:
    unsupported_products: List[str] = []

    for child in list(trade):
        lname = _local_name(child.tag)
        if lname == "tradeHeader":
            continue
        if lname in SUPPORTED_PRODUCTS:
            return lname, child
        unsupported_products.append(lname)

    if unsupported_products:
        raise ParserError(
            [
                ValidationIssue(
                    code=ErrorCode.UNSUPPORTED_PRODUCT.value,
                    message=f"Unsupported product type: {unsupported_products[0]}",
                    path=f"trade/{unsupported_products[0]}",
                )
            ]
        )

    raise ParserError(
        [
            ValidationIssue(
                code=ErrorCode.UNSUPPORTED_PRODUCT.value,
                message="No supported product found under trade (expected fxForward or fxSingleLeg)",
                path="trade",
            )
        ]
    )


def parse_fpml_fx(
    xml_path: str,
    strict: bool = True,
    recovery_mode: bool = False,
) -> "NormalizedFxForward | Tuple[NormalizedFxForward, List[ValidationIssue]]":
    xml_file = Path(xml_path)
    if not xml_file.exists():
        raise ParserError(
            [
                ValidationIssue(
                    code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                    message=f"Input file not found: {xml_file}",
                    path="input",
                )
            ]
        )

    try:
        root = ET.parse(xml_file).getroot()
    except ET.ParseError as exc:
        raise ParserError(
            [
                ValidationIssue(
                    code=ErrorCode.INVALID_VALUE.value,
                    message=f"Invalid XML format: {exc}",
                    path="xml",
                )
            ]
        ) from exc

    return parse_fpml_root(root, strict=strict, recovery_mode=recovery_mode)


def parse_fpml_xml(
    xml_content: str,
    strict: bool = True,
    recovery_mode: bool = False,
) -> "NormalizedFxForward | Tuple[NormalizedFxForward, List[ValidationIssue]]":
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise ParserError(
            [
                ValidationIssue(
                    code=ErrorCode.INVALID_VALUE.value,
                    message=f"Invalid XML format: {exc}",
                    path="xml",
                )
            ]
        ) from exc
    return parse_fpml_root(root, strict=strict, recovery_mode=recovery_mode)


def parse_fpml_root(
    root: ET.Element,
    strict: bool = True,
    recovery_mode: bool = False,
) -> "NormalizedFxForward | Tuple[NormalizedFxForward, List[ValidationIssue]]":
    issues: List[ValidationIssue] = []

    source_namespace = _namespace(root.tag)
    source_version = root.get("fpmlVersion") or root.get("version")

    trade = _find_descendant_local(root, "trade")
    if trade is None:
        raise ParserError(
            [
                ValidationIssue(
                    code=ErrorCode.MISSING_REQUIRED_FIELD.value,
                    message="Missing required element: trade",
                    path="trade",
                )
            ]
        )

    source_product, product_node = _detect_supported_product(trade)

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

    trade_identifiers = []
    if trade_header is not None:
        for pti in _iter_children_local(trade_header, "partyTradeIdentifier"):
            trade_id_elem = _find_child_local(pti, "tradeId")
            trade_id = _text(trade_id_elem)
            if trade_id:
                entry: Dict[str, str] = {"tradeId": trade_id}
                party_ref = _find_child_local(pti, "partyReference")
                if party_ref is not None:
                    href = party_ref.get("href")
                    if href:
                        entry["issuer"] = href
                if trade_id_elem is not None:
                    scheme = trade_id_elem.get("tradeIdScheme")
                    if scheme:
                        entry["scheme"] = scheme
                trade_identifiers.append(entry)

    # Ruleset-driven extraction: evaluate candidate paths deterministically.
    from .ruleset_engine import extract_fx_product_fields
    from .rulesets import get_base_ruleset

    ruleset = get_base_ruleset(source_product)
    product_fields = extract_fx_product_fields(
        product_node=product_node,
        adapter_id=source_product,
        ruleset=ruleset,
        issues=issues,
    )

    value_date = product_fields.get("valueDate")
    currency1 = product_fields.get("currency1")
    amount1 = product_fields.get("amount1")
    currency2 = product_fields.get("currency2")
    amount2 = product_fields.get("amount2")
    exchange_rate = product_fields.get("exchangeRate")

    settlement_type = product_fields.get("settlementType") or "PHYSICAL"
    settlement_currency = product_fields.get("settlementCurrency")

    buyer_party_reference = product_fields.get("buyerPartyReference")
    seller_party_reference = product_fields.get("sellerPartyReference")
    currency2_payer = product_fields.get("currency2PayerPartyReference")
    currency2_receiver = product_fields.get("currency2ReceiverPartyReference")

    parties = []
    for party in _iter_descendants_local(root, "party"):
        party_id = party.get("id", "")
        party_name = _text(_find_child_local(party, "partyName"))
        if party_name is None:
            party_name = _text(_find_child_local(party, "partyId"))
        parties.append({"id": party_id, "name": party_name})

    if not recovery_mode:
        if strict and issues:
            raise ParserError(issues)

        if not strict:
            for issue in issues:
                if issue.level == "error":
                    raise ParserError(issues)

    model = NormalizedFxForward(
        tradeDate=trade_date or "",
        valueDate=value_date or "",
        currency1=currency1 or "",
        currency2=currency2 or "",
        amount1=amount1 or 0.0,
        amount2=amount2 or 0.0,
        tradeIdentifiers=trade_identifiers,
        parties=parties,
        exchangeRate=exchange_rate,
        settlementType=settlement_type,
        settlementCurrency=settlement_currency,
        buyerPartyReference=buyer_party_reference,
        sellerPartyReference=seller_party_reference,
        currency2PayerPartyReference=currency2_payer,
        currency2ReceiverPartyReference=currency2_receiver,
        sourceProduct=source_product,
        sourceNamespace=source_namespace,
        sourceVersion=source_version,
    )

    if recovery_mode:
        return model, issues
    return model
