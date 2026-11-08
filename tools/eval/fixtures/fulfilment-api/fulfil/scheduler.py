"""Periodic jobs: expire reservations, reconcile stock and charges."""


def sweep(service):
    return service.warehouse.expire_stale()


def reconcile_stock(service, counted):
    """Check the shelf count against the system's stock. Returns the skus that drift."""
    return {
        sku: (service.warehouse.stock.get(sku, 0), count)
        for sku, count in counted.items()
        if service.warehouse.stock.get(sku, 0) != count
    }


def reconcile_charges(service):
    """Orders the provider charged more than once, with the charge ids."""
    seen = {}
    for charge in service.gateway.charges.values():
        seen.setdefault(charge["order_id"], []).append(charge["id"])
    return {order_id: ids for order_id, ids in seen.items() if len(ids) > 1}
