"""
Orchestrates Phase-3 agent-style enrichment: LEI, taxonomy, address indirection, diff-fix.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, TYPE_CHECKING

from .cdm_address_refactor import apply_document_address_pattern
from .cdm_diff_fix import run_diff_fix_agent
from .lei_resolver import LeiResolver, enrich_parties_with_lei
from .taxonomy import classify_taxonomy_deterministic, classify_taxonomy_llm, classify_taxonomy_rules_ndf

if TYPE_CHECKING:
    from ..llm.base import LLMProvider
    from ..types import NormalizedFxForward, ValidationIssue

TaxonomyMode = Literal["deterministic", "rules_ndf", "agent"]


@dataclass
class EnrichmentConfig:
    """Optional post-parse / post-transform enrichment."""

    lei_resolver: Optional[LeiResolver] = None
    taxonomy_mode: TaxonomyMode = "deterministic"
    taxonomy_llm: Optional["LLMProvider"] = None
    apply_document_addresses: bool = False
    run_diff_fix: bool = False
    diff_fix_llm: Optional[Callable[[str], str]] = None


def apply_parse_time_enrichment(
    model: "NormalizedFxForward",
    config: EnrichmentConfig,
) -> Dict[str, Any]:
    """
    Mutates ``model`` in place (parties, productTaxonomyQualifier).
    Returns a trace dict for ``ConversionResult.enrichment_trace``.
    """
    trace: Dict[str, Any] = {}

    if config.lei_resolver:
        touched = enrich_parties_with_lei(model.parties, config.lei_resolver)
        if touched:
            trace["lei_resolved_party_ids"] = touched

    if config.taxonomy_mode == "deterministic":
        # Omit ``productTaxonomyQualifier`` — transformer uses the same default (schema stays minimal).
        pass
    elif config.taxonomy_mode == "rules_ndf":
        q = classify_taxonomy_rules_ndf(model)
        model.productTaxonomyQualifier = q
        trace["productTaxonomyQualifier"] = q
    else:
        prov = config.taxonomy_llm
        if prov is not None:
            q = classify_taxonomy_llm(model, prov.complete)
        else:
            q = classify_taxonomy_deterministic(model)
            trace["taxonomy_fallback"] = "no_llm_provider"
        model.productTaxonomyQualifier = q
        trace["productTaxonomyQualifier"] = q

    return trace


def apply_post_transform_enrichment(
    cdm: Dict[str, Any],
    config: EnrichmentConfig,
    validation_errors: List["ValidationIssue"],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Returns ``(cdm_out, trace)`` — may deep-copy.
    """
    trace: Dict[str, Any] = {}
    out = cdm

    if config.apply_document_addresses:
        out = apply_document_address_pattern(out)
        trace["document_addresses"] = True

    if config.run_diff_fix and validation_errors:
        out, rest, dtrace = run_diff_fix_agent(
            out,
            validation_errors,
            llm_fix=config.diff_fix_llm,
        )
        trace["diff_fix"] = dtrace
        trace["diff_fix_remaining_errors"] = len(rest)

    return out, trace
