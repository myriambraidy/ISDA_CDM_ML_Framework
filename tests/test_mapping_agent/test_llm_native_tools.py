"""Tests for LLM-native submit_llm_cdm tool and validate_best_effort."""

import json
import unittest
from pathlib import Path

from fpml_cdm.mapping_agent.tools import submit_llm_cdm, validate_best_effort
from fpml_cdm.parser import parse_fpml_fx
from fpml_cdm.transformer import transform_to_cdm_v6


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "fpml"


class TestSubmitLlmCdm(unittest.TestCase):
    def _path(self, name: str) -> str:
        p = FIXTURES_DIR / name
        if not p.exists():
            self.skipTest(f"Missing {p}")
        return str(p)

    def test_rejects_missing_trade_key(self):
        out = submit_llm_cdm(
            self._path("fx_forward.xml"),
            {"product": {}},
            "fxForward",
            enable_rosetta=False,
        )
        self.assertIn("error", out)

    def test_deterministic_cdm_passes(self):
        fpml = self._path("fx_forward.xml")
        norm = parse_fpml_fx(fpml, strict=False)
        cdm = transform_to_cdm_v6(norm)
        out = submit_llm_cdm(
            fpml,
            cdm,
            "fxForward",
            enable_rosetta=False,
        )
        self.assertNotIn("error", out)
        self.assertTrue(out.get("llm_native"))
        vs = out.get("validation_summary") or {}
        self.assertEqual(vs.get("schema_error_count"), 0)
        self.assertEqual(vs.get("semantic_error_count"), 0)
        fb = out.get("feedback_for_model") or {}
        self.assertIn("human_readable", fb)
        self.assertIn("validation_summary", fb)

    def test_bare_string_values_do_not_crash(self):
        """LLM sends tradeDate as bare string instead of {"value": "..."} — must not crash, should return feedback."""
        fpml = self._path("fx_forward.xml")
        cdm = {
            "trade": {
                "tradeDate": "2001-11-19",
                "party": [{"partyId": "party1"}],
                "tradeLot": [{"priceQuantity": [{"quantity": [{"value": 1000}]}]}],
                "product": {"economicTerms": {"payout": [{"SettlementPayout": {}}]}},
            }
        }
        out = submit_llm_cdm(fpml, cdm, "fxForward", enable_rosetta=False)
        self.assertNotIn("error", out, f"Should not crash; got: {out.get('error')}")
        vs = out.get("validation_summary") or {}
        self.assertGreater(vs.get("semantic_error_count", 0), 0)
        fb = out.get("feedback_for_model") or {}
        self.assertIn("human_readable", fb)

    def test_string_cdm_json_auto_deserialized(self):
        """If cdm_json is passed as a JSON string, it should be auto-deserialized."""
        fpml = self._path("fx_forward.xml")
        norm = parse_fpml_fx(fpml, strict=False)
        cdm = transform_to_cdm_v6(norm)
        cdm_str = json.dumps(cdm)
        out = submit_llm_cdm(fpml, cdm_str, "fxForward", enable_rosetta=False)
        self.assertNotIn("error", out, f"String cdm_json should be auto-deserialized; got: {out.get('error')}")
        vs = out.get("validation_summary") or {}
        self.assertEqual(vs.get("schema_error_count"), 0)

    def test_empty_trade_returns_feedback(self):
        """submit_llm_cdm with empty trade should return validation feedback, not crash."""
        fpml = self._path("fx_forward.xml")
        out = submit_llm_cdm(fpml, {"trade": {}}, "fxForward", enable_rosetta=False)
        self.assertNotIn("error", out, f"Should not crash; got: {out.get('error')}")
        fb = out.get("feedback_for_model") or {}
        self.assertIn("human_readable", fb)


class TestValidateBestEffort(unittest.TestCase):
    def _path(self, name: str) -> str:
        p = FIXTURES_DIR / name
        if not p.exists():
            self.skipTest(f"Missing {p}")
        return str(p)

    def test_string_cdm_json_auto_deserialized(self):
        fpml = self._path("fx_forward.xml")
        norm = parse_fpml_fx(fpml, strict=False)
        cdm = transform_to_cdm_v6(norm)
        cdm_str = json.dumps(cdm)
        out = validate_best_effort(fpml, cdm_str, enable_rosetta=False)
        self.assertNotIn("error", out, f"Should auto-deserialize; got: {out.get('error')}")

    def test_bare_string_values_do_not_crash(self):
        fpml = self._path("fx_forward.xml")
        cdm = {"trade": {"tradeDate": "2001-11-19"}}
        out = validate_best_effort(fpml, cdm, enable_rosetta=False)
        self.assertNotIn("error", out, f"Should not crash; got: {out.get('error')}")
        fb = out.get("feedback_for_model") or {}
        self.assertIn("human_readable", fb)


if __name__ == "__main__":
    unittest.main()
