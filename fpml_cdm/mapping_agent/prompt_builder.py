"""Build system prompt + bootstrap messages for the mapping agent."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .classifier import ClassifierResult
from .skill_store import SkillMeta, catalog_summary


BASE_SYSTEM_PROMPT = """\
You are a mapping agent that converts FpML XML documents to ISDA CDM v6 JSON.

Rules:
1) You MUST use tools — never output CDM JSON directly.
2) Propose structured ruleset patches via run_conversion_with_patch. Each patch adjusts how the deterministic parser maps FpML fields to the CDM normalized model.
3) Use inspect_fpml_trade, get_active_ruleset_summary, and list_supported_fx_adapters to understand the input before patching.
4) Prefer patches that reduce schema validation failures first, then semantic mismatches.
5) Avoid repeating identical tool calls. Each iteration should try a new patch or finish.
6) When you are satisfied with the result or cannot improve further, call the finish tool with status and summary.
7) If Rosetta validation is enabled, also aim to reduce Rosetta failures.
"""

LLM_NATIVE_SYSTEM_PROMPT = """\
You are a mapping agent that converts FpML XML documents to ISDA CDM v6 JSON using an LLM-native path.

Rules:
1) You MUST use tools — never paste CDM as plain assistant text; submit it only via submit_llm_cdm.
2) Build a complete CDM v6 envelope: {"trade": { ... }} matching the official CDM Trade JSON shape (party, counterparty, tradeDate, tradeIdentifier, tradeLot, product, etc.). Do **not** add `trade.partyRole`. Ground values in the source FpML.
3) Call inspect_fpml_trade (and list_supported_fx_adapters if needed) to understand the XML; use validate_best_effort or fpml_coverage_report to debug drafts.
4) Submit candidates with submit_llm_cdm(fpml_path, cdm_json, adapter_id, enable_rosetta, rosetta_timeout_seconds). The tool returns validation_summary counts and feedback_for_model.human_readable (concrete schema, semantic, Rosetta, and structure errors). If anything still fails, fix the trade JSON and submit again — the loop continues until all counts are zero (and Rosetta passes when enabled) or you call finish.
5) Avoid repeating identical tool calls with the same JSON.
6) When satisfied or stuck, call finish with status and summary.
"""


def build_system_prompt(
    catalog: List[SkillMeta],
    skill: Optional[SkillMeta] = None,
    *,
    mapping_mode: str = "ruleset",
) -> str:
    base = LLM_NATIVE_SYSTEM_PROMPT if mapping_mode == "llm_native" else BASE_SYSTEM_PROMPT
    parts = [base.strip()]
    parts.append("")
    parts.append(catalog_summary(catalog))

    if skill is not None:
        parts.append("")
        parts.append(f"## Active Skill: {skill.name} (v{skill.version})")
        parts.append("")
        parts.append(skill.body)

    return "\n".join(parts)


def build_bootstrap_user_message(
    fpml_path: str,
    classifier_result: ClassifierResult,
    best_adapter: str,
    problem_statement: str,
    enable_rosetta: bool,
    rosetta_timeout_seconds: int,
    *,
    mapping_mode: str = "ruleset",
    reference_cdm: Optional[Dict[str, Any]] = None,
) -> str:
    lines = [
        f"FpML input: {fpml_path}",
        f"Classified product: {classifier_result.product_local_names}",
        f"Initial best adapter: {best_adapter}",
    ]
    if classifier_result.skill_id:
        lines.append(f"Active skill: {classifier_result.skill_id}")
    lines.append("")
    lines.append(problem_statement)
    lines.append("")
    if mapping_mode == "llm_native":
        lines.append("There is no deterministic baseline. Call submit_llm_cdm with your best CDM v6 JSON.")
        lines.append(
            f"Use enable_rosetta={'true' if enable_rosetta else 'false'} and "
            f"rosetta_timeout_seconds={rosetta_timeout_seconds} on submit_llm_cdm."
        )
        lines.append(f"Use adapter_id={best_adapter!r} (from classification).")
        if reference_cdm:
            lines.append("")
            lines.append(
                "## Reference CDM shape\n"
                "Below is a REFERENCE CDM v6 JSON produced by the deterministic pipeline for this FpML input. "
                "Use it as a **structural template** for nesting, key names, and value-wrapper conventions "
                "(e.g. `\"tradeDate\": {\"value\": \"...\"}`, `\"currency\": {\"value\": \"...\"}`, "
                "`\"SettlementPayout\"` discriminator key).\n\n"
                "**IMPORTANT**: The reference is approximate and may contain errors in:\n"
                "- Counterparty role assignment (Party1/Party2 mapping)\n"
                "- Trade identifier issuer references\n"
                "- Party identifiers (may use XML id instead of partyId content)\n"
                "- Settlement type\n"
                "- Missing price composite (spotRate/forwardPoints)\n"
                "- Extra fields such as `partyRole` on Trade (omit `partyRole` — use `counterparty` and product payout only)\n\n"
                "You MUST derive all values independently from the FpML source, following the rules in the active skill. "
                "Do NOT blindly copy the reference — use it only for structural guidance.\n"
            )
            lines.append("```json")
            lines.append(json.dumps(reference_cdm, indent=2))
            lines.append("```")
    else:
        lines.append("Propose a structured patch and call run_conversion_with_patch.")
        lines.append(
            f"Set enable_rosetta={'true' if enable_rosetta else 'false'} and "
            f"rosetta_timeout_seconds={rosetta_timeout_seconds} in run_conversion_with_patch."
        )
        lines.append("If schema errors are 0 but semantic errors remain, focus on semantic mismatches.")
    lines.append("When satisfied or unable to improve, call finish.")
    return "\n".join(lines)
