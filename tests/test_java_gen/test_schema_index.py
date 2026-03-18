"""Tests for the CDM schema index."""

import unittest

from fpml_cdm.java_gen.schema_index import SchemaIndex, _camel_to_screaming_snake


class CamelToScreamingSnakeTests(unittest.TestCase):
    def test_simple(self) -> None:
        self.assertEqual(_camel_to_screaming_snake("Physical"), "PHYSICAL")

    def test_two_words(self) -> None:
        self.assertEqual(_camel_to_screaming_snake("ExchangeRate"), "EXCHANGE_RATE")

    def test_trailing_digit(self) -> None:
        self.assertEqual(_camel_to_screaming_snake("Party1"), "PARTY_1")
        self.assertEqual(_camel_to_screaming_snake("Party2"), "PARTY_2")

    def test_all_upper(self) -> None:
        self.assertEqual(_camel_to_screaming_snake("USD"), "USD")

    def test_multi_word_with_digit(self) -> None:
        self.assertEqual(_camel_to_screaming_snake("Cash"), "CASH")


class SchemaIndexBuildTests(unittest.TestCase):
    """Test the index builds correctly over the real 845 schema files."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.idx = SchemaIndex()

    def test_has_trade(self) -> None:
        filename = self.idx.type_name_to_file("Trade")
        self.assertEqual(filename, "cdm-event-common-Trade.schema.json")

    def test_has_party(self) -> None:
        filename = self.idx.type_name_to_file("Party")
        self.assertEqual(filename, "cdm-base-staticdata-party-Party.schema.json")

    def test_has_settlement_payout(self) -> None:
        filename = self.idx.type_name_to_file("SettlementPayout")
        self.assertIsNotNone(filename)
        self.assertIn("SettlementPayout", filename)

    def test_case_insensitive_lookup(self) -> None:
        self.assertEqual(
            self.idx.type_name_to_file("trade"),
            self.idx.type_name_to_file("Trade"),
        )

    def test_unknown_type_returns_none(self) -> None:
        self.assertIsNone(self.idx.type_name_to_file("DoesNotExist12345"))

    def test_all_type_names_not_empty(self) -> None:
        names = self.idx.all_type_names()
        self.assertGreater(len(names), 800)
        self.assertIn("Trade", names)
        self.assertIn("Party", names)

    def test_reverse_lookup(self) -> None:
        self.assertEqual(
            self.idx.file_to_type_name("cdm-event-common-Trade.schema.json"),
            "Trade",
        )


class JavaClassResolverTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.idx = SchemaIndex()

    def test_trade(self) -> None:
        result = self.idx.schema_ref_to_java_class(
            "cdm-event-common-Trade.schema.json"
        )
        self.assertEqual(result, "cdm.event.common.Trade")

    def test_party(self) -> None:
        result = self.idx.schema_ref_to_java_class(
            "cdm-base-staticdata-party-Party.schema.json"
        )
        self.assertEqual(result, "cdm.base.staticdata.party.Party")

    def test_field_with_meta_string(self) -> None:
        result = self.idx.schema_ref_to_java_class(
            "com-rosetta-model-metafields-FieldWithMetaString.schema.json"
        )
        self.assertEqual(result, "com.rosetta.model.metafields.FieldWithMetaString")

    def test_settlement_payout(self) -> None:
        result = self.idx.schema_ref_to_java_class(
            "cdm-product-template-SettlementPayout.schema.json"
        )
        self.assertEqual(result, "cdm.product.template.SettlementPayout")

    def test_java_class_parts(self) -> None:
        fq, pkg, simple = self.idx.java_class_parts(
            "cdm-event-common-Trade.schema.json"
        )
        self.assertEqual(fq, "cdm.event.common.Trade")
        self.assertEqual(pkg, "cdm.event.common")
        self.assertEqual(simple, "Trade")

    def test_unknown_ref_returns_none(self) -> None:
        result = self.idx.schema_ref_to_java_class("nonexistent.schema.json")
        self.assertIsNone(result)


class EnumDetectionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.idx = SchemaIndex()

    def test_counterparty_role_is_enum(self) -> None:
        self.assertTrue(self.idx.is_enum_by_name("CounterpartyRoleEnum"))

    def test_trade_is_not_enum(self) -> None:
        self.assertFalse(self.idx.is_enum_by_name("Trade"))

    def test_party_is_not_enum(self) -> None:
        self.assertFalse(self.idx.is_enum_by_name("Party"))

    def test_enum_values_counterparty_role(self) -> None:
        values = self.idx.enum_values_by_name("CounterpartyRoleEnum")
        self.assertIn("Party1", values)
        self.assertIn("Party2", values)

    def test_enum_values_payer_receiver(self) -> None:
        values = self.idx.enum_values_by_name("PayerReceiverEnum")
        self.assertIn("Payer", values)
        self.assertIn("Receiver", values)

    def test_enum_java_constants(self) -> None:
        filename = self.idx.type_name_to_file("CounterpartyRoleEnum")
        assert filename is not None
        constants = self.idx.enum_java_constants(filename)
        json_vals = {c["json_value"] for c in constants}
        java_vals = {c["java_constant"] for c in constants}
        self.assertEqual(json_vals, {"Party1", "Party2"})
        self.assertEqual(java_vals, {"PARTY_1", "PARTY_2"})

    def test_all_enum_names_not_empty(self) -> None:
        enums = self.idx.all_enum_names()
        self.assertGreater(len(enums), 50)
        self.assertIn("CounterpartyRoleEnum", enums)

    def test_non_enum_returns_empty_values(self) -> None:
        values = self.idx.enum_values_by_name("Trade")
        self.assertEqual(values, [])


class GetSchemaTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.idx = SchemaIndex()

    def test_get_trade_schema(self) -> None:
        schema = self.idx.get_schema("Trade")
        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertEqual(schema["title"], "Trade")
        self.assertIn("properties", schema)
        self.assertIn("tradeDate", schema["properties"])

    def test_get_schema_by_ref(self) -> None:
        schema = self.idx.get_schema_by_ref(
            "cdm-base-staticdata-party-Party.schema.json"
        )
        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertEqual(schema["title"], "Party")

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.idx.get_schema("FakeType999"))


if __name__ == "__main__":
    unittest.main()
