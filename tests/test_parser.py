from __future__ import annotations

import json
import unittest
from pathlib import Path

from fpml_cdm import ErrorCode, ParserError, parse_fpml_fx

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class ParserTests(unittest.TestCase):
    def test_parse_fx_forward_matches_expected(self) -> None:
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "fx_forward.xml"))
        expected = _load_json(FIXTURES / "expected" / "fx_forward_parsed.json")
        self.assertEqual(model.to_dict(), expected)

    def test_parse_fx_single_leg_supported(self) -> None:
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "fx_single_leg.xml"))
        self.assertEqual(model.sourceProduct, "fxSingleLeg")
        self.assertEqual(model.currency1, "GBP")
        self.assertEqual(model.currency2, "USD")

    def test_parse_ndf_sets_cash_settlement(self) -> None:
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "ndf_forward.xml"))
        self.assertEqual(model.settlementType, "CASH")
        self.assertEqual(model.settlementCurrency, "USD")

    def test_parse_fx_swap_matches_expected(self) -> None:
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "fx_swap.xml"))
        expected = _load_json(FIXTURES / "expected" / "fx_swap_parsed.json")
        self.assertEqual(model.to_dict(), expected)

    def test_parse_fx_swap_alt_date_paths(self) -> None:
        model = parse_fpml_fx(str(FIXTURES / "fpml" / "fx_swap_alt_dates.xml"))
        self.assertEqual(model.nearValueDate, "2024-07-12")
        self.assertEqual(model.farValueDate, "2024-10-12")

    def test_parse_fx_swap_single_leg_pair_extracts_per_leg_payer_receiver(self) -> None:
        model = parse_fpml_fx(
            str(
                FIXTURES.parent.parent
                / "data"
                / "corpus"
                / "fpml_official"
                / "fpml_4_9_5"
                / "xml"
                / "fx-derivatives"
                / "fx-ex08-fx-swap.xml"
            )
        )
        self.assertEqual(model.nearCurrency2PayerPartyReference, "party1")
        self.assertEqual(model.nearCurrency2ReceiverPartyReference, "party2")
        self.assertEqual(model.farCurrency2PayerPartyReference, "party2")
        self.assertEqual(model.farCurrency2ReceiverPartyReference, "party1")

    def test_parse_invalid_date_raises_structured_error(self) -> None:
        with self.assertRaises(ParserError) as ctx:
            parse_fpml_fx(str(FIXTURES / "fpml" / "invalid_date.xml"))
        codes = {issue.code for issue in ctx.exception.issues}
        self.assertIn(ErrorCode.INVALID_VALUE.value, codes)

    def test_parse_missing_value_date_raises_structured_error(self) -> None:
        with self.assertRaises(ParserError) as ctx:
            parse_fpml_fx(str(FIXTURES / "fpml" / "missing_value_date.xml"))
        codes = {issue.code for issue in ctx.exception.issues}
        self.assertIn(ErrorCode.MISSING_REQUIRED_FIELD.value, codes)

    def test_parse_unsupported_product_rejected(self) -> None:
        with self.assertRaises(ParserError) as ctx:
            parse_fpml_fx(str(FIXTURES / "fpml" / "unsupported_fx_digital_option.xml"))
        self.assertEqual(ctx.exception.issues[0].code, ErrorCode.UNSUPPORTED_PRODUCT.value)


if __name__ == "__main__":
    unittest.main()
