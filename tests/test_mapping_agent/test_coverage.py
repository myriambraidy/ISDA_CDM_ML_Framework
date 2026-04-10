"""Tests for mapping_agent.coverage."""

import json
import unittest
from pathlib import Path

from fpml_cdm.mapping_agent.coverage import (
    compute_coverage,
    fpml_coverage_report,
    _walk_fpml_paths,
    _flatten_cdm_keys,
)


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "fpml"


class TestCoverageWalk(unittest.TestCase):
    def test_flatten_cdm_keys(self):
        cdm = {"trade": {"tradeDate": "2024-01-01", "party": [{"name": {"value": "A"}}]}}
        keys = _flatten_cdm_keys(cdm)
        self.assertIn("tradedate", keys)
        self.assertIn("name", keys)
        self.assertIn("value", keys)

    def test_flatten_empty(self):
        self.assertEqual(_flatten_cdm_keys({}), set())


class TestCoverageCompute(unittest.TestCase):
    def _fixture(self, name: str) -> str:
        p = FIXTURES_DIR / name
        if not p.exists():
            self.skipTest(f"Fixture not found: {p}")
        return str(p)

    def test_fx_forward_coverage(self):
        path = self._fixture("fx_forward.xml")
        from fpml_cdm.parser import parse_fpml_fx
        from fpml_cdm.transformer import transform_to_cdm_v6

        normalized = parse_fpml_fx(path, strict=False)
        cdm = transform_to_cdm_v6(normalized)
        report = compute_coverage(path, cdm)
        self.assertGreater(report.total_paths, 0)
        self.assertGreaterEqual(report.mapped_count, 0)
        d = report.to_dict()
        self.assertIn("coverage_pct", d)
        self.assertGreaterEqual(d["coverage_pct"], 0)

    def test_nonexistent_file(self):
        report = compute_coverage("/does/not/exist.xml", {})
        self.assertEqual(report.total_paths, 0)

    def test_empty_cdm(self):
        path = self._fixture("fx_forward.xml")
        report = compute_coverage(path, {})
        self.assertGreater(report.total_paths, 0)
        self.assertEqual(report.mapped_count, 0)

    def test_tool_wrapper(self):
        path = self._fixture("fx_forward.xml")
        result = fpml_coverage_report(path, {"tradeDate": "2024-01-01"})
        self.assertIn("total_paths", result)
        self.assertIn("coverage_pct", result)


if __name__ == "__main__":
    unittest.main()
