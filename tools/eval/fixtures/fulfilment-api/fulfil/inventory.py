"""Stock and the reservations held against it."""

from fulfil import clock, config


class OutOfStock(Exception):
    pass


class Warehouse:
    def __init__(self, stock):
        self.stock = dict(stock)
        self.reservations = {}

    def reserve(self, order_id, lines):
        for sku, quantity in lines.items():
            if self.stock.get(sku, 0) < quantity:
                raise OutOfStock(sku)
        for sku, quantity in lines.items():
            self.stock[sku] -= quantity
        self.reservations[order_id] = {
            "lines": dict(lines),
            "expires_at": clock.now() + config.RESERVATION_TTL_SECONDS,
        }

    def release(self, order_id):
        reservation = self.reservations.get(order_id)
        if reservation is None:
            return False
        for sku, quantity in reservation["lines"].items():
            self.stock[sku] += quantity
        return True

    def commit(self, order_id):
        del self.reservations[order_id]

    def expire_stale(self):
        expired = [
            order_id for order_id, reservation in self.reservations.items()
            if reservation["expires_at"] <= clock.now()
        ]
        for order_id in expired:
            self.release(order_id)
            del self.reservations[order_id]
        return expired

    def low_stock(self):
        return sorted(sku for sku, count in self.stock.items() if count <= config.LOW_STOCK_THRESHOLD)
