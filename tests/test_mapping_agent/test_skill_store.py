"""Tests for mapping_agent.skill_store."""

import unittest
from pathlib import Path

from fpml_cdm.mapping_agent.skill_store import (
    _parse_yaml_frontmatter,
    load_skill_catalog,
    get_skill_by_id,
    catalog_summary,
)


class TestYamlFrontmatter(unittest.TestCase):
    def test_simple_frontmatter(self):
        text = '---\nname: test\ndescription: A test skill\nversion: "1.0"\n---\n# Body here'
        meta, body = _parse_yaml_frontmatter(text)
        self.assertEqual(meta["name"], "test")
        self.assertEqual(meta["description"], "A test skill")
        self.assertEqual(meta["version"], "1.0")
        self.assertEqual(body, "# Body here")

    def test_list_field(self):
        text = '---\nadapter_ids: ["fxForward", "fxSingleLeg"]\n---\nbody'
        meta, body = _parse_yaml_frontmatter(text)
        self.assertEqual(meta["adapter_ids"], ["fxForward", "fxSingleLeg"])

    def test_no_frontmatter(self):
        text = "# Just markdown"
        meta, body = _parse_yaml_frontmatter(text)
        self.assertEqual(meta, {})
        self.assertEqual(body, text)


class TestSkillCatalog(unittest.TestCase):
    def test_load_default_catalog(self):
        catalog = load_skill_catalog()
        self.assertGreater(len(catalog), 0)
        names = {s.skill_id for s in catalog}
        self.assertIn("fx-forward-like", names)
        self.assertIn("fx-swap", names)
        self.assertIn("fx-option", names)

    def test_skill_metadata(self):
        catalog = load_skill_catalog()
        fwd = next(s for s in catalog if s.skill_id == "fx-forward-like")
        self.assertEqual(fwd.name, "fx-forward-like")
        self.assertIn("fxForward", fwd.adapter_ids)
        self.assertIn("fxSingleLeg", fwd.adapter_ids)
        self.assertTrue(fwd.body.startswith("# FX Forward-Like"))

    def test_get_skill_by_id(self):
        skill = get_skill_by_id("fx-swap")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.skill_id, "fx-swap")
        self.assertIn("fxSwap", skill.adapter_ids)

    def test_get_nonexistent_skill(self):
        self.assertIsNone(get_skill_by_id("nonexistent-skill"))

    def test_catalog_summary(self):
        catalog = load_skill_catalog()
        summary = catalog_summary(catalog)
        self.assertIn("fx-forward-like", summary)
        self.assertIn("fx-swap", summary)

    def test_empty_catalog_summary(self):
        summary = catalog_summary([])
        self.assertIn("No mapping skills", summary)

    def test_load_from_nonexistent_dir(self):
        catalog = load_skill_catalog("/nonexistent/path")
        self.assertEqual(catalog, [])


if __name__ == "__main__":
    unittest.main()
