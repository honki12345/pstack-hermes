#!/usr/bin/env python3
"""Assert every UPSTREAM_PIN declaration agrees, and is well-formed.

AGENTS.md and docs/agent/upstream-drift.md both state the rule - the pin must
stay in lock-step across ci.yml / release.yml / publish-dist.yml - but nothing
enforced it. A bump that missed one file would ship a package built from a pin
that CI never validated, and only a code review would notice. This gate makes
the documented rule machine-checked and exists specifically to de-risk the
re-pin procedure in docs/agent/upstream-drift.md.

Exit codes: 0 all declarations agree and are well-formed; 1 mismatch or
malformed; 2 unreadable input.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Every workflow that MUST declare the pin. Adding a workflow that builds or
# describes a build from the pin means adding it here (and to the runbook).
REQUIRED_DECLARERS = (
    "ci.yml",
    "release.yml",
    "publish-dist.yml",
)

# `UPSTREAM_PIN: <value>` at the start of a YAML mapping entry. References like
# `${{ env.UPSTREAM_PIN }}` do not match because the value must be token-shaped.
DECL_RE = re.compile(r"^\s*UPSTREAM_PIN:\s*(\S+)\s*$", re.MULTILINE)
# HERMES_PIN: full lowercase 40-char SHA, declared exactly once, in validate-gold.yml
HERMES_PIN_RE = re.compile(r"^\s*HERMES_PIN:\s*(\S+)", re.MULTILINE)
HERMES_PIN_WORKFLOW = "validate-gold.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def declarations(root: Path = ROOT) -> list[tuple[str, str]]:
    """Return (filename, value) for every UPSTREAM_PIN declaration in workflows."""
    out: list[tuple[str, str]] = []
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = wf.read_text(encoding="utf-8")
        for match in DECL_RE.finditer(text):
            out.append((wf.name, match.group(1)))
    return out


def check_hermes_pin(root: Path = ROOT) -> tuple[int, list[str]]:
    """Verify HERMES_PIN: full lowercase 40-char SHA, declared exactly once in
    validate-gold.yml, and named in CONTRIBUTING.md (issue #129 item 2)."""
    report: list[str] = []
    problems: list[str] = []

    hermes_wf = root / ".github" / "workflows" / HERMES_PIN_WORKFLOW
    if not hermes_wf.is_file():
        problems.append(f"HERMES_PIN: {HERMES_PIN_WORKFLOW} is missing; cannot verify pin")
        if problems:
            report.extend(f"FAIL: {p}" for p in problems)
            return 1, report
        return 0, report

    try:
        text = hermes_wf.read_text(encoding="utf-8")
    except OSError as exc:
        problems.append(f"cannot read {HERMES_PIN_WORKFLOW}: {exc}")
        report.extend(f"FAIL: {p}" for p in problems)
        return 1, report

    # Count ALL HERMES_PIN declarations in the document.
    all_pin_values: list[str] = []
    top_level_pin_values: list[str] = []

    try:
        composed = yaml.compose(text)
    except yaml.YAMLError as exc:
        problems.append(f"{HERMES_PIN_WORKFLOW} is not valid YAML: {exc}")
        composed = None

    def _walk_all_pins(node):
        """Recursively collect all HERMES_PIN values in the document."""
        if isinstance(node, yaml.MappingNode):
            for key_node, val_node in node.value:
                key = key_node.value if hasattr(key_node, "value") else str(key_node)
                if key == "HERMES_PIN":
                    v = val_node.value if hasattr(val_node, "value") else None
                    all_pin_values.append(v)
                _walk_all_pins(val_node)
                _walk_all_pins(key_node)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                _walk_all_pins(item)

    if composed is not None:
        _walk_all_pins(composed)

    # Top-level env: HERMES_PIN
    if composed is not None and isinstance(composed, yaml.MappingNode):
        for key_node, val_node in composed.value:
            key = key_node.value if hasattr(key_node, "value") else str(key_node)
            if key == "env" and isinstance(val_node, yaml.MappingNode):
                for ek, ev in val_node.value:
                    ek_name = ek.value if hasattr(ek, "value") else str(ek)
                    if ek_name == "HERMES_PIN":
                        v = ev.value if hasattr(ev, "value") else None
                        top_level_pin_values.append(v)

    hermes_values = top_level_pin_values

    # Reject overrides: if HERMES_PIN appears outside top-level env, fail.
    if len(all_pin_values) != len(top_level_pin_values):
        problems.append(
            f"HERMES_PIN declared outside top-level env: "
            f"{len(all_pin_values) - len(top_level_pin_values)} override(s) found"
        )

    # Also check duplicate keys anywhere in the document.
    # (reuse simple duplicate detection)
    dupes = []
    if composed is not None:

        def _dup_walk(n, path=""):
            """Recursively detect duplicate mapping keys."""
            if isinstance(n, yaml.MappingNode):
                seen: set[str] = set()
                for key_node, val_node in n.value:
                    key = key_node.value if hasattr(key_node, "value") else str(key_node)
                    if key in seen:
                        dupes.append(key)
                    seen.add(key)
                    _dup_walk(val_node)
                    _dup_walk(key_node)
            elif isinstance(n, yaml.SequenceNode):
                for item in n.value:
                    _dup_walk(item)

        _dup_walk(composed)
    if "HERMES_PIN" in dupes:
        problems.append(f"HERMES_PIN declared multiple times in {HERMES_PIN_WORKFLOW}")

    if len(hermes_values) != 1 and "HERMES_PIN" not in dupes:
        # If duplicates collapsed, hermes_values may still show 1; rely on dupes check above.
        if len(hermes_values) != 1:
            problems.append(
                f"HERMES_PIN: expected exactly one declaration in "
                f"{HERMES_PIN_WORKFLOW}, found {len(hermes_values)}"
            )

    if len(hermes_values) == 1:
        hv = hermes_values[0]
        if not isinstance(hv, str) or not SHA_RE.fullmatch(hv):
            problems.append(f"HERMES_PIN is not a full lowercase 40-char SHA: {hv!r}")
        else:
            report.append(f"  {HERMES_PIN_WORKFLOW}: HERMES_PIN {hv}")

    contributing = root / "CONTRIBUTING.md"
    if contributing.is_file():
        if "HERMES_PIN" not in contributing.read_text(encoding="utf-8"):
            problems.append("HERMES_PIN is not named in CONTRIBUTING.md")
    else:
        problems.append("CONTRIBUTING.md missing; cannot verify HERMES_PIN is named")

    if problems:
        report.extend(f"FAIL: {p}" for p in problems)
        return 1, report
    return 0, report


def check(root: Path = ROOT) -> tuple[int, list[str]]:
    """Run the check; return (exit_code, report lines)."""
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return 2, [f"error: no workflows directory at {workflows_dir}"]

    decls = declarations(root)
    if not decls:
        return 2, ["error: no UPSTREAM_PIN declaration found in any workflow"]

    report: list[str] = []
    problems: list[str] = []

    by_file: dict[str, list[str]] = {}
    for name, value in decls:
        by_file.setdefault(name, []).append(value)

    for name in REQUIRED_DECLARERS:
        values = by_file.get(name)
        if not values:
            problems.append(f"{name}: does not declare UPSTREAM_PIN")
        elif len(values) > 1:
            problems.append(f"{name}: declares UPSTREAM_PIN {len(values)} times")

    distinct = sorted({v for _, v in decls})
    for name, values in sorted(by_file.items()):
        report.append(f"  {name}: {', '.join(values)}")
    if len(distinct) > 1:
        problems.append(
            "UPSTREAM_PIN values disagree across workflows: " + ", ".join(distinct)
        )
    elif not SHA_RE.match(distinct[0]):
        problems.append(
            f"UPSTREAM_PIN is not a full lowercase 40-char SHA: {distinct[0]!r}"
        )

    if problems:
        report.extend(f"FAIL: {p}" for p in problems)
        return 1, report
    return 0, report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for check_pins."""
    ap = argparse.ArgumentParser(prog="check_pins.py", description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = ap.parse_args(argv)
    code, report = check(args.root)
    stream = sys.stderr if code else sys.stdout
    for line in report:
        print(line, file=stream)
    # HERMES_PIN (issue #129)
    hcode, hreport = check_hermes_pin(args.root)
    hstream = sys.stderr if hcode else sys.stdout
    for line in hreport:
        print(line, file=hstream)
    if code == 0 and hcode == 0:
        print("upstream pin: in lock-step across all workflows")
        print("hermes pin: verified")
    return code or hcode


if __name__ == "__main__":
    raise SystemExit(main())
