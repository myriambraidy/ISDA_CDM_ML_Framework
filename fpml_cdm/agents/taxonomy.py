"""
ISDA CDM product taxonomy qualifier selection.

Default path is deterministic rules; optional LLM hook for exotic classification.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Callable, Optional, Protocol

if TYPE_CHECKING:
    from ..types import NormalizedFxForward

# Known ISDA qualifiers used in this repo / FX space
DEFAULT_FX_SPOT_FWD = "ForeignExchange_Spot_Forward"


class TaxonomyClassifier(Protocol):
    def classify(self, model: "NormalizedFxForward") -> str:
        ...


def classify_taxonomy_deterministic(model: "NormalizedFxForward") -> str:
    """
    Strict compatibility with the default transformer: always FX spot/forward bucket.

    (Rosetta output for cash-settled fxSingleLeg still uses this qualifier.)
    """
    _ = model
    return DEFAULT_FX_SPOT_FWD


def classify_taxonomy_rules_ndf(model: "NormalizedFxForward") -> str:
    """
    Heuristic "edge case" rule: NDF-style cash settlement → ``ForeignExchange_NDF``,
    else ``ForeignExchange_Spot_Forward``. May diverge from Rosetta on some corpora;
    use ``agent`` mode to override via LLM.
    """
    st = (model.settlementType or "").upper()
    if st == "CASH" and model.settlementCurrency:
        return "ForeignExchange_NDF"
    return DEFAULT_FX_SPOT_FWD


_LLM_JSON = re.compile(r"\{[^{}]*\"productQualifier\"[^{}]*\}", re.DOTALL)


def classify_taxonomy_llm(
    model: "NormalizedFxForward",
    complete: Callable[[str], str],
) -> str:
    """
    Ask an LLM for ``productQualifier`` (JSON). Fallback to deterministic on failure.

    ``complete`` is ``(prompt: str) -> str`` — same shape as ``LLMProvider.complete``.
    """
    prompt = (
        "You classify FpML-derived FX trades into ISDA CDM productQualifier strings.\n"
        "Known examples: ForeignExchange_Spot_Forward, ForeignExchange_NDF.\n"
        "Return ONLY compact JSON: {\"productQualifier\": \"...\"}\n\n"
        f"sourceProduct={model.sourceProduct!r}, settlementType={model.settlementType!r}, "
        f"settlementCurrency={model.settlementCurrency!r}, "
        f"currencies={model.currency1!r}/{model.currency2!r}\n"
    )
    try:
        text = complete(prompt)
    except Exception:
        return classify_taxonomy_deterministic(model)

    m = _LLM_JSON.search(text)
    if not m:
        return classify_taxonomy_deterministic(model)
    try:
        data = json.loads(m.group(0))
        q = data.get("productQualifier")
        if isinstance(q, str) and q.strip():
            return q.strip()
    except json.JSONDecodeError:
        pass
    return classify_taxonomy_deterministic(model)
