"""Collects the poteto-delegation-gate cases under tools/tests, the only tree CI's pytest scans.

The cases live beside the plugin in hermes-plugin/poteto-delegation-gate/tests/test_gate.py, so
`pytest hermes-plugin/poteto-delegation-gate/tests` runs the same ones. This wrapper loads that
file the way test_pstack_delegation_routing.py loads routing.py and re-exports every name, so the
fixtures and parametrized cases are collected here too.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CASES = (
    Path(__file__).resolve().parents[2]
    / "hermes-plugin"
    / "poteto-delegation-gate"
    / "tests"
    / "test_gate.py"
)
_spec = importlib.util.spec_from_file_location("poteto_delegation_gate_cases", CASES)
assert _spec is not None and _spec.loader is not None
_cases = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _cases
_spec.loader.exec_module(_cases)

globals().update({k: v for k, v in vars(_cases).items() if not k.startswith("__")})
