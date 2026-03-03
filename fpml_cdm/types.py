from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


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


@dataclass
class NormalizedFxForward:
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
    sourceProduct: str = "fxForward"
    sourceNamespace: Optional[str] = None
    sourceVersion: Optional[str] = None
    llm_recovered_fields: List[str] = field(default_factory=list)

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
        }
        if self.settlementCurrency is not None:
            data["settlementCurrency"] = self.settlementCurrency
        if self.buyerPartyReference is not None:
            data["buyerPartyReference"] = self.buyerPartyReference
        if self.sellerPartyReference is not None:
            data["sellerPartyReference"] = self.sellerPartyReference
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
            sourceProduct=data.get("sourceProduct", "fxForward"),
            sourceNamespace=data.get("sourceNamespace"),
            sourceVersion=data.get("sourceVersion"),
        )


@dataclass
class ConversionResult:
    ok: bool
    normalized: Optional[NormalizedFxForward] = None
    cdm: Optional[Dict[str, Any]] = None
    validation: Optional[ValidationReport] = None
    errors: List[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "normalized": self.normalized.to_dict() if self.normalized else None,
            "cdm": self.cdm,
            "validation": self.validation.to_dict() if self.validation else None,
            "errors": [e.to_dict() for e in self.errors],
        }


class ParserError(Exception):
    def __init__(self, issues: List[ValidationIssue]):
        self.issues = issues
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        super().__init__(message)
