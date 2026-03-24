"""Shared CDM v6 JSON helpers (globalKey, key order, settlement enums)."""

from __future__ import annotations

import json
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Mapping, Optional

CDM_TRADE_KEY_ORDER: List[str] = [
    "product",
    "tradeLot",
    "counterparty",
    "tradeIdentifier",
    "tradeDate",
    "party",
    "partyRole",
    "meta",
]


def reorder_trade_keys(d: Mapping[str, object], key_order: Optional[List[str]] = None) -> Dict[str, object]:
    order = key_order if key_order is not None else CDM_TRADE_KEY_ORDER
    out: Dict[str, object] = {}
    for k in order:
        if k in d:
            out[k] = d[k]
    for k, v in d.items():
        if k not in out:
            out[k] = v
    return out


SETTLEMENT_TYPE_MAP: Dict[str, str] = {
    "PHYSICAL": "Physical",
    "CASH": "Cash",
    "REGULAR": "Physical",
}


def strip_meta(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: strip_meta(v) for k, v in obj.items() if k != "meta"}
    if isinstance(obj, list):
        return [strip_meta(item) for item in obj]
    return obj


def global_key(node: Dict) -> str:
    stripped = strip_meta(node)
    canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    crc = zlib.crc32(canonical.encode("utf-8")) & 0xFFFFFFFF
    return format(crc, "x")


def set_meta(node: Dict, **kwargs: object) -> None:
    if "meta" not in node:
        node["meta"] = {}
    node["meta"].update(kwargs)


def add_global_key(node: Dict) -> str:
    gk = global_key(node)
    set_meta(node, globalKey=gk)
    return gk


@lru_cache(maxsize=1)
def bic_to_lei_table() -> Dict[str, str]:
    path = Path(__file__).resolve().parent.parent.parent / "data" / "lei" / "bic_to_lei.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
