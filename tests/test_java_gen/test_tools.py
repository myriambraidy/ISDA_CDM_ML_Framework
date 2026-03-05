"""Tests for agent tools."""

import json
import unittest
from pathlib import Path

from fpml_cdm.java_gen.tools import (
    inspect_cdm_json,
    lookup_cdm_schema,
    resolve_java_type,
    list_enum_values,
    get_java_template,
    write_java_file,
    read_java_file,
    patch_java_file,
    compile_java,
    run_java,
    diff_json,
    validate_output,
    finish,
    _parse_javac_errors,
    GENERATED_DIR,
    JAR_PATH,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CDM_FIXTURE = FIXTURES / "expected" / "fx_forward_cdm.json"


# ── inspect_cdm_json ─────────────────────────────────────────────────

class InspectCdmJsonTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = inspect_cdm_json(str(CDM_FIXTURE))

    def test_root_type(self) -> None:
        self.assertEqual(self.result["root_type"], "Trade")

    def test_total_nodes_positive(self) -> None:
        self.assertGreater(self.result["total_nodes"], 10)

    def test_tree_is_list(self) -> None:
        self.assertIsInstance(self.result["tree"], list)

    def test_tree_entries_have_json_path(self) -> None:
        for entry in self.result["tree"]:
            self.assertIn("json_path", entry)

    def test_finds_trade_date(self) -> None:
        paths = [e["json_path"] for e in self.result["tree"]]
        self.assertIn("$.trade.tradeDate", paths)

    def test_finds_party_array(self) -> None:
        party_node = next(
            (e for e in self.result["tree"] if e["json_path"] == "$.trade.party"),
            None,
        )
        self.assertIsNotNone(party_node)
        assert party_node is not None
        self.assertTrue(party_node["is_array"])

    def test_type_summary_has_trade(self) -> None:
        self.assertIn("Trade", self.result["type_summary"])

    def test_identifies_field_with_meta_string(self) -> None:
        types = self.result["type_summary"]
        self.assertIn("FieldWithMetaString", types)

    def test_identifies_enums(self) -> None:
        enum_types = {e["enum_type"] for e in self.result["enums_used"]}
        self.assertIn("CounterpartyRoleEnum", enum_types)

    def test_enum_value_captured(self) -> None:
        party1_enums = [
            e for e in self.result["enums_used"]
            if e["value"] == "Party1"
        ]
        self.assertGreater(len(party1_enums), 0)

    def test_leaf_nodes_have_value(self) -> None:
        leaves = [e for e in self.result["tree"] if e.get("is_leaf")]
        self.assertGreater(len(leaves), 0)
        for leaf in leaves:
            self.assertIn("value", leaf)


# ── lookup_cdm_schema ────────────────────────────────────────────────

class LookupCdmSchemaTests(unittest.TestCase):

    def test_trade_basic(self) -> None:
        result = lookup_cdm_schema("Trade")
        self.assertEqual(result["type_name"], "Trade")
        self.assertEqual(result["java_class"], "cdm.event.common.Trade")
        self.assertIn("tradeDate", result["properties"])

    def test_trade_party_is_array(self) -> None:
        result = lookup_cdm_schema("Trade")
        self.assertTrue(result["properties"]["party"]["is_array"])
        self.assertEqual(result["properties"]["party"]["setter_hint"], "addParty")

    def test_trade_date_setter(self) -> None:
        result = lookup_cdm_schema("Trade")
        self.assertEqual(
            result["properties"]["tradeDate"]["setter_hint"], "setTradeDate"
        )

    def test_settlement_payout_required(self) -> None:
        result = lookup_cdm_schema("SettlementPayout")
        self.assertIn("underlier", result["required_fields"])

    def test_settlement_payout_ref_resolved(self) -> None:
        result = lookup_cdm_schema("SettlementPayout")
        underlier = result["properties"]["underlier"]
        self.assertIsNotNone(underlier["ref"])
        self.assertIsNotNone(underlier["java_class"])

    def test_unknown_type_returns_error(self) -> None:
        result = lookup_cdm_schema("FakeType999")
        self.assertIn("error", result)

    def test_has_java_package(self) -> None:
        result = lookup_cdm_schema("Party")
        self.assertEqual(result["java_package"], "cdm.base.staticdata.party")

    def test_has_description(self) -> None:
        result = lookup_cdm_schema("Trade")
        self.assertIsInstance(result["description"], str)
        self.assertGreater(len(result["description"]), 0)


# ── resolve_java_type ────────────────────────────────────────────────

class ResolveJavaTypeTests(unittest.TestCase):

    def test_trade(self) -> None:
        result = resolve_java_type("cdm-event-common-Trade.schema.json")
        self.assertEqual(result["java_class"], "cdm.event.common.Trade")
        self.assertEqual(result["simple_name"], "Trade")
        self.assertEqual(result["builder_entry"], "Trade.builder()")
        self.assertEqual(result["import_statement"], "import cdm.event.common.Trade;")

    def test_field_with_meta_string(self) -> None:
        result = resolve_java_type(
            "com-rosetta-model-metafields-FieldWithMetaString.schema.json"
        )
        self.assertEqual(
            result["java_class"],
            "com.rosetta.model.metafields.FieldWithMetaString",
        )
        self.assertEqual(result["simple_name"], "FieldWithMetaString")
        self.assertEqual(result["builder_entry"], "FieldWithMetaString.builder()")

    def test_party(self) -> None:
        result = resolve_java_type(
            "cdm-base-staticdata-party-Party.schema.json"
        )
        self.assertEqual(result["java_package"], "cdm.base.staticdata.party")

    def test_unknown_ref_returns_error(self) -> None:
        result = resolve_java_type("nonexistent.schema.json")
        self.assertIn("error", result)

    def test_builder_class(self) -> None:
        result = resolve_java_type("cdm-event-common-Trade.schema.json")
        self.assertEqual(result["builder_class"], "Trade.TradeBuilder")


# ── list_enum_values ─────────────────────────────────────────────────

class ListEnumValuesTests(unittest.TestCase):

    def test_counterparty_role(self) -> None:
        result = list_enum_values("CounterpartyRoleEnum")
        self.assertEqual(result["enum_name"], "CounterpartyRoleEnum")
        json_vals = {v["json_value"] for v in result["values"]}
        self.assertEqual(json_vals, {"Party1", "Party2"})

    def test_java_constants(self) -> None:
        result = list_enum_values("CounterpartyRoleEnum")
        java_consts = {v["java_constant"] for v in result["values"]}
        self.assertIn("CounterpartyRoleEnum.PARTY_1", java_consts)

    def test_has_import(self) -> None:
        result = list_enum_values("CounterpartyRoleEnum")
        self.assertIn("import", result["import_statement"])

    def test_unknown_enum_returns_error(self) -> None:
        result = list_enum_values("FakeEnum999")
        self.assertIn("error", result)

    def test_non_enum_type_returns_error(self) -> None:
        result = list_enum_values("Trade")
        self.assertIn("error", result)

    def test_payer_receiver_enum(self) -> None:
        result = list_enum_values("PayerReceiverEnum")
        json_vals = {v["json_value"] for v in result["values"]}
        self.assertIn("Payer", json_vals)
        self.assertIn("Receiver", json_vals)


# ── get_java_template ────────────────────────────────────────────────

class GetJavaTemplateTests(unittest.TestCase):

    def test_returns_template(self) -> None:
        result = get_java_template()
        self.assertIn("template", result)
        self.assertIsInstance(result["template"], str)

    def test_has_class_declaration(self) -> None:
        result = get_java_template()
        self.assertIn("public class CdmTradeBuilder", result["template"])

    def test_has_main_method(self) -> None:
        result = get_java_template()
        self.assertIn("public static void main", result["template"])

    def test_has_imports_placeholder(self) -> None:
        result = get_java_template()
        self.assertIn("IMPORTS_PLACEHOLDER", result["template"])

    def test_has_builder_placeholder(self) -> None:
        result = get_java_template()
        self.assertIn("BUILDER_CODE_PLACEHOLDER", result["template"])

    def test_has_rosetta_object_mapper(self) -> None:
        result = get_java_template()
        self.assertIn("RosettaObjectMapper", result["template"])

    def test_class_name(self) -> None:
        result = get_java_template()
        self.assertEqual(result["class_name"], "CdmTradeBuilder")

    def test_placeholders_list(self) -> None:
        result = get_java_template()
        self.assertEqual(len(result["placeholders"]), 2)


# ── write_java_file ──────────────────────────────────────────────────

class WriteJavaFileTests(unittest.TestCase):

    def setUp(self) -> None:
        self.test_file = "TestWrite.java"
        cleanup = GENERATED_DIR / self.test_file
        if cleanup.exists():
            cleanup.unlink()

    def tearDown(self) -> None:
        cleanup = GENERATED_DIR / self.test_file
        if cleanup.exists():
            cleanup.unlink()

    def test_writes_file(self) -> None:
        code = "public class TestWrite {}"
        result = write_java_file(code=code, filename=self.test_file)
        self.assertTrue(result["success"])
        self.assertTrue(Path(result["path"]).exists())

    def test_returns_line_count(self) -> None:
        code = "line1\nline2\nline3"
        result = write_java_file(code=code, filename=self.test_file)
        self.assertEqual(result["lines"], 3)

    def test_overwrites_existing(self) -> None:
        write_java_file(code="old", filename=self.test_file)
        write_java_file(code="new", filename=self.test_file)
        content = (GENERATED_DIR / self.test_file).read_text()
        self.assertEqual(content, "new")

    def test_creates_directory(self) -> None:
        result = write_java_file(code="x", filename=self.test_file)
        self.assertTrue(GENERATED_DIR.is_dir())


# ── read_java_file ───────────────────────────────────────────────────

class ReadJavaFileTests(unittest.TestCase):

    def setUp(self) -> None:
        self.test_file = "TestRead.java"
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        (GENERATED_DIR / self.test_file).write_text(
            "public class TestRead {}", encoding="utf-8"
        )

    def tearDown(self) -> None:
        cleanup = GENERATED_DIR / self.test_file
        if cleanup.exists():
            cleanup.unlink()

    def test_reads_content(self) -> None:
        result = read_java_file(filename=self.test_file)
        self.assertEqual(result["content"], "public class TestRead {}")

    def test_returns_line_count(self) -> None:
        result = read_java_file(filename=self.test_file)
        self.assertEqual(result["lines"], 1)

    def test_missing_file_returns_error(self) -> None:
        result = read_java_file(filename="DoesNotExist.java")
        self.assertIn("error", result)


# ── patch_java_file ──────────────────────────────────────────────────

class PatchJavaFileTests(unittest.TestCase):

    def setUp(self) -> None:
        self.test_file = "TestPatch.java"
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        (GENERATED_DIR / self.test_file).write_text(
            "import foo;\npublic class TestPatch {}", encoding="utf-8"
        )

    def tearDown(self) -> None:
        cleanup = GENERATED_DIR / self.test_file
        if cleanup.exists():
            cleanup.unlink()

    def test_replaces_text(self) -> None:
        result = patch_java_file(
            old_text="import foo;",
            new_text="import bar;",
            filename=self.test_file,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["replacements_made"], 1)
        content = (GENERATED_DIR / self.test_file).read_text()
        self.assertIn("import bar;", content)
        self.assertNotIn("import foo;", content)

    def test_old_text_not_found_returns_warning(self) -> None:
        result = patch_java_file(
            old_text="does not exist",
            new_text="replacement",
            filename=self.test_file,
        )
        self.assertFalse(result["success"])
        self.assertIn("warnings", result)

    def test_missing_file_returns_error(self) -> None:
        result = patch_java_file(
            old_text="x",
            new_text="y",
            filename="DoesNotExist.java",
        )
        self.assertIn("error", result)

    def test_multiple_replacements(self) -> None:
        (GENERATED_DIR / self.test_file).write_text(
            "a = 1;\na = 2;\na = 3;", encoding="utf-8"
        )
        result = patch_java_file(
            old_text="a =",
            new_text="b =",
            filename=self.test_file,
        )
        self.assertEqual(result["replacements_made"], 3)


# ── compile_java ─────────────────────────────────────────────────────

HAS_JAR = JAR_PATH.exists()


@unittest.skipUnless(HAS_JAR, "rosetta-validator JAR not built")
class CompileJavaTests(unittest.TestCase):

    def setUp(self) -> None:
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for f in ("TestCompile.java", "TestCompile.class", "TestFail.java"):
            p = GENERATED_DIR / f
            if p.exists():
                p.unlink()

    def test_valid_java_compiles(self) -> None:
        code = "public class TestCompile { public static void main(String[] a) {} }"
        write_java_file(code=code, filename="TestCompile.java")
        result = compile_java(filename="TestCompile.java")
        self.assertTrue(result["success"])
        self.assertIn("class_file", result)

    def test_invalid_java_returns_errors(self) -> None:
        code = "public class TestFail { void m() { UnknownClass x; } }"
        write_java_file(code=code, filename="TestFail.java")
        result = compile_java(filename="TestFail.java")
        self.assertFalse(result["success"])
        self.assertGreater(result["error_count"], 0)
        self.assertIn("line", result["errors"][0])
        self.assertIn("message", result["errors"][0])

    def test_missing_source_returns_error(self) -> None:
        result = compile_java(filename="DoesNotExist.java")
        self.assertFalse(result["success"])


class CompileJavaNoJarTests(unittest.TestCase):

    def test_missing_jar_returns_error(self) -> None:
        if HAS_JAR:
            self.skipTest("JAR exists, cannot test missing-JAR path")
        result = compile_java(filename="X.java")
        self.assertFalse(result["success"])


# ── run_java ─────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_JAR, "rosetta-validator JAR not built")
class RunJavaTests(unittest.TestCase):

    def setUp(self) -> None:
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for f in ("RunTest.java", "RunTest.class"):
            p = GENERATED_DIR / f
            if p.exists():
                p.unlink()

    def test_run_simple_class(self) -> None:
        code = (
            'public class RunTest {\n'
            '    public static void main(String[] args) {\n'
            '        System.out.println("{\\"ok\\":true}");\n'
            '    }\n'
            '}\n'
        )
        write_java_file(code=code, filename="RunTest.java")
        compile_java(filename="RunTest.java")
        result = run_java(class_name="RunTest")
        self.assertTrue(result["success"])
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["stdout_is_valid_json"])

    def test_run_missing_class_fails(self) -> None:
        result = run_java(class_name="ClassThatDoesNotExist")
        self.assertFalse(result["success"])


