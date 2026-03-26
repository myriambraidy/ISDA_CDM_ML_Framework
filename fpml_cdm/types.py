from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class ErrorCode(str, Enum):
    UNSUPPORTED_PRODUCT = "UNSUPPORTED_PRODUCT"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_VALUE = "INVALID_VALUE"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    SEMANTIC_VALIDATION_FAILED = "SEMANTIC_VALIDATION_FAILED"


@dataclass
class ValidationIssue:
    code: str
    message: str
    path: str = ""
    level: str = "error"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "level": self.level,
        }


@dataclass
class MappingScore:
    total_fields: int = 0
    matched_fields: int = 0
    accuracy_percent: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_fields": self.total_fields,
            "matched_fields": self.matched_fields,
            "accuracy_percent": self.accuracy_percent,
        }


@dataclass
class ValidationReport:
    valid: bool
    mapping_score: MappingScore = field(default_factory=MappingScore)
    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "mapping_score": self.mapping_score.to_dict(),
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }


# Discriminator for JSON and ``transform_to_cdm_v6`` dispatch; expand union types later.
NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE = "fx_spot_forward_like"
NORMALIZED_KIND_FX_SWAP = "fx_swap"
NORMALIZED_KIND_FX_OPTION = "fx_option"


@dataclass
class NormalizedFxForward:
    """
    Spot/forward-like FX economics (fxForward, fxSingleLeg, NDF).

    This type is the first member of the planned ``NormalizedFxTrade`` union.
    """

    tradeDate: str
    valueDate: str
    currency1: str
    currency2: str
    amount1: float
    amount2: float
    tradeIdentifiers: List[Dict[str, str]] = field(default_factory=list)
    parties: List[Dict[str, Optional[str]]] = field(default_factory=list)
    exchangeRate: Optional[float] = None
    settlementType: str = "PHYSICAL"
    settlementCurrency: Optional[str] = None
    buyerPartyReference: Optional[str] = None
    sellerPartyReference: Optional[str] = None
    #: FpML exchangedCurrency2 payer/receiver — drives CDM SettlementPayout.payerReceiver (Rosetta)
    currency2PayerPartyReference: Optional[str] = None
    currency2ReceiverPartyReference: Optional[str] = None
    sourceProduct: str = "fxForward"
    #: Stable tag for transformer/schema dispatch (not FpML element name).
    normalized_kind: str = NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE
    sourceNamespace: Optional[str] = None
    sourceVersion: Optional[str] = None
    llm_recovered_fields: List[str] = field(default_factory=list)
    #: Override ISDA productQualifier in CDM taxonomy (agent / rules enrichment)
    productTaxonomyQualifier: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "tradeDate": self.tradeDate,
            "tradeIdentifiers": self.tradeIdentifiers,
            "currency1": self.currency1,
            "amount1": self.amount1,
            "currency2": self.currency2,
            "amount2": self.amount2,
            "exchangeRate": self.exchangeRate,
            "valueDate": self.valueDate,
            "settlementType": self.settlementType,
            "parties": self.parties,
            "sourceProduct": self.sourceProduct,
            "normalizedKind": self.normalized_kind,
        }
        if self.settlementCurrency is not None:
            data["settlementCurrency"] = self.settlementCurrency
        if self.buyerPartyReference is not None:
            data["buyerPartyReference"] = self.buyerPartyReference
        if self.sellerPartyReference is not None:
            data["sellerPartyReference"] = self.sellerPartyReference
        if self.currency2PayerPartyReference is not None:
            data["currency2PayerPartyReference"] = self.currency2PayerPartyReference
        if self.currency2ReceiverPartyReference is not None:
            data["currency2ReceiverPartyReference"] = self.currency2ReceiverPartyReference
        if self.productTaxonomyQualifier is not None:
            data["productTaxonomyQualifier"] = self.productTaxonomyQualifier
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NormalizedFxForward":
        return cls(
            tradeDate=data.get("tradeDate", ""),
            valueDate=data.get("valueDate", ""),
            currency1=data.get("currency1", ""),
            currency2=data.get("currency2", ""),
            amount1=float(data.get("amount1")) if data.get("amount1") is not None else 0.0,
            amount2=float(data.get("amount2")) if data.get("amount2") is not None else 0.0,
            tradeIdentifiers=list(data.get("tradeIdentifiers", [])),
            parties=list(data.get("parties", [])),
            exchangeRate=float(data.get("exchangeRate")) if data.get("exchangeRate") is not None else None,
            settlementType=data.get("settlementType", "PHYSICAL"),
            settlementCurrency=data.get("settlementCurrency"),
            buyerPartyReference=data.get("buyerPartyReference"),
            sellerPartyReference=data.get("sellerPartyReference"),
            currency2PayerPartyReference=data.get("currency2PayerPartyReference"),
            currency2ReceiverPartyReference=data.get("currency2ReceiverPartyReference"),
            sourceProduct=data.get("sourceProduct", "fxForward"),
            normalized_kind=data.get("normalizedKind", NORMALIZED_KIND_FX_SPOT_FORWARD_LIKE),
            sourceNamespace=data.get("sourceNamespace"),
            sourceVersion=data.get("sourceVersion"),
            llm_recovered_fields=list(data.get("llm_recovered_fields", [])),
            productTaxonomyQualifier=data.get("productTaxonomyQualifier"),
        )


