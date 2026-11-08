"""Request handling: payload in, order summary out."""

from fulfil import payments


def post_order(service, payload):
    lines = {item["sku"]: item["quantity"] for item in payload["items"]}
    try:
        result = service.place(payload["order_id"], lines, payload["amount"])
    except payments.Declined:
        return {"status": 402}
    return {"status": 200 if result["state"] == "paid" else 409, "order": result}


def post_hold(service, payload):
    lines = {item["sku"]: item["quantity"] for item in payload["items"]}
    service.hold(payload["order_id"], lines)
    return {"status": 200}


def post_cancel(service, order_id):
    return {"status": 200, "order": service.cancel(order_id)}
