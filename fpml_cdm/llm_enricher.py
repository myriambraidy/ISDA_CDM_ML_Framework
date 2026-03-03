from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from .llm.base import LLMProvider
from .parser import _normalize_date_only, _parse_amount, _parse_currency
from .types import ErrorCode, NormalizedFxForward, ValidationIssue

_RECOVERABLE_CODES = {ErrorCode.MISSING_REQUIRED_FIELD.value, ErrorCode.INVALID_VALUE.value}

_DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parents[1]
    / ".agent"
    / "skills"
    / "fpml-to-cdm-fx-forward"
    / "references"
    / "LLM_RECOVERY_RULES.md"
)

# Map field names in NormalizedFxForward to their setter logic
# Each value is (setter_fn, field_attr_name)
_FIELD_HANDLERS = {
    "tradeDate": "date",
    "valueDate": "date",
    "currency1": "currency",
    "currency2": "currency",
    "amount1": "amount",
    "amount2": "amount",
    "exchangeRate": "amount",
    "settlementCurrency": "currency",
    "buyerPartyReference": "string",
    "sellerPartyReference": "string",
}


def _extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from an LLM response string."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Find first {...} block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


class LLMFieldEnricher:
    def __init__(
        self,
        provider: LLMProvider,
        rules_path: Optional[str] = None,
    ) -> None:
        self._provider = provider
        self._rules_path = Path(rules_path) if rules_path else _DEFAULT_RULES_PATH

    def _load_rules(self) -> str:
        if self._rules_path.exists():
            return self._rules_path.read_text(encoding="utf-8")
        return "(No recovery rules file found.)"

    def _build_prompt(
        self,
        xml_content: str,
        recoverable_issues: List[ValidationIssue],
        rules_content: str,
    ) -> str:
        missing_lines = "\n".join(
            f"- {issue.path}: {issue.message}" for issue in recoverable_issues
        )
        return (
            "You are an FpML FX Forward field recovery assistant.\n\n"
            "## Recovery Rules\n"
            f"{rules_content}\n\n"
            "## Source FpML XML\n"
            f"```xml\n{xml_content}\n```\n\n"
            "## Missing Fields\n"
            f"{missing_lines}\n\n"
            "Return ONLY a JSON object with inferred values. "
            "Omit any field you cannot find with high confidence. Do not guess."
        )

    def _infer_field_name(self, path: str) -> Optional[str]:
        """Derive a NormalizedFxForward attribute name from an XML path."""
        segment = path.split("/")[-1]
        mappings = {
            "valueDate": "valueDate",
            "tradeDate": "tradeDate",
            "currency": None,  # need parent context
            "amount": None,    # need parent context
            "rate": "exchangeRate",
            "settlementCurrency": "settlementCurrency",
            "buyerPartyReference": "buyerPartyReference",
            "sellerPartyReference": "sellerPartyReference",
        }

        # Direct mapping
        if segment in _FIELD_HANDLERS:
            return segment

        # Path-based mappings
        if "exchangedCurrency1" in path and "currency" in segment:
            return "currency1"
        if "exchangedCurrency1" in path and "amount" in segment:
            return "amount1"
        if "exchangedCurrency2" in path and "currency" in segment:
            return "currency2"
        if "exchangedCurrency2" in path and "amount" in segment:
            return "amount2"
        if "exchangeRate" in path and "rate" in segment:
            return "exchangeRate"
        if segment in mappings:
            return mappings[segment]

        return None

    def _apply_field(
        self,
        model: NormalizedFxForward,
        field_name: str,
        raw_value: str,
        issues: List[ValidationIssue],
    ) -> bool:
        """Validate and apply a single recovered field. Returns True on success."""
        kind = _FIELD_HANDLERS.get(field_name)
        if kind is None:
            return False

        dummy_issues: List[ValidationIssue] = []

        if kind == "date":
            normalized = _normalize_date_only(str(raw_value))
            if normalized is None:
                return False
            setattr(model, field_name, normalized)
            return True

        if kind == "currency":
            val = _parse_currency(str(raw_value).strip().upper(), f"llm/{field_name}", dummy_issues)
            if val is None or dummy_issues:
                return False
            setattr(model, field_name, val)
            return True

        if kind == "amount":
            val = _parse_amount(str(raw_value), f"llm/{field_name}", dummy_issues)
            if val is None or dummy_issues:
                return False
            setattr(model, field_name, val)
            return True

        if kind == "string":
            setattr(model, field_name, str(raw_value))
            return True

        return False

    def enrich(
        self,
        xml_content: str,
        partial_model: NormalizedFxForward,
        issues: List[ValidationIssue],
    ) -> Tuple[NormalizedFxForward, List[ValidationIssue]]:
        """Attempt to recover missing/invalid fields via LLM.

        Returns the (possibly enriched) model and the updated issues list.
        Resolved issues are downgraded from error to warning.
        Unresolved issues remain unchanged.
        """
        recoverable = [i for i in issues if i.code in _RECOVERABLE_CODES]
        if not recoverable:
            return partial_model, issues

        rules_content = self._load_rules()
        prompt = self._build_prompt(xml_content, recoverable, rules_content)

        try:
            response = self._provider.complete(prompt)
        except Exception as exc:
            # Provider failure — return unchanged
            return partial_model, issues

        recovered_json = _extract_json(response)
        if recovered_json is None:
            return partial_model, issues

        # Map path-based issue to field name for fast lookup
        issue_by_field: dict[str, ValidationIssue] = {}
        for issue in recoverable:
            field_name = self._infer_field_name(issue.path)
            if field_name:
                issue_by_field[field_name] = issue

        # Also allow LLM to use direct field names
        for field_name in list(recovered_json.keys()):
            if field_name not in issue_by_field and field_name in _FIELD_HANDLERS:
                # Find a matching issue by field name substring
                for issue in recoverable:
                    if field_name.lower() in issue.path.lower() or field_name.lower() in issue.message.lower():
                        issue_by_field[field_name] = issue
                        break

        resolved_issues: set[int] = set()

        for field_name, raw_value in recovered_json.items():
            if field_name not in _FIELD_HANDLERS:
                continue
            if raw_value is None:
                continue

            success = self._apply_field(partial_model, field_name, str(raw_value), [])
            if success:
                partial_model.llm_recovered_fields.append(field_name)
                # Downgrade the matching issue to a warning
                matching_issue = issue_by_field.get(field_name)
                if matching_issue is not None:
                    idx = issues.index(matching_issue)
                    resolved_issues.add(idx)
                    issues[idx] = ValidationIssue(
                        code=matching_issue.code,
                        message=f"LLM-recovered: {matching_issue.message}",
                        path=matching_issue.path,
                        level="warning",
                    )

        return partial_model, issues
