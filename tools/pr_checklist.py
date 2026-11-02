#!/usr/bin/env python3
"""Check a pull-request description against the tree that PR proposes.

Advisory by design: this reports contradictions, it does not enforce them. The
enforcing gate is the CI `build` job. Exit codes:

  0  every machine-checkable claim in the PR body holds (or there was nothing
     to check - a PR that does not touch the generated package)
  1  a verifiable claim is contradicted (wrong pin, or a checked box with no
     evidence pasted)
  2  the input could not be read/parsed at all

Usage:
  pr_checklist.py <pr-body-file> [--provenance .build-provenance.txt]
  pr_checklist.py --help

The generated package (root `plugin.json`, `skills/`, `config/`, `assets/`,
`README.md`, `.build-provenance.txt`) is build output. The single most important
thing a
contributor can get wrong is claiming a rebuild from a pin that is not the one
their tree was actually built from, so that comparison is the core check here.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FENCE_RE = re.compile(
    r"^```yaml\s*\n(.*?)^```\s*$",
    re.MULTILINE | re.DOTALL,
)
CHECKED_BOX_RE = re.compile(r"^\s*[-*]\s*\[x\]\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
# Evidence is either an explicit "Evidence:" label or a fenced code block
# (which is what .github/PULL_REQUEST_TEMPLATE.md actually asks for).
EVIDENCE_HINT_RE = re.compile(r"evidence\s*:|^\s*```", re.IGNORECASE | re.MULTILINE)


PLACEHOLDER_RE = re.compile(r"<[^>]*>|\b(your|fill|todo|tbd)\b", re.IGNORECASE)


def parse_provenance_block(body: str) -> dict[str, str] | None:
    """Return the fenced ```yaml provenance block as a flat dict, or None.

    A key left as an unfilled template placeholder (``<...>``, ``your ...``)
    is treated as absent rather than as a wrong value, so a contributor who
    simply forgot the block gets "missing", not a bogus mismatch."""
    for raw in FENCE_RE.findall(body):
        # Only the block that declares provenance keys is ours; a PR may paste
        # other yaml (frontmatter examples, config snippets).
        if "source_commit" not in raw:
            continue
        data: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            value = value.strip().strip("'\"")
            if not value or PLACEHOLDER_RE.search(value):
                continue
            data[key.strip()] = value
        return data
    return None


def read_provenance_file(path: Path) -> dict[str, str]:
    """Parse `key: value` lines out of a .build-provenance.txt file."""
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        data[key.strip()] = value.strip()
    return data


def strip_html_comments(body: str) -> str:
    """Remove <!-- ... --> blocks. The template embeds its instructions as HTML
    comments, and a checked box plus its placeholder output live inside those
    comments in the template itself - not a contributor claim."""
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)


def check_box_evidence(body: str) -> list[str]:
    """A checked box with no pasted evidence proves nothing - the template says
    so itself. Flag every checked box whose own section carries no evidence."""
    problems: list[str] = []
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = CHECKED_BOX_RE.match(line)
        if not m:
            continue
        # Scan forward until the next checkbox or heading: that window is this
        # box's own evidence slot. If it ends without an evidence marker (or at
        # the next checkbox/heading immediately), the box is bare.
        found = False
        for follow in lines[i + 1 :]:
            if CHECKED_BOX_RE.match(follow) or follow.strip().startswith("#"):
                break
            if EVIDENCE_HINT_RE.search(follow):
                found = True
                break
        if not found:
            problems.append(
                f"checked box has no evidence pasted: {m.group(1)[:70]!r}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="pr_checklist.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("body", type=Path, help="PR description file ('-' for stdin)")
    ap.add_argument(
        "--provenance",
        type=Path,
        default=Path(".build-provenance.txt"),
        help="provenance file from the tree this PR proposes (default: %(default)s)",
    )
    args = ap.parse_args(argv)

    if str(args.body) == "-":
        body = sys.stdin.read()
    else:
        if not args.body.is_file():
            print(f"error: PR body file not found: {args.body}", file=sys.stderr)
            return 2
        body = args.body.read_text(encoding="utf-8", errors="replace")

    failures: list[str] = []
    notes: list[str] = []
    # Evidence is only meaningful for the Verification section. Boxes elsewhere
    # (e.g. "Affected surface") are scope declarations, not claims backed by
    # pasted output, so requiring evidence for them would be noise.
    verification_only = "--all-boxes" not in argv

    claimed = parse_provenance_block(body)
    actual = read_provenance_file(args.provenance)

    if actual.get("source_commit"):
        if claimed is None or "source_commit" not in claimed:
            # Not necessarily an error: a PR that only touches docs need not
            # rebuild. Report as a note so the contributor knows it was skipped.
            notes.append(
                "no filled-in ```yaml provenance``` block in the PR body; pin "
                f"comparison skipped (tree records source_commit "
                f"{actual['source_commit'][:12]})"
            )
        elif claimed.get("source_commit") != actual["source_commit"]:
            failures.append(
                "provenance block claims source_commit "
                f"{claimed.get('source_commit')!r} but the tree records "
                f"{actual['source_commit']!r} - rebuild from the current pin, "
                "or correct the block"
            )
        else:
            print(f"pin matches: {actual['source_commit'][:12]}")
        converter_actual = actual.get("converter", "")
        converter_claimed = claimed.get("converter", "") if claimed else ""
        if converter_actual and converter_claimed:
            if converter_actual != converter_claimed:
                failures.append(
                    f"provenance block claims converter {converter_claimed!r} but "
                    f"the tree records {converter_actual!r}"
                )
    elif not args.provenance.is_file():
        notes.append(f"provenance file absent at {args.provenance}; pin check skipped")

    body_wo_comments = strip_html_comments(body)
    if not verification_only:
        failures.extend(check_box_evidence(body_wo_comments))
    else:
        section = re.search(r"^##\s+Verification\b(.*?)(?=^##\s|\Z)",
                            body_wo_comments, re.MULTILINE | re.DOTALL)
        if section:
            failures.extend(check_box_evidence(section.group(1)))
        else:
            notes.append("no '## Verification' section found; box-evidence check skipped")

    for note in notes:
        print(f"note: {note}")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)

    if failures:
        print(f"\npr-checklist: {len(failures)} contradiction(s)", file=sys.stderr)
        return 1
    print("pr-checklist: no contradictions found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
