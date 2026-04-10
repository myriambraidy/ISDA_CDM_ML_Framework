"""Pre-loop classifier: FpML path -> skill_id + confidence."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .skill_store import SkillMeta


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


@dataclass
class ClassifierResult:
    skill_id: Optional[str]
    confidence: float
    adapter_id: Optional[str]
    product_local_names: List[str]
    reason: str

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "confidence": self.confidence,
            "adapter_id": self.adapter_id,
            "product_local_names": self.product_local_names,
            "reason": self.reason,
        }


def classify_fpml(fpml_path: str, catalog: List[SkillMeta]) -> ClassifierResult:
    """Rules-based classifier: XML local names under <trade> -> best skill."""
    path = Path(fpml_path)
    if not path.is_file():
        return ClassifierResult(
            skill_id=None, confidence=0.0, adapter_id=None,
            product_local_names=[], reason=f"File not found: {fpml_path}",
        )

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return ClassifierResult(
            skill_id=None, confidence=0.0, adapter_id=None,
            product_local_names=[], reason=f"XML parse error: {exc}",
        )

    trade = None
    for elem in root.iter():
        if _local_name(elem.tag) == "trade":
            trade = elem
            break

    if trade is None:
        return ClassifierResult(
            skill_id=None, confidence=0.0, adapter_id=None,
            product_local_names=[], reason="No <trade> element found",
        )

    product_names: List[str] = []
    for child in list(trade):
        lname = _local_name(child.tag)
        if lname != "tradeHeader":
            product_names.append(lname)

    if not product_names:
        return ClassifierResult(
            skill_id=None, confidence=0.0, adapter_id=None,
            product_local_names=product_names, reason="No product children under <trade>",
        )

    adapter_to_skill = {}
    for skill in catalog:
        for aid in skill.adapter_ids:
            adapter_to_skill[aid] = skill

    for pname in product_names:
        if pname in adapter_to_skill:
            skill = adapter_to_skill[pname]
            return ClassifierResult(
                skill_id=skill.skill_id,
                confidence=1.0,
                adapter_id=pname,
                product_local_names=product_names,
                reason=f"Matched product '{pname}' to skill '{skill.skill_id}'",
            )

    return ClassifierResult(
        skill_id=None, confidence=0.0, adapter_id=None,
        product_local_names=product_names,
        reason=f"No skill matched products: {product_names}",
    )
