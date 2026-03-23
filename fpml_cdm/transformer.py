from __future__ import annotations

import json
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from .types import NormalizedFxForward

# Rosetta-style key order for CDM Trade (top-level)
CDM_TRADE_KEY_ORDER: List[str] = [
    "product",
    "tradeLot",
    "counterparty",
    "tradeIdentifier",
    "tradeDate",
    "party",
    "partyRole",
    "meta",
]


def _reorder_keys(d: Mapping[str, object], key_order: List[str]) -> Dict[str, object]:
    """Build a new dict: ``key_order`` first (if present), then any other keys in original order."""
    out: Dict[str, object] = {}
    for k in key_order:
        if k in d:
            out[k] = d[k]
    for k, v in d.items():
        if k not in out:
            out[k] = v
    return out


SETTLEMENT_TYPE_MAP: Dict[str, str] = {
    "PHYSICAL": "Physical",
    "CASH": "Cash",
    "REGULAR": "Physical",
}


# ---------------------------------------------------------------------------
# Global-key helpers (Rosetta-compatible deterministic hashing)
# ---------------------------------------------------------------------------

def _strip_meta(obj: object) -> object:
    """Recursively remove all ``meta`` keys from nested dicts/lists."""
    if isinstance(obj, dict):
        return {k: _strip_meta(v) for k, v in obj.items() if k != "meta"}
    if isinstance(obj, list):
        return [_strip_meta(item) for item in obj]
    return obj


def _global_key(node: Dict) -> str:
    """CRC32 hex of canonical JSON with all ``meta`` fields stripped."""
    stripped = _strip_meta(node)
    canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    crc = zlib.crc32(canonical.encode("utf-8")) & 0xFFFFFFFF
    return format(crc, "x")


def _set_meta(node: Dict, **kwargs: object) -> None:
    if "meta" not in node:
        node["meta"] = {}
    node["meta"].update(kwargs)


def _add_global_key(node: Dict) -> str:
    """Compute globalKey, attach to *node*[``meta``], and return it."""
    gk = _global_key(node)
    _set_meta(node, globalKey=gk)
    return gk


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _build_party_role_map(model: NormalizedFxForward) -> Dict[str, str]:
    """Map party IDs to CounterpartyRoleEnum values (Party1, Party2)."""
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


@lru_cache(maxsize=1)
def _bic_to_lei_table() -> Dict[str, str]:
    path = Path(__file__).resolve().parent.parent / "data" / "lei" / "bic_to_lei.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Main transformer
# ---------------------------------------------------------------------------

def transform_to_cdm_v6(model: NormalizedFxForward) -> Dict[str, object]:
    settlement_type = SETTLEMENT_TYPE_MAP.get(model.settlementType, "Physical")
    party_roles = _build_party_role_map(model)

    # -- tradeDate --------------------------------------------------------
    trade_date_node: Dict[str, object] = {"value": model.tradeDate}
    _add_global_key(trade_date_node)

    # -- parties (first, so we can build id→globalKey map) ----------------
    party_nodes: List[Dict[str, object]] = []
    party_gk: Dict[str, str] = {}

    for party in model.parties:
        pid = party.get("id")
        pname = (party.get("name") or "").strip().upper()
        resolved_lei = party.get("lei") or _bic_to_lei_table().get(pname)
        lei = party.get("lei")
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
            _set_meta(pnode, externalKey=pid)
        gk = _add_global_key(pnode)
        if pid:
            party_gk[pid] = gk
        party_nodes.append(pnode)

    def _party_ref(pid: Optional[str], *, dual: bool = True) -> Dict[str, str]:
        """Build a partyReference dict, resolving pid → hashed globalKey."""
        ref: Dict[str, str] = {
            "globalReference": party_gk.get(pid, pid) if pid else "",
        }
        if dual and pid:
            ref["externalReference"] = pid
        return ref

    # -- tradeIdentifiers
    id_nodes: List[Dict[str, object]] = []
    for ident in model.tradeIdentifiers:
        id_val: Dict[str, object] = {"value": ident.get("tradeId")}
        scheme = ident.get("scheme")
        if scheme:
            _set_meta(id_val, scheme=scheme)

        assigned = [{"identifier": id_val}]

        issuer = ident.get("issuer")
        if not issuer and model.parties:
            issuer = model.parties[0].get("id", "")

        if issuer:
            with_issuer: Dict[str, object] = {
                "issuerReference": _party_ref(issuer),
                "assignedIdentifier": assigned,
            }
            _add_global_key(with_issuer)
            id_nodes.append(with_issuer)

            # Rosetta-style duplicate row without issuerReference; keep issuer choice valid.
            without_ref: Dict[str, object] = {
                "issuer": {"value": issuer},
                "assignedIdentifier": assigned,
            }
            _add_global_key(without_ref)
            id_nodes.append(without_ref)

    # -- counterparty -----------------------------------------------------
    cp_nodes: List[Dict[str, object]] = []
    for idx, party in enumerate(model.parties[:2]):
        pid = party.get("id")
        cp_nodes.append({
            "role": f"Party{idx + 1}",
            "partyReference": _party_ref(pid),
        })

    # -- partyRole (buyer / seller) ---------------------------------------
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

    # -- settlement -------------------------------------------------------
    settlement_date_node: Dict[str, object] = {"valueDate": model.valueDate}
    _add_global_key(settlement_date_node)

    settlement_terms: Dict[str, object] = {
        "settlementType": settlement_type,
        "settlementDate": settlement_date_node,
    }
    if model.settlementCurrency:
        settlement_terms["settlementCurrency"] = {"value": model.settlementCurrency}
    _add_global_key(settlement_terms)

    # -- payout (CDM: payer/receiver follow exchangedCurrency2, not currency1 buyer/seller)
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
    _add_global_key(payout_price_quantity)

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
    _add_global_key(payout_entry)

    # -- product ----------------------------------------------------------
    product_qualifier = model.productTaxonomyQualifier or "ForeignExchange_Spot_Forward"
    product: Dict[str, object] = {
        "taxonomy": [{
            "source": "ISDA",
            "productQualifier": product_qualifier,
        }],
        "economicTerms": {"payout": [payout_entry]},
    }
    _add_global_key(product)

    # -- priceQuantity ----------------------------------------------------
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
    _add_global_key(price_quantity)

    # -- assemble trade ---------------------------------------------------
    trade: Dict[str, object] = {
        "tradeDate": trade_date_node,
        "tradeIdentifier": id_nodes,
        "party": party_nodes,
        "counterparty": cp_nodes,
        "partyRole": pr_nodes,
        "product": product,
        "tradeLot": [{"priceQuantity": [price_quantity]}],
    }
    _add_global_key(trade)

    trade = _reorder_keys(trade, CDM_TRADE_KEY_ORDER)
    return {"trade": trade}
