from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from fpml_cdm.adapters.registry import (
    SUPPORTED_FX_ADAPTER_IDS,
    detect_fx_adapter_product,
    iter_fx_adapter_ids_by_priority,
)
from fpml_cdm.rulesets import list_ruleset_adapter_ids
from fpml_cdm.xml_utils import _local_name


class AdapterRegistryTests(unittest.TestCase):
    def test_rulesets_match_registry(self) -> None:
        self.assertEqual(set(list_ruleset_adapter_ids()), set(SUPPORTED_FX_ADAPTER_IDS))

    def test_priority_order_forward_before_single_leg(self) -> None:
        self.assertEqual(
            iter_fx_adapter_ids_by_priority(),
            ["fxForward", "fxSingleLeg", "fxOption", "fxSwap"],
        )

    def test_detect_prefers_lower_priority_adapter_id(self) -> None:
        trade = ET.fromstring(
            """
            <trade xmlns="http://www.fpml.org/FpML-5/confirmation">
              <tradeHeader><tradeDate>2024-01-01</tradeDate></tradeHeader>
              <fxSingleLeg>
                <exchangedCurrency1><paymentAmount><currency>USD</currency><amount>1</amount></paymentAmount></exchangedCurrency1>
                <exchangedCurrency2><paymentAmount><currency>EUR</currency><amount>1</amount></paymentAmount></exchangedCurrency2>
                <valueDate>2024-02-01</valueDate>
              </fxSingleLeg>
              <fxForward>
                <exchangedCurrency1><paymentAmount><currency>GBP</currency><amount>2</amount></paymentAmount></exchangedCurrency1>
                <exchangedCurrency2><paymentAmount><currency>JPY</currency><amount>2</amount></paymentAmount></exchangedCurrency2>
                <valueDate>2024-03-01</valueDate>
              </fxForward>
            </trade>
            """
        )
        adapter_id, node = detect_fx_adapter_product(trade)
        self.assertEqual(adapter_id, "fxForward")
        self.assertEqual(_local_name(node.tag), "fxForward")


if __name__ == "__main__":
    unittest.main()
