"""A settable clock so reservations and sweeps are repeatable."""

_now = 1_000_000.0


def now():
    return _now


def advance(seconds):
    global _now
    _now += seconds


def reset(start=1_000_000.0):
    global _now
    _now = start