# ── diff_json ────────────────────────────────────────────────────────

class DiffJsonTests(unittest.TestCase):

    def test_identical_match(self) -> None:
        data = json.loads(CDM_FIXTURE.read_text())
        result = diff_json(str(CDM_FIXTURE), json.dumps(data))
        self.assertTrue(result["match"])
        self.assertEqual(result["match_percentage"], 100.0)
        self.assertEqual(len(result["differences"]), 0)

    def test_missing_field(self) -> None:
        data = json.loads(CDM_FIXTURE.read_text())
        del data["trade"]["tradeDate"]
        result = diff_json(str(CDM_FIXTURE), json.dumps(data))
        self.assertFalse(result["match"])
        missing = [d for d in result["differences"] if d["type"] == "missing_in_actual"]
        self.assertGreater(len(missing), 0)

    def test_value_mismatch(self) -> None:
        data = json.loads(CDM_FIXTURE.read_text())
        data["trade"]["tradeDate"]["value"] = "1999-01-01"
        result = diff_json(str(CDM_FIXTURE), json.dumps(data))
        self.assertFalse(result["match"])
        mismatches = [d for d in result["differences"] if d["type"] == "value_mismatch"]
        self.assertGreater(len(mismatches), 0)

    def test_type_mismatch(self) -> None:
        data = json.loads(CDM_FIXTURE.read_text())
        # Change a number to a string
        data["trade"]["tradeLot"][0]["priceQuantity"][0]["price"][0]["value"]["value"] = "1.28"
        result = diff_json(str(CDM_FIXTURE), json.dumps(data))
        self.assertFalse(result["match"])
        type_mismatches = [d for d in result["differences"] if d["type"] == "type_mismatch"]
        self.assertGreater(len(type_mismatches), 0)

    def test_extra_in_actual(self) -> None:
        data = json.loads(CDM_FIXTURE.read_text())
        data["trade"]["meta"] = {"globalKey": "abc123"}
        result = diff_json(str(CDM_FIXTURE), json.dumps(data))
        self.assertTrue(result["match"])  # extra fields don't count as mismatch
        self.assertIn("$.trade.meta", result["extra_in_actual"])

    def test_float_tolerance(self) -> None:
        import tempfile
        expected = '{"a": 1.28}'
        actual = '{"a": 1.2800000000001}'
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(expected)
            tmp_path = f.name
        try:
            result = diff_json(tmp_path, actual)
            self.assertTrue(result["match"])
        finally:
            Path(tmp_path).unlink()

    def test_int_float_equivalence(self) -> None:
        expected = '{"a": 5}'
        actual = '{"a": 5.0}'
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(expected)
            tmp_path = f.name
        try:
            result = diff_json(tmp_path, actual)
            self.assertTrue(result["match"])
        finally:
            Path(tmp_path).unlink()

    def test_match_percentage(self) -> None:
        data = json.loads(CDM_FIXTURE.read_text())
        result = diff_json(str(CDM_FIXTURE), json.dumps(data))
        self.assertEqual(result["matched_leaf_values"], result["total_leaf_values"])


