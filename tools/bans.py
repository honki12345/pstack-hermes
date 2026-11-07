"""Single source of truth for the install-scanner-clean banned constructs.

Consumed by tools/validate.py (F-publish check) and tools/scanner_gate.py
(the CI gate) so the two can never drift apart. The scan scope mirrors the
real hermes install scanner: EVERY UTF-8-decodable file in the package,
not just .md/.json/.txt.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

# Package-wide: no exceptions. These mirror hermes install-scanner verdicts
# that BLOCK installs and cannot be --force overridden.
SECURITY_BANS: tuple[tuple[str, str], ...] = (
    ("tailscale.com/install.sh", "privilege escalation: curl|sudo sh installer"),
    ("FOR_AGENTS.md", "persistence: copy-instructions pattern"),
    ("http://127.0.0.1:4173", "network: hardcoded loopback endpoint literal"),
)

# Hermes-facing surface only.
# The build provenance (which documents the fixes by name) is exempt.
DELEGATION_VOCAB_BANS: tuple[str, ...] = (
    "subagent_type",
    "generalPurpose",
    "AskQuestion",
    "`Task`",
    "run_in_background",
    'environment: "cloud"',
)
VOCAB_EXEMPT_PREFIXES: tuple[str, ...] = (".build-provenance.txt",)
GENERATED_DEPENDENCY_DIRS: frozenset[str] = frozenset({"node_modules"})
# Repo-root tooling/documentation directories (only when at package root)
ROOT_TOOLING_DIRS: frozenset[str] = frozenset({"tools", "docs", ".github", ".git", ".venv", "patches", ".pytest-tmp", ".pytest_cache", ".ruff_cache"})

# Top-level entries that constitute the installed Hermes package (issue #128).
# Scanning is allowlist-based: only files under these entries are package content.
# Everything else in a checkout (tools/, docs/, .github/, scratch dumps, ...) is
# repo tooling and must not be scanned as if it were package content.
PACKAGE_TOP_LEVEL: frozenset[str] = frozenset({
    "plugin.json", "README.md", "LICENSE", ".build-provenance.txt",
    "skills", "config", "assets",
})


def is_package_path(rel: Path) -> bool:
    """True when ``rel`` (relative to the package root) is package content."""
    return bool(rel.parts) and rel.parts[0] in PACKAGE_TOP_LEVEL


# Repo-level tooling/documentation files that are NOT part of the installed package.
# These may legitimately reference banned constructs in explanatory context.
EXEMPT_REPO_FILES: frozenset[str] = frozenset({
    "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
    "pyproject.toml", ".gitattributes",
})


def is_generated_dependency_path(path: Path) -> bool:
    """True for dependency installs created by runtime/tool verification."""
    if path.parts and path.parts[0] in GENERATED_DEPENDENCY_DIRS:
        return True
    # Also skip node_modules anywhere for safety
    return any(part == "node_modules" for part in path.parts)


def iter_text_files(pkg: Path) -> Iterator[tuple[str, str]]:
    """Yield (posix-rel-path, text) for every UTF-8-decodable file in pkg."""
    for p in sorted(pkg.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(pkg)
        if not is_package_path(rel):
            continue
        if is_generated_dependency_path(rel):
            continue
        if rel.as_posix() in EXEMPT_REPO_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        yield p.relative_to(pkg).as_posix(), text


def find_violations(pkg: Path) -> list[str]:
    """Return one human-readable line per banned construct found in pkg."""
    bad: list[str] = []
    for rel, text in iter_text_files(pkg):
        for needle, why in SECURITY_BANS:
            if needle in text:
                bad.append(f"{rel}: {needle} ({why}) [security, package-wide]")
        if not rel.startswith(VOCAB_EXEMPT_PREFIXES):
            for needle in DELEGATION_VOCAB_BANS:
                if needle in text:
                    bad.append(f"{rel}: {needle} [delegation vocab, hermes-facing]")
    return bad
