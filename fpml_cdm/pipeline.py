from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING, Tuple

from .llm.base import LLMProvider
from .llm_enricher import LLMFieldEnricher
from .mapping_agent.agent import MappingAgentConfig, MappingAgentResult, run_mapping_agent
from .parser import parse_fpml_fx
from .rosetta_validator import validate_cdm_rosetta_with_retry
from .transformer import transform_to_cdm_v6
from .types import ConversionResult, ErrorCode, NormalizedFxForward, ParserError, ValidationIssue
from .validator import validate_transformation, validate_normalized_and_cdm

if TYPE_CHECKING:
    from .agents.enrichment import EnrichmentConfig


def convert_fpml_to_cdm(
    fpml_path: str,
    strict: bool = True,
    llm_provider: Optional[LLMProvider] = None,
    enrichment: Optional["EnrichmentConfig"] = None,
    *,
    mapping_llm_client: Optional[object] = None,
    mapping_model: Optional[str] = None,
    mapping_config: Optional[MappingAgentConfig] = None,
) -> ConversionResult:
    """
    FpML → normalized → CDM v6.

    ``enrichment`` enables Phase-3 steps: LEI lookup, taxonomy mode, DOCUMENT addresses,
    diff-and-fix (see ``fpml_cdm.agents.EnrichmentConfig``).
    """
    if llm_provider is not None:
        base = _convert_with_llm(fpml_path, llm_provider, enrichment=enrichment)
    else:
        try:
            normalized = parse_fpml_fx(fpml_path, strict=strict)
        except ParserError as exc:
            return ConversionResult(
                ok=False,
                errors=exc.issues,
                compliance={
                    "deterministic_passed": False,
                    "agent_passed": False,
                    "rosetta_passed": False,
                    "overall_compliant": False,
                    "review_required": True,
                    "failure_reason": "PARSER_ERROR",
                },
            )

        trace: Dict[str, Any] = {}
        if enrichment is not None:
            from .agents.enrichment import apply_parse_time_enrichment, apply_post_transform_enrichment

            trace.update(apply_parse_time_enrichment(normalized, enrichment))

        cdm = transform_to_cdm_v6(normalized)
        validation = validate_transformation(fpml_path, cdm)

        if enrichment is not None:
            from .agents.enrichment import apply_post_transform_enrichment

            cdm, post_trace = apply_post_transform_enrichment(
                cdm,
                enrichment,
                validation.errors,
            )
            trace.update(post_trace)
            if enrichment.run_diff_fix or enrichment.apply_document_addresses:
                validation = validate_normalized_and_cdm(normalized, cdm)

        base = ConversionResult(
            ok=validation.valid,
            normalized=normalized,
            cdm=cdm,
            validation=validation,
            errors=[] if validation.valid else validation.errors,
            enrichment_trace=trace or None,
        )

    return _apply_mapping_compliance_stage(
        fpml_path=fpml_path,
        base=base,
        mapping_llm_client=mapping_llm_client,
        mapping_model=mapping_model,
        mapping_config=mapping_config,
    )


def _convert_with_llm(
    fpml_path: str,
    llm_provider: LLMProvider,
    enrichment: Optional["EnrichmentConfig"] = None,
) -> ConversionResult:
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

    trace: Dict[str, Any] = {}
    if enrichment is not None:
        from .agents.enrichment import apply_parse_time_enrichment, apply_post_transform_enrichment

        trace.update(apply_parse_time_enrichment(partial_model, enrichment))

    cdm = transform_to_cdm_v6(partial_model)
    validation = validate_transformation(fpml_path, cdm)

    if enrichment is not None:
        from .agents.enrichment import apply_post_transform_enrichment

        cdm, post_trace = apply_post_transform_enrichment(
            cdm,
            enrichment,
            validation.errors,
        )
        trace.update(post_trace)
        if enrichment.run_diff_fix or enrichment.apply_document_addresses:
            validation = validate_normalized_and_cdm(partial_model, cdm)

    return ConversionResult(
        ok=validation.valid,
        normalized=partial_model,
        cdm=cdm,
        validation=validation,
        errors=[] if validation.valid else validation.errors,
        enrichment_trace=trace or None,
    )


def extract_first_issue_message(result: ConversionResult) -> Optional[str]:
    if result.errors:
        first: ValidationIssue = result.errors[0]
        return f"{first.code}: {first.message}"
    return None


def _score_report(report: Dict[str, Any]) -> Tuple[int, int]:
    errors = report.get("errors") or []
    schema_err = sum(1 for e in errors if e.get("code") == "SCHEMA_VALIDATION_FAILED")
    semantic_err = sum(1 for e in errors if e.get("code") == "SEMANTIC_VALIDATION_FAILED")
    return (schema_err, semantic_err)


