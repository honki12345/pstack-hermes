"""Keys that let the payment provider recognise a repeated request."""

import itertools

_counter = itertools.count(1)


def new_key():
    return f"k-{next(_counter):05d}"


def key_for(order_id):
    return f"order-{order_id}"