# ── validate_output ──────────────────────────────────────────────────

class ValidateOutputTests(unittest.TestCase):

    def test_valid_fixture(self) -> None:
        data = CDM_FIXTURE.read_text()
        result = validate_output(data)
        self.assertIn("valid", result)
        self.assertIsInstance(result["errors"], list)
        self.assertIsInstance(result["error_count"], int)

    def test_empty_trade_has_errors(self) -> None:
        result = validate_output('{"trade": {}}')
        # Empty trade missing required tradeDate
        self.assertGreater(result["error_count"], 0)


# ── finish ───────────────────────────────────────────────────────────

class FinishTests(unittest.TestCase):

    def test_success(self) -> None:
        result = finish(status="success", summary="All good")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary"], "All good")

    def test_failure(self) -> None:
        result = finish(status="failure", summary="Compile errors", match_percentage=45.0)
        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["match_percentage"], 45.0)

    def test_with_java_file(self) -> None:
        result = finish(status="success", summary="Done", java_file="generated/CdmTradeBuilder.java")
        self.assertEqual(result["java_file"], "generated/CdmTradeBuilder.java")


# ── inspect_cdm_json: type_registry & java_type_warnings ─────────────

class InspectTypeRegistryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = inspect_cdm_json(str(CDM_FIXTURE))

    def test_type_registry_exists(self) -> None:
        self.assertIn("type_registry", self.result)
        self.assertIsInstance(self.result["type_registry"], dict)

    def test_registry_has_trade(self) -> None:
        reg = self.result["type_registry"]
        trade_entry = reg.get("cdm-event-common-Trade.schema.json")
        self.assertIsNotNone(trade_entry)
        self.assertEqual(trade_entry["java_class"], "cdm.event.common.Trade")
        self.assertEqual(trade_entry["simple_name"], "Trade")
        self.assertEqual(trade_entry["builder_entry"], "Trade.builder()")
        self.assertIn("import cdm.event.common.Trade;", trade_entry["import_statement"])

    def test_registry_has_import_for_all(self) -> None:
        reg = self.result["type_registry"]
        for ref, entry in reg.items():
            self.assertIn("import_statement", entry, f"Missing import for {ref}")
            self.assertIn("builder_entry", entry, f"Missing builder for {ref}")

    def test_registry_identifies_enums(self) -> None:
        reg = self.result["type_registry"]
        enum_entries = {k: v for k, v in reg.items() if v.get("is_enum")}
        self.assertGreater(len(enum_entries), 0)

    def test_java_type_warnings_exists(self) -> None:
        self.assertIn("java_type_warnings", self.result)
        self.assertIsInstance(self.result["java_type_warnings"], list)

    def test_trade_date_warning(self) -> None:
        warnings = self.result["java_type_warnings"]
        date_warnings = [w for w in warnings if w["property"] == "tradeDate"]
        self.assertGreater(len(date_warnings), 0, "Should warn about tradeDate type mismatch")
        w = date_warnings[0]
        self.assertIn("FieldWithMetaDate", w["actual_java_class"])
        self.assertIn("Date.of", w["java_usage"])


