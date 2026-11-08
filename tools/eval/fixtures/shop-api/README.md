# Shop Apii

Small order-pricing service for a neighbourhood shop. It turns a cart and an
optional coupon into the amount to charge.

## Pricing rules

- A line costs `price_cents * quantity`.
- A percent coupon takes that share off the cart subtotal. A fixed coupon takes
  off its value, but never more than the subtotal.
- Shipping is a flat 499 cents. It is free when the subtotal after the coupon is
  at least 5000 cents.
- All amounts are integer cents.

## Layout

- `shop/catalog.py` - product prices
- `shop/pricing.py` - subtotal, coupon and shipping maths
- `shop/api.py` - turns a request payload into an order summary
- `checks/run.py` - runs the sample orders, `python3 checks/run.py`
