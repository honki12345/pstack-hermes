#!/usr/bin/env python3
"""Model-slug drift check: configured panel/config slugs vs the provider catalog.

The panel (tools/assets/model-panel.json) and the shipped config
(config/models.json) name provider model slugs that were verified
against the catalog when written. Providers deprecate slugs (free tiers
especially), so this tool re-verifies them periodically:

    uv run --frozen tools/slug_drift.py                       # live catalog
    uv run --frozen tools/slug_drift.py --catalog-file <json> # offline/tests

With --prose, also scans skill markdown for backtick-quoted model-slug
defaults (e.g. `claude-fable-5-1-thinking-max`) that were written as
fallback documentation. Those are not in the panel/config, so they are
never re-verified by the default check; a silently-deprecated default slug
in prose would surface as a 404 in a live run. The prose scan is opt-in
because it is a regex heuristic (it cannot tell a model slug from a
backtick-quoted shell token) and is therefore reported separately.

Exit codes: 0 = every configured slug present; 1 = missing slug(s);
2 = tool error (catalog unreachable or unparseable). Run it by hand before
a panel refresh; no scheduled workflow invokes it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent  # tools/
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_PANEL = SCRIPT_DIR / "assets" / "model-panel.json"
DEFAULT_CONFIG = REPO_ROOT / "config" / "models.json"
DEFAULT_SKILLS_DIR = REPO_ROOT / "skills"
DEFAULT_CATALOG_URL = "https://openrouter.ai/api/v1/models"
USER_AGENT = "pstack-hermes-slug-drift"
SELECTOR = "inherit-parent"  # always valid; not a provider slug

# Backtick-quoted tokens that look like provider model slugs. The prose
# defaults in this port (claude-fable-5-1-thinking-max, gpt-5.6-sol-max,
# grok-4.6-fast-xhigh, claude-opus-5-thinking-xhigh) are not OpenRouter
# vendor/model format, so they are reported as "prose-only" rather than
# silently skipped.
#
# Heuristic: a model slug has a provider prefix, a version (digits with
# optional dots/dashes), and an optional suffix. This is intentionally
# conservative — it is a regex, not an LLM, and false positives (e.g.
# `properties_json`, `gpt-4`, `prompt`) are worse than false negatives
# because they flood the drift alert. Tokens without a version component
# are skipped; tokens that look like real slugs but are not in the
# vendor/model format are reported as prose-only defaults.
#
# The version component must appear somewhere in the token: either in the
# provider part (e.g. claude-opus-5-thinking-xhigh) or in the model part
# after a slash (e.g. vendor/missing-1.0). Two patterns are recognized:
# 1. Model-family slugs: `claude-opus-5-thinking-xhigh`, `gpt-5.6-sol-max`
#    (vendor prefix + version + suffix, no slash)
# 2. Vendor/model slugs: `z-ai/glm-5.2`, `meituan/longcat-2.0`
#    (vendor/model-version format matching OpenRouter catalog entries)
# This is a single regex rather than two alternations so the same match
# object carries the whole slug.
PROSE_SLUG_RE = re.compile(
    r"`(?P<slug>"
    r"(?:gpt|claude|grok|gemini|llama|mistral|qwen|deepseek|phi|"
    r"command|sonnet|opus|haiku|nova|flash|pro|thinking|fast|slow|"
    r"xhigh|xlow|mini|max|turbo|preview|latest)"
    r"[-\w/.]*\d[-\w/.]*"
    r"|(?:[a-z][a-z0-9-]*/[a-z][a-z0-9-]*[-\w/.]*\d[-\w/.]*)"
    r")`"
)


def collect_slugs(path: Path) -> dict[str, list[str]]:
    """role -> provider slugs (arrays flattened; selectors/non-strings skipped)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    roles = payload.get("roles", {})
    if not isinstance(roles, dict):
        raise ValueError(f"{path}: 'roles' is not an object")
    out: dict[str, list[str]] = {}
    for role, value in roles.items():
        raw = value if isinstance(value, list) else [value]
        out[str(role)] = [s for s in raw if isinstance(s, str) and s != SELECTOR]
    return out


