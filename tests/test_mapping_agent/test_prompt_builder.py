"""Tests for mapping_agent.prompt_builder."""

import unittest

from fpml_cdm.mapping_agent.classifier import ClassifierResult
from fpml_cdm.mapping_agent.prompt_builder import build_system_prompt, build_bootstrap_user_message, BASE_SYSTEM_PROMPT
from fpml_cdm.mapping_agent.skill_store import load_skill_catalog, get_skill_by_id


class TestPromptBuilder(unittest.TestCase):
    def test_base_prompt_no_skill(self):
        catalog = load_skill_catalog()
        prompt = build_system_prompt(catalog, skill=None)
        self.assertIn("mapping agent", prompt.lower())
        self.assertIn("fx-forward-like", prompt)
        self.assertNotIn("Active Skill", prompt)

    def test_prompt_with_skill(self):
        catalog = load_skill_catalog()
        skill = get_skill_by_id("fx-forward-like")
        prompt = build_system_prompt(catalog, skill=skill)
        self.assertIn("Active Skill: fx-forward-like", prompt)
        self.assertIn("FX Forward-Like", prompt)

    def test_bootstrap_user_message(self):
        cr = ClassifierResult(
            skill_id="fx-forward-like",
            confidence=1.0,
            adapter_id="fxForward",
            product_local_names=["fxForward"],
            reason="matched",
        )
        msg = build_bootstrap_user_message(
            fpml_path="test.xml",
            classifier_result=cr,
            best_adapter="fxForward",
            problem_statement="schema_errors=2",
            enable_rosetta=False,
            rosetta_timeout_seconds=60,
        )
        self.assertIn("test.xml", msg)
        self.assertIn("fxForward", msg)
        self.assertIn("schema_errors=2", msg)
        self.assertIn("finish", msg)

    def test_base_prompt_contains_finish_instruction(self):
        self.assertIn("finish", BASE_SYSTEM_PROMPT.lower())

    def test_llm_native_prompt_mentions_submit(self):
        catalog = load_skill_catalog()
        prompt = build_system_prompt(catalog, mapping_mode="llm_native")
        self.assertIn("submit_llm_cdm", prompt)


if __name__ == "__main__":
    unittest.main()
