from __future__ import annotations

from typing import Dict

from .types import NormalizedFxForward

SETTLEMENT_TYPE_MAP = {
    "PHYSICAL": "SettlementTypeEnum.PHYSICAL",
    "CASH": "SettlementTypeEnum.CASH",
    "REGULAR": "SettlementTypeEnum.REGULAR",
}


def transform_to_cdm_v6(model: NormalizedFxForward) -> Dict:
    settlement_type = SETTLEMENT_TYPE_MAP.get(model.settlementType, "SettlementTypeEnum.PHYSICAL")

    trade = {
        "tradeDate": {"value": model.tradeDate},
        "tradeIdentifier": [],
        "party": [],
        "tradableProduct": {
            "product": {
                "nonTransferableProduct": {
                    "economicTerms": {
                        "payout": {
                            "settlementPayout": []
                        }
                    }
                }
            },
            "tradeLot": [{
                "priceQuantity": []
            }],
        },
    }

    for identifier in model.tradeIdentifiers:
        trade["tradeIdentifier"].append(
            {
                "assignedIdentifier": [
                    {
                        "identifier": {
                            "value": identifier.get("tradeId")
                        }
                    }
                ]
            }
        )

    for party in model.parties:
        trade["party"].append(
            {
                "partyId": [
                    {
                        "identifier": {
                            "value": party.get("id")
                        }
                    }
                ],
                "name": {
                    "value": party.get("name")
                },
            }
        )

    settlement_terms = {
        "settlementDate": {
            "adjustableOrAdjustedDate": {
                "unadjustedDate": {
                    "value": model.valueDate
                }
            }
        },
        "settlementType": settlement_type,
    }

    if model.settlementCurrency:
        settlement_terms["settlementCurrency"] = {"value": model.settlementCurrency}

    settlement_payout = {
        "settlementTerms": settlement_terms,
    }

    if model.buyerPartyReference or model.sellerPartyReference:
        settlement_payout["payerReceiver"] = {
            "payer": {"globalReference": model.buyerPartyReference} if model.buyerPartyReference else None,
            "receiver": {"globalReference": model.sellerPartyReference} if model.sellerPartyReference else None,
        }

    price_quantity = {
        "price": [],
        "quantity": [],
    }

    if model.exchangeRate is not None and model.currency1 and model.currency2:
        price_quantity["price"].append(
            {
                "value": {"value": model.exchangeRate},
                "unit": {"currency": {"value": model.currency2}},
                "perUnitOf": {"currency": {"value": model.currency1}},
            }
        )

    price_quantity["quantity"].append(
        {
            "value": {"value": model.amount1},
            "unit": {"currency": {"value": model.currency1}},
        }
    )
    price_quantity["quantity"].append(
        {
            "value": {"value": model.amount2},
            "unit": {"currency": {"value": model.currency2}},
        }
    )

    trade["tradableProduct"]["product"]["nonTransferableProduct"]["economicTerms"]["payout"]["settlementPayout"].append(settlement_payout)
    trade["tradableProduct"]["tradeLot"][0]["priceQuantity"].append(price_quantity)

    return {"trade": trade}
