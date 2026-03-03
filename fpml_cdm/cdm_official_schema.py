from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas" / "jsonschema"
TRADE_SCHEMA_FILENAME = "cdm-event-common-Trade.schema.json"
TRADE_SCHEMA_PATH = SCHEMA_DIR / TRADE_SCHEMA_FILENAME


def get_trade_schema_validator() -> "Draft4Validator":
    """
    Load the official FINOS CDM Trade schema and return a Draft-04 validator
    configured to resolve local $ref references within the schema directory.
    """
    try:
        from jsonschema import Draft4Validator, RefResolver
    except Exception as exc:  # pragma: no cover - defensive, mirrored in caller
        raise RuntimeError(
            "jsonschema dependency missing. Install with: pip install jsonschema"
        ) from exc

    if not TRADE_SCHEMA_PATH.exists():
        raise FileNotFoundError(f"CDM Trade schema not found: {TRADE_SCHEMA_PATH}")

    with TRADE_SCHEMA_PATH.open("r", encoding="utf-8") as f:
        schema: dict[str, Any] = json.load(f)

    # Configure a resolver so that relative $ref values like
    # "cdm-product-template-TradeLot.schema.json" are resolved against the
    # local jsonschema directory, using file:// URLs only (no network).
    base_uri = SCHEMA_DIR.as_uri().rstrip("/") + "/"
    resolver = RefResolver(base_uri=base_uri, referrer=schema)

    return Draft4Validator(schema, resolver=resolver)

