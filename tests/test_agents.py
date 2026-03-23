from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fpml_cdm import convert_fpml_to_cdm, parse_fpml_fx, transform_to_cdm_v6
from fpml_cdm.rosetta_validator import RosettaValidationResult
from fpml_cdm.agents import (
    EnrichmentConfig,
    LocalBicLeiTable,
    apply_document_address_pattern,
    classify_taxonomy_deterministic,
    classify_taxonomy_rules_ndf,
    default_lei_table_path,
    enrich_parties_with_lei,
    looks_like_bic,
    run_diff_fix_agent,
)
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class LeiResolverTests(unittest.TestCase):
    def test_looks_like_bic(self) -> None:
        self.assertTrue(looks_like_bic("CITIUS33"))
        self.assertFalse(looks_like_bic("Global Bank"))

    def test_local_table_resolves(self) -> None:
        tbl = LocalBicLeiTable(default_lei_table_path())
        self.assertEqual(tbl.resolve_lei("CITIUS33"), "5493000SCC07UI6DB380")

    def test_enrich_parties_mutates(self) -> None:
        tbl = LocalBicLeiTable(default_lei_table_path())
        parties = [{"id": "p1", "name": "CITIUS33"}]
        touched = enrich_parties_with_lei(parties, tbl)
        self.assertEqual(touched, ["p1"])
        self.assertEqual(parties[0].get("lei"), "5493000SCC07UI6DB380")


class TaxonomyTests(unittest.TestCase):
    def test_deterministic_default(self) -> None:
        m = parse_fpml_fx(str(FIXTURES / "fpml" / "ndf_forward.xml"))
        self.assertEqual(classify_taxonomy_deterministic(m), "ForeignExchange_Spot_Forward")

    def test_rules_ndf(self) -> None:
        m = parse_fpml_fx(str(FIXTURES / "fpml" / "ndf_forward.xml"))
        self.assertEqual(classify_taxonomy_rules_ndf(m), "ForeignExchange_NDF")


class AddressRefactorTests(unittest.TestCase):
    def test_adds_locations(self) -> None:
        m = parse_fpml_fx(str(FIXTURES / "fpml" / "fx_forward.xml"))
        cdm = transform_to_cdm_v6(m)
        out = apply_document_address_pattern(cdm)
        pq = out["trade"]["tradeLot"][0]["priceQuantity"][0]
        self.assertIn("location", pq["price"][0]["meta"])
        self.assertIn("observable", pq)


class DiffFixTests(unittest.TestCase):
    def test_deterministic_noop_on_empty(self) -> None:
        cdm = {"trade": {"product": {}}}
        out, rest, trace = run_diff_fix_agent(cdm, [])
        self.assertEqual(out["trade"]["product"], {})
        self.assertEqual(rest, [])


class PipelineEnrichmentTests(unittest.TestCase):
    def test_convert_with_lei_enrichment(self) -> None:
        cfg = EnrichmentConfig(lei_resolver=LocalBicLeiTable(default_lei_table_path()))
        # Use fixture where party names are not BIC — trace may be empty
        with patch(
            "fpml_cdm.pipeline.validate_cdm_rosetta_with_retry",
            return_value=RosettaValidationResult(valid=True, failures=[]),
        ):
            r = convert_fpml_to_cdm(str(FIXTURES / "fpml" / "fx_forward.xml"), enrichment=cfg)
        self.assertTrue(r.ok)
        self.assertIsNotNone(r.cdm)

    def test_convert_with_addresses(self) -> None:
        cfg = EnrichmentConfig(apply_document_addresses=True)
        with patch(
            "fpml_cdm.pipeline.validate_cdm_rosetta_with_retry",
            return_value=RosettaValidationResult(valid=True, failures=[]),
        ):
            r = convert_fpml_to_cdm(str(FIXTURES / "fpml" / "fx_forward.xml"), enrichment=cfg)
        self.assertTrue(r.ok)
        self.assertIn("document_addresses", (r.enrichment_trace or {}))


if __name__ == "__main__":
    unittest.main()
