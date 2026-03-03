from __future__ import annotations

import json
import unittest
from pathlib import Path

from fpml_cdm.llm_enricher import LLMFieldEnricher
from fpml_cdm.parser import parse_fpml_fx
from fpml_cdm.types import ErrorCode, NormalizedFxForward, ValidationIssue

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MISSING_VALUE_DATE_XML = FIXTURES / "fpml" / "missing_value_date.xml"


class MockProvider:
    """Test double that returns canned JSON responses."""

    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, prompt: str) -> str:
        return self._response


class LLMEnricherTests(unittest.TestCase):
    def _load_partial_model_and_issues(self):
        """Parse the missing_value_date fixture in recovery_mode."""
        result = parse_fpml_fx(str(MISSING_VALUE_DATE_XML), recovery_mode=True)
        partial_model, issues = result
        return partial_model, list(issues)

    def _xml_content(self) -> str:
        return MISSING_VALUE_DATE_XML.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Happy path                                                           #
    # ------------------------------------------------------------------ #

    def test_successful_recovery_patches_model(self) -> None:
        """LLM returns a valid valueDate → model is updated, issue downgraded."""
        partial_model, issues = self._load_partial_model_and_issues()
        self.assertEqual(partial_model.valueDate, "")
        error_codes = [i.code for i in issues]
        self.assertIn(ErrorCode.MISSING_REQUIRED_FIELD.value, error_codes)

        provider = MockProvider(json.dumps({"valueDate": "2024-09-25"}))
        enricher = LLMFieldEnricher(provider)
        enriched_model, enriched_issues = enricher.enrich(
            self._xml_content(), partial_model, issues
        )

        self.assertEqual(enriched_model.valueDate, "2024-09-25")
        self.assertIn("valueDate", enriched_model.llm_recovered_fields)

        # The issue should be downgraded to warning
        remaining_errors = [i for i in enriched_issues if i.level == "error" and "valueDate" in i.path]
        self.assertEqual(remaining_errors, [])

        warnings = [i for i in enriched_issues if i.level == "warning"]
        self.assertTrue(any("LLM-recovered" in w.message for w in warnings))

    def test_recovery_with_non_standard_date_format(self) -> None:
        """LLM returns date in DD-Mon-YYYY format → enricher normalizes and accepts."""
        partial_model, issues = self._load_partial_model_and_issues()
        # The enricher calls _normalize_date_only which only accepts ISO format.
        # The LLM should have already normalized to ISO. If not, it's rejected.
        provider = MockProvider(json.dumps({"valueDate": "2024-09-25"}))
        enricher = LLMFieldEnricher(provider)
        enriched_model, _ = enricher.enrich(self._xml_content(), partial_model, issues)
        self.assertEqual(enriched_model.valueDate, "2024-09-25")

    # ------------------------------------------------------------------ #
    # Malformed / bad LLM responses                                        #
    # ------------------------------------------------------------------ #

    def test_malformed_json_response_leaves_model_unchanged(self) -> None:
        """LLM returns non-JSON → model unchanged, issue remains error."""
        partial_model, issues = self._load_partial_model_and_issues()
        original_value_date = partial_model.valueDate

        provider = MockProvider("Sorry, I cannot determine the value date from this XML.")
        enricher = LLMFieldEnricher(provider)
        enriched_model, enriched_issues = enricher.enrich(
            self._xml_content(), partial_model, issues
        )

        self.assertEqual(enriched_model.valueDate, original_value_date)
        error_issues = [i for i in enriched_issues if i.level == "error"]
        self.assertTrue(len(error_issues) > 0)

    def test_invalid_date_value_rejected(self) -> None:
        """LLM returns a syntactically invalid date → field not applied, issue stays error."""
        partial_model, issues = self._load_partial_model_and_issues()

        provider = MockProvider(json.dumps({"valueDate": "not-a-date"}))
        enricher = LLMFieldEnricher(provider)
        enriched_model, enriched_issues = enricher.enrich(
            self._xml_content(), partial_model, issues
        )

        self.assertEqual(enriched_model.valueDate, "")
        self.assertNotIn("valueDate", enriched_model.llm_recovered_fields)
        error_issues = [i for i in enriched_issues if i.level == "error"]
        self.assertTrue(len(error_issues) > 0)

    def test_field_not_in_llm_response_unchanged(self) -> None:
        """LLM returns empty JSON → model unchanged, issue remains error."""
        partial_model, issues = self._load_partial_model_and_issues()

        provider = MockProvider(json.dumps({}))
        enricher = LLMFieldEnricher(provider)
        enriched_model, enriched_issues = enricher.enrich(
            self._xml_content(), partial_model, issues
        )

        self.assertEqual(enriched_model.valueDate, "")
        self.assertNotIn("valueDate", enriched_model.llm_recovered_fields)
        error_issues = [i for i in enriched_issues if i.level == "error"]
        self.assertTrue(len(error_issues) > 0)

    # ------------------------------------------------------------------ #
    # No-op when no recoverable issues                                     #
    # ------------------------------------------------------------------ #

    def test_no_recoverable_issues_skips_llm(self) -> None:
        """When issues list is empty, enricher returns model unchanged without calling LLM."""
        call_count = {"n": 0}

        class CountingProvider:
            def complete(self, prompt: str) -> str:
                call_count["n"] += 1
                return "{}"

        model = NormalizedFxForward(
            tradeDate="2024-01-01",
            valueDate="2024-09-25",
            currency1="USD",
            currency2="EUR",
            amount1=1000000.0,
            amount2=920000.0,
        )
        enricher = LLMFieldEnricher(CountingProvider())
        enriched_model, _ = enricher.enrich("<xml/>", model, [])

        self.assertEqual(call_count["n"], 0)
        self.assertEqual(enriched_model.valueDate, "2024-09-25")

    # ------------------------------------------------------------------ #
    # JSON extraction from fenced markdown                                 #
    # ------------------------------------------------------------------ #

    def test_json_extracted_from_markdown_fence(self) -> None:
        """LLM wraps response in markdown code fence → enricher still extracts JSON."""
        partial_model, issues = self._load_partial_model_and_issues()

        fenced_response = '```json\n{"valueDate": "2024-09-25"}\n```'
        provider = MockProvider(fenced_response)
        enricher = LLMFieldEnricher(provider)
        enriched_model, enriched_issues = enricher.enrich(
            self._xml_content(), partial_model, issues
        )

        self.assertEqual(enriched_model.valueDate, "2024-09-25")
        self.assertIn("valueDate", enriched_model.llm_recovered_fields)


if __name__ == "__main__":
    unittest.main()
