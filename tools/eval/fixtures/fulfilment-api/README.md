# Fulfilment API

Order fulfilment for a small online shop. It reserves stock, charges the card, records the
charge in a ledger and tells the shipping partner. A sweep job runs every few minutes.

## Flow

1. `fulfil/api.py` receives a request and calls `fulfil/orders.py`.
2. `orders.place` reserves stock in `fulfil/inventory.py`, charges through `fulfil/payments.py`
   and records the charge in `fulfil/ledger.py`.
3. A payment that fails releases the reservation.
4. `fulfil/notifier.py` posts a webhook to the shipping partner. The partner is flaky; a
   delivery is retried a few times and then dropped with a warning.
5. `fulfil/scheduler.py` expires stale reservations and runs the nightly reconcile.

## Operating notes

- Production writes to `logs/ops.log`; the last days are kept in this checkout.
- `python3 checks/run.py` replays a few order scenarios against in-memory fakes.
- Amounts are integer cents. Time comes from `fulfil/clock.py`, so scenarios are repeatable.
