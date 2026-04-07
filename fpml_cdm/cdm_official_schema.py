from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas" / "jsonschema"
TRADE_SCHEMA_FILENAME = "cdm-event-common-Trade.schema.json"
TRADE_STATE_SCHEMA_FILENAME = "cdm-event-common-TradeState.schema.json"
TRADE_SCHEMA_PATH = SCHEMA_DIR / TRADE_SCHEMA_FILENAME


def get_draft4_validator_for_schema_file(filename: str) -> "Draft4Validator":
    """
    Load a FINOS CDM JSON Schema file from ``schemas/jsonschema/`` and return
    a Draft-04 validator with local ``$ref`` resolution (no network).

    Used by :func:`get_trade_schema_validator`, ``get_trade_state_schema_validator``,
    and the unified CDM structure validator (L1 / json_schema layer).
    """
    try:
        from jsonschema import Draft4Validator, RefResolver
    except Exception as exc:  # pragma: no cover - defensive, mirrored in caller
        raise RuntimeError(
            "jsonschema dependency missing. Install with: pip install jsonschema"
        ) from exc

    path = SCHEMA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"CDM schema not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        schema: dict[str, Any] = json.load(f)

    base_uri = SCHEMA_DIR.as_uri().rstrip("/") + "/"
    resolver = RefResolver(base_uri=base_uri, referrer=schema)

    return Draft4Validator(schema, resolver=resolver)


def get_trade_schema_validator() -> "Draft4Validator":
    """
    Load the official FINOS CDM Trade schema and return a Draft-04 validator
    configured to resolve local $ref references within the schema directory.
    """
    return get_draft4_validator_for_schema_file(TRADE_SCHEMA_FILENAME)


def get_trade_state_schema_validator() -> "Draft4Validator":
    """Load the official FINOS CDM TradeState root schema (Draft-04, local $ref)."""
    return get_draft4_validator_for_schema_file(TRADE_STATE_SCHEMA_FILENAME)

