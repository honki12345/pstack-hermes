"""Runs the sample orders and prints every mismatch. Exit code 1 when one differs."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shop.api import create_order  # noqa: E402

CASES = [
    ("one mug, no coupon", {"items": [{"sku": "mug", "quantity": 1}]}, 1800 + 499),
    ("three teas, no coupon", {"items": [{"sku": "tea", "quantity": 3}]}, 3600 + 499),
    ("grinder, no coupon", {"items": [{"sku": "grinder", "quantity": 1}]}, 5500),
    (
        "kettle and tea, fixed coupon",
        {
            "items": [{"sku": "kettle", "quantity": 1}, {"sku": "tea", "quantity": 1}],
            "coupon": {"kind": "fixed", "value": 500},
        },
        5700 - 500,
    ),
    (
        "grinder, 20 percent coupon",
        {"items": [{"sku": "grinder", "quantity": 1}], "coupon": {"kind": "percent", "value": 20}},
        4400 + 499,
    ),
]


def main():
    failed = 0
    for name, payload, expected in CASES:
        got = create_order(payload)["total"]
        if got != expected:
            failed += 1
            print(f"MISMATCH {name}: expected {expected}, got {got}")
    print(f"{len(CASES) - failed}/{len(CASES)} orders match")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
