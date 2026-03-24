"""
Registry of supported FpML FX product adapters (direct children of ``<trade>``).

Detection is namespace-agnostic (XML local names only). When multiple registered
products appear under the same ``<trade>``, the adapter with the lower ``priority``
value wins; ties break by ``adapter_id`` then document order.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..types import ErrorCode, ParserError, ValidationIssue
from ..xml_utils import _local_name


@dataclass(frozen=True)
class FxAdapterSpec:
    """One supported FX FpML product under ``<trade>``."""

    adapter_id: str
    #: Lower value is preferred when multiple registered products are present.
    priority: int
    asset_class: str = "fx"
    #: Discriminator for normalized JSON and transformer dispatch.
    normalized_kind: str = "fx_spot_forward_like"
    #: Short description for tooling and docs.
    description: str = ""


# Ordered declaratively; runtime iteration uses priority + stable tie-breaks.
FX_ADAPTER_REGISTRY: Tuple[FxAdapterSpec, ...] = (
    FxAdapterSpec(
        adapter_id="fxForward",
        priority=10,
        description="Deliverable / cash-settled FX forward",
    ),
    FxAdapterSpec(
        adapter_id="fxSingleLeg",
        priority=20,
        description="Single-leg FX (spot-forward style economics)",
    ),
    FxAdapterSpec(
        adapter_id="fxSwap",
        priority=30,
        normalized_kind="fx_swap",
        description="Two-leg FX swap (near and far settlement)",
    ),
)

_ADAPTER_BY_ID: Dict[str, FxAdapterSpec] = {s.adapter_id: s for s in FX_ADAPTER_REGISTRY}
SUPPORTED_FX_ADAPTER_IDS: frozenset[str] = frozenset(_ADAPTER_BY_ID.keys())


def get_fx_adapter_spec(adapter_id: str) -> Optional[FxAdapterSpec]:
    return _ADAPTER_BY_ID.get(adapter_id)


def iter_fx_adapter_ids_by_priority() -> List[str]:
    """Registered adapter ids sorted by priority (then id)."""
    return [s.adapter_id for s in sorted(FX_ADAPTER_REGISTRY, key=lambda s: (s.priority, s.adapter_id))]


def _economic_presence_score(product_el: ET.Element) -> int:
    """Heuristic signal strength for disambiguation (higher = more likely complete product)."""
    names = {_local_name(e.tag) for e in product_el.iter()}
    score = 0
    for tag in (
        "exchangedCurrency1",
        "exchangedCurrency2",
        "nearLeg",
        "farLeg",
        "valueDate",
        "exchangeRate",
        "nonDeliverableSettlement",
        "nonDeliverableForward",
    ):
        if tag in names:
            score += 1
    return score


def detect_fx_adapter_product(trade: ET.Element) -> Tuple[str, ET.Element]:
    """
    Pick the active FX product under ``trade``.

    Returns:
        (adapter_id, product_element)

    Raises:
        ParserError: if no registered product is found.
    """
    rows: List[Tuple[int, str, int, ET.Element]] = []
    unsupported: List[str] = []

    for idx, child in enumerate(list(trade)):
        lname = _local_name(child.tag)
        if lname == "tradeHeader":
            continue
        spec = _ADAPTER_BY_ID.get(lname)
        if spec is not None:
            rows.append((spec.priority, spec.adapter_id, idx, child))
        else:
            unsupported.append(lname)

    if not rows:
        if unsupported:
            first = unsupported[0]
            raise ParserError(
                [
                    ValidationIssue(
                        code=ErrorCode.UNSUPPORTED_PRODUCT.value,
                        message=f"Unsupported product type: {first}",
                        path=f"trade/{first}",
                    )
                ]
            )
        raise ParserError(
            [
                ValidationIssue(
                    code=ErrorCode.UNSUPPORTED_PRODUCT.value,
                    message="No supported product found under trade",
                    path="trade",
                )
            ]
        )

    # Sort: priority, then stronger economic subtree, then adapter_id, then XML order.
    rows.sort(
        key=lambda r: (
            r[0],
            -_economic_presence_score(r[3]),
            r[1],
            r[2],
        )
    )
    _, adapter_id, _, product_el = rows[0]
    return adapter_id, product_el


def describe_fx_adapter_registry() -> List[Dict[str, object]]:
    """Machine-readable summary for mapping tools and agents."""
    return [
        {
            "adapter_id": s.adapter_id,
            "priority": s.priority,
            "asset_class": s.asset_class,
            "normalized_kind": s.normalized_kind,
            "description": s.description,
        }
        for s in sorted(FX_ADAPTER_REGISTRY, key=lambda x: (x.priority, x.adapter_id))
    ]


def fpml_trade_product_local_names(fpml_path: str) -> List[str]:
    """
    Local names of direct ``<trade>`` children (excluding ``tradeHeader``), in document order.
    Used for corpus reporting when conversion fails before a normalized model exists.
    """
    from pathlib import Path

    from ..xml_utils import _find_descendant_local

    path = Path(fpml_path)
    if not path.is_file():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    trade = _find_descendant_local(root, "trade")
    if trade is None:
        return []
    out: List[str] = []
    for child in list(trade):
        lname = _local_name(child.tag)
        if lname != "tradeHeader":
            out.append(lname)
    return out
