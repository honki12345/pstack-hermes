#!/usr/bin/env python3
"""CI scanner gate: assert the converted package holds its scanner-clean invariants.

Replaces the inline heredoc that used to live in ci.yml. Ban definitions live
in bans.py — the single source of truth shared with tools/validate.py — and
the scan covers every UTF-8-decodable file, mirroring the real hermes
install scanner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bans import find_violations, iter_text_files  # noqa: E402


def run(pkg: Path) -> int:
    """0 = clean, 1 = banned constructs present, 2 = package dir missing."""
    if not pkg.is_dir():
        print(f"scanner gate: package dir not found: {pkg}", file=sys.stderr)
        return 2
    scanned = sum(1 for _ in iter_text_files(pkg))
    violations = find_violations(pkg)
    for v in violations:
        print(f"BANNED {v}")
    if violations:
        print(f"scanner gate: {len(violations)} banned construct(s) present", file=sys.stderr)
        return 1
    print(f"scanner-clean invariants: OK ({scanned} text files scanned)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", required=True, help="converted package dir (e.g. the repo root)")
    args = ap.parse_args()
    return run(Path(args.package).resolve())


if __name__ == "__main__":
    sys.exit(main())
