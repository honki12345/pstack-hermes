#!/usr/bin/env python3
"""Validate .github/dependabot.yml against the repo's documented rules.

AGENTS.md and .github/dependabot.yml document the contract (cooldowns, semver
tiers, groups with update-types, directory-scoped entries for composite actions).
This gate makes it machine-checked. Fixture-based, no network.

Exit codes: 0 valid; 1 invalid; 2 unreadable input.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _duplicate_keys(text: str) -> list[str]:
    """Return duplicate mapping keys found in a YAML document."""
    dupes: list[str] = []
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return dupes

    def walk(n):
        if isinstance(n, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, _ in n.value:
                key = key_node.value if hasattr(key_node, "value") else str(key_node)
                if key in seen:
                    dupes.append(key)
                seen.add(key)
            for key_node, val_node in n.value:
                walk(val_node)
                walk(key_node)
        elif isinstance(n, yaml.SequenceNode):
            for item in n.value:
                walk(item)

    if node is not None:
        walk(node)
    return dupes


# Ecosystems whose Dependabot cooldown options allow semver-* days.
# Per GitHub docs (dependabot options reference), semver-* cooldowns are
# supported only for a subset of ecosystems; github-actions, gitsubmodule,
# docker, terraform, etc. do NOT support them.
SEMVER_COOLDOWN_SUPPORTED: set[str] = {
    "uv", "pip", "npm", "bundler", "cargo", "composer", "gomod",
    "maven", "gradle", "nuget", "bun", "conda", "deno", "elm",
    "hex", "julia", "pub", "sbt", "swift",
}


def load_dependabot(root: Path = ROOT) -> tuple[int, dict | None, list[str]]:
    path = root / ".github" / "dependabot.yml"
    if not path.is_file():
        return 2, None, [f"error: no dependabot.yml at {path}"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return 2, None, [f"error: cannot read {path}: {exc}"]
    dupes = _duplicate_keys(text)
    if dupes:
        return 1, None, [f"error: duplicate keys in {path}: {sorted(set(dupes))}"]
    try:
        data = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        return 2, None, [f"error: cannot parse {path}: {exc}"]
    if not isinstance(data, dict):
        return 2, None, [f"error: {path} is not a mapping"]
    return 0, data, []


def composite_actions_with_uses(root: Path = ROOT) -> list[str]:
    """Return directory names under .github/actions whose action.yml/yaml uses
    an EXTERNAL action (a `uses:` step that is not a local `./...` reference)."""
    out: list[str] = []
    actions_dir = root / ".github" / "actions"
    if not actions_dir.is_dir():
        return out
    for action_dir in sorted(actions_dir.rglob(".")):
        if not action_dir.is_dir() or action_dir == actions_dir:
            continue
        # Only consider directories that directly contain action.yml/yaml
        if not any((action_dir / c).is_file() for c in ("action.yml", "action.yaml")):
            continue
        action_yml = None
        for cand in ("action.yml", "action.yaml"):
            p = action_dir / cand
            if p.is_file():
                action_yml = p
                break
        if action_yml is None:
            continue
        try:
            text = action_yml.read_text(encoding="utf-8")
        except OSError:
            continue
        has_external_uses = False
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            for step in (data.get("runs", {}) or {}).get("steps", []) or []:
                if isinstance(step, dict) and "uses" in step:
                    val = step["uses"]
                    if isinstance(val, str) and not val.startswith("./"):
                        has_external_uses = True
                        break
        # Only fall back to text scan when YAML parsing failed.
        if data is None:
            for line in text.splitlines():
                s = line.strip()
                if s and not s.startswith("#") and "uses:" in s:
                    # extract value
                    val = s.split("uses:", 1)[-1].strip()
                    if val and not val.startswith("./"):
                        has_external_uses = True
                        break
        if has_external_uses:
            rel = action_dir.relative_to(actions_dir)
            out.append(str(rel).replace(chr(92), "/"))
    return out


def check(root: Path = ROOT) -> tuple[int, list[str]]:
    code, data, errs = load_dependabot(root)
    if code != 0:
        return code, errs

    report: list[str] = []
    problems: list[str] = []

    updates = data.get("updates")
    if not isinstance(updates, list) or not updates:
        problems.append("updates: must be a non-empty list")
        return 1, problems

    # Collect scoped directories and their ecosystems, tracking singular vs plural.
    scoped: list[tuple[str, str, bool]] = []  # (directory, ecosystem, is_plural)
    for entry in updates:
        if isinstance(entry, dict):
            eco = entry.get("package-ecosystem")
            d = entry.get("directory")
            if isinstance(d, str) and isinstance(eco, str):
                scoped.append((d, eco, False))  # singular: no globs
            ds = entry.get("directories")
            if isinstance(ds, list):
                for dd in ds:
                    if isinstance(dd, str) and isinstance(eco, str):
                        scoped.append((dd, eco, True))  # plural: globs supported

    # Required composite actions covered by a github-actions entry.
    for name in composite_actions_with_uses(root):
        expected = f"/.github/actions/{name}"
        matching = []
        for d, e, is_plural in scoped:
            if e != "github-actions":
                continue
            # Singular 'directory' does not support wildcards; only exact match.
            # Plural 'directories' supports glob patterns.
            if d == expected:
                matching.append((d, e))
            elif is_plural and "*" in d and fnmatch.fnmatch(expected, d):
                # Only count if this d came from a 'directories' (plural) entry.
                # We track that by checking the original entry; here we approximate
                # by requiring the pattern to not be an exact singular path.
                matching.append((d, e))
        if not matching:
            problems.append(
                f"composite action {name!r} uses external actions but has no "
                f"directory-scoped github-actions dependabot entry ({expected})"
            )

    VALID_UPDATE_TYPES = {"major", "minor", "patch"}
    VALID_COOLDOWN_KEYS = {"default-days", "semver-major-days", "semver-minor-days", "semver-patch-days", "include", "exclude"}

    for i, entry in enumerate(updates):
        loc = f"updates[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{loc}: must be a mapping")
            continue
        eco = entry.get("package-ecosystem")
        directory = entry.get("directory")
        directories = entry.get("directories")
        if not isinstance(eco, str):
            problems.append(f"{loc}: package-ecosystem must be a string")
            continue
        has_dir = isinstance(directory, str)
        has_dirs = isinstance(directories, list) and len(directories) > 0 and all(isinstance(x, str) for x in directories)
        if not has_dir and not has_dirs:
            problems.append(f"{loc}: directory or directories must be a string/list of strings")
            continue

        cooldown = entry.get("cooldown")
        if not isinstance(cooldown, dict) or "default-days" not in cooldown:
            problems.append(f"{loc} ({eco}): cooldown.default-days missing")
        if isinstance(cooldown, dict):
            # unknown keys
            for key in cooldown:
                if key not in VALID_COOLDOWN_KEYS:
                    problems.append(f"{loc} ({eco}): unknown cooldown key {key!r}")
            # default-days value
            dd = cooldown.get("default-days")
            if not isinstance(dd, int) or isinstance(dd, bool) or dd < 1 or dd > 90:
                problems.append(f"{loc} ({eco}): cooldown.default-days must be an integer 1-90")
            # semver-* days only where supported
            for key in cooldown:
                if key.startswith("semver-") and key.endswith("-days"):
                    if eco not in SEMVER_COOLDOWN_SUPPORTED:
                        problems.append(
                            f"{loc} ({eco}): {key} is not supported "
                            f"for ecosystem {eco!r}"
                        )
                    else:
                        val = cooldown.get(key)
                        if not isinstance(val, int) or isinstance(val, bool) or val < 1 or val > 90:
                            problems.append(f"{loc} ({eco}): {key} must be an integer 1-90")

        groups = entry.get("groups")
        if groups is not None:
            if not isinstance(groups, dict):
                problems.append(f"{loc} ({eco}): groups must be a mapping")
            else:
                for gname, gspec in groups.items():
                    if not isinstance(gspec, dict) or "update-types" not in gspec:
                        problems.append(
                            f"{loc} ({eco}): group {gname!r} missing update-types"
                        )
                        continue
                    ut = gspec.get("update-types")
                    if not isinstance(ut, list) or not ut:
                        problems.append(
                            f"{loc} ({eco}): group {gname!r} update-types must be a non-empty list"
                        )
                        continue
                    if not all(isinstance(x, str) and x in VALID_UPDATE_TYPES for x in ut):
                        problems.append(
                            f"{loc} ({eco}): group {gname!r} update-types must contain "
                            f"only major/minor/patch"
                        )

    if problems:
        report.extend(f"FAIL: {p}" for p in problems)
        return 1, report
    report.append("dependabot.yml: all entries valid")
    return 0, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_dependabot.py", description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = ap.parse_args(argv)
    code, report = check(args.root)
    stream = sys.stderr if code else sys.stdout
    for line in report:
        print(line, file=stream)
    if code == 0:
        print("dependabot: valid")
    return code


if __name__ == "__main__":
    raise SystemExit(main())