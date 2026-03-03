from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .parser import parse_fpml_fx
from .types import (
    ErrorCode,
    MappingScore,
    NormalizedFxForward,
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


def _float_equal(left: Optional[float], right: Optional[float], tol: float) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= tol


def _semantic_validation(model: NormalizedFxForward, cdm_data: Dict[str, Any]) -> Tuple[List[ValidationIssue], MappingScore]:
    issues: List[ValidationIssue] = []

    trade = cdm_data.get("trade", {})
    tradable_product = trade.get("tradableProduct", {})
    trade_lot = (tradable_product.get("tradeLot", [{}]) or [{}])[0]
    price_quantity = (trade_lot.get("priceQuantity", [{}]) or [{}])[0]
    quantities = price_quantity.get("quantity", []) or []
    prices = price_quantity.get("price", []) or []
    settlement_payout = (
        tradable_product
        .get("product", {})
        .get("nonTransferableProduct", {})
        .get("economicTerms", {})
        .get("payout", {})
        .get("settlementPayout", [{}])
    )[0]
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
        .get("adjustableOrAdjustedDate", {})
        .get("unadjustedDate", {})
        .get("value")
    )
    check(
        cdm_settlement_date == model.valueDate,
        f"Value date mismatch: model={model.valueDate}, cdm={cdm_settlement_date}",
        "trade.tradableProduct.product.nonTransferableProduct.economicTerms.payout.settlementPayout[0].settlementTerms.settlementDate.adjustableOrAdjustedDate.unadjustedDate.value",
    )

    expected_settlement_enum = {
        "PHYSICAL": "SettlementTypeEnum.PHYSICAL",
        "CASH": "SettlementTypeEnum.CASH",
        "REGULAR": "SettlementTypeEnum.REGULAR",
    }.get(model.settlementType, "SettlementTypeEnum.PHYSICAL")
    cdm_settlement_type = settlement_terms.get("settlementType")
    check(
        cdm_settlement_type == expected_settlement_enum,
        f"Settlement type mismatch: model={expected_settlement_enum}, cdm={cdm_settlement_type}",
        "trade.tradableProduct.product.nonTransferableProduct.economicTerms.payout.settlementPayout[0].settlementTerms.settlementType",
    )

    quantity1 = quantities[0] if len(quantities) > 0 else {}
    quantity2 = quantities[1] if len(quantities) > 1 else {}

    cdm_currency1 = quantity1.get("unit", {}).get("currency", {}).get("value")
    cdm_currency2 = quantity2.get("unit", {}).get("currency", {}).get("value")

    check(cdm_currency1 == model.currency1, f"Currency1 mismatch: model={model.currency1}, cdm={cdm_currency1}", "trade.tradableProduct.tradeLot[0].priceQuantity[0].quantity[0].unit.currency.value")
    check(cdm_currency2 == model.currency2, f"Currency2 mismatch: model={model.currency2}, cdm={cdm_currency2}", "trade.tradableProduct.tradeLot[0].priceQuantity[0].quantity[1].unit.currency.value")

    cdm_amount1 = quantity1.get("value", {}).get("value")
    cdm_amount2 = quantity2.get("value", {}).get("value")

    check(
        _float_equal(model.amount1, float(cdm_amount1) if cdm_amount1 is not None else None, 0.01),
        f"Amount1 mismatch: model={model.amount1}, cdm={cdm_amount1}",
        "trade.tradableProduct.tradeLot[0].priceQuantity[0].quantity[0].value.value",
    )
    check(
        _float_equal(model.amount2, float(cdm_amount2) if cdm_amount2 is not None else None, 0.01),
        f"Amount2 mismatch: model={model.amount2}, cdm={cdm_amount2}",
        "trade.tradableProduct.tradeLot[0].priceQuantity[0].quantity[1].value.value",
    )

    if model.exchangeRate is not None:
        price = prices[0] if prices else {}
        cdm_rate = price.get("value", {}).get("value")
        cdm_quote = price.get("unit", {}).get("currency", {}).get("value")
        cdm_base = price.get("perUnitOf", {}).get("currency", {}).get("value")
        check(
            _float_equal(model.exchangeRate, float(cdm_rate) if cdm_rate is not None else None, 0.0001),
            f"Exchange rate mismatch: model={model.exchangeRate}, cdm={cdm_rate}",
            "trade.tradableProduct.tradeLot[0].priceQuantity[0].price[0].value.value",
        )
        check(
            cdm_quote == model.currency2,
            f"Rate quote currency mismatch: model={model.currency2}, cdm={cdm_quote}",
            "trade.tradableProduct.tradeLot[0].priceQuantity[0].price[0].unit.currency.value",
        )
        check(
            cdm_base == model.currency1,
            f"Rate base currency mismatch: model={model.currency1}, cdm={cdm_base}",
            "trade.tradableProduct.tradeLot[0].priceQuantity[0].price[0].perUnitOf.currency.value",
        )

    if model.settlementType == "CASH":
        cdm_settlement_currency = settlement_terms.get("settlementCurrency", {}).get("value")
        check(
            cdm_settlement_currency == model.settlementCurrency,
            f"Settlement currency mismatch: model={model.settlementCurrency}, cdm={cdm_settlement_currency}",
            "trade.tradableProduct.product.nonTransferableProduct.economicTerms.payout.settlementPayout[0].settlementTerms.settlementCurrency.value",
        )

    payer_receiver = settlement_payout.get("payerReceiver", {})
    if model.buyerPartyReference:
        cdm_payer = payer_receiver.get("payer", {}).get("globalReference")
        check(
            cdm_payer == model.buyerPartyReference,
            f"payer reference mismatch: model={model.buyerPartyReference}, cdm={cdm_payer}",
            "trade.tradableProduct.product.nonTransferableProduct.economicTerms.payout.settlementPayout[0].payerReceiver.payer.globalReference",
        )
    if model.sellerPartyReference:
        cdm_receiver = payer_receiver.get("receiver", {}).get("globalReference")
        check(
            cdm_receiver == model.sellerPartyReference,
            f"receiver reference mismatch: model={model.sellerPartyReference}, cdm={cdm_receiver}",
            "trade.tradableProduct.product.nonTransferableProduct.economicTerms.payout.settlementPayout[0].payerReceiver.receiver.globalReference",
        )

    accuracy = (checks_matched / checks_total) * 100 if checks_total else 0.0
    mapping_score = MappingScore(
        total_fields=checks_total,
        matched_fields=checks_matched,
        accuracy_percent=accuracy,
    )
    return issues, mapping_score


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

    normalized_schema_errors = validate_schema_data("fpml_fx_forward_parsed.schema.json", normalized.to_dict())
    errors.extend(normalized_schema_errors)

    cdm_schema_errors = validate_schema_data("cdm_fx_forward.schema.json", cdm_obj)
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
