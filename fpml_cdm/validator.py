from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cdm_official_schema import get_trade_schema_validator
from .parser import parse_fpml_fx
from .types import (
    NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE,
    NORMALIZED_KIND_FX_SWAP,
    ErrorCode,
    MappingScore,
    NormalizedFxForward,
    NormalizedFxSwap,
    NormalizedFxTrade,
    ParserError,
    ValidationIssue,
    ValidationReport,
)

SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "schemas"


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
    Validate a CDM Trade object against the official FINOS CDM JSON Schemas.
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


def _semantic_validation_fx_forward_like(
    model: NormalizedFxForward, cdm_data: Dict[str, Any]
) -> Tuple[List[ValidationIssue], MappingScore]:
    issues: List[ValidationIssue] = []

    trade = cdm_data.get("trade", {})
    trade_lot = (trade.get("tradeLot", [{}]) or [{}])[0]
    price_quantity = (trade_lot.get("priceQuantity", [{}]) or [{}])[0]
    quantities = price_quantity.get("quantity", []) or []
    prices = price_quantity.get("price", []) or []
    payout_list = trade.get("product", {}).get("economicTerms", {}).get("payout", [])
    first_payout = payout_list[0] if payout_list else {}
    settlement_payout = first_payout.get("SettlementPayout", {})
    settlement_terms = settlement_payout.get("settlementTerms", {})

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

    cdm_trade_date = trade.get("tradeDate", {}).get("value")
    check(cdm_trade_date == model.tradeDate, f"Trade date mismatch: model={model.tradeDate}, cdm={cdm_trade_date}", "trade.tradeDate.value")

    cdm_settlement_date = (
        settlement_terms
        .get("settlementDate", {})
        .get("valueDate")
    )
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
    cdm_settlement_type = settlement_terms.get("settlementType")
    check(
        cdm_settlement_type == expected_settlement_enum,
        f"Settlement type mismatch: model={expected_settlement_enum}, cdm={cdm_settlement_type}",
        "trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementType",
    )

    quantity1 = quantities[0] if len(quantities) > 0 else {}
    quantity2 = quantities[1] if len(quantities) > 1 else {}

    cdm_currency1 = quantity1.get("value", {}).get("unit", {}).get("currency", {}).get("value")
    cdm_currency2 = quantity2.get("value", {}).get("unit", {}).get("currency", {}).get("value")

    check(cdm_currency1 == model.currency1, f"Currency1 mismatch: model={model.currency1}, cdm={cdm_currency1}", "trade.tradeLot[0].priceQuantity[0].quantity[0].unit.currency.value")
    check(cdm_currency2 == model.currency2, f"Currency2 mismatch: model={model.currency2}, cdm={cdm_currency2}", "trade.tradeLot[0].priceQuantity[0].quantity[1].unit.currency.value")

    cdm_amount1 = quantity1.get("value", {}).get("value")
    cdm_amount2 = quantity2.get("value", {}).get("value")

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
        price = prices[0] if prices else {}
        price_inner = price.get("value", {})
        cdm_rate = price_inner.get("value")
        cdm_quote = price_inner.get("unit", {}).get("currency", {}).get("value")
        cdm_base = price_inner.get("perUnitOf", {}).get("currency", {}).get("value")
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
        cdm_settlement_currency = settlement_terms.get("settlementCurrency", {}).get("value")
        check(
            cdm_settlement_currency == model.settlementCurrency,
            f"Settlement currency mismatch: model={model.settlementCurrency}, cdm={cdm_settlement_currency}",
            "trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementCurrency.value",
        )

    payer_receiver = settlement_payout.get("payerReceiver", {})
    counterparties: Dict[str, str] = {}
    for cp in trade.get("counterparty", []):
        ref = cp.get("partyReference", {})
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

    trade = cdm_data.get("trade", {})
    check(trade.get("tradeDate", {}).get("value") == model.tradeDate, "Trade date mismatch", "trade.tradeDate.value")

    payouts = trade.get("product", {}).get("economicTerms", {}).get("payout", []) or []
    check(len(payouts) >= 2, "FX swap must emit at least two payouts", "trade.product.economicTerms.payout")
    if len(payouts) >= 2:
        near = payouts[0].get("SettlementPayout", {}).get("settlementTerms", {}).get("settlementDate", {}).get("valueDate")
        far = payouts[1].get("SettlementPayout", {}).get("settlementTerms", {}).get("settlementDate", {}).get("valueDate")
        check(near == model.nearValueDate, "Near value date mismatch", "trade.product.economicTerms.payout[0].SettlementPayout.settlementTerms.settlementDate.valueDate")
        check(far == model.farValueDate, "Far value date mismatch", "trade.product.economicTerms.payout[1].SettlementPayout.settlementTerms.settlementDate.valueDate")
        near_st = payouts[0].get("SettlementPayout", {}).get("settlementTerms", {}).get("settlementType")
        far_st = payouts[1].get("SettlementPayout", {}).get("settlementTerms", {}).get("settlementType")
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
        for cp in trade.get("counterparty", []):
            ref = cp.get("partyReference", {})
            key = ref.get("externalReference") or ref.get("globalReference")
            if key:
                counterparties[key] = cp.get("role", "")
        near_pr = payouts[0].get("SettlementPayout", {}).get("payerReceiver", {})
        far_pr = payouts[1].get("SettlementPayout", {}).get("payerReceiver", {})
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

    pqs = ((trade.get("tradeLot", [{}]) or [{}])[0].get("priceQuantity", []) or [])
    check(len(pqs) >= 2, "FX swap must emit two priceQuantity entries", "trade.tradeLot[0].priceQuantity")
    if len(pqs) >= 2:
        near_qty = pqs[0].get("quantity", []) or []
        far_qty = pqs[1].get("quantity", []) or []
        if len(near_qty) >= 2:
            check(
                near_qty[0].get("value", {}).get("unit", {}).get("currency", {}).get("value") == model.nearCurrency1,
                "Near leg currency1 mismatch",
                "trade.tradeLot[0].priceQuantity[0].quantity[0].value.unit.currency.value",
            )
            check(
                near_qty[1].get("value", {}).get("unit", {}).get("currency", {}).get("value") == model.nearCurrency2,
                "Near leg currency2 mismatch",
                "trade.tradeLot[0].priceQuantity[0].quantity[1].value.unit.currency.value",
            )
        if len(far_qty) >= 2:
            check(
                far_qty[0].get("value", {}).get("unit", {}).get("currency", {}).get("value") == model.farCurrency1,
                "Far leg currency1 mismatch",
                "trade.tradeLot[0].priceQuantity[1].quantity[0].value.unit.currency.value",
            )
            check(
                far_qty[1].get("value", {}).get("unit", {}).get("currency", {}).get("value") == model.farCurrency2,
                "Far leg currency2 mismatch",
                "trade.tradeLot[0].priceQuantity[1].quantity[1].value.unit.currency.value",
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
