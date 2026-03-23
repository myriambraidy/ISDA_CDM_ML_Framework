"""
Rosetta-style DOCUMENT ``location`` metas on tradeLot ``priceQuantity`` + duplicated ``observable``.

Conservative: does **not** add ``SettlementPayout.priceQuantity`` schedule indirection (often fails
strict CDM JSON Schema validation with our minimal Trade shape). Extend when your schema bundle
includes those types.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, MutableMapping, cast

_SCOPE_DOC = {"scope": "DOCUMENT"}


def apply_document_address_pattern(cdm: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a **deep copy** of ``cdm`` with:

    - ``trade.tradeLot[*].priceQuantity[*]``: ``meta.location`` on each ``price`` / ``quantity`` item
    - ``observable`` sibling under the same ``priceQuantity`` (copy of Cash asset from payout underlier)
    """
    out = copy.deepcopy(cdm)
    trade = out.get("trade")
    if not isinstance(trade, dict):
        return out

    product = trade.get("product")
    if not isinstance(product, dict):
        return out

    et = product.get("economicTerms")
    if not isinstance(et, dict):
        return out

    payouts = et.get("payout")
    if not isinstance(payouts, list) or not payouts:
        return out

    first_p = payouts[0]
    if not isinstance(first_p, dict):
        return out

    sp = first_p.get("SettlementPayout")
    if not isinstance(sp, dict):
        return out

    underlier = sp.get("underlier")
    obs_inner: Dict[str, Any] | None = None
    if isinstance(underlier, dict):
        obs = underlier.get("Observable")
        if isinstance(obs, dict):
            val = obs.get("value")
            if isinstance(val, dict):
                obs_inner = val

    trade_lots = trade.get("tradeLot")
    if not isinstance(trade_lots, list) or not trade_lots:
        return out

    tl0 = trade_lots[0]
    if not isinstance(tl0, dict):
        return out

    pqs = tl0.get("priceQuantity")
    if not isinstance(pqs, list) or not pqs:
        return out

    pq0 = pqs[0]
    if not isinstance(pq0, dict):
        return out

    prices = pq0.get("price")
    if isinstance(prices, list) and prices and isinstance(prices[0], dict):
        if "meta" not in prices[0]:
            prices[0]["meta"] = {}
        cast(MutableMapping[str, Any], prices[0]["meta"])["location"] = [
            {**_SCOPE_DOC, "value": "price-1"}
        ]

    quants = pq0.get("quantity")
    if isinstance(quants, list):
        for idx, q in enumerate(quants):
            if not isinstance(q, dict):
                continue
            if "meta" not in q:
                q["meta"] = {}
            label = f"quantity-{idx + 1}"
            cast(MutableMapping[str, Any], q["meta"])["location"] = [
                {**_SCOPE_DOC, "value": label}
            ]

    if obs_inner is not None:
        pq0["observable"] = {
            "value": copy.deepcopy(obs_inner),
            "meta": {
                "location": [{**_SCOPE_DOC, "value": "observable-1"}],
            },
        }

    return out