@dataclass
class NormalizedFxSwap:
    tradeDate: str
    nearValueDate: str
    farValueDate: str
    nearCurrency1: str
    nearCurrency2: str
    nearAmount1: float
    nearAmount2: float
    farCurrency1: str
    farCurrency2: str
    farAmount1: float
    farAmount2: float
    tradeIdentifiers: List[Dict[str, str]] = field(default_factory=list)
    parties: List[Dict[str, Optional[str]]] = field(default_factory=list)
    nearExchangeRate: Optional[float] = None
    farExchangeRate: Optional[float] = None
    nearSettlementType: str = "PHYSICAL"
    farSettlementType: str = "PHYSICAL"
    #: Per-leg payer/receiver from exchangedCurrency2 for near leg.
    nearCurrency2PayerPartyReference: Optional[str] = None
    nearCurrency2ReceiverPartyReference: Optional[str] = None
    #: Per-leg payer/receiver from exchangedCurrency2 for far leg.
    farCurrency2PayerPartyReference: Optional[str] = None
    farCurrency2ReceiverPartyReference: Optional[str] = None
    buyerPartyReference: Optional[str] = None
    sellerPartyReference: Optional[str] = None
    sourceProduct: str = "fxSwap"
    normalized_kind: str = NORMALIZED_KIND_FX_SWAP
    sourceNamespace: Optional[str] = None
    sourceVersion: Optional[str] = None
    llm_recovered_fields: List[str] = field(default_factory=list)
    productTaxonomyQualifier: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "tradeDate": self.tradeDate,
            "nearValueDate": self.nearValueDate,
            "farValueDate": self.farValueDate,
            "nearCurrency1": self.nearCurrency1,
            "nearAmount1": self.nearAmount1,
            "nearCurrency2": self.nearCurrency2,
            "nearAmount2": self.nearAmount2,
            "farCurrency1": self.farCurrency1,
            "farAmount1": self.farAmount1,
            "farCurrency2": self.farCurrency2,
            "farAmount2": self.farAmount2,
            "tradeIdentifiers": self.tradeIdentifiers,
            "parties": self.parties,
            "sourceProduct": self.sourceProduct,
            "normalizedKind": self.normalized_kind,
        }
        if self.nearExchangeRate is not None:
            data["nearExchangeRate"] = self.nearExchangeRate
        if self.farExchangeRate is not None:
            data["farExchangeRate"] = self.farExchangeRate
        data["nearSettlementType"] = self.nearSettlementType
        data["farSettlementType"] = self.farSettlementType
        if self.nearCurrency2PayerPartyReference is not None:
            data["nearCurrency2PayerPartyReference"] = self.nearCurrency2PayerPartyReference
        if self.nearCurrency2ReceiverPartyReference is not None:
            data["nearCurrency2ReceiverPartyReference"] = self.nearCurrency2ReceiverPartyReference
        if self.farCurrency2PayerPartyReference is not None:
            data["farCurrency2PayerPartyReference"] = self.farCurrency2PayerPartyReference
        if self.farCurrency2ReceiverPartyReference is not None:
            data["farCurrency2ReceiverPartyReference"] = self.farCurrency2ReceiverPartyReference
        if self.buyerPartyReference is not None:
            data["buyerPartyReference"] = self.buyerPartyReference
        if self.sellerPartyReference is not None:
            data["sellerPartyReference"] = self.sellerPartyReference
        if self.productTaxonomyQualifier is not None:
            data["productTaxonomyQualifier"] = self.productTaxonomyQualifier
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NormalizedFxSwap":
        return cls(
            tradeDate=data.get("tradeDate", ""),
            nearValueDate=data.get("nearValueDate", ""),
            farValueDate=data.get("farValueDate", ""),
            nearCurrency1=data.get("nearCurrency1", ""),
            nearCurrency2=data.get("nearCurrency2", ""),
            nearAmount1=float(data.get("nearAmount1")) if data.get("nearAmount1") is not None else 0.0,
            nearAmount2=float(data.get("nearAmount2")) if data.get("nearAmount2") is not None else 0.0,
            farCurrency1=data.get("farCurrency1", ""),
            farCurrency2=data.get("farCurrency2", ""),
            farAmount1=float(data.get("farAmount1")) if data.get("farAmount1") is not None else 0.0,
            farAmount2=float(data.get("farAmount2")) if data.get("farAmount2") is not None else 0.0,
            tradeIdentifiers=list(data.get("tradeIdentifiers", [])),
            parties=list(data.get("parties", [])),
            nearExchangeRate=float(data.get("nearExchangeRate")) if data.get("nearExchangeRate") is not None else None,
            farExchangeRate=float(data.get("farExchangeRate")) if data.get("farExchangeRate") is not None else None,
            nearSettlementType=data.get("nearSettlementType", "PHYSICAL"),
            farSettlementType=data.get("farSettlementType", "PHYSICAL"),
            nearCurrency2PayerPartyReference=data.get("nearCurrency2PayerPartyReference"),
            nearCurrency2ReceiverPartyReference=data.get("nearCurrency2ReceiverPartyReference"),
            farCurrency2PayerPartyReference=data.get("farCurrency2PayerPartyReference"),
            farCurrency2ReceiverPartyReference=data.get("farCurrency2ReceiverPartyReference"),
            buyerPartyReference=data.get("buyerPartyReference"),
            sellerPartyReference=data.get("sellerPartyReference"),
            sourceProduct=data.get("sourceProduct", "fxSwap"),
            normalized_kind=data.get("normalizedKind", NORMALIZED_KIND_FX_SWAP),
            sourceNamespace=data.get("sourceNamespace"),
            sourceVersion=data.get("sourceVersion"),
            llm_recovered_fields=list(data.get("llm_recovered_fields", [])),
            productTaxonomyQualifier=data.get("productTaxonomyQualifier"),
        )


