from __future__ import annotations

from typing import Dict

from .transformers.cdm_common import CDM_TRADE_KEY_ORDER, SETTLEMENT_TYPE_MAP
from .transformers.fx_option import transform_fx_option_to_cdm_v6
from .transformers.fx_spot_forward import transform_fx_spot_forward_like_to_cdm_v6
from .transformers.fx_swap import transform_fx_swap_to_cdm_v6
from .types import (
    NORMALIZED_KIND_FX_OPTION,
    NORMALIZED_KIND_FX_SWAP,
    NormalizedFxForward,
    NormalizedFxOption,
    NormalizedFxSwap,
    NormalizedFxTrade,
)

__all__ = [
    "CDM_TRADE_KEY_ORDER",
    "SETTLEMENT_TYPE_MAP",
    "transform_to_cdm_v6",
    "transform_fx_spot_forward_like_to_cdm_v6",
    "transform_fx_swap_to_cdm_v6",
    "transform_fx_option_to_cdm_v6",
]


def transform_to_cdm_v6(model: NormalizedFxTrade) -> Dict[str, object]:
    """
    Map normalized trade state to CDM v6 ``{"trade": ...}``.

    Dispatches on ``normalized_kind`` (see ``NormalizedFxForward`` and future union members).
    """
    kind = getattr(model, "normalized_kind", "fx_spot_forward_like")
    if kind == "fx_spot_forward_like":
        if not isinstance(model, NormalizedFxForward):
            raise TypeError(f"Expected NormalizedFxForward for {kind!r}, got {type(model).__name__}")
        return transform_fx_spot_forward_like_to_cdm_v6(model)
    if kind == NORMALIZED_KIND_FX_SWAP:
        if not isinstance(model, NormalizedFxSwap):
            raise TypeError(f"Expected NormalizedFxSwap for {kind!r}, got {type(model).__name__}")
        return transform_fx_swap_to_cdm_v6(model)
    if kind == NORMALIZED_KIND_FX_OPTION:
        if not isinstance(model, NormalizedFxOption):
            raise TypeError(f"Expected NormalizedFxOption for {kind!r}, got {type(model).__name__}")
        return transform_fx_option_to_cdm_v6(model)
    raise TypeError(f"No CDM transformer registered for normalized_kind={kind!r}")