# ── patch_java_file: no-op guard & batch mode ────────────────────────

class PatchNoOpGuardTests(unittest.TestCase):

    def setUp(self) -> None:
        self.test_file = "TestPatchNoop.java"
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        (GENERATED_DIR / self.test_file).write_text(
            "import foo;\npublic class TestPatchNoop {}", encoding="utf-8"
        )

    def tearDown(self) -> None:
        cleanup = GENERATED_DIR / self.test_file
        if cleanup.exists():
            cleanup.unlink()

    def test_noop_patch_returns_warning(self) -> None:
        result = patch_java_file(
            old_text="import foo;",
            new_text="import foo;",
            filename=self.test_file,
        )
        self.assertFalse(result["success"])
        self.assertIn("warnings", result)
        self.assertTrue(any("No-op" in w for w in result["warnings"]))


class PatchBatchModeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.test_file = "TestPatchBatch.java"
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        (GENERATED_DIR / self.test_file).write_text(
            "import a;\nimport b;\nimport c;\npublic class TestPatchBatch {}",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        cleanup = GENERATED_DIR / self.test_file
        if cleanup.exists():
            cleanup.unlink()

    def test_batch_patches(self) -> None:
        result = patch_java_file(
            filename=self.test_file,
            patches=[
                {"old_text": "import a;", "new_text": "import x;"},
                {"old_text": "import b;", "new_text": "import y;"},
            ],
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["replacements_made"], 2)
        content = (GENERATED_DIR / self.test_file).read_text()
        self.assertIn("import x;", content)
        self.assertIn("import y;", content)
        self.assertIn("import c;", content)

    def test_batch_with_missing_old_text(self) -> None:
        result = patch_java_file(
            filename=self.test_file,
            patches=[
                {"old_text": "import a;", "new_text": "import x;"},
                {"old_text": "NOT_FOUND", "new_text": "replacement"},
            ],
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["replacements_made"], 1)
        self.assertIn("warnings", result)

    def test_batch_all_noop_returns_failure(self) -> None:
        result = patch_java_file(
            filename=self.test_file,
            patches=[
                {"old_text": "import a;", "new_text": "import a;"},
            ],
        )
        self.assertFalse(result["success"])


# ── compile_java: error hints ────────────────────────────────────────

class CompileErrorHintTests(unittest.TestCase):

    def test_type_mismatch_hint(self) -> None:
        stderr = (
            "Foo.java:29: error: incompatible types: FieldWithMetaString "
            "cannot be converted to FieldWithMetaDate\n"
            "            .setTradeDate(FieldWithMetaString.builder())\n"
            "                         ^\n"
        )
        errors = _parse_javac_errors(stderr, Path("Foo.java"))
        self.assertEqual(len(errors), 1)
        self.assertIn("hint", errors[0])
        self.assertEqual(errors[0]["expected_type"], "FieldWithMetaDate")
        self.assertEqual(errors[0]["actual_type"], "FieldWithMetaString")

    def test_missing_symbol_hint(self) -> None:
        stderr = (
            "Foo.java:30: error: cannot find symbol\n"
            "            .addTradeIdentifier(TradeIdentifier.builder()\n"
            "                                ^\n"
            "  symbol:   variable TradeIdentifier\n"
            "  location: class CdmTradeBuilder\n"
        )
        errors = _parse_javac_errors(stderr, Path("Foo.java"))
        self.assertEqual(len(errors), 1)
        self.assertIn("hint", errors[0])
        self.assertEqual(errors[0]["missing_symbol"], "TradeIdentifier")


# ── SYSTEM_PROMPT checks ─────────────────────────────────────────────

class SystemPromptTests(unittest.TestCase):

    def test_mentions_date_types(self) -> None:
        from fpml_cdm.java_gen.agent import SYSTEM_PROMPT
        self.assertIn("FieldWithMetaDate", SYSTEM_PROMPT)
        self.assertIn("Date.of", SYSTEM_PROMPT)

    def test_mentions_batch_patches(self) -> None:
        from fpml_cdm.java_gen.agent import SYSTEM_PROMPT
        self.assertIn("patches", SYSTEM_PROMPT)

    def test_mentions_type_registry(self) -> None:
        from fpml_cdm.java_gen.agent import SYSTEM_PROMPT
        self.assertIn("type_registry", SYSTEM_PROMPT)

    def test_no_one_at_a_time_rule(self) -> None:
        from fpml_cdm.java_gen.agent import SYSTEM_PROMPT
        self.assertNotIn("ONE AT A TIME", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