def _apply_mapping_compliance_stage(
    *,
    fpml_path: str,
    base: ConversionResult,
    mapping_llm_client: Optional[object],
    mapping_model: Optional[str],
    mapping_config: Optional[MappingAgentConfig],
) -> ConversionResult:
    deterministic_cdm = copy.deepcopy(base.cdm) if isinstance(base.cdm, dict) else None
    deterministic_report = base.validation.to_dict() if base.validation is not None else {}
    det_schema_err, det_sem_err = _score_report(deterministic_report)
    det_ros = validate_cdm_rosetta_with_retry(
        deterministic_cdm or {},
        timeout_seconds=(mapping_config.rosetta_timeout_seconds if mapping_config else 60),
        max_attempts=2,
    )
    det_ros_fail = 0 if det_ros.valid else max(1, len(det_ros.failures))
    deterministic_passed = (det_schema_err, det_sem_err, det_ros_fail) == (0, 0, 0)

    mapping_result: Optional[MappingAgentResult] = None
    mapping_agent_cdm = deterministic_cdm
    agent_schema_err, agent_sem_err, agent_ros_fail = det_schema_err, det_sem_err, det_ros_fail
    agent_report_dict = deterministic_report
    agent_rosetta_report = det_ros.to_dict()

    cfg = copy.deepcopy(mapping_config) if mapping_config is not None else MappingAgentConfig()
    cfg.enable_rosetta = True

    if mapping_llm_client is not None and mapping_model:
        mapping_result = run_mapping_agent(
            fpml_path=fpml_path,
            llm_client=mapping_llm_client,
            model=mapping_model,
            config=cfg,
        )
        mapping_agent_cdm = mapping_result.best_cdm_json
        agent_report_dict = mapping_result.best_validation_report
        agent_schema_err = int(mapping_result.best_schema_error_count)
        agent_sem_err = int(mapping_result.best_semantic_error_count)
        agent_ros_fail = int(mapping_result.best_rosetta_failure_count)
        ros = validate_cdm_rosetta_with_retry(
            mapping_agent_cdm or {},
            timeout_seconds=cfg.rosetta_timeout_seconds,
            max_attempts=2,
        )
        agent_rosetta_report = ros.to_dict()
        agent_ros_fail = 0 if ros.valid else max(1, len(ros.failures))

    agent_passed = (agent_schema_err, agent_sem_err, agent_ros_fail) == (0, 0, 0)
    overall_compliant = agent_passed
    review_required = not overall_compliant

    rosetta_error = (agent_rosetta_report or {}).get("error")
    failure_reason = None
    if not overall_compliant:
        failure_reason = "ROSETTA_INFRA_UNAVAILABLE" if rosetta_error else "COMPLIANCE_NOT_REACHED"

    agent_coverage_gaps = mapping_result.best_coverage_gaps if mapping_result else -1

    compliance = {
        "deterministic_passed": deterministic_passed,
        "agent_passed": agent_passed,
        "rosetta_passed": agent_ros_fail == 0,
        "overall_compliant": overall_compliant,
        "review_required": review_required,
        "failure_reason": failure_reason,
        "deterministic_score": {
            "schema_error_count": det_schema_err,
            "semantic_error_count": det_sem_err,
            "rosetta_failure_count": det_ros_fail,
        },
        "agent_score": {
            "schema_error_count": agent_schema_err,
            "semantic_error_count": agent_sem_err,
            "rosetta_failure_count": agent_ros_fail,
            "coverage_gaps": agent_coverage_gaps,
        },
        "rosetta_report": agent_rosetta_report,
        "skill_id": mapping_result.skill_id if mapping_result else None,
        "skill_version": mapping_result.skill_version if mapping_result else None,
    }

    review_ticket = None
    if review_required:
        review_ticket = {
            "status": "pending_manual_review",
            "fpml_path": fpml_path,
            "failure_reason": failure_reason,
            "deterministic_score": compliance["deterministic_score"],
            "agent_score": compliance["agent_score"],
            "adapter_id": mapping_result.adapter_id if mapping_result is not None else (base.normalized.sourceProduct if base.normalized else None),
            "rosetta_failures": (agent_rosetta_report or {}).get("failures", []),
            "top_validation_errors": (agent_report_dict.get("errors") or [])[:10],
        }

    final_validation = base.validation
    final_errors = list(base.errors)
    if mapping_result is not None:
        # Keep deterministic normalized model as reference, but validate final CDM against source.
        from .validator import validate_transformation

        final_validation = validate_transformation(fpml_path, mapping_agent_cdm or {})
        final_errors = [] if final_validation.valid else list(final_validation.errors)

    return ConversionResult(
        ok=overall_compliant,
        normalized=base.normalized,
        cdm=mapping_agent_cdm,
        deterministic_cdm=deterministic_cdm,
        mapping_agent_cdm=mapping_agent_cdm,
        validation=final_validation,
        errors=final_errors,
        enrichment_trace=base.enrichment_trace,
        compliance=compliance,
        review_ticket=review_ticket,
    )
