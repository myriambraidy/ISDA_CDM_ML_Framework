"""CDM v6 transform for FX swaps (near/far legs)."""

from __future__ import annotations

from typing import Dict, List, Optional

from ..types import NormalizedFxSwap
from .cdm_common import SETTLEMENT_TYPE_MAP, add_global_key, reorder_trade_keys
from .fx_spot_forward import transform_fx_spot_forward_like_to_cdm_v6


def _leg_price_quantity(
    *,
    amount1: float,
    currency1: str,
    amount2: float,
    currency2: str,
    exchange_rate: float | None,
    quantity_addr: str,
    price_addr: str,
    observable_addr: str,
) -> Dict[str, object]:
    pq: Dict[str, object] = {"price": [], "quantity": []}
    if exchange_rate is not None and currency1 and currency2:
        pq["price"].append(
            {
                "value": {
                    "value": exchange_rate,
                    "unit": {"currency": {"value": currency2}},
                    "perUnitOf": {"currency": {"value": currency1}},
                    "priceType": "ExchangeRate",
                },
                "meta": {"location": [{"scope": "DOCUMENT", "value": price_addr}]},
            }
        )
    pq["quantity"].append(
        {
            "value": {"value": amount1, "unit": {"currency": {"value": currency1}}},
            "meta": {"location": [{"scope": "DOCUMENT", "value": quantity_addr}]},
        }
    )
    pq["quantity"].append(
        {
            "value": {"value": amount2, "unit": {"currency": {"value": currency2}}},
            "meta": {"location": [{"scope": "DOCUMENT", "value": f"{quantity_addr}-2"}]},
        }
    )
    pq["observable"] = {
        "value": {
            "Asset": {
                "Cash": {
                    "identifier": [
                        {
                            "identifier": {"value": currency1},
                            "identifierType": "CurrencyCode",
                        }
                    ]
                }
            }
        },
        "meta": {"location": [{"scope": "DOCUMENT", "value": observable_addr}]},
    }
    add_global_key(pq)
    return pq


def transform_fx_swap_to_cdm_v6(model: NormalizedFxSwap) -> Dict[str, object]:
    # Build near leg using existing, battle-tested spot/forward logic.
    from ..types import NormalizedFxForward

    near_as_forward = NormalizedFxForward(
        tradeDate=model.tradeDate,
        valueDate=model.nearValueDate,
        currency1=model.nearCurrency1,
        currency2=model.nearCurrency2,
        amount1=model.nearAmount1,
        amount2=model.nearAmount2,
        tradeIdentifiers=model.tradeIdentifiers,
        parties=model.parties,
        exchangeRate=model.nearExchangeRate,
        settlementType=model.nearSettlementType,
        buyerPartyReference=model.buyerPartyReference,
        sellerPartyReference=model.sellerPartyReference,
        currency2PayerPartyReference=model.nearCurrency2PayerPartyReference,
        currency2ReceiverPartyReference=model.nearCurrency2ReceiverPartyReference,
        sourceProduct=model.sourceProduct,
    )
    near_trade = transform_fx_spot_forward_like_to_cdm_v6(near_as_forward)["trade"]
    party_roles: Dict[str, str] = {}
    for idx, p in enumerate(model.parties[:2]):
        pid = p.get("id")
        if pid:
            party_roles[pid] = f"Party{idx + 1}"

    def _role_for(pid: Optional[str], fallback: str) -> str:
        if not pid:
            return fallback
        return party_roles.get(pid, fallback)

    far_settlement_date = {"valueDate": model.farValueDate}
    add_global_key(far_settlement_date)
    far_settlement_terms = {
        "settlementType": SETTLEMENT_TYPE_MAP.get(model.farSettlementType, "Physical"),
        "settlementDate": far_settlement_date,
    }
    add_global_key(far_settlement_terms)
    far_payout_price_quantity = {
        "quantitySchedule": {"address": {"scope": "DOCUMENT", "value": "quantity-3"}},
        "priceSchedule": [{"address": {"scope": "DOCUMENT", "value": "price-2"}}],
    }
    add_global_key(far_payout_price_quantity)
    far_settlement_payout = {
        "payerReceiver": {
            "payer": _role_for(model.farCurrency2PayerPartyReference, "Party2"),
            "receiver": _role_for(model.farCurrency2ReceiverPartyReference, "Party1"),
        },
        "priceQuantity": far_payout_price_quantity,
        "settlementTerms": far_settlement_terms,
        "underlier": {"Observable": {"address": {"scope": "DOCUMENT", "value": "observable-2"}}},
    }
    far_payout_entry = {"SettlementPayout": far_settlement_payout}
    add_global_key(far_payout_entry)

    product = near_trade.get("product", {})
    payouts: List[Dict[str, object]] = list(product.get("economicTerms", {}).get("payout", []))
    payouts.append(far_payout_entry)
    product["economicTerms"] = {"payout": payouts}
    product["taxonomy"] = [
        {
            "source": "ISDA",
            "productQualifier": model.productTaxonomyQualifier or "ForeignExchange_Swap",
        }
    ]
    add_global_key(product)

    near_pq = near_trade.get("tradeLot", [{}])[0].get("priceQuantity", [{}])[0]
    far_pq = _leg_price_quantity(
        amount1=model.farAmount1,
        currency1=model.farCurrency1,
        amount2=model.farAmount2,
        currency2=model.farCurrency2,
        exchange_rate=model.farExchangeRate,
        quantity_addr="quantity-3",
        price_addr="price-2",
        observable_addr="observable-2",
    )

    trade = dict(near_trade)
    trade["product"] = product
    trade["tradeLot"] = [{"priceQuantity": [near_pq, far_pq]}]
    add_global_key(trade)
    trade = reorder_trade_keys(trade)
    return {"trade": trade}

