from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cdm_official_schema import get_trade_schema_validator
from .parser import parse_fpml_fx
from .types import (
    NORMALIZED_KIND_FX_OPTION,
    NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE,
    NORMALIZED_KIND_FX_SWAP,
    ErrorCode,
    MappingScore,
    NormalizedFxForward,
    NormalizedFxOption,
    NormalizedFxSwap,
    NormalizedFxTrade,
    ParserError,
    ValidationIssue,
    ValidationReport,
)

SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "schemas"


def _dget(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts without crashing when an intermediate value is a non-dict (e.g. a bare string)."""
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
    if obj is None:
        return default
    return obj


def _get_schema(schema_name: str) -> Dict[str, Any]:
    schema_path = SCHEMA_ROOT / schema_name
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_schema_data(schema_name: str, data: Dict[str, Any]) -> List[ValidationIssue]:
    try:
        from jsonschema import Draft202012Validator
    except Exception:
        return [
            ValidationIssue(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED.value,
                message="jsonschema dependency missing. Install with: pip install jsonschema",
                path=schema_name,
            )
        ]

    schema = _get_schema(schema_name)
    validator = Draft202012Validator(schema)
    issues: List[ValidationIssue] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in err.path) or "<root>"
        issues.append(
            ValidationIssue(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED.value,
                message=err.message,
                path=path,
            )
        )
    return issues


def validate_schema_file(schema_name: str, json_path: str) -> List[ValidationIssue]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return validate_schema_data(schema_name, data)


def normalized_parsed_schema_for_kind(normalized_kind: str) -> str:
    """JSON Schema filename under ``schemas/`` for a normalized trade discriminator."""
    if normalized_kind == NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE:
        return "fpml_fx_forward_parsed.schema.json"
    if normalized_kind == NORMALIZED_KIND_FX_SWAP:
        return "fpml_fx_swap_parsed.schema.json"
    if normalized_kind == NORMALIZED_KIND_FX_OPTION:
        return "fpml_fx_option_parsed.schema.json"
    raise KeyError(normalized_kind)


def validate_normalized_parsed_dict(data: Dict[str, Any]) -> List[ValidationIssue]:
    """Validate parsed normalized JSON using the schema for ``normalizedKind``."""
    kind = data.get("normalizedKind", NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE)
    try:
        schema_name = normalized_parsed_schema_for_kind(str(kind))
    except KeyError:
        return [
            ValidationIssue(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED.value,
                message=f"No JSON Schema registered for normalizedKind={kind!r}",
                path="normalizedKind",
            )
        ]
    return validate_schema_data(schema_name, data)


def validate_cdm_official_schema(trade_dict: Dict[str, Any]) -> List[ValidationIssue]:
    """
    Validate a CDM Trade object against the official FINOS CDM JSON Schemas (Draft 04 bundle).

    Used by :func:`fpml_cdm.cdm_structure_validator.validate_cdm_structure` (L1 ``json_schema`` layer)
    and legacy CLI / FpML flows. For full structural validation (schema + Rosetta + supplementary),
    use :func:`fpml_cdm.cdm_structure_validator.validate_cdm_structure`.
    """
    try:
        validator = get_trade_schema_validator()
    except Exception as exc:
        return [
            ValidationIssue(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED.value,
                message=f"Failed to load official CDM Trade schema: {exc}",
                path="<schema>",
            )
        ]

    issues: List[ValidationIssue] = []
    for err in sorted(validator.iter_errors(trade_dict), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in err.path) or "<root>"
        issues.append(
            ValidationIssue(
                code=ErrorCode.SCHEMA_VALIDATION_FAILED.value,
                message=err.message,
                path=path,
            )
        )
    return issues


def _float_equal(left: Optional[float], right: Optional[float], tol: float) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= tol


def _safe_list_get(lst: Any, idx: int, default: Any = None) -> Any:
    """Safely index into a list, returning *default* if *lst* is not a list or *idx* is out of range."""
    if not isinstance(lst, list):
        return default if default is not None else {}
    if idx < len(lst):
        v = lst[idx]
        return v if v is not None else (default if default is not None else {})
    return default if default is not None else {}


def _semantic_validation_fx_forward_like(
    model: NormalizedFxForward, cdm_data: Dict[str, Any]
) -> Tuple[List[ValidationIssue], MappingScore]:
    issues: List[ValidationIssue] = []

    trade = cdm_data.get("trade") if isinstance(cdm_data, dict) else {}
    if not isinstance(trade, dict):
        trade = {}
    trade_lots = trade.get("tradeLot") if isinstance(trade.get("tradeLot"), list) else [{}]
    trade_lot = _safe_list_get(trade_lots, 0, {})
    pq_list = trade_lot.get("priceQuantity") if isinstance(trade_lot, dict) and isinstance(trade_lot.get("priceQuantity"), list) else [{}]
    price_quantity = _safe_list_get(pq_list, 0, {})
    quantities = price_quantity.get("quantity", []) if isinstance(price_quantity, dict) else []
    if not isinstance(quantities, list):
        quantities = []
    prices = price_quantity.get("price", []) if isinstance(price_quantity, dict) else []
    if not isinstance(prices, list):
        prices = []
    payout_list = _dget(trade, "product", "economicTerms", "payout", default=[])
    if not isinstance(payout_list, list):
        payout_list = []
    first_payout = _safe_list_get(payout_list, 0, {})
    settlement_payout = first_payout.get("SettlementPayout", {}) if isinstance(first_payout, dict) else {}
    if not isinstance(settlement_payout, dict):
        settlement_payout = {}
    settlement_terms = settlement_payout.get("settlementTerms", {}) if isinstance(settlement_payout, dict) else {}
    if not isinstance(settlement_terms, dict):
        settlement_terms = {}

    checks_total = 0
    checks_matched = 0

    def check(condition: bool, message: str, path: str) -> None:
        nonlocal checks_total, checks_matched
        checks_total += 1
        if condition:
            checks_matched += 1
        else:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.SEMANTIC_VALIDATION_FAILED.value,
                    message=message,
                    path=path,
                )
            )

    cdm_trade_date = _dget(trade, "tradeDate", "value")
    check(cdm_trade_date == model.tradeDate, f"Trade date mismatch: model={model.tradeDate}, cdm={cdm_trade_date}", "trade.tradeDate.value")

    # For FX confirmation-style trades, we intentionally do not emit trade.partyRole.
    check(
        "partyRole" not in trade,
        "trade.partyRole must be omitted for FX spot/forward-like confirmations (use counterparty + payerReceiver instead).",
        "trade.partyRole",
    )

    cdm_settlement_date = _dget(settlement_terms, "settlementDate", "valueDate")
    check(
        cdm_settlement_date == model.valueDate,
        f"Value date mismatch: model={model.valueDate}, cdm={cdm_settlement_date}",
        "trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementDate.valueDate",
    )

    expected_settlement_enum = {
        "PHYSICAL": "Physical",
        "CASH": "Cash",
        "REGULAR": "Physical",
    }.get(model.settlementType, "Physical")
    cdm_settlement_type = settlement_terms.get("settlementType") if isinstance(settlement_terms, dict) else None
    check(
        cdm_settlement_type == expected_settlement_enum,
        f"Settlement type mismatch: model={expected_settlement_enum}, cdm={cdm_settlement_type}",
        "trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementType",
    )

    # Counterparty role assignment: Party1/Party2 must follow exchangedCurrency1 payer/receiver (buyer/seller refs in normalized model).
    # In normalized forward-like, buyerPartyReference is extracted from exchangedCurrency1/payerPartyReference (when present).
    cp_list = trade.get("counterparty") if isinstance(trade.get("counterparty"), list) else []
    party1_ref = None
    party2_ref = None
    for cp in cp_list:
        if not isinstance(cp, dict):
            continue
        role = cp.get("role")
        pref = cp.get("partyReference", {})
        if not isinstance(pref, dict):
            pref = {}
        ext = pref.get("externalReference") or pref.get("globalReference")
        if role == "Party1":
            party1_ref = ext
        elif role == "Party2":
            party2_ref = ext
    if model.buyerPartyReference:
        check(
            party1_ref == model.buyerPartyReference,
            f"Counterparty Party1 mismatch: expected={model.buyerPartyReference}, got={party1_ref}",
            "trade.counterparty[role=Party1].partyReference.externalReference",
        )
    if model.sellerPartyReference:
        check(
            party2_ref == model.sellerPartyReference,
            f"Counterparty Party2 mismatch: expected={model.sellerPartyReference}, got={party2_ref}",
            "trade.counterparty[role=Party2].partyReference.externalReference",
        )

    # Party identity preservation: party.meta.externalKey -> partyId.identifier.value should match normalized parties[id].name (partyId value).
    party_nodes = trade.get("party") if isinstance(trade.get("party"), list) else []
    norm_party_id_by_external: Dict[str, Optional[str]] = {}
    for p in model.parties:
        if not isinstance(p, dict):
            continue
        pid = p.get("id")
        pname = p.get("name")
        if pid:
            norm_party_id_by_external[str(pid)] = str(pname) if pname is not None else None
    for p in party_nodes:
        if not isinstance(p, dict):
            continue
        ext_key = _dget(p, "meta", "externalKey")
        if not ext_key:
            continue
        cdm_partyid = _dget(_safe_list_get(p.get("partyId") if isinstance(p.get("partyId"), list) else [], 0, {}), "identifier", "value")
        expected_partyid = norm_party_id_by_external.get(str(ext_key))
        if expected_partyid:
            check(
                cdm_partyid == expected_partyid,
                f"PartyId mismatch for externalKey={ext_key}: expected={expected_partyid}, got={cdm_partyid}",
                f"trade.party[meta.externalKey={ext_key}].partyId[0].identifier.value",
            )

    # Trade identifiers: enforce issuerReference mapping and duplicate control (2 entries per distinct tradeId).
    ti_list = trade.get("tradeIdentifier") if isinstance(trade.get("tradeIdentifier"), list) else []
    # Count occurrences per tradeId value in CDM
    cdm_tradeid_counts: Dict[str, int] = {}
    cdm_issuer_by_tradeid: Dict[str, set] = {}
    for ti in ti_list:
        if not isinstance(ti, dict):
            continue
        assigned = ti.get("assignedIdentifier") if isinstance(ti.get("assignedIdentifier"), list) else []
        if not assigned:
            continue
        ident0 = _safe_list_get(assigned, 0, {})
        trade_id_val = _dget(ident0, "identifier", "value")
        if not trade_id_val:
            continue
        trade_id_val = str(trade_id_val)
        cdm_tradeid_counts[trade_id_val] = cdm_tradeid_counts.get(trade_id_val, 0) + 1
        issuer_ref = _dget(ti, "issuerReference", "externalReference")
        if issuer_ref:
            cdm_issuer_by_tradeid.setdefault(trade_id_val, set()).add(str(issuer_ref))
    for t in model.tradeIdentifiers:
        if not isinstance(t, dict):
            continue
        tid = t.get("tradeId")
        issuer = t.get("issuer")
        scheme = t.get("scheme")
        if not tid:
            continue
        tid = str(tid)
        if issuer:
            # Must have at least one issuerReference entry with matching issuer and scheme.
            found = False
            for ti in ti_list:
                if not isinstance(ti, dict):
                    continue
                if _dget(ti, "issuerReference", "externalReference") != issuer:
                    continue
                assigned = ti.get("assignedIdentifier") if isinstance(ti.get("assignedIdentifier"), list) else []
                ident0 = _safe_list_get(assigned, 0, {})
                if _dget(ident0, "identifier", "value") != tid:
                    continue
                if scheme and _dget(ident0, "identifier", "meta", "scheme") not in (scheme, None):
                    continue
                found = True
                break
            check(
                found,
                f"TradeIdentifier issuer mismatch for tradeId={tid}: expected issuerReference.externalReference={issuer}",
                "trade.tradeIdentifier[].issuerReference.externalReference",
            )
        # Duplicate control: official pattern emits 2 entries per distinct tradeId.
        cnt = cdm_tradeid_counts.get(tid, 0)
        check(
            cnt in (0, 2),
            f"TradeIdentifier count mismatch for tradeId={tid}: expected 2 entries, got {cnt}",
            "trade.tradeIdentifier",
        )

    quantity1 = _safe_list_get(quantities, 0, {})
    quantity2 = _safe_list_get(quantities, 1, {})

    cdm_currency1 = _dget(quantity1, "value", "unit", "currency", "value")
    cdm_currency2 = _dget(quantity2, "value", "unit", "currency", "value")

    check(cdm_currency1 == model.currency1, f"Currency1 mismatch: model={model.currency1}, cdm={cdm_currency1}", "trade.tradeLot[0].priceQuantity[0].quantity[0].unit.currency.value")
    check(cdm_currency2 == model.currency2, f"Currency2 mismatch: model={model.currency2}, cdm={cdm_currency2}", "trade.tradeLot[0].priceQuantity[0].quantity[1].unit.currency.value")

    cdm_amount1 = _dget(quantity1, "value", "value")
    cdm_amount2 = _dget(quantity2, "value", "value")

    check(
        _float_equal(model.amount1, float(cdm_amount1) if cdm_amount1 is not None else None, 0.01),
        f"Amount1 mismatch: model={model.amount1}, cdm={cdm_amount1}",
        "trade.tradeLot[0].priceQuantity[0].quantity[0].value",
    )
    check(
        _float_equal(model.amount2, float(cdm_amount2) if cdm_amount2 is not None else None, 0.01),
        f"Amount2 mismatch: model={model.amount2}, cdm={cdm_amount2}",
        "trade.tradeLot[0].priceQuantity[0].quantity[1].value",
    )

    if model.exchangeRate is not None:
        price = _safe_list_get(prices, 0, {})
        price_inner = price.get("value", {}) if isinstance(price, dict) else {}
        if not isinstance(price_inner, dict):
            price_inner = {}
        cdm_rate = price_inner.get("value")
        cdm_quote = _dget(price_inner, "unit", "currency", "value")
        cdm_base = _dget(price_inner, "perUnitOf", "currency", "value")
        check(
            _float_equal(model.exchangeRate, float(cdm_rate) if cdm_rate is not None else None, 0.0001),
            f"Exchange rate mismatch: model={model.exchangeRate}, cdm={cdm_rate}",
            "trade.tradeLot[0].priceQuantity[0].price[0].value",
        )
        check(
            cdm_quote == model.currency2,
            f"Rate quote currency mismatch: model={model.currency2}, cdm={cdm_quote}",
            "trade.tradeLot[0].priceQuantity[0].price[0].unit.currency.value",
        )
        check(
            cdm_base == model.currency1,
            f"Rate base currency mismatch: model={model.currency1}, cdm={cdm_base}",
            "trade.tradeLot[0].priceQuantity[0].price[0].perUnitOf.currency.value",
        )

    if model.settlementType == "CASH":
        cdm_settlement_currency = _dget(settlement_terms, "settlementCurrency", "value")
        check(
            cdm_settlement_currency == model.settlementCurrency,
            f"Settlement currency mismatch: model={model.settlementCurrency}, cdm={cdm_settlement_currency}",
            "trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementCurrency.value",
        )

    payer_receiver = settlement_payout.get("payerReceiver", {}) if isinstance(settlement_payout, dict) else {}
    if not isinstance(payer_receiver, dict):
        payer_receiver = {}
    counterparties: Dict[str, str] = {}
    for cp in (trade.get("counterparty") or []):
        if not isinstance(cp, dict):
            continue
        ref = cp.get("partyReference", {})
        if not isinstance(ref, dict):
            continue
        key = ref.get("externalReference") or ref.get("globalReference")
        if key:
            counterparties[key] = cp.get("role", "")
    payer_src = model.currency2PayerPartyReference or model.buyerPartyReference
    if payer_src:
        expected_payer_role = counterparties.get(payer_src, "Party1")
        cdm_payer = payer_receiver.get("payer")
        check(
            cdm_payer == expected_payer_role,
            f"payer role mismatch: model={expected_payer_role}, cdm={cdm_payer}",
            "trade.product.economicTerms.payout[0].SettlementPayout.payerReceiver.payer",
        )
    receiver_src = model.currency2ReceiverPartyReference or model.sellerPartyReference
    if receiver_src:
        expected_receiver_role = counterparties.get(receiver_src, "Party2")
        cdm_receiver = payer_receiver.get("receiver")
        check(
            cdm_receiver == expected_receiver_role,
            f"receiver role mismatch: model={expected_receiver_role}, cdm={cdm_receiver}",
            "trade.product.economicTerms.payout[0].SettlementPayout.payerReceiver.receiver",
        )

    accuracy = (checks_matched / checks_total) * 100 if checks_total else 0.0
    mapping_score = MappingScore(
        total_fields=checks_total,
        matched_fields=checks_matched,
        accuracy_percent=accuracy,
    )
    return issues, mapping_score


def _semantic_validation(model: NormalizedFxTrade, cdm_data: Dict[str, Any]) -> Tuple[List[ValidationIssue], MappingScore]:
    kind = getattr(model, "normalized_kind", NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE)
    if kind == NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE:
        if not isinstance(model, NormalizedFxForward):
            return (
                [
                    ValidationIssue(
                        code=ErrorCode.SEMANTIC_VALIDATION_FAILED.value,
                        message=f"Expected NormalizedFxForward for {kind!r}, got {type(model).__name__}",
                        path="normalized",
                    )
                ],
                MappingScore(),
            )
        return _semantic_validation_fx_forward_like(model, cdm_data)
    if kind == NORMALIZED_KIND_FX_SWAP:
        if not isinstance(model, NormalizedFxSwap):
            return (
                [
                    ValidationIssue(
                        code=ErrorCode.SEMANTIC_VALIDATION_FAILED.value,
                        message=f"Expected NormalizedFxSwap for {kind!r}, got {type(model).__name__}",
                        path="normalized",
                    )
                ],
                MappingScore(),
            )
        return _semantic_validation_fx_swap(model, cdm_data)
    if kind == NORMALIZED_KIND_FX_OPTION:
        if not isinstance(model, NormalizedFxOption):
            return (
                [
                    ValidationIssue(
                        code=ErrorCode.SEMANTIC_VALIDATION_FAILED.value,
                        message=f"Expected NormalizedFxOption for {kind!r}, got {type(model).__name__}",
                        path="normalized",
                    )
                ],
                MappingScore(),
            )
        return _semantic_validation_fx_option(model, cdm_data)
    return (
        [
            ValidationIssue(
                code=ErrorCode.SEMANTIC_VALIDATION_FAILED.value,
                message=f"No semantic validator for normalized_kind={kind!r}",
                path="normalized_kind",
            )
        ],
        MappingScore(),
    )


def _semantic_validation_fx_swap(
    model: NormalizedFxSwap, cdm_data: Dict[str, Any]
) -> Tuple[List[ValidationIssue], MappingScore]:
    issues: List[ValidationIssue] = []
    checks_total = 0
    checks_matched = 0

    def check(condition: bool, message: str, path: str) -> None:
        nonlocal checks_total, checks_matched
        checks_total += 1
        if condition:
            checks_matched += 1
            return
        issues.append(
            ValidationIssue(
                code=ErrorCode.SEMANTIC_VALIDATION_FAILED.value,
                message=message,
                path=path,
            )
        )

    trade = cdm_data.get("trade", {}) if isinstance(cdm_data, dict) else {}
    if not isinstance(trade, dict):
        trade = {}
    check(
        "partyRole" not in trade,
        "trade.partyRole must be omitted for FX confirmations (use counterparty + payerReceiver instead).",
        "trade.partyRole",
    )
    check(_dget(trade, "tradeDate", "value") == model.tradeDate, "Trade date mismatch", "trade.tradeDate.value")

    payouts = _dget(trade, "product", "economicTerms", "payout", default=[])
    if not isinstance(payouts, list):
        payouts = []
    check(len(payouts) >= 2, "FX swap must emit at least two payouts", "trade.product.economicTerms.payout")
    if len(payouts) >= 2:
        p0 = _safe_list_get(payouts, 0, {})
        p1 = _safe_list_get(payouts, 1, {})
        near = _dget(p0, "SettlementPayout", "settlementTerms", "settlementDate", "valueDate")
        far = _dget(p1, "SettlementPayout", "settlementTerms", "settlementDate", "valueDate")
        check(near == model.nearValueDate, "Near value date mismatch", "trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementDate.valueDate")
        check(far == model.farValueDate, "Far value date mismatch", "trade.product.economicTerms.payout[1].SettlementPayout.settlementTerms.settlementDate.valueDate")
        near_st = _dget(p0, "SettlementPayout", "settlementTerms", "settlementType")
        far_st = _dget(p1, "SettlementPayout", "settlementTerms", "settlementType")
        expected_near_st = {"PHYSICAL": "Physical", "CASH": "Cash", "REGULAR": "Physical"}.get(model.nearSettlementType, "Physical")
        expected_far_st = {"PHYSICAL": "Physical", "CASH": "Cash", "REGULAR": "Physical"}.get(model.farSettlementType, "Physical")
        check(
            near_st == expected_near_st,
            f"Near settlement type mismatch: expected={expected_near_st}, got={near_st}",
            "trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementType",
        )
        check(
            far_st == expected_far_st,
            f"Far settlement type mismatch: expected={expected_far_st}, got={far_st}",
            "trade.product.economicTerms.payout[1].SettlementPayout.settlementTerms.settlementType",
        )
        counterparties: Dict[str, str] = {}
        for cp in (trade.get("counterparty") or []):
            if not isinstance(cp, dict):
                continue
            ref = cp.get("partyReference", {})
            if not isinstance(ref, dict):
                continue
            key = ref.get("externalReference") or ref.get("globalReference")
            if key:
                counterparties[key] = cp.get("role", "")
        near_pr = _dget(p0, "SettlementPayout", "payerReceiver", default={})
        if not isinstance(near_pr, dict):
            near_pr = {}
        far_pr = _dget(p1, "SettlementPayout", "payerReceiver", default={})
        if not isinstance(far_pr, dict):
            far_pr = {}
        if model.nearCurrency2PayerPartyReference:
            near_expected_payer = counterparties.get(model.nearCurrency2PayerPartyReference, "Party1")
            check(
                near_pr.get("payer") == near_expected_payer,
                f"Near payer mismatch: expected={near_expected_payer}, got={near_pr.get('payer')}",
                "trade.product.economicTerms.payout[0].SettlementPayout.payerReceiver.payer",
            )
        if model.nearCurrency2ReceiverPartyReference:
            near_expected_receiver = counterparties.get(model.nearCurrency2ReceiverPartyReference, "Party2")
            check(
                near_pr.get("receiver") == near_expected_receiver,
                f"Near receiver mismatch: expected={near_expected_receiver}, got={near_pr.get('receiver')}",
                "trade.product.economicTerms.payout[0].SettlementPayout.payerReceiver.receiver",
            )
        if model.farCurrency2PayerPartyReference:
            far_expected_payer = counterparties.get(model.farCurrency2PayerPartyReference, "Party2")
            check(
                far_pr.get("payer") == far_expected_payer,
                f"Far payer mismatch: expected={far_expected_payer}, got={far_pr.get('payer')}",
                "trade.product.economicTerms.payout[1].SettlementPayout.payerReceiver.payer",
            )
        if model.farCurrency2ReceiverPartyReference:
            far_expected_receiver = counterparties.get(model.farCurrency2ReceiverPartyReference, "Party1")
            check(
                far_pr.get("receiver") == far_expected_receiver,
                f"Far receiver mismatch: expected={far_expected_receiver}, got={far_pr.get('receiver')}",
                "trade.product.economicTerms.payout[1].SettlementPayout.payerReceiver.receiver",
            )

    trade_lot = _safe_list_get(trade.get("tradeLot") if isinstance(trade.get("tradeLot"), list) else [{}], 0, {})
    pqs = trade_lot.get("priceQuantity", []) if isinstance(trade_lot, dict) else []
    if not isinstance(pqs, list):
        pqs = []
    check(len(pqs) >= 2, "FX swap must emit two priceQuantity entries", "trade.tradeLot[0].priceQuantity")
    if len(pqs) >= 2:
        near_pq = _safe_list_get(pqs, 0, {})
        far_pq = _safe_list_get(pqs, 1, {})
        near_qty = near_pq.get("quantity", []) if isinstance(near_pq, dict) else []
        if not isinstance(near_qty, list):
            near_qty = []
        far_qty = far_pq.get("quantity", []) if isinstance(far_pq, dict) else []
        if not isinstance(far_qty, list):
            far_qty = []
        if len(near_qty) >= 2:
            check(
                _dget(_safe_list_get(near_qty, 0, {}), "value", "unit", "currency", "value") == model.nearCurrency1,
                "Near leg currency1 mismatch",
                "trade.tradeLot[0].priceQuantity[0].quantity[0].value.unit.currency.value",
            )
            check(
                _dget(_safe_list_get(near_qty, 1, {}), "value", "unit", "currency", "value") == model.nearCurrency2,
                "Near leg currency2 mismatch",
                "trade.tradeLot[0].priceQuantity[0].quantity[1].value.unit.currency.value",
            )
        if len(far_qty) >= 2:
            check(
                _dget(_safe_list_get(far_qty, 0, {}), "value", "unit", "currency", "value") == model.farCurrency1,
                "Far leg currency1 mismatch",
                "trade.tradeLot[0].priceQuantity[1].quantity[0].value.unit.currency.value",
            )
            check(
                _dget(_safe_list_get(far_qty, 1, {}), "value", "unit", "currency", "value") == model.farCurrency2,
                "Far leg currency2 mismatch",
                "trade.tradeLot[0].priceQuantity[1].quantity[1].value.unit.currency.value",
            )

    accuracy = (checks_matched / checks_total) * 100 if checks_total else 0.0
    return (
        issues,
        MappingScore(total_fields=checks_total, matched_fields=checks_matched, accuracy_percent=accuracy),
    )


def _semantic_validation_fx_option(
    model: NormalizedFxOption, cdm_data: Dict[str, Any]
) -> Tuple[List[ValidationIssue], MappingScore]:
    issues: List[ValidationIssue] = []
    checks_total = 0
    checks_matched = 0

    def check(condition: bool, message: str, path: str) -> None:
        nonlocal checks_total, checks_matched
        checks_total += 1
        if condition:
            checks_matched += 1
            return
        issues.append(
            ValidationIssue(
                code=ErrorCode.SEMANTIC_VALIDATION_FAILED.value,
                message=message,
                path=path,
            )
        )

    trade = cdm_data.get("trade", {}) if isinstance(cdm_data, dict) else {}
    if not isinstance(trade, dict):
        trade = {}
    check(
        "partyRole" not in trade,
        "trade.partyRole must be omitted for FX confirmations (buyer/seller is modeled on OptionPayout.buyerSeller).",
        "trade.partyRole",
    )
    check(_dget(trade, "tradeDate", "value") == model.tradeDate, "Trade date mismatch", "trade.tradeDate.value")

    payouts = _dget(trade, "product", "economicTerms", "payout", default=[])
    if not isinstance(payouts, list):
        payouts = []
    check(len(payouts) >= 1, "FX option must emit at least one payout", "trade.product.economicTerms.payout")
    op: Dict[str, Any] = {}
    if payouts:
        p0 = _safe_list_get(payouts, 0, {})
        check(isinstance(p0, dict) and "OptionPayout" in p0, "First payout must be OptionPayout", "trade.product.economicTerms.payout[0]")
        op = _dget(p0, "OptionPayout", default={})
        if not isinstance(op, dict):
            op = {}

    et = op.get("exerciseTerms", {}) if isinstance(op, dict) else {}
    if not isinstance(et, dict):
        et = {}
    exp_dates = et.get("expirationDate") or []
    if not isinstance(exp_dates, list):
        exp_dates = []
    cdm_exp = None
    if exp_dates:
        cdm_exp = _dget(_safe_list_get(exp_dates, 0, {}), "adjustableDate", "unadjustedDate")
    check(cdm_exp == model.expiryDate, "Expiry date mismatch", "trade.product.economicTerms.payout[0].OptionPayout.exerciseTerms.expirationDate")

    check(op.get("optionType") == model.optionType, "Option type mismatch", "trade.product.economicTerms.payout[0].OptionPayout.optionType")

    sp = _dget(op, "strike", "strikePrice", default={})
    if not isinstance(sp, dict):
        sp = {}
    sp_val = sp.get("value")
    check(
        _float_equal(model.strikeRate, float(sp_val) if sp_val is not None else None, 0.0001),
        "Strike rate mismatch",
        "trade.product.economicTerms.payout[0].OptionPayout.strike.strikePrice.value",
    )

    bs = op.get("buyerSeller", {}) if isinstance(op, dict) else {}
    if not isinstance(bs, dict):
        bs = {}
    counterparties: Dict[str, str] = {}
    for cp in (trade.get("counterparty") or []):
        if not isinstance(cp, dict):
            continue
        ref = cp.get("partyReference", {})
        if not isinstance(ref, dict):
            continue
        key = ref.get("externalReference") or ref.get("globalReference")
        if key:
            counterparties[key] = cp.get("role", "")
    if model.buyerPartyReference:
        check(
            bs.get("buyer") == counterparties.get(model.buyerPartyReference, "Party1"),
            "Buyer role mismatch",
            "trade.product.economicTerms.payout[0].OptionPayout.buyerSeller.buyer",
        )
    if model.sellerPartyReference:
        check(
            bs.get("seller") == counterparties.get(model.sellerPartyReference, "Party2"),
            "Seller role mismatch",
            "trade.product.economicTerms.payout[0].OptionPayout.buyerSeller.seller",
        )

    trade_lot = _safe_list_get(trade.get("tradeLot") if isinstance(trade.get("tradeLot"), list) else [{}], 0, {})
    pqs = trade_lot.get("priceQuantity", []) if isinstance(trade_lot, dict) else []
    if not isinstance(pqs, list):
        pqs = []
    if pqs:
        pq0 = _safe_list_get(pqs, 0, {})
        qtys = pq0.get("quantity", []) if isinstance(pq0, dict) else []
        if not isinstance(qtys, list):
            qtys = []
        if len(qtys) >= 2:
            check(
                _dget(_safe_list_get(qtys, 0, {}), "value", "unit", "currency", "value") == model.putCurrency,
                "Put currency mismatch",
                "trade.tradeLot[0].priceQuantity[0].quantity[0]",
            )
            check(
                _dget(_safe_list_get(qtys, 1, {}), "value", "unit", "currency", "value") == model.callCurrency,
                "Call currency mismatch",
                "trade.tradeLot[0].priceQuantity[0].quantity[1]",
            )

    accuracy = (checks_matched / checks_total) * 100 if checks_total else 0.0
    return (
        issues,
        MappingScore(total_fields=checks_total, matched_fields=checks_matched, accuracy_percent=accuracy),
    )


def validate_transformation(fpml_path: str, cdm_obj: Dict[str, Any]) -> ValidationReport:
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []

    try:
        normalized = parse_fpml_fx(fpml_path, strict=True)
    except ParserError as exc:
        errors.extend(exc.issues)
        return ValidationReport(
            valid=False,
            mapping_score=MappingScore(total_fields=0, matched_fields=0, accuracy_percent=0.0),
            errors=errors,
            warnings=warnings,
        )

    normalized_schema_errors = validate_normalized_parsed_dict(normalized.to_dict())
    errors.extend(normalized_schema_errors)

    trade_dict = cdm_obj.get("trade", {})
    cdm_schema_errors = validate_cdm_official_schema(trade_dict)
    errors.extend(cdm_schema_errors)

    semantic_errors, mapping_score = _semantic_validation(normalized, cdm_obj)
    errors.extend(semantic_errors)

    return ValidationReport(
        valid=len(errors) == 0,
        mapping_score=mapping_score,
        errors=errors,
        warnings=warnings,
    )


def validate_normalized_and_cdm(normalized: NormalizedFxTrade, cdm_obj: Dict[str, Any]) -> ValidationReport:
    """
    Validate a *patched* normalized model directly (no re-parse from FpML).

    This is required by the mapping agent: it proposes structured ruleset patches,
    deterministically extracts a patched normalized model, transforms it, and we
    need validation against that patched model.
    """
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []

    normalized_schema_errors = validate_normalized_parsed_dict(normalized.to_dict())
    errors.extend(normalized_schema_errors)

    trade_dict = cdm_obj.get("trade", {})
    cdm_schema_errors = validate_cdm_official_schema(trade_dict)
    errors.extend(cdm_schema_errors)

    semantic_errors, mapping_score = _semantic_validation(normalized, cdm_obj)
    errors.extend(semantic_errors)

    return ValidationReport(
        valid=len(errors) == 0,
        mapping_score=mapping_score,
        errors=errors,
        warnings=warnings,
    )


def validate_conversion_files(fpml_path: str, cdm_json_path: str) -> ValidationReport:
    with open(cdm_json_path, "r", encoding="utf-8") as f:
        cdm_obj = json.load(f)
    return validate_transformation(fpml_path, cdm_obj)
