from __future__ import annotations

import json
import unittest
from pathlib import Path

from fpml_cdm import parse_fpml_fx, transform_to_cdm_v6, validate_schema_data

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class TransformerTests(unittest.TestCase):
    def test_transform_matches_expected_forward_shape(self) -> None:
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "fx_forward.xml"))
        cdm = transform_to_cdm_v6(model)
        expected = _load_json(FIXTURES / "expected" / "fx_forward_cdm.json")
        self.assertEqual(cdm, expected)

    def test_transform_ndf_includes_settlement_currency(self) -> None:
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "ndf_forward.xml"))
        cdm = transform_to_cdm_v6(model)
        terms = (
            cdm["trade"]["product"]
            ["economicTerms"]["payout"][0]["SettlementPayout"]["settlementTerms"]
        )
        self.assertEqual(terms["settlementType"], "Cash")
        self.assertEqual(terms["settlementCurrency"]["value"], "USD")

    def test_transform_missing_exchange_rate_produces_valid_output(self) -> None:
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "missing_exchange_rate.xml"))
        cdm = transform_to_cdm_v6(model)
        trade = cdm.get("trade", {})
        prices = trade["tradeLot"][0]["priceQuantity"][0]["price"]
        self.assertEqual(prices, [])


if __name__ == "__main__":
    unittest.main()
