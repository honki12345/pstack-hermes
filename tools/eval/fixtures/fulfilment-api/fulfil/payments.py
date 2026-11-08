"""Charging the card through the payment provider."""

from fulfil import config, idempotency, retry


class GatewayTimeout(Exception):
    pass


class Declined(Exception):
    pass


class Gateway:
    """In-memory stand-in for the provider. A repeated key returns the first charge."""

    def __init__(self, lost_replies=0):
        self.charges = {}
        self.lost_replies = lost_replies
        self._sequence = 0

    def charge(self, order_id, amount, key):
        charge = self.charges.get(key)
        if charge is None:
            self._sequence += 1
            charge = {"id": f"ch_{self._sequence:05d}", "order_id": order_id, "amount": amount}
            self.charges[key] = charge
        if self.lost_replies:
            self.lost_replies -= 1
            raise GatewayTimeout(order_id)
        return charge


def capture(gateway, order_id, amount):
    def attempt():
        key = idempotency.new_key()
        return gateway.charge(order_id, amount, key)

    return retry.call(
        attempt,
        attempts=config.PAYMENT_ATTEMPTS,
        retry_on=(GatewayTimeout,),
        backoff=config.RETRY_BACKOFF_SECONDS,
    )
