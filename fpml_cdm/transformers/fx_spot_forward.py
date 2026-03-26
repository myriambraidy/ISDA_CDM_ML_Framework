"""CDM v6 transform for spot/forward-like FX (fxForward, fxSingleLeg, NDF variants)."""

from __future__ import annotations

from typing import Dict, List, Optional

from ..types import NormalizedFxForward
from .cdm_common import (
    SETTLEMENT_TYPE_MAP,
    add_global_key,
    bic_to_lei_table,
    reorder_trade_keys,
    set_meta,
)


def _build_party_role_map(model: NormalizedFxForward) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for idx, party in enumerate(model.parties[:2]):
        pid = party.get("id")
        if pid:
            mapping[pid] = f"Party{idx + 1}"
    return mapping


def _build_underlier(model: NormalizedFxForward) -> Dict[str, object]:
    return {
        "Observable": {
            "value": {
                "Asset": {
                    "Cash": {
                        "identifier": [
                            {
                                "identifier": {"value": model.currency1},
                                "identifierType": "CurrencyCode",
                            }
                        ]
                    }
                }
            }
        }
    }


def transform_fx_spot_forward_like_to_cdm_v6(model: NormalizedFxForward) -> Dict[str, object]:
    settlement_type = SETTLEMENT_TYPE_MAP.get(model.settlementType, "Physical")
    party_roles = _build_party_role_map(model)

    trade_date_node: Dict[str, object] = {"value": model.tradeDate}
    add_global_key(trade_date_node)

    party_nodes: List[Dict[str, object]] = []
    party_gk: Dict[str, str] = {}

    for party in model.parties:
        pid = party.get("id")
        pname = (party.get("name") or "").strip().upper()
        resolved_lei = party.get("lei") or bic_to_lei_table().get(pname)
        if resolved_lei:
            pnode = {
                "partyId": [
                    {
                        "identifier": {
                            "value": resolved_lei,
                            "meta": {
                                "scheme": "http://www.fpml.org/coding-scheme/external/iso17442",
                            },
                        },
                        "identifierType": "LEI",
                    }
                ]
            }
        else:
            pnode = {
                "partyId": [{"identifier": {"value": pid}}],
                "name": {"value": party.get("name")},
            }
        if pid:
            set_meta(pnode, externalKey=pid)
        gk = add_global_key(pnode)
        if pid:
            party_gk[pid] = gk
        party_nodes.append(pnode)

    def _party_ref(pid: Optional[str], *, dual: bool = True) -> Dict[str, str]:
        ref: Dict[str, str] = {
            "globalReference": party_gk.get(pid, pid) if pid else "",
        }
        if dual and pid:
            ref["externalReference"] = pid
        return ref

    id_nodes: List[Dict[str, object]] = []
    for ident in model.tradeIdentifiers:
        id_val: Dict[str, object] = {"value": ident.get("tradeId")}
        scheme = ident.get("scheme")
        if scheme:
            set_meta(id_val, scheme=scheme)

        assigned = [{"identifier": id_val}]

        issuer = ident.get("issuer")
        if not issuer and model.parties:
            issuer = model.parties[0].get("id", "")

        if issuer:
            with_issuer: Dict[str, object] = {
                "issuerReference": _party_ref(issuer),
                "assignedIdentifier": assigned,
            }
            add_global_key(with_issuer)
            id_nodes.append(with_issuer)

            without_ref: Dict[str, object] = {
                "issuer": {"value": issuer},
                "assignedIdentifier": assigned,
            }
            add_global_key(without_ref)
            id_nodes.append(without_ref)

    cp_nodes: List[Dict[str, object]] = []
    for idx, party in enumerate(model.parties[:2]):
        pid = party.get("id")
        cp_nodes.append({
            "role": f"Party{idx + 1}",
            "partyReference": _party_ref(pid),
        })

    buyer_ref = model.buyerPartyReference
    seller_ref = model.sellerPartyReference
    if not buyer_ref and len(model.parties) >= 1:
        buyer_ref = model.parties[0].get("id")
    if not seller_ref and len(model.parties) >= 2:
        seller_ref = model.parties[1].get("id")

    pr_nodes: List[Dict[str, object]] = []
    if buyer_ref:
        pr_nodes.append({
            "role": "Buyer",
            "partyReference": _party_ref(buyer_ref, dual=False),
        })
    if seller_ref:
        pr_nodes.append({
            "role": "Seller",
            "partyReference": _party_ref(seller_ref, dual=False),
        })

    settlement_date_node: Dict[str, object] = {"valueDate": model.valueDate}
    add_global_key(settlement_date_node)

    settlement_terms: Dict[str, object] = {
        "settlementType": settlement_type,
        "settlementDate": settlement_date_node,
    }
    if model.settlementCurrency:
        settlement_terms["settlementCurrency"] = {"value": model.settlementCurrency}
    add_global_key(settlement_terms)

    payer_pid = model.currency2PayerPartyReference or model.buyerPartyReference
    receiver_pid = model.currency2ReceiverPartyReference or model.sellerPartyReference
    payer_role = party_roles.get(payer_pid, "Party1") if payer_pid else "Party1"
    receiver_role = party_roles.get(receiver_pid, "Party2") if receiver_pid else "Party2"

    payout_price_quantity: Dict[str, object] = {
        "quantitySchedule": {
            "address": {
                "scope": "DOCUMENT",
                "value": "quantity-1",
            }
        },
        "priceSchedule": [
            {
                "address": {
                    "scope": "DOCUMENT",
                    "value": "price-1",
                }
            }
        ],
    }
    add_global_key(payout_price_quantity)

    settlement_payout: Dict[str, object] = {
        "payerReceiver": {"payer": payer_role, "receiver": receiver_role},
        "priceQuantity": payout_price_quantity,
        "settlementTerms": settlement_terms,
        "underlier": {
            "Observable": {
                "address": {
                    "scope": "DOCUMENT",
                    "value": "observable-1",
                }
            }
        },
    }

    payout_entry: Dict[str, object] = {"SettlementPayout": settlement_payout}
    add_global_key(payout_entry)

    product_qualifier = model.productTaxonomyQualifier or "ForeignExchange_Spot_Forward"
    product: Dict[str, object] = {
        "taxonomy": [{
            "source": "ISDA",
            "productQualifier": product_qualifier,
        }],
        "economicTerms": {"payout": [payout_entry]},
    }
    add_global_key(product)

    price_quantity: Dict[str, object] = {"price": [], "quantity": []}

    if model.exchangeRate is not None and model.currency1 and model.currency2:
        price_quantity["price"].append({
            "value": {
                "value": model.exchangeRate,
                "unit": {"currency": {"value": model.currency2}},
                "perUnitOf": {"currency": {"value": model.currency1}},
                "priceType": "ExchangeRate",
            },
            "meta": {
                "location": [
                    {"scope": "DOCUMENT", "value": "price-1"},
                ]
            },
        })

    price_quantity["quantity"].append({
        "value": {
            "value": model.amount1,
            "unit": {"currency": {"value": model.currency1}},
        },
        "meta": {
            "location": [
                {"scope": "DOCUMENT", "value": "quantity-1"},
            ]
        },
    })
    price_quantity["quantity"].append({
        "value": {
            "value": model.amount2,
            "unit": {"currency": {"value": model.currency2}},
        },
        "meta": {
            "location": [
                {"scope": "DOCUMENT", "value": "quantity-2"},
            ]
        },
    })
    price_quantity["observable"] = {
        "value": _build_underlier(model)["Observable"]["value"],
        "meta": {
            "location": [
                {"scope": "DOCUMENT", "value": "observable-1"},
            ]
        },
    }
    add_global_key(price_quantity)

    trade: Dict[str, object] = {
        "tradeDate": trade_date_node,
        "tradeIdentifier": id_nodes,
        "party": party_nodes,
        "counterparty": cp_nodes,
        "partyRole": pr_nodes,
        "product": product,
        "tradeLot": [{"priceQuantity": [price_quantity]}],
    }
    add_global_key(trade)

    trade = reorder_trade_keys(trade)
    return {"trade": trade}