def catalog_ids(payload: dict) -> set[str]:
    """Extract model ids from an OpenRouter-style {"data": [{"id": ...}]} payload.

    An empty id set raises: a decodable but model-less catalog is a provider
    hiccup, and reporting every slug missing off it would be a false alert.
    """
    data = payload.get("data", [])
    if not isinstance(data, list):
        raise ValueError("catalog payload 'data' is not a list")
    ids = {m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)}
    if not ids:
        # Fail closed: an empty catalog means the fetch went sideways (transient
        # provider hiccup, paginated empty page). Reporting every configured slug
        # as missing would post a false actionable alert — treat it as an
        # unavailable catalog instead.
        raise ValueError("catalog payload contains no model ids (empty or malformed 'data')")
    return ids


def load_catalog_file(path: Path) -> set[str]:
    return catalog_ids(json.loads(path.read_text(encoding="utf-8")))


def load_catalog_url(url: str, timeout: float = 30.0) -> set[str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return catalog_ids(json.loads(resp.read().decode("utf-8")))


def check(paths: list[Path], ids: set[str]) -> tuple[list[str], int]:
    """Return (findings, slugs_checked). A finding per missing slug."""
    findings: list[str] = []
    checked = 0
    for path in paths:
        if not path.is_file():
            findings.append(f"{path}: file not found")
            continue
        for role, slugs in collect_slugs(path).items():
            for slug in slugs:
                checked += 1
                if slug not in ids:
                    findings.append(f"{path}: role '{role}' slug '{slug}' "
                                    "is not in the provider catalog")
    return findings, checked


def scan_prose(skills_dir: Path, ids: set[str]) -> tuple[list[str], int]:
    """Scan skill markdown for backtick-quoted model-slug defaults.

    Returns (findings, slugs_checked). A finding per prose slug that is
    either missing from the catalog or not in OpenRouter vendor/model
    format (prose-only defaults like ``claude-fable-5-1-thinking-max``).
    """
    findings: list[str] = []
    checked = 0
    skipped: list[str] = []
    for md in sorted(skills_dir.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            skipped.append(str(md.relative_to(skills_dir)))
            continue
        for m in PROSE_SLUG_RE.finditer(text):
            slug = m.group("slug")
            checked += 1
            if "/" in slug:
                if slug not in ids:
                    rel = md.relative_to(skills_dir)
                    findings.append(f"{rel}: prose slug '{slug}' is not in the provider catalog")
            else:
                rel = md.relative_to(skills_dir)
                findings.append(f"{rel}: prose slug '{slug}' is not in OpenRouter vendor/model format "
                                "(prose-only default; verify against the provider catalog manually)")
    if skipped:
        findings.append(f"NOTE: {len(skipped)} skill file(s) could not be read as UTF-8 "
                        f"(prose scan may be incomplete): {', '.join(skipped)}")
    return findings, checked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--panel", default=str(DEFAULT_PANEL),
                    help="repo-owned model panel (default: tools/assets/model-panel.json)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help="shipped config (default: config/models.json)")
    ap.add_argument("--catalog-url", default=DEFAULT_CATALOG_URL)
    ap.add_argument("--catalog-file", default=None,
                    help="read catalog ids from a JSON file instead of the network")
    ap.add_argument("--prose", action="store_true",
                    help="also scan skill markdown for backtick-quoted model-slug defaults")
    args = ap.parse_args()
    paths = [Path(args.panel), Path(args.config)]

    try:
        if args.catalog_file:
            ids = load_catalog_file(Path(args.catalog_file))
        else:
            ids = load_catalog_url(args.catalog_url)
        findings, checked = check(paths, ids)
        prose_findings: list[str] = []
        prose_checked = 0
        if args.prose:
            prose_findings, prose_checked = scan_prose(DEFAULT_SKILLS_DIR, ids)
    except Exception as exc:
        print(f"slug drift: tool error: {exc}", file=sys.stderr)
        return 2

    for f in findings:
        print(f"MISSING {f}")
    for f in prose_findings:
        print(f"PROSE {f}")
    if findings:
        print(f"slug drift: {len(findings)} configured slug(s) missing from the catalog",
              file=sys.stderr)
        return 1
    # Prose findings are informational only: prose defaults are not in
    # OpenRouter vendor/model format by design, so they cannot be checked
    # against the catalog automatically. They are printed above so a human
    # can decide whether to update them; they do not fail the watch.
    print(f"model slugs: OK ({checked} configured slug(s) all present; "
          f"{len(ids)} catalog ids checked"
          + (f"; {prose_checked} prose slug(s) scanned" if args.prose else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
