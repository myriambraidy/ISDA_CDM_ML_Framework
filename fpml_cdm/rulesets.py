from __future__ import annotations

from typing import Any, Dict, List


# Rulesets are deliberately small: one entry per adapter_id in
# ``fpml_cdm.adapters.registry`` (FX). Each ruleset defines candidate XML
# local-name paths for key normalized fields.

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
                "candidates": [
                    "settlementType",
                    "settlementTerms/settlementType",
                ],
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
                    # Some FpML variants encode payer/receiver under the exchangedCurrency legs.
                    # Our normalized model treats "buyerPartyReference" as the payer party.
                    "exchangedCurrency1/payerPartyReference/@href",
                ],
            },
            "sellerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "sellerPartyReference/@href",
                    # Our normalized model treats "sellerPartyReference" as the receiver party.
                    "exchangedCurrency1/receiverPartyReference/@href",
                ],
            },
            "currency2PayerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "exchangedCurrency2/payerPartyReference/@href",
                ],
            },
            "currency2ReceiverPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "exchangedCurrency2/receiverPartyReference/@href",
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
                "candidates": [
                    "settlementType",
                    "settlementTerms/settlementType",
                ],
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
                    "exchangedCurrency1/payerPartyReference/@href",
                ],
            },
            "sellerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "sellerPartyReference/@href",
                    "exchangedCurrency1/receiverPartyReference/@href",
                ],
            },
            "currency2PayerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "exchangedCurrency2/payerPartyReference/@href",
                ],
            },
            "currency2ReceiverPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "exchangedCurrency2/receiverPartyReference/@href",
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
    "fxSwap": {
        "adapter_id": "fxSwap",
        "fields": {
            "nearValueDate": {
                "required": True,
                "parser": "date_only",
                "candidates": [
                    "nearLeg/valueDate",
                    "nearLeg/currency1ValueDate",
                    "nearLeg/currency2ValueDate",
                    "fxSingleLeg[0]/valueDate",
                    "fxSingleLeg[0]/currency1ValueDate",
                    "fxSingleLeg[0]/currency2ValueDate",
                ],
            },
            "farValueDate": {
                "required": True,
                "parser": "date_only",
                "candidates": [
                    "farLeg/valueDate",
                    "farLeg/currency1ValueDate",
                    "farLeg/currency2ValueDate",
                    "fxSingleLeg[1]/valueDate",
                    "fxSingleLeg[1]/currency1ValueDate",
                    "fxSingleLeg[1]/currency2ValueDate",
                ],
            },
            "nearCurrency1": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "nearLeg/exchangedCurrency1/paymentAmount/currency",
                    "fxSingleLeg[0]/exchangedCurrency1/paymentAmount/currency",
                ],
            },
            "nearAmount1": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "nearLeg/exchangedCurrency1/paymentAmount/amount",
                    "fxSingleLeg[0]/exchangedCurrency1/paymentAmount/amount",
                ],
            },
            "nearCurrency2": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "nearLeg/exchangedCurrency2/paymentAmount/currency",
                    "fxSingleLeg[0]/exchangedCurrency2/paymentAmount/currency",
                ],
            },
            "nearAmount2": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "nearLeg/exchangedCurrency2/paymentAmount/amount",
                    "fxSingleLeg[0]/exchangedCurrency2/paymentAmount/amount",
                ],
            },
            "farCurrency1": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "farLeg/exchangedCurrency1/paymentAmount/currency",
                    "fxSingleLeg[1]/exchangedCurrency1/paymentAmount/currency",
                ],
            },
            "farAmount1": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "farLeg/exchangedCurrency1/paymentAmount/amount",
                    "fxSingleLeg[1]/exchangedCurrency1/paymentAmount/amount",
                ],
            },
            "farCurrency2": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "farLeg/exchangedCurrency2/paymentAmount/currency",
                    "fxSingleLeg[1]/exchangedCurrency2/paymentAmount/currency",
                ],
            },
            "farAmount2": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "farLeg/exchangedCurrency2/paymentAmount/amount",
                    "fxSingleLeg[1]/exchangedCurrency2/paymentAmount/amount",
                ],
            },
            "nearExchangeRate": {
                "required": False,
                "parser": "amount",
                "candidates": [
                    "nearLeg/exchangeRate/rate",
                    "fxSingleLeg[0]/exchangeRate/rate",
                ],
            },
            "farExchangeRate": {
                "required": False,
                "parser": "amount",
                "candidates": [
                    "farLeg/exchangeRate/rate",
                    "fxSingleLeg[1]/exchangeRate/rate",
                ],
            },
            "nearSettlementType": {
                "required": False,
                "parser": "settlement_type_from_ndf_presence",
                "candidates": [
                    "nearLeg/settlementType",
                    "nearLeg/settlementTerms/settlementType",
                    "fxSingleLeg[0]/settlementType",
                    "fxSingleLeg[0]/settlementTerms/settlementType",
                ],
                "ndf_candidates": [
                    "nearLeg/nonDeliverableSettlement",
                    "nearLeg/nonDeliverableForward",
                    "fxSingleLeg[0]/nonDeliverableSettlement",
                    "fxSingleLeg[0]/nonDeliverableForward",
                ],
                "cash_value": "CASH",
                "physical_value": "PHYSICAL",
            },
            "farSettlementType": {
                "required": False,
                "parser": "settlement_type_from_ndf_presence",
                "candidates": [
                    "farLeg/settlementType",
                    "farLeg/settlementTerms/settlementType",
                    "fxSingleLeg[1]/settlementType",
                    "fxSingleLeg[1]/settlementTerms/settlementType",
                ],
                "ndf_candidates": [
                    "farLeg/nonDeliverableSettlement",
                    "farLeg/nonDeliverableForward",
                    "fxSingleLeg[1]/nonDeliverableSettlement",
                    "fxSingleLeg[1]/nonDeliverableForward",
                ],
                "cash_value": "CASH",
                "physical_value": "PHYSICAL",
            },
            "buyerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "nearLeg/buyerPartyReference/@href",
                    "buyerPartyReference/@href",
                    "nearLeg/exchangedCurrency1/payerPartyReference/@href",
                    "fxSingleLeg[0]/buyerPartyReference/@href",
                    "fxSingleLeg[0]/exchangedCurrency1/payerPartyReference/@href",
                ],
            },
            "sellerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "nearLeg/sellerPartyReference/@href",
                    "sellerPartyReference/@href",
                    "nearLeg/exchangedCurrency1/receiverPartyReference/@href",
                    "fxSingleLeg[0]/sellerPartyReference/@href",
                    "fxSingleLeg[0]/exchangedCurrency1/receiverPartyReference/@href",
                ],
            },
            "nearCurrency2PayerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "nearLeg/exchangedCurrency2/payerPartyReference/@href",
                    "fxSingleLeg[0]/exchangedCurrency2/payerPartyReference/@href",
                ],
            },
            "nearCurrency2ReceiverPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "nearLeg/exchangedCurrency2/receiverPartyReference/@href",
                    "fxSingleLeg[0]/exchangedCurrency2/receiverPartyReference/@href",
                ],
            },
            "farCurrency2PayerPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "farLeg/exchangedCurrency2/payerPartyReference/@href",
                    "fxSingleLeg[1]/exchangedCurrency2/payerPartyReference/@href",
                ],
            },
            "farCurrency2ReceiverPartyReference": {
                "required": False,
                "parser": "href",
                "candidates": [
                    "farLeg/exchangedCurrency2/receiverPartyReference/@href",
                    "fxSingleLeg[1]/exchangedCurrency2/receiverPartyReference/@href",
                ],
            },
        },
        "derived": {},
    },
    "fxOption": {
        "adapter_id": "fxOption",
        "fields": {
            "exerciseStyle": {
                "required": True,
                "parser": "fx_option_exercise_style",
                "candidates": [],
            },
            "expiryDate": {
                "required": True,
                "parser": "date_only",
                "candidates": [
                    "europeanExercise/expiryDate/adjustableDate/unadjustedDate",
                    "europeanExercise/expiryDate",
                    "americanExercise/earliestExerciseDate",
                    "americanExercise/expirationDate",
                    "americanExercise/expirationDate/adjustableDate/unadjustedDate",
                    "bermudaExercise/bermudaExerciseDates/expirationDate",
                ],
            },
            "putCurrency": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "putCurrencyAmount/currency",
                ],
            },
            "putAmount": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "putCurrencyAmount/amount",
                ],
            },
            "callCurrency": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "callCurrencyAmount/currency",
                ],
            },
            "callAmount": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "callCurrencyAmount/amount",
                ],
            },
            "strikeRate": {
                "required": True,
                "parser": "amount",
                "candidates": [
                    "strike/exchangeRate/rate",
                    "strike/rate",
                    "exchangeRate/rate",
                ],
            },
            "strikeCurrency1": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "strike/exchangeRate/quotedCurrencyPair/currency1",
                    "strike/quotedCurrencyPair/currency1",
                ],
            },
            "strikeCurrency2": {
                "required": True,
                "parser": "currency3",
                "candidates": [
                    "strike/exchangeRate/quotedCurrencyPair/currency2",
                    "strike/quotedCurrencyPair/currency2",
                ],
            },
            "optionType": {
                "required": True,
                "parser": "fx_option_call_put",
                "candidates": [],
            },
            "valueDate": {
                "required": False,
                "parser": "date_only",
                "candidates": [
                    "valueDate",
                    "exerciseProcedure/spotRateSource/valueDate",
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
            "premiumAmount": {
                "required": False,
                "parser": "amount",
                "candidates": [
                    "premiumAmount/amount",
                    "premium/paymentAmount/amount",
                ],
            },
            "premiumCurrency": {
                "required": False,
                "parser": "currency3",
                "candidates": [
                    "premiumAmount/currency",
                    "premium/paymentAmount/currency",
                ],
            },
            "premiumPaymentDate": {
                "required": False,
                "parser": "date_only",
                "candidates": [
                    "premium/paymentDate",
                ],
            },
            "settlementType": {
                "required": False,
                "parser": "settlement_type_from_ndf_presence",
                "candidates": [
                    "settlementType",
                    "settlementTerms/settlementType",
                ],
                "ndf_candidates": [
                    "nonDeliverableSettlement",
                    "nonDeliverableForward",
                ],
                "cash_value": "CASH",
                "physical_value": "PHYSICAL",
            },
        },
        "derived": {},
    },
}


def get_base_ruleset(adapter_id: str) -> Dict[str, Any]:
    rs = _BASE_RULESETS.get(adapter_id)
    if rs is None:
        raise KeyError(f"No base ruleset for adapter_id: {adapter_id}")
    # Return a deep-ish copy to prevent accidental mutation.
    import copy

    return copy.deepcopy(rs)


def list_ruleset_adapter_ids() -> List[str]:
    """Adapter ids that have a base ruleset (should match the FX registry)."""
    return sorted(_BASE_RULESETS.keys())

