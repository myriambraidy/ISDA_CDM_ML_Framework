from __future__ import annotations

import json
import unittest
from pathlib import Path

from fpml_cdm import parse_fpml_fx, transform_to_cdm_v6

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

    def test_trade_identifier_rows_include_issuer_choice(self) -> None:
        """Each emitted tradeIdentifier must include issuerReference or issuer."""
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "fx-ex01-fx-spot.xml"))
        cdm = transform_to_cdm_v6(model)
        ids = cdm["trade"]["tradeIdentifier"]
        self.assertEqual(len(ids), 4)
        for ident in ids:
            self.assertTrue("issuerReference" in ident or "issuer" in ident)

    def test_payer_receiver_follows_exchanged_currency2(self) -> None:
        """SettlementPayout uses exchangedCurrency2 payer/receiver (not currency1 buyer/seller)."""
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "fx-ex01-fx-spot.xml"))
        self.assertEqual(model.currency2PayerPartyReference, "party1")
        self.assertEqual(model.currency2ReceiverPartyReference, "party2")
        # ec1 would imply opposite: payer party2 / receiver party1
        self.assertEqual(model.buyerPartyReference, "party2")
        self.assertEqual(model.sellerPartyReference, "party1")
        cdm = transform_to_cdm_v6(model)
        pr = (
            cdm["trade"]["product"]["economicTerms"]["payout"][0]["SettlementPayout"]["payerReceiver"]
        )
        self.assertEqual(pr["payer"], "Party1")
        self.assertEqual(pr["receiver"], "Party2")


if __name__ == "__main__":
    unittest.main()
