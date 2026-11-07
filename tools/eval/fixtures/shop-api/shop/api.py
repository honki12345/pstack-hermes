"""Request handling: payload in, order summary out."""

from shop import catalog, pricing


def build_lines(items):
    return [
        {"sku": item["sku"], "price_cents": catalog.price_of(item["sku"]), "quantity": item["quantity"]}
        for item in items
    ]


def create_order(payload):
    lines = build_lines(payload["items"])
    summary = pricing.order_total(lines, payload.get("coupon"))
    return {"lines": lines, **summary}
