"""check_pins must fail when the pin drifts, is missing, or is malformed.

The gate exists so a bump that misses a declaring workflow cannot reach a
release (the failure mode called out in CONTRIBUTING.md and the re-pin runbook).
These tests pin exactly that property, plus the false-positive case a naive
implementation would hit: a `${{ env.UPSTREAM_PIN }}` reference is not a
declaration and must not be counted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_pins  # noqa: E402

SHA = "93b00b89ef425a9c1bac0d0b317dfc49c930ac99"
OTHER = "4e5b1cf2ccb0ea3716f08c8ee0a5856b5ab93536"
REQUIRED = ("ci.yml",)


def _decl(value: str) -> str:
    return f"name: x\nenv:\n  UPSTREAM_PIN: {value}\n"


def _tree(tmp_path: Path, bodies: dict) -> Path:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name, body in bodies.items():
        (wf / name).write_text(body, encoding="utf-8")
    return tmp_path


def _all(value: str) -> dict:
    return {name: _decl(value) for name in REQUIRED}


def test_all_required_declarers_agree_passes(tmp_path):
    code, _ = check_pins.check(_tree(tmp_path, _all(SHA)))
    assert code == 0


def test_one_disagreeing_value_fails(tmp_path):
    bodies = _all(SHA)
    bodies["extra.yml"] = _decl(OTHER)
    code, report = check_pins.check(_tree(tmp_path, bodies))
    assert code == 1
    assert any("disagree" in line for line in report)


def test_missing_declaration_fails(tmp_path):
    bodies = {"extra.yml": _decl(SHA)}
    code, report = check_pins.check(_tree(tmp_path, bodies))
    assert code == 1
    assert any("ci.yml" in line and "does not declare" in line for line in report)


def test_duplicate_declaration_in_one_file_fails(tmp_path):
    bodies = _all(SHA)
    bodies["ci.yml"] = _decl(SHA) + _decl(SHA)
    code, report = check_pins.check(_tree(tmp_path, bodies))
    assert code == 1
    assert any("2 times" in line for line in report)


def test_malformed_value_fails(tmp_path):
    code, report = check_pins.check(_tree(tmp_path, _all("abc123")))
    assert code == 1
    assert any("40-char SHA" in line for line in report)


def test_expression_reference_is_not_counted_as_a_declaration(tmp_path):
    bodies = _all(SHA)
    bodies["notes.yml"] = "name: n\nenv:\n  UPSTREAM_PIN: ${{ env.UPSTREAM_PIN }}\n"
    root = _tree(tmp_path, bodies)
    decls = check_pins.declarations(root)
    assert not any(name == "notes.yml" for name, _ in decls)
    code, _ = check_pins.check(root)
    assert code == 0


def test_no_declarations_at_all_is_a_tool_error(tmp_path):
    root = _tree(tmp_path, {"ci.yml": "name: x\n"})
    code, report = check_pins.check(root)
    assert code == 2
    assert any("no UPSTREAM_PIN declaration" in line for line in report)
