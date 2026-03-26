"""CDM v6 transform for FpML ``fxOption`` (vanilla FX options → ``OptionPayout``)."""

from __future__ import annotations

from typing import Dict, List, Optional

from ..types import NormalizedFxOption
from .cdm_common import add_global_key, reorder_trade_keys, set_meta


def _party_role_map(model: NormalizedFxOption) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for idx, party in enumerate(model.parties[:2]):
        pid = party.get("id")
        if pid:
            mapping[pid] = f"Party{idx + 1}"
    return mapping


def _buyer_seller_roles(model: NormalizedFxOption) -> Dict[str, str]:
    roles = _party_role_map(model)
    buyer = model.buyerPartyReference
    seller = model.sellerPartyReference
    if not buyer and model.parties:
        buyer = model.parties[0].get("id")
    if not seller and len(model.parties) > 1:
        seller = model.parties[1].get("id")
    return {
        "buyer": roles.get(buyer or "", "Party1"),
        "seller": roles.get(seller or "", "Party2"),
    }


def _underlier_observable_value(currency_code: str) -> Dict[str, object]:
    return {
        "Asset": {
            "Cash": {
                "identifier": [
                    {
                        "identifier": {"value": currency_code},
                        "identifierType": "CurrencyCode",
                    }
                ]
            }
        }
    }


def transform_fx_option_to_cdm_v6(model: NormalizedFxOption) -> Dict[str, object]:
    """Map normalized FX option → CDM ``trade`` with a single ``OptionPayout``."""
    party_roles = _party_role_map(model)
    bs = _buyer_seller_roles(model)

    trade_date_node: Dict[str, object] = {"value": model.tradeDate}
    add_global_key(trade_date_node)

    party_nodes: List[Dict[str, object]] = []
    party_gk: Dict[str, str] = {}

    for party in model.parties:
        pid = party.get("id")
        pname = (party.get("name") or "").strip().upper()
        from .cdm_common import bic_to_lei_table

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

    style = model.exerciseStyle
    if style not in ("European", "American", "Bermuda"):
        style = "European"

    expiration_block: Dict[str, object] = {
        "adjustableDate": {
            "unadjustedDate": model.expiryDate,
        }
    }
    add_global_key(expiration_block)

    exercise_terms: Dict[str, object] = {
        "style": style,
        "expirationDate": [expiration_block],
        "expirationTime": {
            "hourMinuteTime": "17:00:00",
            "businessCenter": {"value": "GBLO"},
        },
        "expirationTimeType": "SpecificTime",
    }
    if model.valueDate:
        ud: Dict[str, object] = {"adjustableDate": {"unadjustedDate": model.valueDate}}
        add_global_key(ud)
        exercise_terms["relevantUnderlyingDate"] = [ud]
    add_global_key(exercise_terms)

    strike_price: Dict[str, object] = {
        "value": model.strikeRate,
        "unit": {"currency": {"value": model.strikeCurrency2}},
        "perUnitOf": {"currency": {"value": model.strikeCurrency1}},
        "priceType": "ExchangeRate",
    }
    strike: Dict[str, object] = {"strikePrice": strike_price}
    add_global_key(strike)

    opt_type = model.optionType if model.optionType in ("Call", "Put") else "Call"

    base_ccy = model.strikeCurrency1 or model.callCurrency

    option_pq: Dict[str, object] = {
        "quantitySchedule": {"address": {"scope": "DOCUMENT", "value": "quantity-1"}},
        "priceSchedule": [{"address": {"scope": "DOCUMENT", "value": "price-1"}}],
    }
    add_global_key(option_pq)

    option_payout: Dict[str, object] = {
        "payerReceiver": {
            "payer": bs["buyer"],
            "receiver": bs["seller"],
        },
        "buyerSeller": {
            "buyer": bs["buyer"],
            "seller": bs["seller"],
        },
        "optionType": opt_type,
        "strike": strike,
        "exerciseTerms": exercise_terms,
        "underlier": {
            "Observable": {
                "address": {"scope": "DOCUMENT", "value": "observable-1"},
            }
        },
        "priceQuantity": option_pq,
    }
    add_global_key(option_payout)

    payout_entry: Dict[str, object] = {"OptionPayout": option_payout}
    add_global_key(payout_entry)

    product_qualifier = model.productTaxonomyQualifier or "ForeignExchange_Option"
    product: Dict[str, object] = {
        "taxonomy": [{
            "source": "ISDA",
            "productQualifier": product_qualifier,
        }],
        "economicTerms": {"payout": [payout_entry]},
    }
    add_global_key(product)

    price_quantity: Dict[str, object] = {"price": [], "quantity": []}
    if model.strikeRate and model.strikeCurrency1 and model.strikeCurrency2:
        price_quantity["price"].append({
            "value": {
                "value": model.strikeRate,
                "unit": {"currency": {"value": model.strikeCurrency2}},
                "perUnitOf": {"currency": {"value": model.strikeCurrency1}},
                "priceType": "ExchangeRate",
            },
            "meta": {"location": [{"scope": "DOCUMENT", "value": "price-1"}]},
        })

    price_quantity["quantity"].append({
        "value": {
            "value": model.putAmount,
            "unit": {"currency": {"value": model.putCurrency}},
        },
        "meta": {"location": [{"scope": "DOCUMENT", "value": "quantity-1"}]},
    })
    price_quantity["quantity"].append({
        "value": {
            "value": model.callAmount,
            "unit": {"currency": {"value": model.callCurrency}},
        },
        "meta": {"location": [{"scope": "DOCUMENT", "value": "quantity-2"}]},
    })
    price_quantity["observable"] = {
        "value": _underlier_observable_value(base_ccy),
        "meta": {"location": [{"scope": "DOCUMENT", "value": "observable-1"}]},
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
