"""Cart maths. Every amount is an integer number of cents."""

FREE_SHIPPING_FROM = 5000
FLAT_SHIPPING = 499


def subtotal(lines):
    return sum(line["price_cents"] * line["quantity"] for line in lines)


def coupon_discount(amount, coupon):
    if coupon is None:
        return 0
    if coupon["kind"] == "percent":
        return amount * coupon["value"] // 100
    if coupon["kind"] == "fixed":
        return min(coupon["value"], amount)
    raise ValueError(f"unknown coupon kind: {coupon['kind']}")


def shipping(amount):
    return 0 if amount >= FREE_SHIPPING_FROM else FLAT_SHIPPING


def order_total(lines, coupon=None):
    amount = subtotal(lines)
    discount = coupon_discount(amount, coupon)
    return {
        "subtotal": amount,
        "discount": discount,
        "shipping": shipping(amount),
        "total": amount - discount + shipping(amount),
    }
