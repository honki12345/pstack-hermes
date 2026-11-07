"""Tell the shipping partner about a paid order. The partner endpoint is often unavailable."""

from fulfil import config, retry


class PartnerUnavailable(Exception):
    pass


class Partner:
    def __init__(self, failures=0):
        self.failures = failures
        self.delivered = []

    def post(self, order_id):
        if self.failures:
            self.failures -= 1
            raise PartnerUnavailable(order_id)
        self.delivered.append(order_id)


def notify(partner, order_id):
    """Returns True when the partner accepted it. A dropped delivery is not an order failure."""
    try:
        retry.call(
            lambda: partner.post(order_id),
            attempts=config.WEBHOOK_ATTEMPTS,
            retry_on=(PartnerUnavailable,),
            backoff=config.RETRY_BACKOFF_SECONDS,
        )
    except PartnerUnavailable:
        return False
    return True
