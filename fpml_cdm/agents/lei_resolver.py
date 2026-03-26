"""
LEI resolution — deterministic lookup (local table + optional GLEIF HTTP).

Use as a drop-in "skill" instead of LLM for party identifier enrichment.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

# Typical BIC / BEI pattern (8 or 11 chars, alphanumeric; conservative)
_BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")


class LeiResolver(Protocol):
    def resolve_lei(self, bic_or_name: str) -> Optional[str]:
        """Return 20-char LEI if found, else None."""
        ...


def looks_like_bic(value: str) -> bool:
    v = (value or "").strip().upper()
    return bool(_BIC_RE.match(v))


@dataclass
class LocalBicLeiTable:
    """Load a JSON map ``bic -> lei`` (keys uppercased)."""

    path: Path

    def __post_init__(self) -> None:
        self._map: Dict[str, str] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, str) and len(v) == 20:
                    self._map[k.strip().upper()] = v.strip().upper()

    def resolve_lei(self, bic_or_name: str) -> Optional[str]:
        key = (bic_or_name or "").strip().upper()
        return self._map.get(key)


@dataclass
class ChainedLeiResolver:
    """Try resolvers in order; first non-None wins."""

    resolvers: list[LeiResolver]

    def resolve_lei(self, bic_or_name: str) -> Optional[str]:
        for r in self.resolvers:
            out = r.resolve_lei(bic_or_name)
            if out:
                return out
        return None


@dataclass
class GleifLeiResolver:
    """
    Optional GLEIF public API search by entity name / identifier text.
    Best-effort: not all BICs are indexed as legal names; prefer LocalBicLeiTable.

    Uses stdlib urllib only (no extra deps).
    """

    timeout_sec: float = 15.0
    base_url: str = "https://api.gleif.org/api/v1/lei-records"

    def resolve_lei(self, bic_or_name: str) -> Optional[str]:
        q = (bic_or_name or "").strip()
        if not q:
            return None
        # JSON:API filter — search legal name (GLEIF supports partial match)
        params = urllib.parse.urlencode(
            {
                "page[size]": "1",
                "filter[entity.legalName]": q,
            }
        )
        url = f"{self.base_url}?{params}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.api+json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None

        data = body.get("data") or []
        if not data:
            return None
        first = data[0]
        attrs = first.get("attributes") or {}
        lei = attrs.get("lei")
        if isinstance(lei, str) and len(lei) == 20:
            return lei.upper()
        return None


def default_lei_table_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "lei" / "bic_to_lei.json"


def enrich_parties_with_lei(
    parties: list[Dict[str, Any]],
    resolver: LeiResolver,
    *,
    only_if_looks_like_bic: bool = True,
) -> list[str]:
    """
    Mutates each party dict with optional ``lei`` when resolved.
    Returns list of party ``id``s that received an LEI.
    """
    touched: list[str] = []
    for p in parties:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        if only_if_looks_like_bic and not looks_like_bic(name):
            continue
        if p.get("lei"):
            continue
        lei = resolver.resolve_lei(name)
        if lei:
            p["lei"] = lei
            pid = p.get("id")
            if pid:
                touched.append(pid)
    return touched