@dataclass
class NormalizedFxOption:
    """Vanilla FX option economics (FpML ``fxOption``)."""

    tradeDate: str
    expiryDate: str
    exerciseStyle: str
    putCurrency: str
    putAmount: float
    callCurrency: str
    callAmount: float
    strikeRate: float
    strikeCurrency1: str
    strikeCurrency2: str
    optionType: str
    tradeIdentifiers: List[Dict[str, str]] = field(default_factory=list)
    parties: List[Dict[str, Optional[str]]] = field(default_factory=list)
    buyerPartyReference: Optional[str] = None
    sellerPartyReference: Optional[str] = None
    valueDate: Optional[str] = None
    premiumAmount: Optional[float] = None
    premiumCurrency: Optional[str] = None
    premiumPaymentDate: Optional[str] = None
    settlementType: str = "PHYSICAL"
    sourceProduct: str = "fxOption"
    normalized_kind: str = NORMALIZED_KIND_FX_OPTION
    sourceNamespace: Optional[str] = None
    sourceVersion: Optional[str] = None
    llm_recovered_fields: List[str] = field(default_factory=list)
    productTaxonomyQualifier: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "tradeDate": self.tradeDate,
            "expiryDate": self.expiryDate,
            "exerciseStyle": self.exerciseStyle,
            "putCurrency": self.putCurrency,
            "putAmount": self.putAmount,
            "callCurrency": self.callCurrency,
            "callAmount": self.callAmount,
            "strikeRate": self.strikeRate,
            "strikeCurrency1": self.strikeCurrency1,
            "strikeCurrency2": self.strikeCurrency2,
            "optionType": self.optionType,
            "tradeIdentifiers": self.tradeIdentifiers,
            "parties": self.parties,
            "settlementType": self.settlementType,
            "sourceProduct": self.sourceProduct,
            "normalizedKind": self.normalized_kind,
        }
        if self.buyerPartyReference is not None:
            data["buyerPartyReference"] = self.buyerPartyReference
        if self.sellerPartyReference is not None:
            data["sellerPartyReference"] = self.sellerPartyReference
        if self.valueDate is not None:
            data["valueDate"] = self.valueDate
        if self.premiumAmount is not None:
            data["premiumAmount"] = self.premiumAmount
        if self.premiumCurrency is not None:
            data["premiumCurrency"] = self.premiumCurrency
        if self.premiumPaymentDate is not None:
            data["premiumPaymentDate"] = self.premiumPaymentDate
        if self.productTaxonomyQualifier is not None:
            data["productTaxonomyQualifier"] = self.productTaxonomyQualifier
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NormalizedFxOption":
        return cls(
            tradeDate=data.get("tradeDate", ""),
            expiryDate=data.get("expiryDate", ""),
            exerciseStyle=data.get("exerciseStyle", "European"),
            putCurrency=data.get("putCurrency", ""),
            putAmount=float(data.get("putAmount")) if data.get("putAmount") is not None else 0.0,
            callCurrency=data.get("callCurrency", ""),
            callAmount=float(data.get("callAmount")) if data.get("callAmount") is not None else 0.0,
            strikeRate=float(data.get("strikeRate")) if data.get("strikeRate") is not None else 0.0,
            strikeCurrency1=data.get("strikeCurrency1", ""),
            strikeCurrency2=data.get("strikeCurrency2", ""),
            optionType=data.get("optionType", "Call"),
            tradeIdentifiers=list(data.get("tradeIdentifiers", [])),
            parties=list(data.get("parties", [])),
            buyerPartyReference=data.get("buyerPartyReference"),
            sellerPartyReference=data.get("sellerPartyReference"),
            valueDate=data.get("valueDate"),
            premiumAmount=float(data.get("premiumAmount")) if data.get("premiumAmount") is not None else None,
            premiumCurrency=data.get("premiumCurrency"),
            premiumPaymentDate=data.get("premiumPaymentDate"),
            settlementType=data.get("settlementType", "PHYSICAL"),
            sourceProduct=data.get("sourceProduct", "fxOption"),
            normalized_kind=data.get("normalizedKind", NORMALIZED_KIND_FX_OPTION),
            sourceNamespace=data.get("sourceNamespace"),
            sourceVersion=data.get("sourceVersion"),
            llm_recovered_fields=list(data.get("llm_recovered_fields", [])),
            productTaxonomyQualifier=data.get("productTaxonomyQualifier"),
        )


