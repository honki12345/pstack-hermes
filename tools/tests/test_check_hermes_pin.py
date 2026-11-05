"""check_pins: HERMES_PIN checks (issue #129 item 2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_pins  # noqa: E402

SHA = "f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab"


def _tree(tmp_path, hermes_pin=None, contributing=True, validate_gold=True):
    root = tmp_path
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    if validate_gold:
        body = "name: x\nenv:\n"
        if hermes_pin is not None:
            body += f"  HERMES_PIN: {hermes_pin}\n"
        (wf / "validate-gold.yml").write_text(body, encoding="utf-8")
    if contributing:
        text = "# Contributing\n" + ("HERMES_PIN\n" if True else "")
        (root / "CONTRIBUTING.md").write_text(text, encoding="utf-8")
    return root


def test_valid_hermes_pin_passes(tmp_path):
    code, _ = check_pins.check_hermes_pin(_tree(tmp_path, SHA))
    assert code == 0


def test_missing_validate_gold_fails(tmp_path):
    code, report = check_pins.check_hermes_pin(_tree(tmp_path, SHA, validate_gold=False))
    assert code == 1
    assert any("missing" in line for line in report)


def test_missing_contributing_fails(tmp_path):
    code, report = check_pins.check_hermes_pin(_tree(tmp_path, SHA, contributing=False))
    assert code == 1
    assert any("CONTRIBUTING.md missing" in line for line in report)


def test_wrong_count_fails(tmp_path):
    root = _tree(tmp_path, SHA)
    # add a second declaration
    (root / ".github" / "workflows" / "validate-gold.yml").write_text(
        f"name: x\nenv:\n  HERMES_PIN: {SHA}\n  HERMES_PIN: {SHA}\n", encoding="utf-8"
    )
    code, report = check_pins.check_hermes_pin(root)
    assert code == 1
    assert any("declared multiple times" in line or "expected exactly one" in line for line in report)


def test_malformed_sha_fails(tmp_path):
    code, report = check_pins.check_hermes_pin(_tree(tmp_path, "abc123"))
    assert code == 1
    assert any("40-char SHA" in line for line in report)


def test_unnamed_in_contributing_fails(tmp_path):
    root = _tree(tmp_path, SHA)
    (root / "CONTRIBUTING.md").write_text("# Contributing\nno mention\n", encoding="utf-8")
    code, report = check_pins.check_hermes_pin(root)
    assert code == 1
    assert any("not named in CONTRIBUTING.md" in line for line in report)


def test_trailing_comment_accepted(tmp_path):
    root = _tree(tmp_path, SHA)
    (root / ".github" / "workflows" / "validate-gold.yml").write_text(
        f"name: x\nenv:\n  HERMES_PIN: {SHA}  # v0.22.6\n", encoding="utf-8"
    )
    code, _ = check_pins.check_hermes_pin(root)
    assert code == 0