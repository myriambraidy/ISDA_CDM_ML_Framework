from __future__ import annotations

from typing import Any, Dict


# Rulesets are deliberately small and only cover the supported FX adapters today.
# Each ruleset defines candidate XML local-name paths for key normalized fields.

_BASE_RULESETS: Dict[str, Dict[str, Any]] = {
    "fxForward": {
        "adapter_id": "fxForward",
        "fields": {
            "valueDate": {
                "required": True,
                "parser": "date_only",
                "candidates": [
                    "valueDate",
                    "currency1ValueDate",
                    "currency2ValueDate",
                ],
            },
            "currency1": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "exchangedCurrency1/paymentAmount/currency",
                ],
            },
            "amount1": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "exchangedCurrency1/paymentAmount/amount",
                ],
            },
            "currency2": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "exchangedCurrency2/paymentAmount/currency",
                ],
            },
            "amount2": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "exchangedCurrency2/paymentAmount/amount",
                ],
            },
            "exchangeRate": {
                "required": False,
                "parser": "amount",
                "candidates": [
                    "exchangeRate/rate",
                ],
            },
            "settlementType": {
                "required": True,
                "parser": "settlement_type_from_ndf_presence",
                "ndf_candidates": [
                    "nonDeliverableSettlement",
                    "nonDeliverableForward",
                ],
                "cash_value": "CASH",
                "physical_value": "PHYSICAL",
            },
            "settlementCurrency": {
                "required": False,
                "parser": "currency3",
                "candidates": [
                    "nonDeliverableSettlement/settlementCurrency",
                    "nonDeliverableForward/settlementCurrency",
                ],
            },
            "buyerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "buyerPartyReference/@href",
                ],
            },
            "sellerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "sellerPartyReference/@href",
                ],
            },
        },
        "derived": {
            # Disabled by default so the deterministic baseline stays identical.
            "exchangeRate": {
                "enabled": False,
                "strategy": "amount_ratio",  # amount2 / amount1
            }
        },
    },
    "fxSingleLeg": {
        "adapter_id": "fxSingleLeg",
        "fields": {
            "valueDate": {
                "required": True,
                "parser": "date_only",
                "candidates": [
                    "valueDate",
                    "currency1ValueDate",
                    "currency2ValueDate",
                ],
            },
            "currency1": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "exchangedCurrency1/paymentAmount/currency",
                ],
            },
            "amount1": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "exchangedCurrency1/paymentAmount/amount",
                ],
            },
            "currency2": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "exchangedCurrency2/paymentAmount/currency",
                ],
            },
            "amount2": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "exchangedCurrency2/paymentAmount/amount",
                ],
            },
            "exchangeRate": {
                "required": False,
                "parser": "amount",
                "candidates": [
                    "exchangeRate/rate",
                ],
            },
            "settlementType": {
                "required": True,
                "parser": "settlement_type_from_ndf_presence",
                "ndf_candidates": [
                    "nonDeliverableSettlement",
                    "nonDeliverableForward",
                ],
                "cash_value": "CASH",
                "physical_value": "PHYSICAL",
            },
            "settlementCurrency": {
                "required": False,
                "parser": "currency3",
                "candidates": [
                    "nonDeliverableSettlement/settlementCurrency",
                    "nonDeliverableForward/settlementCurrency",
                ],
            },
            "buyerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "buyerPartyReference/@href",
                ],
            },
            "sellerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "sellerPartyReference/@href",
                ],
            },
        },
        "derived": {
            "exchangeRate": {
                "enabled": False,
                "strategy": "amount_ratio",
            }
        },
    },
}


def get_base_ruleset(adapter_id: str) -> Dict[str, Any]:
    rs = _BASE_RULESETS.get(adapter_id)
    if rs is None:
        raise KeyError(f"No base ruleset for adapter_id: {adapter_id}")
    # Return a deep-ish copy to prevent accidental mutation.
    import copy

    return copy.deepcopy(rs)

