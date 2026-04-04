"""compact_context + pre-send compaction behavior."""

import json
import os
import unittest
from unittest.mock import patch

from fpml_cdm.java_gen import agent as agent_module
from fpml_cdm.java_gen.agent import _presend_compact_messages
from fpml_cdm.java_gen.tools import compact_context, reset_payload_store, store_large_payload
from tests.test_java_gen.test_tools import CDM_FIXTURE


def reset_payload_store_for_tests() -> None:
    reset_payload_store()


class CompactContextRoundtripTests(unittest.TestCase):

    def setUp(self) -> None:
        reset_payload_store_for_tests()

    def test_roundtrip_via_compact_until_done(self) -> None:
        blob = json.dumps({"x": "y" * 5000}, ensure_ascii=False)
        st = store_large_payload(kind="test:roundtrip", payload_json=blob)
        self.assertTrue(st.get("success"))
        handle = str(st.get("handle"))
        parts: list[str] = []
        offset = 0
        limit = 4000
        last = {}
        for _ in range(50):
            fr = compact_context(handle, offset=offset, limit=limit)
            self.assertTrue(fr.get("success"))
            parts.append(str(fr["chunk"]))
            last = fr
            if fr.get("done"):
                break
            offset += limit
        recovered = "".join(parts)
        self.assertEqual(recovered, blob)
        self.assertIn("provenance", last)


class PresendBudgetTests(unittest.TestCase):

    def setUp(self) -> None:
        reset_payload_store_for_tests()

    @patch.dict(
        os.environ,
        {
            "FPML_JAVA_GEN_MAX_PROMPT_CHARS": "8000",
            "FPML_JAVA_GEN_PROMPT_HEADROOM_CHARS": "0",
        },
        clear=False,
    )
    def test_presend_keeps_under_budget(self) -> None:
        huge = "Z" * 50_000
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "ok"},
            {"role": "tool", "content": json.dumps({"raw": huge})},
        ]
        before = agent_module._message_list_utf8_bytes(messages)
        self.assertGreater(before, 8000)
        _presend_compact_messages(messages)
        after = agent_module._message_list_utf8_bytes(messages)
        self.assertLessEqual(after, 8000)
        tool_c = messages[3].get("content")
        self.assertIsInstance(tool_c, str)
        d = json.loads(str(tool_c))
        self.assertTrue(d.get("context_stub"))
        self.assertIn("handle", d)


class InspectDefaultTreeTests(unittest.TestCase):
    """Regression: default inspect includes lossless tree."""

    def test_tree_present_default(self) -> None:
        from fpml_cdm.java_gen.tools import inspect_cdm_json

        r = inspect_cdm_json(str(CDM_FIXTURE))
        self.assertIn("tree", r)
        self.assertIsInstance(r["tree"], list)
