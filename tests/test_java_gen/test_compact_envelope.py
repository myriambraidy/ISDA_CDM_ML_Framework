"""prepare_tool_result_for_llm: inspect envelope + tree split and byte cap."""

import json
import os
import unittest
from unittest.mock import patch

from fpml_cdm.java_gen.agent import prepare_tool_result_for_llm
from fpml_cdm.java_gen.tools import compact_context, reset_payload_store


def _reset_store() -> None:
    reset_payload_store()


class InspectEnvelopeSplitTests(unittest.TestCase):

    def setUp(self) -> None:
        _reset_store()

    @patch.dict(os.environ, {"FPML_JAVA_GEN_MAX_TOOL_CHARS": "8000"}, clear=False)
    def test_tree_externalized_envelope_inline(self) -> None:
        tree = [
            {"json_path": f"$.trade.n[{i}]", "cdm_type": "N", "is_leaf": True, "value": i}
            for i in range(4000)
        ]
        inspect: dict = {
            "root_type": "Trade",
            "total_nodes": len(tree),
            "tree": tree,
            "type_summary": {"Trade": 1, "N": 4000},
            "type_registry": {"cdm-event-common-Trade.schema.json": {"simple_name": "Trade"}},
            "reference_pattern_total": 0,
            "reference_patterns_sample": [],
            "location_array_warnings": [],
            "java_type_warnings": [],
            "enums_used": [],
            "well_known_imports": {},
            "well_known_imports_note": "n",
        }
        raw = json.dumps(inspect, ensure_ascii=False)
        self.assertGreater(len(raw), 8000)
        out, meta = prepare_tool_result_for_llm("inspect_cdm_json", raw)
        self.assertTrue(meta.get("tree_split"))
        self.assertFalse(meta.get("oversize_full"))
        d = json.loads(out)
        self.assertNotIn("tree", d)
        self.assertEqual(d.get("type_summary"), {"Trade": 1, "N": 4000})
        self.assertTrue(d.get("tree_stored"))
        self.assertEqual(d.get("storage_mode"), "inspect_tree_only")
        th = d.get("tree_handle")
        self.assertIsInstance(th, str)
        fr = compact_context(str(th), offset=0, limit=2_000_000)
        self.assertTrue(fr.get("success"))
        recovered = json.loads(str(fr["chunk"]))
        self.assertEqual(len(recovered), len(tree))
        self.assertEqual(recovered[0]["json_path"], tree[0]["json_path"])

    @patch.dict(
        os.environ,
        {"FPML_JAVA_GEN_MAX_TOOL_BYTES": "9000", "FPML_JAVA_GEN_MAX_TOOL_CHARS": "999999"},
        clear=False,
    )
    def test_byte_cap_triggers_split(self) -> None:
        _reset_store()
        tree = [{"i": n} for n in range(3000)]
        inspect: dict = {
            "root_type": "Trade",
            "total_nodes": len(tree),
            "tree": tree,
            "type_summary": {"Trade": 1},
            "type_registry": {},
            "reference_pattern_total": 0,
            "reference_patterns_sample": [],
            "location_array_warnings": [],
            "java_type_warnings": [],
            "enums_used": [],
            "well_known_imports": {},
            "well_known_imports_note": "n",
        }
        raw = json.dumps(inspect, ensure_ascii=False)
        self.assertGreater(len(raw.encode("utf-8")), 9000)
        out, meta = prepare_tool_result_for_llm("inspect_cdm_json", raw)
        self.assertTrue(meta.get("tree_split"))
        d = json.loads(out)
        self.assertNotIn("tree", d)
        th = d.get("tree_handle")
        assert isinstance(th, str)
        fr = compact_context(th, 0, 5_000_000)
        self.assertTrue(fr.get("success"))
        self.assertEqual(len(json.loads(fr["chunk"])), len(tree))

    @patch.dict(os.environ, {"FPML_JAVA_GEN_MAX_TOOL_CHARS": "200"}, clear=False)
    def test_envelope_still_too_large_falls_back_full_stub(self) -> None:
        _reset_store()
        tree = [{"a": 1}]
        inspect = {
            "huge_pad": "Z" * 5000,
            "tree": tree,
            "type_summary": {"Trade": 1},
            "type_registry": {},
            "reference_pattern_total": 0,
            "reference_patterns_sample": [],
            "location_array_warnings": [],
            "java_type_warnings": [],
            "enums_used": [],
            "well_known_imports": {},
            "well_known_imports_note": "n",
            "root_type": "Trade",
            "total_nodes": 1,
        }
        raw = json.dumps(inspect, ensure_ascii=False)
        out, meta = prepare_tool_result_for_llm("inspect_cdm_json", raw)
        self.assertTrue(meta.get("oversize_full"))
        self.assertFalse(meta.get("tree_split"))
        d = json.loads(out)
        self.assertEqual(d.get("tool"), "inspect_cdm_json")
        self.assertTrue(d.get("stored"))
        self.assertNotIn("tree_handle", d)


class PresendDeprioritizeTests(unittest.TestCase):

    def setUp(self) -> None:
        _reset_store()

    @patch.dict(
        os.environ,
        {
            "FPML_JAVA_GEN_MAX_PROMPT_CHARS": "12000",
            "FPML_JAVA_GEN_PROMPT_HEADROOM_CHARS": "0",
            "FPML_JAVA_GEN_PRESEND_PROTECT_LAST_TOOLS": "0",
        },
        clear=False,
    )
    def test_deprioritized_read_java_stubbed_before_other_large_tool(self) -> None:
        from fpml_cdm.java_gen.agent import _presend_compact_messages, _message_list_utf8_bytes

        read_body = json.dumps(
            {
                "path": "/x.java",
                "content": "Z" * 25_000,
                "lines": 100,
            }
        )
        other = json.dumps({"data": "Y" * 8000})
        messages = [
            {"role": "system", "content": "s"},
            {"role": "tool", "content": read_body},
            {"role": "tool", "content": other},
        ]
        self.assertGreater(_message_list_utf8_bytes(messages), 12000)
        _presend_compact_messages(messages)
        d_read = json.loads(str(messages[1]["content"]))
        d_other = json.loads(str(messages[2]["content"]))
        self.assertTrue(d_read.get("context_stub"))
        self.assertFalse(d_other.get("context_stub"))
        self.assertLessEqual(_message_list_utf8_bytes(messages), 12000)
