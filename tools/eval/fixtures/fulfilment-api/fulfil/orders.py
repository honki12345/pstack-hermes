"""The order lifecycle: place, cancel."""

from fulfil import inventory, notifier, payments


class Service:
    def __init__(self, warehouse, gateway, ledger, partner):
        self.warehouse = warehouse
        self.gateway = gateway
        self.ledger = ledger
        self.partner = partner
        self.orders = {}

    def place(self, order_id, lines, amount):
        try:
            self.warehouse.reserve(order_id, lines)
        except inventory.OutOfStock as error:
            self.orders[order_id] = {"state": "rejected", "reason": f"out of stock: {error}"}
            return self.orders[order_id]
        try:
            charge = payments.capture(self.gateway, order_id, amount)
        except (payments.GatewayTimeout, payments.Declined) as error:
            self.warehouse.release(order_id)
            self.orders[order_id] = {"state": "failed", "reason": type(error).__name__}
            return self.orders[order_id]
        self.ledger.record(charge)
        self.warehouse.commit(order_id)
        shipped = notifier.notify(self.partner, order_id)
        self.orders[order_id] = {"state": "paid", "charge": charge["id"], "notified": shipped}
        return self.orders[order_id]

    def cancel(self, order_id):
        """Cancel an order that is still waiting; paid orders go through refunds instead."""
        order = self.orders.get(order_id)
        if order is not None and order["state"] == "paid":
            return {"state": "paid", "reason": "refund required"}
        self.warehouse.release(order_id)
        self.orders[order_id] = {"state": "cancelled"}
        return self.orders[order_id]

    def hold(self, order_id, lines):
        """Reserve stock for a basket that has not been paid yet."""
        self.warehouse.reserve(order_id, lines)
        self.orders[order_id] = {"state": "held"}
