"""Every successful charge, and the checks finance runs on them."""


class Ledger:
    def __init__(self):
        self.entries = []

    def record(self, charge):
        self.entries.append(dict(charge))

    def charges_for(self, order_id):
        return [entry for entry in self.entries if entry["order_id"] == order_id]

    def duplicates(self):
        """Orders charged more than once, with the charge ids."""
        seen = {}
        for entry in self.entries:
            seen.setdefault(entry["order_id"], []).append(entry["id"])
        return {order_id: ids for order_id, ids in seen.items() if len(ids) > 1}

    def revenue(self):
        return sum(entry["amount"] for entry in self.entries)
