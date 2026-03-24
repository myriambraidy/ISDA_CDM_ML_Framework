from .registry import (
    FX_ADAPTER_REGISTRY,
    FxAdapterSpec,
    SUPPORTED_FX_ADAPTER_IDS,
    describe_fx_adapter_registry,
    detect_fx_adapter_product,
    fpml_trade_product_local_names,
    get_fx_adapter_spec,
    iter_fx_adapter_ids_by_priority,
)

__all__ = [
    "FX_ADAPTER_REGISTRY",
    "FxAdapterSpec",
    "SUPPORTED_FX_ADAPTER_IDS",
    "describe_fx_adapter_registry",
    "detect_fx_adapter_product",
    "fpml_trade_product_local_names",
    "get_fx_adapter_spec",
    "iter_fx_adapter_ids_by_priority",
]
