from .agha_noor import AghaNoorBrandAdapter
from .generic import GenericBrandAdapter
from .sapphire import SapphireBrandAdapter
from .shopify import ShopifyBrandAdapter


def get_adapter(adapter_key, brand):
    adapters = {
        "agha_noor": AghaNoorBrandAdapter,
        "generic": GenericBrandAdapter,
        "sapphire": SapphireBrandAdapter,
        "shopify": ShopifyBrandAdapter,
    }
    adapter_class = adapters.get(adapter_key)
    if not adapter_class:
        raise ValueError(f"Unknown adapter: {adapter_key}")
    return adapter_class(brand=brand)
