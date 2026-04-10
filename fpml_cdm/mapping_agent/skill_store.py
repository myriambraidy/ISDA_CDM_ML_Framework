"""Skill store: discovers, parses, and serves SKILL.md files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SkillMeta:
    skill_id: str
    name: str
    description: str
    adapter_ids: List[str]
    cdm_target: str
    fpml_profile: str
    version: str
    body: str
    path: str


def _parse_yaml_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    """Minimal YAML frontmatter parser (no PyYAML dependency)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw_front = text[3:end].strip()
    body = text[end + 4:].strip()
    meta: Dict[str, Any] = {}
    for line in raw_front.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]
            meta[key] = items
        else:
            meta[key] = val
    return meta, body


def _default_skills_dir() -> Path:
    return Path(__file__).parent / "skills"


def load_skill_catalog(skills_dir: Optional[str] = None) -> List[SkillMeta]:
    root = Path(skills_dir) if skills_dir else _default_skills_dir()
    if not root.is_dir():
        return []
    skills: List[SkillMeta] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            continue
        text = skill_file.read_text(encoding="utf-8")
        meta, body = _parse_yaml_frontmatter(text)
        adapter_ids_raw = meta.get("adapter_ids", [])
        if isinstance(adapter_ids_raw, str):
            adapter_ids_raw = [adapter_ids_raw]
        skills.append(SkillMeta(
            skill_id=child.name,
            name=meta.get("name", child.name),
            description=meta.get("description", ""),
            adapter_ids=adapter_ids_raw,
            cdm_target=meta.get("cdm_target", ""),
            fpml_profile=meta.get("fpml_profile", ""),
            version=meta.get("version", "0.0.0"),
            body=body,
            path=str(skill_file),
        ))
    return skills


def get_skill_by_id(skill_id: str, skills_dir: Optional[str] = None) -> Optional[SkillMeta]:
    for s in load_skill_catalog(skills_dir):
        if s.skill_id == skill_id:
            return s
    return None


def catalog_summary(catalog: List[SkillMeta]) -> str:
    if not catalog:
        return "No mapping skills available."
    lines = ["Available mapping skills:"]
    for s in catalog:
        lines.append(f"- {s.skill_id}: {s.description} (adapters: {', '.join(s.adapter_ids)})")
    return "\n".join(lines)
