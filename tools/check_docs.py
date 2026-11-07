#!/usr/bin/env python3
"""Docs drift gate for the hand-written instructions.

The package is generated, so `validate.py` keeps it honest. The *docs* have no
such gate, and they have already drifted once: the root-layout migration left
`AGENTS.md` and `CONTRIBUTING.md` describing a `pstack/` subdirectory that no
longer exists. This tool checks the facts that actually broke:

1. Router links in `AGENTS.md` / `docs/agent/README.md` resolve.
2. `docs/agent/layout.md` classifies generated vs hand-written paths the same
   way `.gitattributes` does (this is how `README.md` was mislabelled).
3. Every doc paragraph that lists `UPSTREAM_PIN` workflows lists exactly
   `tools/check_pins.py::REQUIRED_DECLARERS`.
4. Hand-written docs (plus the generated `README.md`) carry no stale
   package-subdirectory paths (`pstack/skills/`, `pstack/config/`,
   `$PWD/pstack`, ...).
5. Install docs (`README.md`, `docs/USING.md`) do not resurrect the dead
   `master` subdirectory install route.
6. Repository identity: this is the independent `honki12345/pstack-hermes`
   fork. Docs may name the original `iap/pstack-hermes` port only on an
   explicit attribution line (history lives in `NOTICE.md`, `CHANGELOG.md`
   and `docs/fork/AUDIT-ko.md`, which are not scanned), the package README
   must give the fork's install command, and no doc may point at removed
   original-port machinery (dist branch, release signing, bot-review gates).

Exit codes: 0 clean; 1 drift found; 2 unreadable input.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

ROUTER_DOCS = ("AGENTS.md", "docs/agent/README.md")
HAND_WRITTEN_DOCS = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
)
HAND_WRITTEN_DOC_GLOBS = ("docs/*.md", "docs/agent/*.md")
# README.md is generated, but its install prose is user-facing and drifted once
# with the root-layout migration; scan it for the same stale package paths.
SCAN_EXTRA_DOCS = ("README.md",)
INSTALL_ROUTE_DOCS = ("README.md", "docs/USING.md")

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
PATH_CELL_RE = re.compile(r"^\|\s*(?P<paths>(?:`[^`]+`[,\s]*)+)\|\s*(?P<kind>[^|]+?)\s*\|")
BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")
WORKFLOW_TOKEN_RE = re.compile(r"`([A-Za-z0-9_.-]+\.ya?ml)`")
STALE_PATH_RE = re.compile(
    r"(?<![\w./-])pstack/(?:skills|config|assets|agents|docs|patches)/"
    r"|pstack/(?:plugin\.json|README\.md|\.gitattributes)"
    r"|pstack/\.build-provenance"
    r"|\$PWD/pstack"
    r"|pwd\)/pstack"
)
GENERATED_KINDS = frozenset({"generated", "copied"})
HAND_WRITTEN_KINDS = frozenset({"hand-written"})
STALE_INSTALL_RE = re.compile(
    r"`?master`?\s+subdirectory\s+installs?"
    r"|subdirectory\s+installs?\s+are\s+re-cloned"
    r"|Subdirectory\s+updates\s+replace\s+shipped\s+skills",
    re.IGNORECASE,
)
LEGACY_REPO = "iap/pstack-hermes"
LEGACY_PREFIX_TERMINATORS = " \t\r\n/`'\"(=:"
LEGACY_TOKEN_BOUNDARIES = " \t\r\n`'\"(=<>[]{};,!#|*_~"
LEGACY_NAME_CONTINUATIONS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
)
BARE_PACKAGE_RE = re.compile(r"(?<![\w./-])pstack/(?=\s|$|`)")
CONTRACT_DOCS = frozenset({"AGENTS.md", "CONTRIBUTING.md"})
IDENTITY_REPO = "honki12345/pstack-hermes"
IDENTITY_INSTALL = f"hermes plugins install {IDENTITY_REPO}"
IDENTITY_INSTALL_DOCS = ("README.md",)
IDENTITY_EXTRA_GLOBS = (".github/ISSUE_TEMPLATE/*.yml", ".github/ISSUE_TEMPLATE/*.yaml")
# The original port may be named only where it is credited.
OLD_OWNER_RE = re.compile(r"iap/pstack-hermes|github\.com[/:]iap(?![\w-])", re.IGNORECASE)
ATTRIBUTION_RE = re.compile(
    r"attribution|deriv(?:e|es|ed)\s+from|forked\s+from", re.IGNORECASE
)
REMOVED_MACHINERY_RE = re.compile(
    r"coderabbit|qodo|publish-dist|round-ledger|model-drift-watch|labels-bootstrap"
    r"|allowed_signers|bot-reviews\.md|review-ops\.md|PATCHES\.md"
    r"|`?dist`?\s+(?:compatibility\s+)?(?:branch|channel)",
    re.IGNORECASE,
)
LAYOUT_GENERATED_EXEMPT = frozenset({"plugin.yaml"})


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _github_repo_prefix(text: str, pos: int) -> bool:
    start = pos
    while start > 0 and text[start - 1] not in LEGACY_TOKEN_BOUNDARIES:
        start -= 1
    token = text[start:pos].lower()
    if token.endswith(":"):
        return token[:-1].rsplit("@", 1)[-1] == "github.com"
    if "://" in token:
        parsed = urlparse(token)
        return (parsed.hostname or "") == "github.com" and parsed.path == "/"
    return token == "github.com/"


def _subdir_token_ends(rest: str) -> bool:
    if not rest:
        return True
    if rest[0] != ".":
        return rest[0] not in LEGACY_NAME_CONTINUATIONS
    return len(rest) == 1 or rest[1] not in LEGACY_NAME_CONTINUATIONS


def _legacy_target(text: str, pos: int) -> bool:
    """True when the repo name at *pos* points at the removed `pstack/` subdir."""
    if pos > 0:
        separator = text[pos - 1]
        if separator not in LEGACY_PREFIX_TERMINATORS:
            return False
        if separator in "/:" and not _github_repo_prefix(text, pos):
            return False
    tail = text[pos + len(LEGACY_REPO) :].lower()
    if tail.startswith(".git"):
        tail = tail[4:]
    if tail.startswith("/tree/"):
        _, sep, subdir = tail[len("/tree/") :].partition("/")
        if not sep or not subdir.startswith("pstack"):
            return False
        return _subdir_token_ends(subdir[len("pstack") :])
    if not (tail.startswith("/pstack") or tail.startswith("#pstack")):
        return False
    return _subdir_token_ends(tail[7:])


def _doc_paths(root: Path) -> list[Path]:
    paths = [root / rel for rel in (*HAND_WRITTEN_DOCS, *SCAN_EXTRA_DOCS)]
    for pattern in HAND_WRITTEN_DOC_GLOBS:
        paths.extend(sorted(root.glob(pattern)))
    seen: dict[Path, None] = {}
    for path in paths:
        seen.setdefault(path, None)
    return list(seen)


def check_router_links(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for rel in ROUTER_DOCS:
        doc = root / rel
        if not doc.is_file():
            problems.append(f"{rel}: missing router doc")
            continue
        for target in LINK_RE.findall(_read(doc)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            resolved = (doc.parent / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                problems.append(f"{rel}: broken link target {target}")
    return problems


def _gitattributes_generated(root: Path) -> set[str]:
    attrs = root / ".gitattributes"
    if not attrs.is_file():
        raise FileNotFoundError(attrs)
    markers: set[str] = set()
    for line in _read(attrs).splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "linguist-generated=true" not in line:
            continue
        path = line.split()[0].lstrip("/")  # "/x" anchors x at the repo root; same path for the layout check
        markers.add(path[:-2] if path.endswith("/**") else path)
    return markers


def _covers(marker: str, path: str) -> bool:
    bare = path.rstrip("/")
    if marker.endswith("/"):
        return bare == marker.rstrip("/") or bare.startswith(marker)
    return bare == marker


def check_layout_manifest(root: Path = ROOT) -> list[str]:
    layout = root / "docs" / "agent" / "layout.md"
    if not layout.is_file():
        return ["docs/agent/layout.md: missing layout doc"]
    try:
        markers = _gitattributes_generated(root)
    except FileNotFoundError:
        return [".gitattributes: missing (cannot cross-check generated paths)"]

    problems: list[str] = []
    documented: list[str] = []
    for line in _read(layout).splitlines():
        match = PATH_CELL_RE.match(line)
        if not match:
            continue
        kind = match.group("kind").strip().lower()
        for path in BACKTICK_PATH_RE.findall(match.group("paths")):
            generated = any(_covers(marker, path) for marker in markers)
            if kind in GENERATED_KINDS:
                documented.append(path)
            if kind in GENERATED_KINDS and not generated:
                problems.append(
                    f"docs/agent/layout.md: {path} listed {kind} but .gitattributes does not mark it generated"
                )
            if kind in HAND_WRITTEN_KINDS and generated:
                problems.append(
                    f"docs/agent/layout.md: {path} listed hand-written but .gitattributes marks it generated"
                )
    for marker in sorted(
        m for m in markers if m.rstrip("/") not in LAYOUT_GENERATED_EXEMPT
    ):
        bare = marker.rstrip("/")
        if not any(
            path.rstrip("/") == bare or path.rstrip("/").startswith(f"{bare}/")
            for path in documented
        ):
            problems.append(
                f"docs/agent/layout.md: .gitattributes marks {marker} generated but layout.md does not list it"
            )
    return problems


def _pin_required() -> tuple[str, ...]:
    sys.path.insert(0, str(ROOT))
    import check_pins  # noqa: PLC0415

    return tuple(check_pins.REQUIRED_DECLARERS)


def check_pin_declarers(root: Path = ROOT) -> list[str]:
    try:
        required = set(_pin_required())
    except Exception as exc:  # noqa: BLE001 - a broken importer is a tool error
        return [f"tools/check_pins.py: cannot read REQUIRED_DECLARERS ({exc})"]

    problems: list[str] = []
    docs = [
        root / "AGENTS.md",
        root / "CONTRIBUTING.md",
        root / "docs" / "agent" / "upstream-drift.md",
    ]
    for doc in docs:
        if not doc.is_file():
            continue
        rel = doc.relative_to(root).as_posix()
        lines = _read(doc).splitlines()
        seen: set[frozenset[str]] = set()
        for index, line in enumerate(lines):
            if "UPSTREAM_PIN" not in line:
                continue
            # The workflow list usually wraps onto the next two lines; keep the
            # window tight so a neighbouring bullet (dependabot, actionlint)
            # cannot contribute an unrelated workflow name.
            window = "\n".join(lines[index : index + 4])
            listed = frozenset(WORKFLOW_TOKEN_RE.findall(window))
            if listed and listed != required and listed not in seen:
                seen.add(listed)
                problems.append(
                    f"{rel}:{index + 1}: UPSTREAM_PIN paragraph lists {sorted(listed)}, "
                    f"check_pins.REQUIRED_DECLARERS is {sorted(required)}"
                )
    return problems


def check_stale_paths(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for doc in _doc_paths(root):
        if not doc.is_file():
            continue
        rel = doc.relative_to(root).as_posix()
        for lineno, line in enumerate(_read(doc).splitlines(), start=1):
            if STALE_PATH_RE.search(line):
                problems.append(f"{rel}:{lineno}: stale package subdirectory path: {line.strip()}")
            elif rel in CONTRACT_DOCS and BARE_PACKAGE_RE.search(line):
                problems.append(f"{rel}:{lineno}: stale bare pstack/ package path: {line.strip()}")
    template = root / ".github" / "PULL_REQUEST_TEMPLATE.md"
    if template.is_file():
        for lineno, line in enumerate(_read(template).splitlines(), start=1):
            if "pstack/" in line:
                problems.append(
                    f".github/PULL_REQUEST_TEMPLATE.md:{lineno}: stale pstack/ path: {line.strip()}"
                )
    return problems


def check_stale_install_route(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for rel in INSTALL_ROUTE_DOCS:
        doc = root / rel
        if not doc.is_file():
            continue
        text = _read(doc)
        for match in STALE_INSTALL_RE.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            snippet = " ".join(match.group(0).split())
            problems.append(
                f"{rel}:{lineno}: stale subdirectory-install route: {snippet}"
            )
        lower = text.lower()
        start = 0
        while True:
            pos = lower.find(LEGACY_REPO, start)
            if pos == -1:
                break
            if _legacy_target(text, pos):
                lineno = text.count("\n", 0, pos) + 1
                line_end = text.find("\n", pos)
                snippet = " ".join(
                    text[pos : line_end if line_end != -1 else len(text)].split()
                )
                problems.append(
                    f"{rel}:{lineno}: stale subdirectory-install route: {snippet}"
                )
            start = pos + len(LEGACY_REPO)
    return problems


def _identity_paths(root: Path) -> list[Path]:
    paths = _doc_paths(root)
    for pattern in IDENTITY_EXTRA_GLOBS:
        paths.extend(sorted(root.glob(pattern)))
    seen: dict[Path, None] = {}
    for path in paths:
        seen.setdefault(path, None)
    return list(seen)


def check_repo_identity(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for doc in _identity_paths(root):
        if not doc.is_file():
            continue
        rel = doc.relative_to(root).as_posix()
        for lineno, line in enumerate(_read(doc).splitlines(), start=1):
            if OLD_OWNER_RE.search(line) and not ATTRIBUTION_RE.search(line):
                problems.append(
                    f"{rel}:{lineno}: original-port repository outside an attribution line "
                    f"(use {IDENTITY_REPO}): {line.strip()}"
                )
            match = REMOVED_MACHINERY_RE.search(line)
            if match:
                problems.append(
                    f"{rel}:{lineno}: reference to removed original-port machinery "
                    f"({match.group(0)}): {line.strip()}"
                )
    for rel in IDENTITY_INSTALL_DOCS:
        doc = root / rel
        if doc.is_file() and IDENTITY_INSTALL not in _read(doc):
            problems.append(f"{rel}: missing install command `{IDENTITY_INSTALL}`")
    return problems


CHECKS = (
    ("router links resolve", check_router_links),
    ("layout matches .gitattributes", check_layout_manifest),
    ("UPSTREAM_PIN declarers match check_pins", check_pin_declarers),
    ("no stale package subdirectory paths", check_stale_paths),
    ("no stale subdirectory-install route", check_stale_install_route),
    ("repository identity is honki12345/pstack-hermes", check_repo_identity),
)


def check(root: Path = ROOT) -> tuple[int, list[str]]:
    lines: list[str] = [f"== check_docs.py ==\nroot: {root}"]
    problems: list[str] = []
    for label, fn in CHECKS:
        found = fn(root)
        if found:
            lines.append(f"  FAIL  {label}")
            lines.extend(f"        {item}" for item in found)
            problems.extend(found)
        else:
            lines.append(f"  PASS  {label}")
    lines.append("")
    lines.append("verdict: " + ("PASS" if not problems else f"FAIL ({len(problems)} problem(s))"))
    return (0 if not problems else 1), lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT), help="repository root (default: this repo)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    code, lines = check(root)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
