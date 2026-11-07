"""Product prices in cents."""

PRICES = {
    "tea": 1200,
    "mug": 1800,
    "kettle": 4500,
    "grinder": 5500,
}


def price_of(sku):
    try:
        return PRICES[sku]
    except KeyError:
        raise ValueError(f"unknown sku: {sku}") from None