NormalizedFxTrade = Union[NormalizedFxForward, NormalizedFxSwap, NormalizedFxOption]


@dataclass
class ConversionResult:
    ok: bool
    normalized: Optional[NormalizedFxTrade] = None
    cdm: Optional[Dict[str, Any]] = None
    deterministic_cdm: Optional[Dict[str, Any]] = None
    mapping_agent_cdm: Optional[Dict[str, Any]] = None
    validation: Optional[ValidationReport] = None
    errors: List[ValidationIssue] = field(default_factory=list)
    #: Optional trace from agent enrichment (LEI, taxonomy, addresses, diff-fix)
    enrichment_trace: Optional[Dict[str, Any]] = None
    #: Compliance status contract for deterministic + mapping stages.
    compliance: Optional[Dict[str, Any]] = None
    #: Optional machine-readable review ticket for manual triage.
    review_ticket: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "normalized": self.normalized.to_dict() if self.normalized else None,
            "cdm": self.cdm,
            "deterministic_cdm": self.deterministic_cdm,
            "mapping_agent_cdm": self.mapping_agent_cdm,
            "validation": self.validation.to_dict() if self.validation else None,
            "errors": [e.to_dict() for e in self.errors],
            "enrichment_trace": self.enrichment_trace,
            "compliance": self.compliance,
            "review_ticket": self.review_ticket,
        }


class ParserError(Exception):
    def __init__(self, issues: List[ValidationIssue]):
        self.issues = issues
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        super().__init__(message)
