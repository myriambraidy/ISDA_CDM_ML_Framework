from __future__ import annotations

from typing import Dict, List, Optional

from .types import NormalizedFxForward

SETTLEMENT_TYPE_MAP: Dict[str, str] = {
    "PHYSICAL": "Physical",
    "CASH": "Cash",
    "REGULAR": "Physical",
}


def _build_party_role_map(model: NormalizedFxForward) -> Dict[str, str]:
    """Map party IDs to CounterpartyRoleEnum values (Party1, Party2)."""
    mapping: Dict[str, str] = {}
    for idx, party in enumerate(model.parties[:2]):
        pid = party.get("id")
        if pid:
            mapping[pid] = f"Party{idx + 1}"
    return mapping


def _build_underlier(model: NormalizedFxForward) -> Dict[str, object]:
    """Build Underlier with an Observable Cash asset for the FX currency pair."""
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


def transform_to_cdm_v6(model: NormalizedFxForward) -> Dict[str, object]:
    settlement_type = SETTLEMENT_TYPE_MAP.get(model.settlementType, "Physical")
    party_roles = _build_party_role_map(model)

    trade: Dict[str, object] = {
        "tradeDate": {"value": model.tradeDate},
        "tradeIdentifier": [],
        "party": [],
        "counterparty": [],
        "partyRole": [],
        "product": {
            "economicTerms": {
                "payout": []
            }
        },
        "tradeLot": [{
            "priceQuantity": []
        }],
    }

    for identifier in model.tradeIdentifiers:
        entry: Dict[str, object] = {
            "assignedIdentifier": [
                {
                    "identifier": {
                        "value": identifier.get("tradeId")
                    }
                }
            ]
        }
        issuer = identifier.get("issuer")
        if issuer:
            entry["issuer"] = {"value": issuer}
        elif model.parties:
            entry["issuer"] = {"value": model.parties[0].get("id", "")}
        trade["tradeIdentifier"].append(entry)

    for party in model.parties:
        pid = party.get("id")
        trade["party"].append(
            {
                "partyId": [
                    {
                        "identifier": {
                            "value": pid
                        }
                    }
                ],
                "name": {
                    "value": party.get("name")
                },
            }
        )

    for idx, party in enumerate(model.parties[:2]):
        pid = party.get("id")
        trade["counterparty"].append({
            "role": f"Party{idx + 1}",
            "partyReference": {"globalReference": pid},
        })

    buyer_ref = model.buyerPartyReference
    seller_ref = model.sellerPartyReference
    if not buyer_ref and len(model.parties) >= 1:
        buyer_ref = model.parties[0].get("id")
    if not seller_ref and len(model.parties) >= 2:
        seller_ref = model.parties[1].get("id")

    party_role_list: List[Dict[str, object]] = []
    if buyer_ref:
        party_role_list.append({
            "role": "Buyer",
            "partyReference": {"globalReference": buyer_ref},
        })
    if seller_ref:
        party_role_list.append({
            "role": "Seller",
            "partyReference": {"globalReference": seller_ref},
        })
    trade["partyRole"] = party_role_list

    settlement_terms: Dict[str, object] = {
        "settlementDate": {
            "valueDate": model.valueDate,
        },
        "settlementType": settlement_type,
    }

    if model.settlementCurrency:
        settlement_terms["settlementCurrency"] = {"value": model.settlementCurrency}

    payer_role = party_roles.get(model.buyerPartyReference, "Party1") if model.buyerPartyReference else "Party1"
    receiver_role = party_roles.get(model.sellerPartyReference, "Party2") if model.sellerPartyReference else "Party2"

    settlement_payout: Dict[str, object] = {
        "payerReceiver": {
            "payer": payer_role,
            "receiver": receiver_role,
        },
        "settlementTerms": settlement_terms,
        "underlier": _build_underlier(model),
    }

    trade["product"]["economicTerms"]["payout"].append({
        "SettlementPayout": settlement_payout
    })

    price_quantity: Dict[str, object] = {
        "price": [],
        "quantity": [],
    }

    if model.exchangeRate is not None and model.currency1 and model.currency2:
        price_quantity["price"].append({
            "value": {
                "value": model.exchangeRate,
                "unit": {"currency": {"value": model.currency2}},
                "perUnitOf": {"currency": {"value": model.currency1}},
                "priceType": "ExchangeRate",
            }
        })

    price_quantity["quantity"].append({
        "value": {
            "value": model.amount1,
            "unit": {"currency": {"value": model.currency1}},
        }
    })
    price_quantity["quantity"].append({
        "value": {
            "value": model.amount2,
            "unit": {"currency": {"value": model.currency2}},
        }
    })

    trade["tradeLot"][0]["priceQuantity"].append(price_quantity)

    return {"trade": trade}
