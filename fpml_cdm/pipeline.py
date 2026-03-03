from __future__ import annotations

from pathlib import Path
from typing import Optional

from .llm.base import LLMProvider
from .llm_enricher import LLMFieldEnricher
from .parser import parse_fpml_fx
from .transformer import transform_to_cdm_v6
from .types import ConversionResult, ErrorCode, ParserError, ValidationIssue
from .validator import validate_transformation


def convert_fpml_to_cdm(
    fpml_path: str,
    strict: bool = True,
    llm_provider: Optional[LLMProvider] = None,
) -> ConversionResult:
    if llm_provider is not None:
        return _convert_with_llm(fpml_path, llm_provider)

    try:
        normalized = parse_fpml_fx(fpml_path, strict=strict)
    except ParserError as exc:
        return ConversionResult(ok=False, errors=exc.issues)

    cdm = transform_to_cdm_v6(normalized)
    validation = validate_transformation(fpml_path, cdm)

    return ConversionResult(
        ok=validation.valid,
        normalized=normalized,
        cdm=cdm,
        validation=validation,
        errors=[] if validation.valid else validation.errors,
    )


def _convert_with_llm(fpml_path: str, llm_provider: LLMProvider) -> ConversionResult:
    try:
        result = parse_fpml_fx(fpml_path, recovery_mode=True)
    except ParserError as exc:
        return ConversionResult(ok=False, errors=exc.issues)

    partial_model, issues = result  # type: ignore[misc]

    recoverable_codes = {ErrorCode.MISSING_REQUIRED_FIELD.value, ErrorCode.INVALID_VALUE.value}
    has_recoverable = any(i.code in recoverable_codes for i in issues)

    if has_recoverable:
        xml_content = Path(fpml_path).read_text(encoding="utf-8")
        enricher = LLMFieldEnricher(llm_provider)
        partial_model, issues = enricher.enrich(xml_content, partial_model, issues)

    remaining_errors = [i for i in issues if i.level == "error"]
    if remaining_errors:
        return ConversionResult(ok=False, errors=remaining_errors)

    cdm = transform_to_cdm_v6(partial_model)
    validation = validate_transformation(fpml_path, cdm)

    return ConversionResult(
        ok=validation.valid,
        normalized=partial_model,
        cdm=cdm,
        validation=validation,
        errors=[] if validation.valid else validation.errors,
    )


def extract_first_issue_message(result: ConversionResult) -> Optional[str]:
    if result.errors:
        first: ValidationIssue = result.errors[0]
        return f"{first.code}: {first.message}"
    return None
