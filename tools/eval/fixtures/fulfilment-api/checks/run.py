"""Replays order scenarios against in-memory fakes and prints every mismatch.

Exit code 1 when one differs.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fulfil import api, clock, config, notifier, orders, payments, scheduler  # noqa: E402
from fulfil.inventory import Warehouse  # noqa: E402
from fulfil.ledger import Ledger  # noqa: E402

ITEMS = {"items": [{"sku": "mug", "quantity": 2}]}


def build(stock=None, lost_replies=0, partner_failures=0):
    clock.reset()
    return orders.Service(
        Warehouse(stock or {"mug": 10, "tea": 20}),
        payments.Gateway(lost_replies=lost_replies),
        Ledger(),
        notifier.Partner(failures=partner_failures),
    )


def plain_order():
    service = build()
    result = api.post_order(service, {"order_id": "o-1", "amount": 3600, **ITEMS})
    ok = result["status"] == 200 and service.warehouse.stock["mug"] == 8
    return ok, f"expected a paid order and 8 mugs left, got {result['status']} and "\
               f"{service.warehouse.stock['mug']}"


def out_of_stock():
    service = build(stock={"mug": 1})
    result = api.post_order(service, {"order_id": "o-2", "amount": 3600, **ITEMS})
    return result["status"] == 409 and service.warehouse.stock["mug"] == 1, \
        f"expected a rejected order with the stock untouched, got {result}"


def dropped_webhook():
    service = build(partner_failures=config.WEBHOOK_ATTEMPTS)
    result = api.post_order(service, {"order_id": "o-3", "amount": 3600, **ITEMS})
    order = result["order"]
    return result["status"] == 200 and order["notified"] is False, \
        f"a dropped webhook must not fail the order, got {result}"


def hold_expires():
    service = build()
    api.post_hold(service, {"order_id": "o-6", **ITEMS})
    clock.advance(config.RESERVATION_TTL_SECONDS + 1)
    expired = scheduler.sweep(service)
    return expired == ["o-6"] and service.warehouse.stock["mug"] == 10, \
        f"expected the hold to expire and 10 mugs back, got {expired} and "\
        f"{service.warehouse.stock['mug']}"


def timeout_then_retry():
    service = build(lost_replies=1)
    api.post_order(service, {"order_id": "o-4", "amount": 3600, **ITEMS})
    duplicates = scheduler.reconcile_charges(service)
    return not duplicates, f"the provider charged o-4 more than once: {duplicates}"


def cancel_then_expiry():
    service = build()
    api.post_hold(service, {"order_id": "o-5", **ITEMS})
    api.post_cancel(service, "o-5")
    clock.advance(config.RESERVATION_TTL_SECONDS + 1)
    scheduler.sweep(service)
    drift = scheduler.reconcile_stock(service, {"mug": 10, "tea": 20})
    return not drift, f"shelf count differs from the system (system, counted): {drift}"


SCENARIOS = [
    ("one plain order", plain_order),
    ("out of stock is rejected", out_of_stock),
    ("a dropped webhook keeps the order", dropped_webhook),
    ("an unpaid hold expires", hold_expires),
    ("timeout then retry charges once", timeout_then_retry),
    ("cancel then expiry restores stock once", cancel_then_expiry),
]


def main():
    failed = 0
    for name, scenario in SCENARIOS:
        ok, detail = scenario()
        if not ok:
            failed += 1
            print(f"MISMATCH {name}: {detail}")
    print(f"{len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios match")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
