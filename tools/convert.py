#!/usr/bin/env python3
"""Phase-0 converter: Cursor pstack plugin -> hermes agent-plugins-v1 portable package.

Reproducible: re-running against the same source clone produces an identical package
modulo the provenance timestamp (output dir is rebuilt from scratch each run; no
incremental state). Set SOURCE_DATE_EPOCH=<unix-ts> for a fully deterministic build.

Contract implemented (study Ch3 sections 3.1-3.2 + subagent_03a loader facts):
  - Root-level plugin.json (agent-plugins-v1 manifest; hermes probes the package root, never
    .cursor-plugin/); whitelisted fields only (9 emitted; extensions omitted
    since pstack has none), exact $schema URL.
  - skills/ single level: flatten skills/grokbot/make-bot-ui -> skills/make-bot-ui,
    delete the now-empty grokbot container.
  - Fix SKILL.md frontmatter names so name == directory name in
    kebab-case (agent_plugins.py:158-168).
  - agents/ is not emitted (Hermes-native package; no Cursor dual-load);
    automations/benny is excluded by the publisher contract.
  - Every copied text file normalized to UTF-8 WITHOUT BOM, LF line endings.
  - .cursor-plugin/ is not emitted; hermes reads the root manifest only.
  - Provenance (source commit) written to .build-provenance.txt.

Usage:
    uv run --frozen tools/convert.py --source <pstack-clone>

Defaults: --out = repository root (this script lives in tools/);
--source is required (there is no built-in default clone path).
This script NEVER executes anything from the upstream pstack clone (pure file copy).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent  # tools/
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_OUT = REPO_ROOT

SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
# agent_plugins.py:24-35 whitelist (extensions intentionally NOT emitted: pstack has none,
# and every whitelisted-but-empty field we can omit, we omit).
WHITELIST = [
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
]
MANIFEST_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Extensions treated as text (normalized). Anything else is sniffed: NUL byte
# or non-UTF-8 => byte-for-byte copy.
TEXT_EXTS = {
    ".md",
    ".markdown",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ts",
    ".tsx",
    ".mjs",
    ".js",
    ".cjs",
    ".py",
    ".sh",
    ".bash",
    ".ps1",
    ".html",
    ".css",
    ".csv",
    ".tsv",
    ".lock",
    ".cfg",
    ".ini",
}

# The conversion gate (study 3.2 fixes 2): frontmatter name must equal the
# directory name (kebab-case) for the portable loader. Empty since the c742dd6
# re-pin: upstream #702 names every skill after its folder ("name: poteto-mode"),
# so the old "Poteto Mode" fix was pruned. validate.py still enforces the rule.
FRONTMATTER_FIXES: dict[str, tuple[str, str]] = {}

# ---------------------------------------------------------------------------
# Phase-1 hygiene transforms (study findings R1, F10-F12; F16 retired at the
# c47b1284 re-pin because upstream now ships the slug-agnostic fix). Each is a
# targeted post-copy edit so the upstream clone stays faithful and every
# rebuild re-applies the fixes deterministically.
# ---------------------------------------------------------------------------

# R1 retired at the c742dd6 re-pin: upstream now curates the poteto-mode
# principles index itself (#633/#634: per-entry "when it applies" wording that
# intentionally differs from the leaf descriptions, plus a title -> slug naming
# rule). Regenerating it from the leaves would overwrite upstream prose, which
# is not a platform adaptation. What survives is the consistency gate: every
# index entry must resolve to a shipped principle leaf and every leaf must be
# indexed (principle_index_drift, also run by validate.py).
_PRINCIPLES_HEADING = "## Principles\n"
_NEXT_SECTION = "## Autonomy"
_INDEX_ENTRY_RE = re.compile(
    r"^- \*\*(?P<title>[^*]+?)\.?\*\*(?: \(\*\*(?P<slug>principle-[a-z0-9-]+)\*\*\))?"
)


def principle_slug(title: str) -> str:
    """Upstream naming rule: kebab case, hyphens kept, other punctuation dropped."""
    kept = re.sub(r"[^a-z0-9\s-]", "", title.lower())
    return "principle-" + re.sub(r"\s+", "-", kept.strip())


def principle_index_drift(skills_dir: Path) -> list[str]:
    """Findings where the poteto-mode principles index and the leaves disagree."""
    pm = skills_dir / "poteto-mode" / "SKILL.md"
    text = pm.read_text(encoding="utf-8")
    start = text.find(_PRINCIPLES_HEADING)
    end = text.find(_NEXT_SECTION, start)
    if start == -1 or end == -1:
        return ["poteto-mode/SKILL.md: principles block anchors not found"]
    indexed: list[str] = []
    for line in text[start:end].split("\n"):
        m = _INDEX_ENTRY_RE.match(line)
        if m:
            indexed.append(m.group("slug") or principle_slug(m.group("title")))
    leaves = sorted(
        p.name for p in skills_dir.iterdir() if p.name.startswith("principle-") and p.is_dir()
    )
    drift = [f"index entry without a shipped leaf: {s}" for s in indexed if s not in leaves]
    drift += [f"leaf missing from the index: {s}" for s in leaves if s not in indexed]
    drift += [f"duplicate index entry: {s}" for s in sorted(set(indexed)) if indexed.count(s) > 1]
    return drift


# F16 retired at the c47b1284 re-pin: upstream generalized both sites, so no
# transform is needed - check-plan.mjs carries
#   const LANES = /Ten lanes on `<slug>` at the PR head/;
# and the multi-phase template says "Ten lanes on `<swarm workers model>` at
# the PR head". validate.py asserts those shapes survive conversion.

# F10/F12 retired at the c742dd6 re-pin: upstream #805 picks GNU vs BSD stat
# and date flags itself (`stat -c %Y /` probe, `stat_fmt`, `fmt_day`), so the
# stat_mtime/date_epoch helper insertion was pruned. F11 survives: upstream's
# awk still prints only `$2` of `worktree <path>`, truncating paths with spaces.
WORKTREE_AWK_PAIRS = [
    ("awk '/^worktree /{print $2; exit}'", "awk '/^worktree /{print substr($0, 10); exit}'"),
    (
        "awk '/^worktree /{print $2}' | while read -r wt",
        "awk '/^worktree /{print substr($0, 10)}' | while read -r wt",
    ),
]

ADAPTATION_LINE = (
    "Adapted for Hermes Agent plugin compatibility from github.com/cursor/plugins pstack."
)


class ConvertError(RuntimeError):
    pass


class Stats:
    def __init__(self) -> None:
        self.files_copied = 0
        self.text_normalized = 0
        self.byte_copied = 0
        self.bom_stripped = 0
        self.crlf_fixed = 0
        self.path_fixes = 0
        self.fixes: list[str] = []
        self.warnings: list[str] = []
        # (map_name, anchor_index) -> occurrences replaced package-wide
        self.anchor_hits: dict[tuple[str, int], int] = {}
        self.anchor_audited = 0


def normalize_text(data: bytes, st: Stats) -> str | None:
    """Decode and normalize a text payload; None means 'not text'."""
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data.decode("utf-8-sig" if bom else "utf-8")
    except UnicodeDecodeError:
        return None
    if bom:
        st.bom_stripped += 1
    if b"\r\n" in data or b"\r" in data:
        st.crlf_fixed += 1
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # The package lives at the repository root (repo layout v2), so an upstream
    # ``pstack/skills/...`` reference is stale: it would point outside the
    # install root. Rewrite it wherever prose quotes a path (`git show
    # origin/main:...`, `node ...`, markdown links).
    if "pstack/skills" in text:
        text = text.replace("pstack/skills", "skills")
        st.path_fixes += 1
    return text


def is_text_candidate(path: Path, data: bytes) -> bool:
    if path.suffix.lower() in TEXT_EXTS:
        return True
    if path.name == ".gitignore":
        return True
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def copy_file(src: Path, dst: Path, st: Stats) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    if is_text_candidate(src, data):
        text = normalize_text(data, st)
        if text is None:  # claimed text but not UTF-8: copy bytes, warn
            st.warnings.append(f"non-UTF-8 text-looking file copied verbatim: {src}")
            dst.write_bytes(data)
            st.byte_copied += 1
        else:
            dst.write_bytes(text.encode("utf-8"))  # UTF-8 no BOM, LF preserved
            st.text_normalized += 1
    else:
        dst.write_bytes(data)
        st.byte_copied += 1
    st.files_copied += 1


def copy_tree(src_dir: Path, dst_dir: Path, st: Stats) -> int:
    n = 0
    for root, dirs, files in os.walk(src_dir):
        root_p = Path(root)
        dirs.sort()
        rel = root_p.relative_to(src_dir)
        (dst_dir / rel).mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            copy_file(root_p / name, dst_dir / rel / name, st)
            n += 1
    return n


def fix_frontmatter_name(text: str, old: str, new: str, rel: str) -> tuple[str, str]:
    """Replace exactly one frontmatter line `name: <old>` with `name: <new>`.

    Returns (new_text, fix_note)."""
    if not text.startswith("---\n"):
        raise ConvertError(f"{rel}: SKILL.md does not start with a frontmatter block")
    end = text.find("\n---", 4)
    if end == -1:
        raise ConvertError(f"{rel}: frontmatter block is unterminated")
    block = text[:end]
    needle = f"name: {old}"
    if not re.search(rf"(?m)^{re.escape(needle)}$", block):
        raise ConvertError(
            f"{rel}: expected exactly one frontmatter line 'name: {old}', none found"
        )
    if block.count(needle) != 1:
        raise ConvertError(f"{rel}: 'name: {old}' appears more than once in frontmatter")
    new_block = (
        block[: block.index(needle)] + f"name: {new}" + block[block.index(needle) + len(needle) :]
    )
    st_fix = f"{rel}: frontmatter name '{old}' -> '{new}'"
    return new_block + text[end:], st_fix


def t11_targets(out: Path) -> list[Path]:
    """T11 pass scope: every skill markdown plus shell scripts.

    worktree-audit.sh carries the one Cursor-only transcript comment the T11
    map annotates; scripts were out of scope before, which silently skipped
    that fix for the package's entire history.
    """
    return sorted((out / "skills").rglob("*.md")) + sorted((out / "skills").rglob("*.sh"))


def replace_required(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise ConvertError(f"{path.name}: {label} anchor not found")
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def apply_phase1_transforms(out: Path, st: Stats) -> None:
    """Apply R1/F16/F10-F12 hygiene to the copied package. Fail-loud on any
    anchor mismatch so a silent no-op can never masquerade as a fix."""
    # --- T0: hermes-port script package metadata + self-contained scripts ---
    scripts_pkg = out / "skills" / "poteto-mode" / "scripts" / "package.json"
    scripts_payload = json.loads(scripts_pkg.read_text(encoding="utf-8"))
    scripts_payload["name"] = "@pstack-hermes/poteto-mode-tools"
    scripts_payload["scripts"] = {
        "deps": "bun install --frozen-lockfile",
        "test": "bun run deps && bun test watch-pr check-plan.test.ts",
        "typecheck": "bun run deps && tsc --project watch-pr/tsconfig.json --noEmit --strict",
    }
    scripts_pkg.write_bytes(json.dumps(scripts_payload, indent=2).encode("utf-8") + b"\n")
    lock = out / "skills" / "poteto-mode" / "scripts" / "bun.lock"
    lock_text = lock.read_text(encoding="utf-8")
    if '"name": "@cursor-skill/poteto-mode-tools"' in lock_text:
        lock.write_bytes(
            lock_text.replace(
                '"name": "@cursor-skill/poteto-mode-tools"',
                '"name": "@pstack-hermes/poteto-mode-tools"',
                1,
            ).encode("utf-8")
        )
    # The orch scripts (and with them the git-signing / Graphite-timeout test
    # patches and the T15/T16 stack-provider passes) were retired at the c742dd6
    # re-pin: upstream #1043 deleted scripts/orch/ and made Orchestrate agentic.
    st.fixes.append(
        "script package metadata renamed for hermes; test/typecheck scripts bootstrap deps"
    )

    # --- T1 (R1, retired): verify the upstream-curated principles index ---
    drift = principle_index_drift(out / "skills")
    if drift:
        raise ConvertError(f"poteto-mode principles index drifted from the leaves: {drift}")

    # --- T2/T3 (F16) retired at the c47b1284 re-pin ---
    # Upstream ships the slug-agnostic fix itself (regex in check-plan.mjs;
    # `<swarm workers model>` in the multi-phase template), so the
    # env-override transform and its companion note were removed.

    # --- F16b: tighten upstream's lane regex (review finding, PR #70) ---
    # The upstream body `[^`<>]+` permits a whitespace-only slug; a plan with
    # no model name would pass. Require at least one non-whitespace character.
    cp = out / "skills" / "poteto-mode" / "scripts" / "check-plan.mjs"
    cp_text = cp.read_text(encoding="utf-8")
    F16B_OLD = "const LANES = /Ten lanes on `[^`<>]+` at the PR head/;"
    F16B_NEW = "const LANES = /Ten lanes on `(?=[^`<>]*[^\\s`<>])[^`<>]+` at the PR head/;"
    if F16B_OLD not in cp_text:
        raise ConvertError("check-plan.mjs: upstream lane regex not found for the F16b tightening")
    cp.write_bytes(cp_text.replace(F16B_OLD, F16B_NEW, 1).encode("utf-8"))
    st.fixes.append(
        "F16b: check-plan.mjs lane regex rejects whitespace-only slugs (review finding)"
    )

    # --- T4: worktree-audit.sh space-safe paths (F11; F10/F12 retired) ---
    wa = out / "skills" / "poteto-mode" / "scripts" / "worktree-audit.sh"
    wa_text = wa.read_text(encoding="utf-8")
    for old, new in WORKTREE_AWK_PAIRS:
        if old not in wa_text:
            raise ConvertError(f"worktree-audit.sh: awk anchor not found: {old!r}")
        wa_text = wa_text.replace(old, new, 1)
    wa.write_bytes(wa_text.encode("utf-8"))
    st.fixes.append(
        "F11: worktree-audit.sh awk no longer truncates worktree paths with spaces "
        "(F10/F12 GNU/BSD stat+date retired: upstream #805 ships the fix)"
    )

    # --- T5: neutralize localhost endpoint literals in example docs (install ---
    # --- scan: hardcoded_ip_port rule matches any IP:port literal)            ---
    # The feature-map example uses a local Notes app URL purely as sample
    # content. Keep the meaning, drop the dotted-quad:port form entirely.
    neutralized = 0
    for p in sorted((out / "skills").rglob("*.md")):
        t = p.read_text(encoding="utf-8")
        if "http://127.0.0.1:4173" in t:
            t2 = t.replace("http://127.0.0.1:4173", "the loopback HTTP endpoint, port 4173")
            p.write_bytes(t2.encode("utf-8"))
            neutralized += t.count("http://127.0.0.1:4173")
    if neutralized:
        st.fixes.append(
            f"F-publish: {neutralized} localhost endpoint literals neutralized "
            "in feature-map example docs (install-scanner network findings)"
        )
    for p in sorted((out / "skills").rglob("*.md")):
        t = p.read_text(encoding="utf-8")
        if re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:", t) or "sudo" in t:
            st.warnings.append(f"scanner-flaggable construct remains: {p.relative_to(out)}")

    # --- T8: factual fixes from the hermes deep review (readonly semantics,
    # --- swarm leftovers, tool-name mappings, doc links) -------------------
    T8_MAP = [
        (
            ' Use `environment: "local"` only when the worker needs access to something on the user\'s computer.',
            "",
        ),
        (
            "When a worker must start from a non-default pushed branch, pass `cloud_base_branch`.",
            "When a worker must start from a non-default branch, pass the branch name explicitly in the task prompt.",
        ),
        (
            "Every spawn is `generalPurpose` with `readonly: false` (agent mode). **Do not use readonly/Ask mode.** It strips MCP access, which disables the MCP-backed investigators and the synthesizer's citation spot-checks. Investigators still shouldn't write anything.",
            "Every spawn is a read-only `delegate_task` task: `access: read-only` plus the READ-ONLY `context` line, per the Hermes delegation contract (`../poteto-mode/references/hermes-delegation.md`). Hermes children keep the parent's MCP tools, so the MCP-backed investigators and the synthesizer's citation spot-checks keep their lookups. Investigators still shouldn't write anything.",
        ),
        ("Use Read, Grep, and Glob as needed.", "Use read_file and search_files as needed."),
        (
            "in `~/.cursor/rules/pstack-models.mdc`",
            "in the configured pstack model panel (`pstack-models.json` in the hermes config dir next to `config.yaml`)",
        ),
        (
            "[Guard the Context Window](../principle-guard-the-context-window/SKILL.md)",
            "the **guard-the-context-window** principle skill",
        ),
        (
            "in the `pstack-models.mdc` rule",
            "in the pstack model panel (`pstack-models.json` in the hermes config dir next to `config.yaml`)",
        ),
        (
            "A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.",
            "A line whose role is not in step 5 is from a retired role. Drop it.",
        ),
        (
            'built from `references/explainer-prompt.md` without the explorer-findings section. Go to step 4.',
            'built from `references/explainer-prompt.md` without the explorer-findings section, and tell the explainer to inspect the code itself before answering (there are no explorer findings to rely on). Go to step 4.',
        ),
        ("`auto` or `inherit-parent`", "`inherit-parent`"),
        (
            'list, or alias (`inherit-parent`, `auto`). `unlimited`, `large`, `medium`, and `small` set',
            "list, or alias (`inherit-parent`). On a fresh configuration every role starts as `inherit-parent` (the parent chat model), so setting concrete slugs is what lets the budget's effort tiers apply. `unlimited`, `large`, `medium`, and `small` set",
        ),
        ("`inherit-parent` and `auto` do not change.", "`inherit-parent` does not change."),
        (
            'An `inherit-parent` or `auto` value never judges.',
            'An `inherit-parent` value never judges.',
        ),
    ]
    t8_files = apply_map(sorted((out / "skills").rglob("*.md")), T8_MAP, map_name="T8_MAP", st=st)
    note_added = False
    # adaptation note for why's source playbooks (Cursor MCP tool names are examples)
    sp = out / "skills" / "why" / "references" / "source-playbook.md"
    if sp.is_file():
        sp_text = sp.read_text(encoding="utf-8")
        note = "\n\n> Hermes note: the tool names in these source playbooks (Linear, Notion, Slack, Datadog, Sentry, Databricks) are Cursor MCP examples - adapt them to your configured MCP servers' schemas.\n"
        if "Cursor MCP examples" not in sp_text:
            sp.write_bytes((sp_text.rstrip("\n") + note).encode("utf-8"))
            note_added = True
    if t8_files or note_added:
        st.fixes.append(
            f"T8: factual fixes from the hermes deep review applied to "
            f"{t8_files} skill file(s)"
            + (" + the source-playbook adaptation note" if note_added else "")
            + " (readonly-MCP rationale, swarm Cursor params, tool-name "
            "mappings, model-panel path, principle cross-link)"
        )

    # --- T6: delegation translation (Phase-2A) ---
    apply_delegation_translation(out, st)

    # --- T-FIX: avoid inline-shell false trigger in TS patterns (issue #107) ---
    ts_pattern_old = "The tells are `!`, `arr[0] as T`"
    ts_pattern_new = "The tells are non-null assertions, `arr[0] as T`"
    ts_fixed = 0
    for p in sorted((out / "skills").rglob("*.md")):
        t = p.read_text(encoding="utf-8")
        if ts_pattern_old in t:
            t = t.replace(ts_pattern_old, ts_pattern_new)
            p.write_bytes(t.encode("utf-8"))
            ts_fixed += 1
    if ts_fixed:
        st.fixes.append(
            f"issue #107: replaced inline-shell-triggering `!` with 'non-null assertions' in {ts_fixed} TS pattern file(s)"
        )

    # --- T9/T10: hermes-native discovery + config (Stages A+B) ---
    # Stage A (T10): setup-pstack writes config/models.json (package-local)
    # instead of ~/.cursor/rules/pstack-models.mdc; poteto-mode reads it.
    # Stage B (T9): why/reflect/recall discovery sections rewritten to
    # hermes-native mechanisms (session_search, session tool catalog).
    T10_MAP = [
        (
            "Detects your available models and writes an always-applied rule that overrides the skill defaults.",
            "Detects your available models and writes a per-role panel beside `config.yaml` in the hermes config directory, outside the plugin tree, so it survives plugin updates.",
        ),
        (
            "Write `~/.cursor/rules/pstack-models.mdc`, an always-applied rule that sets pstack's model per role.",
            "Write `pstack-models.json` in the hermes config directory (next to `config.yaml`: `~/.hermes/` on Linux, `%LOCALAPPDATA%\\hermes\\` on Windows) - **outside** the plugin directory. pstack replaces the installed package on every `plugins update`, so a panel written inside the plugin tree is silently discarded, while a file beside `config.yaml` survives reinstalls.",
        ),
        (
            "Enumerate the model slugs you can pass to a `delegate_task` subagent in this session. That is the dependable source. If Cursor also exposes a models API or CLI that lists the user's entitled models, prefer it for completeness. If you cannot detect any, ask the user to paste the slugs they have access to. Never write a real slug you have not confirmed is available. The aliases `inherit-parent` and `auto` are always valid even though they are not detected slugs.",
            "Enumerate the model slugs available in this session (the configured providers' catalog); that is the dependable source. If you cannot detect any, ask the user via `clarify` to paste the slugs they have access to. Never write a real slug you have not confirmed is available. `inherit-parent` is always valid even though it is not a detected slug (hermes has no Cursor-style `auto` selector; the parent chat model IS the inherit-parent semantic).",
        ),
        (
            "The default role-to-model mapping is the rule shape shown in step 5 below. If `~/.cursor/rules/pstack-models.mdc` already exists, read it and treat its `# budget` line and its role values as the current choices. Otherwise start from those defaults.",
            "The default role-to-model mapping is the shape shown in step 5 below. If `pstack-models.json` already exists in the hermes config directory, read it and treat its `budget` entry and its role values as the current choices. Otherwise start from these defaults. Do NOT try to recover an older in-package `config/models.json`: a plugin update replaces that file with the shipped default before this skill runs, so anything found there is the default rather than the user's saved choices, and copying it forward would silently discard their configuration. If they expected a configuration to still be there, tell them plainly that an update overwrote it and ask them to re-run setup-pstack.",
        ),
        (
            "offering the detected models plus `inherit-parent` and `auto` (both mean: this role runs on the parent chat model, which is how Auto users stay on Auto) as the options.",
            "offering the detected models plus `inherit-parent` (this role runs on the parent chat model) as the options.",
        ),
        (
            "Every real slug written must be in the detected set. `inherit-parent` and `auto` always pass.",
            "Every real slug written must be in the detected set; `inherit-parent` always passes.",
        ),
        (
            '### 5. Write the rule\n\nWrite `~/.cursor/rules/pstack-models.mdc` with `alwaysApply: true`, a `# budget` line with the chosen label and its target effort, and one line per role, using the same labels poteto-mode uses. Overwrite the whole file so re-runs stay idempotent. Shape:\n\n```\n---\ndescription: pstack per-role model choices (overrides skill defaults)\nalwaysApply: true\n---\n# pstack model configuration. One line per role. Delete a line to fall back to the skill default.\n# `inherit-parent` or `auto` as a value: the role runs on the parent chat model (omit Task `model`). Alias entries in a panel list still count toward its fan-out.\n# budget: large (xhigh)\nfeature, refactoring: grok-4.7-xhigh-fast\nbug-fix: grok-4.7-xhigh-fast\nperf-issue: grok-4.7-xhigh-fast\nhillclimb: grok-4.7-xhigh-fast\njudgment and prose: claude-opus-5-5-xhigh\nhardest tasks: claude-opus-5-5-xhigh\nhow explorer: grok-4.7-xhigh-fast\nhow explainer: claude-opus-5-5-xhigh\nwhy investigators: grok-4.7-xhigh-fast\nwhy synthesizer: claude-opus-5-5-xhigh\nreflect tooling: grok-4.7-xhigh-fast\nreflect judgment, divergent, synthesizer: claude-opus-5-5-xhigh\narena runners: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast\narena cross-judge pool: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast\nswarm workers: grok-4.7-xhigh-fast\narchitect runners: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast\ninterrogate reviewers: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast\n```',
            '### 5. Write the config\n\nWrite `pstack-models.json` in the hermes config directory (next to `config.yaml`), never inside the plugin directory - a panel stored with the package is lost on the next `plugins update`. One entry per role, using the same labels poteto-mode uses. Overwrite the whole file so re-runs stay idempotent. Shape:\n\n```json\n{\n  "budget": "large",\n  "roles": {\n    "feature, refactoring": "inherit-parent",\n    "bug-fix": "inherit-parent",\n    "perf-issue": "inherit-parent",\n    "hillclimb": "inherit-parent",\n    "judgment and prose": "inherit-parent",\n    "hardest tasks": "inherit-parent",\n    "how explorer": "inherit-parent",\n    "how explainer": "inherit-parent",\n    "how critics": ["inherit-parent"],\n    "why investigators": "inherit-parent",\n    "why synthesizer": "inherit-parent",\n    "reflect tooling": "inherit-parent",\n    "reflect judgment, divergent, synthesizer": "inherit-parent",\n    "arena runners": ["inherit-parent"],\n    "arena cross-judge pool": ["inherit-parent"],\n    "swarm workers": "inherit-parent",\n    "architect runners": ["inherit-parent"],\n    "interrogate reviewers": ["inherit-parent"]\n  }\n}\n```\nPanel roles (how critics, arena runners, arena cross-judge pool, architect runners, interrogate reviewers) take an ARRAY; one subagent runs per entry, so the list length sets the count. `swarm workers` is the default for every worker unless a race assigns another model per arm.',
        ),
        (
            "On yes, invoke `/create-verification-skill` (resolves wherever pstack is installed: workspace, user, or plugin).",
            "On yes, load the create-verification-skill skill via skill_view (it ships in this plugin) and follow it.",
        ),
        (
            'On yes, invoke `/create-formal-verification-skill` (resolves wherever pstack is installed: workspace, user, or plugin).',
            'On yes, load the create-formal-verification-skill skill via skill_view (it ships in this plugin) and follow it.',
        ),
    ]
    T9_MAP = [
        (
            "Drafts or revises a personal -mode skill via create-skill + unslop",
            "Drafts or revises a personal -mode skill via the `skill_manage` tool (action: `create`) + unslop",
        ),
        (
            "an inline mining pass (see step 1), Cursor's built-in `create-skill` (authoring), and the **unslop** skill",
            "an inline mining pass (see step 1), the `skill_manage` tool (action: `create`, authoring), and the **unslop** skill",
        ),
        (
            "Use Cursor's built-in `create-skill` skill to author the skill.",
            "Use the `skill_manage` tool (action: `create`) to author the skill.",
        ),
        ("follow `create-skill`'s YAML rules", "follow the `skill_manage` authoring YAML rules"),
        (
            "Apply the **unslop** skill and `create-skill`'s writing guidelines to every line.",
            "Apply the **unslop** skill and the `skill_manage` authoring writing guidelines to every line.",
        ),
        (
            "A `create-skill`-style test/iterate benchmark loop isn't useful here.",
            "A skill-authoring test/iterate benchmark loop isn't useful here.",
        ),
        (
            "`create-skill` alone, no mining required.",
            "the `skill_manage` tool alone, no mining required.",
        ),
        (
            "Agent-facing prose also follows the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).",
            "Agent-facing prose also follows the **skill_manage** tool (hermes' authoring operation for SKILL.md files).",
        ),
        (
            "1. Use the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).",
            "1. Use the `skill_manage` tool (action: `create`, hermes' authoring operation for SKILL.md files).",
        ),
        (
            "- Existing-skill-first: propose `new skill via create-skill:` only when no existing skill is a real home, the pattern recurs, and the topic deserves its own skill.",
            "- Existing-skill-first: propose `new skill via the skill_manage tool:` only when no existing skill is a real home, the pattern recurs, and the topic deserves its own skill.",
        ),
        (
            "| <new pattern, no existing skill is a real home> | <draft a new skill via create-skill> | <new skill via create-skill: <kebab-name>> |",
            "| <new pattern, no existing skill is a real home> | <draft a new skill via the skill_manage tool> | <new skill via the skill_manage tool: <kebab-name>> |",
        ),
        (
            "Before spawning investigators, list the available MCPs from the Cursor environment. Use the available-tools map when present. Otherwise inspect the `mcps/` directory Cursor exposes for enabled MCP servers.",
            "Before spawning investigators, list the MCP tools available in this session (the configured MCP servers' tool catalog). If the session has no MCP tools, mark the unreachable evidence categories null in the coverage map instead of inventing a tool.",
        ),
        (
            "## 1. Locate the active transcript\n\nBefore fanning out, find this conversation's transcript in the `agent-transcripts/` directory the system prompt names. Do not glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.\n\n```bash\nls -t <agent-transcripts>/*.jsonl <agent-transcripts>/*/*.jsonl <agent-transcripts>/*/subagents/*.jsonl 2>/dev/null | head -10\n```\n\nThat covers the legacy flat (`<id>.jsonl`), current nested (`<id>/<id>.jsonl`), and subagent (`<parent>/subagents/<child>.jsonl`) layouts. For each candidate, read the first JSONL line. Take the file whose `message.content[0].text` contains the conversation's opening user prompt. If none matches, pass a tight digest of the session instead.",
            '## 1. Locate the active transcript\n\nThe parent locates the current session via `session_search` (hermes stores sessions in its SQLite store; there are no JSONL transcript files). Query for the active conversation and take the most recent matching session id. If the exact session cannot be resolved, write a tight digest of the conversation and pass that instead.',
        ),
        (
            '| Judgment | `reflect judgment, divergent, synthesizer` | `claude-opus-5-5-xhigh` | `references/judgment-reviewer.md` |',
            "| Judgment | `reflect judgment, divergent, synthesizer` | the line's value, else the parent chat model | `references/judgment-reviewer.md` |",
        ),
        (
            '| Tooling | `reflect tooling` | `grok-4.7-xhigh-fast` | `references/tooling-reviewer.md` |',
            "| Tooling | `reflect tooling` | the line's value, else the parent chat model | `references/tooling-reviewer.md` |",
        ),
        (
            '| Divergent | `reflect judgment, divergent, synthesizer` | `claude-opus-5-5-xhigh` | `references/divergent-reviewer.md` |',
            "| Divergent | `reflect judgment, divergent, synthesizer` | the line's value, else the parent chat model | `references/divergent-reviewer.md` |",
        ),
        (
            "Read the active transcript at <ABSOLUTE_PATH> (or use the digest below if no path is given).",
            "Use the provided Hermes session id to inspect the active conversation with `session_search`, or use the digest below if no session id is given.",
        ),
        (
            "Pass each template verbatim, substituting the transcript path or digest where marked.",
            "Pass each template verbatim, substituting the Hermes session id or digest where marked.",
        ),
        (
            "- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to Cursor's built-in `create-skill` skill and run its draft / test / iterate loop.",
            "- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to the hermes skill-authoring flow (the hermes-agent skill's guidance, or `skill_manage(action='create')`) and run its draft / test / iterate loop.",
        ),
        (
            "- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to `create-skill` and run its description-optimization loop.",
            "- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to the hermes skill-authoring flow's description pass.",
        ),
        (
            "- `new skill via create-skill: <kebab-name>`: hand creation to `create-skill`. Do not invent the shape ad hoc.",
            "- `new skill: <kebab-name>`: hand creation to the hermes skill-authoring flow. Do not invent the shape ad hoc.",
        ),
        (
            'Transcripts live at `~/.cursor/projects/<slug>/agent-transcripts/<uuid>/<uuid>.jsonl`, where `<slug>` is the workspace path with the leading slash dropped and each "/" turned into "-" (so `/Users/you/proj` becomes `Users-you-proj`). Every line is one chat message.',
            "Sessions live in hermes' SQLite store; there are no JSONL transcript files. Query them via `session_search` (topic + recency filters).",
        ),
        (
            "One specific prior chat to resume is the `session-pickup` playbook, not this.",
            "One specific prior chat to resume is the session-pickup playbook (in this plugin's playbooks/), not this.",
        ),
        (
            "Tell every subagent to order candidates by real modification time (`ls -t`) and never by UUID name, grep the topic first and then read only the matching chats and only their relevant regions, and skip the current chat plus obvious noise (subagent, eval, and test chats).",
            "Tell every subagent to query `session_search` by topic and recency, read only the matching sessions' relevant regions, and skip the current chat plus obvious noise (subagent, eval, and test chats).",
        ),
    ]
    t910 = apply_map(sorted((out / "skills").rglob("*.md")), T10_MAP, map_name="T10_MAP", st=st)
    t910 += apply_map(sorted((out / "skills").rglob("*.md")), T9_MAP, map_name="T9_MAP", st=st)
    cfg_dir = out / "config"
    cfg_dir.mkdir(exist_ok=True)
    cfg = cfg_dir / "models.json"
    if not cfg.exists():
        roles = {
            "feature, refactoring": "inherit-parent",
            "bug-fix": "inherit-parent",
            "perf-issue": "inherit-parent",
            "hillclimb": "inherit-parent",
            "judgment and prose": "inherit-parent",
            "hardest tasks": "inherit-parent",
            "how explorer": "inherit-parent",
            "how explainer": "inherit-parent",
            "how critics": ["inherit-parent"],
            "why investigators": "inherit-parent",
            "why synthesizer": "inherit-parent",
            "reflect tooling": "inherit-parent",
            "reflect judgment, divergent, synthesizer": "inherit-parent",
            "arena runners": ["inherit-parent"],
            "arena cross-judge pool": ["inherit-parent"],
            "swarm workers": "inherit-parent",
            "architect runners": ["inherit-parent"],
            "interrogate reviewers": ["inherit-parent"],
        }
        # Stage-D: the model panel is repo-owned content (tools/assets/).
        # When present it replaces the inherit-parent defaults wholesale; the
        # role set must match exactly or the build fails loudly.
        panel = SCRIPT_DIR / "assets" / "model-panel.json"
        if panel.is_file():
            imported = json.loads(panel.read_text(encoding="utf-8")).get("roles", {})
            if sorted(imported) != sorted(roles):
                diff = sorted(set(imported) ^ set(roles))
                raise ConvertError(f"model panel roles mismatch the pstack role set: {diff}")
            roles = imported
        cfg.write_bytes((json.dumps({"roles": roles}, indent=2) + "\n").encode("utf-8"))
        if panel.is_file():
            st.fixes.append("Stage-D: config/models.json shipped with the model panel (18 roles)")
        else:
            st.fixes.append("T10: config/models.json shipped with inherit-parent defaults")
    if t910:
        st.fixes.append(
            f"T9/T10: hermes-native discovery/config fixes applied across "
            f"{t910} skill file pass(es) "
            "(setup-pstack models.json, why/reflect/recall session_search)"
        )

    t11_files = apply_map(t11_targets(out), T11_MAP, map_name="T11_MAP", st=st)
    st.fixes.append(
        f"T11: hardcoded-path fixes applied to {t11_files} skill file(s) "
        "(.cursor rules/projects/skills "
        "paths, agent-transcripts recipes, /tmp scratch dirs, malformed-link "
        "checks, worktree-cleanup macOS note)"
    )

    # --- T12: Cursor built-in /loop + /goal rewording (Phase-2 wrap-up) ----
    t12_files = apply_map(
        sorted((out / "skills").rglob("*.md")), T12_MAP, map_name="T12_MAP", st=st
    )
    check_plan = out / "skills" / "poteto-mode" / "scripts" / "check-plan.mjs"
    t12_script_files = apply_map([check_plan], T12_SCRIPT_MAP, map_name="T12_SCRIPT_MAP", st=st)
    st.fixes.append(
        f"T12: Cursor built-in wake/goal rewording applied across "
        f"{t12_files} skill file(s) + {t12_script_files} checker "
        "file(s) (terminal /loop -> gateway cron wake; armed /goal "
        "-> goal.md in the agent store; check-plan marker aligned)"
    )

    # Dead-anchor audit: every audited transform must have matched somewhere
    # in this build (fail-loud replacement for the historical silent no-op).
    t13_files = apply_map(
        sorted((out / "skills").rglob("*.md")), T13_MAP, map_name="T13_MAP", st=st
    )
    st.fixes.append(
        f"T13: residual Cursor-vendor coupling Phase-2 wrap-up applied "
        f"across {t13_files} skill file(s) "
        "(cursor-team-kit plugin references, cloud-agent fleet, "
        "Graphite `gt` rewording)"
    )

    help_files = apply_map(
        sorted((out / "skills" / "poteto-help").rglob("*.md")), HELP_MAP, map_name="HELP_MAP", st=st
    )
    st.fixes.append(
        f"P1: poteto-help Cursor-only mechanisms re-pointed at Hermes across {help_files} file(s) "
        "(install, Custom Modes, setup-pstack panel, cross-harness note, /loop, hermesbot row)"
    )
    apply_h1_contract(out, st)
    audit_anchor_hits(
        {
            "T8_MAP": T8_MAP,
            "T9_MAP": T9_MAP,
            "T10_MAP": T10_MAP,
            "T11_MAP": T11_MAP,
            "DELEGATION_MAP": DELEGATION_MAP,
            "T12_MAP": T12_MAP,
            "T12_SCRIPT_MAP": T12_SCRIPT_MAP,
            "T13_MAP": T13_MAP,
            "HELP_MAP": HELP_MAP,
            "H1_MAP": H1_MAP,
        },
        st,
    )


# --- T6 (Phase-2A): delegation translation --------------------------------
# Cursor Task/subagent vocabulary -> hermes delegate_task vocabulary, applied to
# every package markdown file. Ordered most-specific first; the final global pair
# catches remaining backticked `Task` tool references. agents/*.md are EXCLUDED
# (they are the Cursor-side persona definitions, kept verbatim for dual-load).
# --- T11: hardcoded-path cleanup (path audit 2026-08-30) ---
# Cursor-specific paths, /tmp scratch dirs, and malformed links across the
# package. worktree-audit.sh keeps its Cursor transcript path (graceful
# skip on hermes) with a note; provenance is by-design; github.ts:352 was
# a regex false positive in the audit.
T11_MAP = [
    (
        "Look recursively for `.cursor/skills/**/*-mode/SKILL.md` and `~/.cursor/skills/*-mode/SKILL.md` matching the user's handle. Mode skills can live in a personal category directory (`.cursor/skills/<handle>/`), not only at the top level.",
        "Look recursively for `*-mode/SKILL.md` matching the user's handle: in this plugin's `skills/` directory, in `~/.hermes/skills/`, or wherever the user's mode skills live. Mode skills can live in a personal category directory (`skills/<handle>/`), not only at the top level.",
    ),
    (
        "Locate the active workspace's transcripts before fanning out. The system prompt names the workspace's `agent-transcripts/` directory. Use only that path. Don't glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.",
        "Locate the active session history before fanning out. Query `session_search` for the active conversation's sessions; hermes stores them in its SQLite store. Don't read sessions from unrelated projects or users.",
    ),
    (
        "- Path: preserve an existing mode skill's category. For a new mode, use `.cursor/skills/<handle>/<handle>-mode/SKILL.md` when the repo has an established personal category for that handle. Otherwise default to `.cursor/skills/<handle>-mode/SKILL.md` in the project (or `~/.cursor/skills/<handle>-mode/` if the user prefers a personal skill).",
        "- Path: preserve an existing mode skill's category. For a new mode, use `skills/<handle>/<handle>-mode/SKILL.md` when the plugin has an established personal category for that handle; otherwise default to `~/.hermes/skills/<handle>-mode/SKILL.md` (a personal skill location).",
    ),
    (
        "Read each candidate's local transcript under the active workspace's `agent-transcripts/` directory (the system prompt names this path). Do not glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.",
        "Verify each candidate's trail via `session_search` over the hermes session store (query the candidate's topic and read the matching session's relevant regions). Hermes sessions cross project boundaries only when the work did.",
    ),
    (
        "1. Locate the prior trail. A local transcript under the active workspace's `agent-transcripts/` directory (the system prompt names the path. Do not glob across `~/.cursor/projects/*/`, that crosses workspace boundaries and reads private chats from unrelated projects), a cloud-agent URL, or a pushed branch.",
        "1. Locate the prior trail. The hermes session store (query `session_search` for the prior conversation), a cloud-agent URL, or a pushed branch.",
    ),
    (
        "Read this run's transcript under the active workspace's `agent-transcripts/` directory (the system prompt names the path). Don't glob across `~/.cursor/projects/*/`. That reads unrelated private chats.",
        "Read this run's history via `session_search` over the hermes session store (query this conversation). Don't read unrelated private sessions.",
    ),
    (
        'a `Read` of its `SKILL.md` (under `.cursor/skills/`, `~/.cursor/skills/`, or `~/.cursor/plugins/`)',
        "a `read_file` or `skill_view` of its `SKILL.md` (this plugin's `skills/`, `~/.hermes/skills/`, or other configured skills locations)",
    ),
    (
        "otherwise `/tmp/arena-<slug>/candidate-<n>/`",
        "otherwise a scratch directory under the system temp (`arena-<slug>/candidate-<n>/`)",
    ),
    (
        "Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.",
        "Save every screenshot to a scratch directory under the system temp (`swarm-<pr-id>/worker-<n>/<slug>.png`) and return the paths with the report.",
    ),
    (
        "write it to a file like `/tmp/<slug>-resume.md`",
        "write it to a resume file in the system temp directory (`<slug>-resume.md`)",
    ),
    (
        "Set `NOTES_DATA_DIR=/tmp/notes-verify-$RUN_ID` so concurrent runs do not share state.",
        "Set `NOTES_DATA_DIR` to a scratch directory under the system temp (e.g. `notes-verify-$RUN_ID`) so concurrent runs do not share state.",
    ),
    ("6. Simulators and other reclaimers.", "6. Simulators and other reclaimers (macOS/Xcode)."),
    (
        "a project-local skill (`.cursor/skills/verify-<app>/`)",
        "a project-local skill in your harness's skills directory (`%LOCALAPPDATA%\\hermes\\skills\\verify-<app>/` on hermes, `.cursor/skills/verify-<app>/` on Cursor)",
    ),
    (
        "Write `.cursor/skills/verify-<app>/SKILL.md` with YAML frontmatter",
        "Write `verify-<app>/SKILL.md` in that skills directory with YAML frontmatter",
    ),
    (
        "Create `.cursor/skills/verify-<app>/features/README.md` plus one file per user-facing feature",
        "Create `verify-<app>/features/README.md` in that skills directory plus one file per user-facing feature",
    ),
    (
        "(usually `.cursor/skills/verify-*/`)",
        "(usually `verify-*/` in your harness's skills directory)",
    ),
    (
        "# Transcripts dir: ~/.cursor/projects/<slugified-repo-path>/agent-transcripts.",
        "# Transcripts dir: ~/.cursor/projects/<slugified-repo-path>/agent-transcripts (Cursor-specific; on hermes, sessions live in SQLite and this check skips gracefully).",
    ),
    (
        'Never edit a skill under `plugins/cache/`. Before approval, show its destination, `.cursor/skills/<skill>/` or `~/.cursor/skills/<skill>/`. After approval, copy the skill folder there and edit the copy.',
        "Never edit a skill inside an installed plugin, this package included, because `hermes plugins update` replaces it. Before approval, show its destination, a local skill under `~/.hermes/skills/<skill>/` or the project's own skills directory. After approval, copy the skill folder there and edit the copy.",
    ),
    (
        '](../../docs/guide/',
        '](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/',
    ),
]


DELEGATION_MAP = [
    # Specific-first pairs: consume combined fragments so the generic rules
    # below cannot produce "`delegate_task` calls, `delegate_task` (role:
    # `leaf`)" doublings, and reword the unbackticked Cursor "Task" leftovers
    # ("omit Task `model`", "Task subagent") the generic backtick rule misses.
    (
        "three `Task` calls, `subagent_type: generalPurpose`",
        "three `delegate_task` calls (role: `leaf`)",
    ),
    ("One `Task` call, `subagent_type: generalPurpose`", "One `delegate_task` call (role: `leaf`)"),
    (
        "`inherit-parent` or `auto` runs that role on the parent chat model (omit Task `model`)",
        "`inherit-parent` runs that role on the parent chat model (omit the delegate's `model`)",
    ),
    (
        'Spawn `Task` with `subagent_type: "comment-sicko"`',
        'Read `references/comment-sicko.md`, then spawn a delegate with `delegate_task` (role: `leaf`)',
    ),
    (
        'Every spawn is a Task subagent with `subagent_type: generalPurpose` and `readonly: true`.',
        'Every spawn is a read-only `delegate_task` subagent.',
    ),
    (
        'Spawn each adversary as `generalPurpose`.',
        'Spawn each adversary as a `delegate_task` subagent.',
    ),
    ("`subagent_type: generalPurpose`", "`delegate_task` (role: `leaf`)"),
    (
        '`subagent_type: "poteto-agent"`',
        "`delegate_task` (role: `leaf`) with the poteto-mode wrapper instructions in the task prompt",
    ),
    ('`environment: "cloud"`, ', ""),
    ('`environment: "cloud"`', "local execution"),
    ("`run_in_background: true`", "background execution"),
    (
        "Do not restate its rules.",
        "Inline the rules verbatim; do not paraphrase or add commentary.",
    ),
    ("`subagent_type`", "delegate role"),
    ("`AskQuestion`", "`clarify`"),
    ("AskQuestion", "clarify"),
    (
        "agent mode (readonly strips MCP)",
        "agent mode (readonly restricts file writes only; MCP access is unaffected)",
    ),
    ("the Task tool", "delegate_task"),
    ("`Task`", "`delegate_task`"),
]
# Re-exported from bans.py (single source of truth) so the converter's
# leftover check and the scanner gate can never drift apart.
from bans import DELEGATION_VOCAB_BANS as DELEGATION_FORBIDDEN  # noqa: E402

# --- T12: Cursor built-in /loop + /goal rewording (Phase-2 wrap-up) --------
# Cursor's terminal `/loop` (monitored-shell sleep + output-notification
# sentinel) maps to hermes' gateway scheduler: `hermes cron` jobs whose
# delivery re-wakes the session with the tick/wake prompt. Cursor's `/goal`
# primitive maps to a `goal.md` file beside the plan in the agent store,
# re-read by every wake — matching the playbooks' existing file-based tick
# architecture (re-read playbook from trunk each tick). Anchors taken from
# upstream text; none overlap earlier passes' replacements.
T12_MAP = [
    (
        "Pick the wake mechanism using Cursor's `/loop` command (a built-in, not a pstack skill).",
        "Pick the wake mechanism using hermes' scheduler: `hermes cron create` (check `hermes cron --help` first; the flag set is versioned) arms a recurring or one-shot job whose delivery re-wakes this session with the wake prompt.",
    ),
    (
        "Drive a long or stubborn hunt with Cursor's `/loop` command.",
        "Drive a long or stubborn hunt with a hermes cron job that re-wakes this session on the hunt at a fixed interval until the mechanism is confirmed.",
    ),
    (
        "Run `drive` and `background` under `/loop` in dynamic mode.",
        "Hold `drive` and `background` across turns with a hermes cron wake loop.",
    ),
    (
        'under `/loop` until it reports `COMPLETE` at the ceiling.',
        'from a hermes cron wake loop until it reports `COMPLETE` at the ceiling. Each tick is a fresh run, so its prompt carries the stack and the command (`../references/hermes-delegation.md`).',
    ),
    (
        "`/loop` per component until the diff is zero.",
        "Loop per component until the diff is zero, re-arming a hermes cron wake only if the run must outlive the session.",
    ),
    ('"/loop until X"', '"loop until X"'),
    (
        "On the operator's go, arm `/loop 1h` to audit until no delegated work is left, because long runs drift and agents stall silently.",
        "On the operator's go, arm an hourly hermes cron job to audit until no delegated work is left, because long runs drift and agents stall silently. Each tick is a fresh run, never a resume of this session, so follow the wake rules in `../references/hermes-delegation.md`: a self-contained prompt, `--continuity`, `--workdir`, and `hermes cron remove` once no delegated work is left.",
    ),
    (
        "- [ ] On the operator's go, arm the audit tick as `/loop 1h` with the tick prompt below. Never leave the cadence to memory.",
        "- [ ] On the operator's go, arm the audit tick as an hourly hermes cron job (`--continuity`, `--workdir <repo>`, `--deliver origin`) whose prompt is the tick prompt below plus the plan and store paths. Each tick is a fresh run, not a resume of this chat (`skills/poteto-mode/references/hermes-delegation.md`). Record the job id in the plan, and remove the job (`hermes cron remove <id>`) at close. Never leave the cadence to memory.",
    ),
]

# check-plan.mjs validates multi-phase plans; its Program-checklist marker
# must accept the goal.md mechanism the T12-reworded skeleton now instructs
# (tolerant: either marker, so in-flight plans written with /goal still pass).
T12_SCRIPT_MAP = [
    (
        'const PROGRAM_MARKERS = ["git show origin/main:", "/loop 1h", "status message"];',
        'const PROGRAM_MARKERS = ["git show origin/main:", ["hermes cron", "/loop 1h"], "status message"];',
    ),
    (
        '\tfor (const marker of PROGRAM_MARKERS) {\n\t\tif (!bodyText(program).includes(marker)) fail(program.n, `Program checklist lacks "${marker}"`);\n\t}',
        '\tfor (const marker of PROGRAM_MARKERS) {\n\t\tconst alternatives = [marker].flat();\n\t\tif (!alternatives.some((m) => bodyText(program).includes(m)))\n\t\t\tfail(program.n, `Program checklist lacks "${alternatives.join(\'" or "\')}"`);\n\t}',
    ),
]

T13_MAP = [
    # cursor-team-kit plugin references (poteto-mode/SKILL.md, shipping.md,
    # multi-phase-plan.md, opening-a-pr.md, autopilot-full.md,
    # autopilot-stack.md, orchestrate.md). The plugin has no hermes
    # equivalent: `no-comments`, `unslop`, and `technical-writing` ship in
    # this package under their own names, while `deslop`, `control-ui`, and
    # `control-cli` do not (recorded as external prerequisites in the
    # generated README's Known limitations section). References name the
    # skill directly. Anchors are
    # taken from the post-T9-transform text, since T13 runs after T9.
    # Order matters: longer/more-specific anchors must precede shorter
    # substrings of them (e.g., [8] before [0]) so the replacement doesn't
    # break the longer match.
    (
        '`cursor-team-kit` publishes `control-cli` (CLIs and TUIs) and `control-ui`',
        'When installed (they ship outside this package), the control skills are `control-cli` (CLIs and TUIs) and `control-ui`',
    ),
    (
        'each a Cursor cloud agent, runs the change the way a user would with the matching control skill (such as `control-ui` or `control-cli` from `cursor-team-kit`), parent against head',
        'each a local `delegate_task` child, runs the change the way a user would with the matching control skill (such as `control-ui` or `control-cli`, when installed), parent against head',
    ),
    (
        'Each owner is a Cursor cloud agent that builds',
        'Each owner is a local `delegate_task` child that builds',
    ),
    (
        'Browser, Electron, and web UIs use `control-ui` from `cursor-team-kit`. CLIs and TUIs use `control-cli` from `cursor-team-kit`.',
        'Browser, Electron, and web UIs use `control-ui`. CLIs and TUIs use `control-cli`. Both ship outside this package, so use them when installed.',
    ),
    (
        'Each live lane runs on its own cloud VM at the PR head. Drive through `control-ui` or `control-cli` from `cursor-team-kit`.',
        'Each live lane runs on its own lane VM at the PR head. Drive through `control-ui` or `control-cli` when installed.',
    ),
    (
        'Run `/deslop` from `cursor-team-kit` over the diff before commit',
        'Run `/deslop` (when installed; it ships outside this package) over the diff before commit',
    ),
    (
        'the `deslop` skill from the `cursor-team-kit` plugin (`/deslop`)',
        'the `deslop` skill (`/deslop`, when installed; it ships outside this package)',
    ),
    (
        'a chart or diagram from `/visualize`',
        'a chart or diagram from `/visualize` (when your harness has it)',
    ),
    (
        'a `/visualize` chart for numbers',
        'a `/visualize` chart for numbers when your harness has it',
    ),
    (
        'going offline, a Cursor restart, or imminent context compaction',
        'going offline, a Hermes gateway restart, or imminent context compaction',
    ),
    (
        'through its control skill (such as `control-ui` or `control-cli` from `cursor-team-kit`) or a named driver',
        'through its control skill (such as `control-ui` or `control-cli`, when installed) or a named driver',
    ),
]


# --- P1: poteto-help Hermes surface (new upstream skill, c742dd6 re-pin) ----
# poteto-help is upstream's Cursor-facing help desk: install via /add-plugin,
# Custom Modes, cursor.com docs, Claude Code, and the excluded make-bot-ui. Each
# pair re-points one Cursor-only mechanism at its Hermes equivalent (or states
# it is absent); routing, tables, and advice stay upstream's. Audited.
HELP_MAP = [
    (
        "give the user the file's public copy: `https://github.com/cursor/plugins/blob/main/pstack/` followed by its path.",
        "give the user the file's public copy: `https://github.com/honki12345/pstack-hermes/blob/main/` followed by its path. That is this Hermes port; upstream pstack lives at `https://github.com/cursor/plugins/tree/main/pstack`.",
    ),
    (
        'do the work under it, and mention once that a Custom Mode keeps it on.',
        'do the work under it, and mention once that each new task starts with `/poteto-mode` again.',
    ),
    (
        "- No `~/.cursor/rules/pstack-models.mdc` means `/setup-pstack` hasn't run for this user, so every role uses its default model.",
        "- No `pstack-models.json` beside hermes' `config.yaml` means `/setup-pstack` hasn't run for this user, so every role uses its default model.",
    ),
    (
        '1. Install with `/add-plugin pstack` in chat, or from Customize in the sidebar.',
        '1. Install with `hermes plugins install honki12345/pstack-hermes --enable`. The package README covers pinning and updates.',
    ),
    (
        'It asks for a reasoning budget, maps a model to each role, and writes a rule. The rule applies to new chats.',
        "It asks for a reasoning budget, maps a model to each role, and writes `pstack-models.json` beside hermes' `config.yaml`. Spawns follow it only through the pstack delegation backend (`../poteto-mode/references/hermes-delegation.md`).",
    ),
    (
        'which saves tokens when the chat runs on Auto or a cheaper model.',
        'which saves tokens when the chat runs on a cheaper model.',
    ),
    (
        'pstack is built for Cursor, and it also installs in Claude Code with `/plugin marketplace add cursor/plugins`, then `/plugin install pstack@cursor-plugins`. There, each command takes a `pstack:` prefix, such as `/pstack:poteto-mode`. The [Claude Code adapter](../../docs/harness/claude-code.md) maps the Cursor names, and a step that needs a Cursor-only feature, such as Bugbot or Automations, is skipped with a one-line note.\n\nOther tools can read the skills, because they use the Agent Skills format. But most workflow skills, including `/poteto-mode`, `/how`, `/why`, and `/teach`, spawn Cursor subagents with per-role models, and Custom Modes and `/loop` are Cursor features, so those parts may not work there.',
        'This package is the Hermes Agent port of pstack, which upstream is built for Cursor. Spawns go through `delegate_task` per `../poteto-mode/references/hermes-delegation.md`, and wake loops are `hermes cron` jobs. For Cursor or Claude Code, install upstream pstack from `https://github.com/cursor/plugins/tree/main/pstack` instead.',
    ),
    (
        "Whether `/poteto-mode` stays on depends on how the user starts it:\n\n- Enter on `/poteto-mode` attaches the skill to one message. It fades as the chat moves on.\n- Option+Enter on Mac or Alt+Enter on Windows, or Use as Mode from the skill entry, makes it a Custom Mode. It stays in context every turn until the user exits the mode, and it stays out of casual turns.\n- Cursor's docs list Custom Modes in the Agents Window and the CLI. Elsewhere, start each new task with `/poteto-mode`.\n\nLink [Cursor's skills docs](https://cursor.com/docs/skills) when this comes up.",
        'On Hermes, `/poteto-mode` (or loading it with `skill_view`) attaches the skill to the task at hand, and it fades as the chat moves on. Hermes has no Custom Modes, so start each new task with `/poteto-mode`. The package README covers slash commands and `skill_view`.',
    ),
    (
        '`/poteto-mode` already uses `poteto-agent` for the subagents its playbook steps spawn.',
        '`/poteto-mode` already gives the subagents its playbook steps spawn the poteto-mode wrapper instructions.',
    ),
    (
        '| Run parallel checks over slices, or race workers, as cloud agents |',
        '| Run parallel checks over slices, or race workers, as parallel delegate workers |',
    ),
    (
        '| Build a page whose buttons wake a Grok Bot over a webhook | [`/make-bot-ui`](../make-bot-ui/SKILL.md) |',
        '| Build a page or dashboard whose buttons wake a hermes bot over a gateway webhook | [`/hermesbot`](../hermesbot/SKILL.md) |',
    ),
    (
        '- `/deslop`, `control-cli`, and `control-ui` ship in the `cursor-team-kit` plugin.\n- `/loop` and `/create-skill` are Cursor built-ins.',
        "- `/deslop`, `control-cli`, and `control-ui` ship in Cursor's `cursor-team-kit` plugin, outside this package. Use them when installed.\n- Hermes has no built-in `/loop` command. Wake loops are `hermes cron` jobs, and skills are authored with `skill_manage`.",
    ),
    (
        "can start Cursor's own skill for the same job instead.",
        'can start another installed skill for the same job instead.',
    ),
    (
        "pstack has no planning skill. Cursor's Plan Mode works alongside it.",
        "pstack has no planning skill. Your harness's plan mode, if it has one, works alongside it.",
    ),
    (
        '| The mode stopped applying after a few turns | It was started with Enter. Start it as a Custom Mode, or start each task with `/poteto-mode`. |',
        '| The mode stopped applying after a few turns | The skill fades as the chat moves on. Start each task with `/poteto-mode`. |',
    ),
    (
        '| A new model choice had no effect | The rule from `/setup-pstack` applies to new chats. Start one. |',
        "| A new model choice had no effect | Spawns follow `pstack-models.json` only through the pstack delegation backend (`delegation.backend: pstack`). Without it every role runs on the parent model. Check each result's `route` label. |",
    ),
    (
        '| Parallel agents overwrote each other | Give each agent its own worktree, or run them as cloud agents, which each get their own machine. |',
        '| Parallel agents overwrote each other | Give each agent its own worktree. Hermes children are local and share this machine. |',
    ),
    (
        '| An overnight run moved but finished nothing | `/loop` needs a check that can pass or fail, not a duration.',
        '| An overnight run moved but finished nothing | A hermes cron wake loop needs a check that can pass or fail, not a duration.',
    ),
    (
        'give `/loop` that predicate.',
        'give the hermes cron wake loop that predicate.',
    ),
    (
        "don't ask me before committing. /loop until done.",
        "don't ask me before committing. loop until done.",
    ),
]

# --- H1: Hermes delegation contract (fork) ---------------------------------
# Hermes v0.23.0 delegate_task has no per-task `model` (core-reserved: dropped
# silently, the child runs on the parent model) and no `readonly` (undeclared:
# the task is refused). Cron never resumes the arming session. These pairs
# rewrite the post-T13 text so the skills route by `pstack_role` through the
# pstack delegation backend (hermes-plugin/pstack-delegation), keep read-only
# as an advisory + verified contract, and describe cron ticks as fresh runs.
# The contract itself ships as skills/poteto-mode/references/hermes-delegation.md
# (tools/assets/hermes-delegation.md). Audited like every other map.
H1_CONTRACT = "`../poteto-mode/references/hermes-delegation.md`"
H1_PB_CONTRACT = "`../references/hermes-delegation.md`"
H1_RO = "- read-only: `access: read-only` plus the READ-ONLY `context` line, per the Hermes delegation contract (" + H1_CONTRACT + ")"
H1_MAP = [
    (
        "Set `model` to that line's value, or to the default if the rule or the line is missing. Leave `model` unset when the value is `inherit-parent`. If delegate_task rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message.",
        "Route each spawn per the Hermes delegation contract (" + H1_CONTRACT + "): set `pstack_role` to that line's name when `delegate_task` offers it, and never pass `model`, which Hermes drops. Without the pstack delegation backend every spawn runs on the parent model; say so in the reply.",
    ),
    (
        "For `inherit-parent`, omit `model` so the workers run on the parent model. If delegate_task rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message. For a model race, name each arm's model up front.",
        "Route workers with `pstack_role: swarm workers` per " + H1_CONTRACT + "; never pass `model`. For a model race, give each arm a panel seat (`pstack_role: arena runners`, `seat: n`) and name each arm's resolved model from its result `route` label.",
    ),
    (
        "runs that role on the parent chat model (omit the delegate's `model`).",
        "runs that role on the parent chat model (the pstack delegation backend returns the parent route; never pass `model`).",
    ),
    (
        "with `delegate_task` (role: `leaf`) with the poteto-mode wrapper instructions in the task prompt and an explicit model per the Subagents section",
        "with `delegate_task` tasks carrying the poteto-mode wrapper instructions in `context` and an explicit `pstack_role` per the Subagents section",
    ),
    (
        'No "standing by" reply that waits on a nested agent.',
        'No "standing by" reply that waits on a nested agent. On Hermes a delegate\'s own spawns return inside its call, and the top-level chat ends its turn with a one-line status after dispatching (' + H1_PB_CONTRACT + ', background rule); neither is a standing-by reply.',
    ),
    (
        "arms a recurring or one-shot job whose delivery re-wakes this session with the wake prompt.",
        "arms a recurring or one-shot job. Each tick is a fresh agent run, never a resume of this session, so follow the wake rules in " + H1_PB_CONTRACT + ": a self-contained prompt, `--continuity`, `--workdir`, and `hermes cron remove` once the predicate holds.",
    ),
    (
        "with a hermes cron job that re-wakes this session on the hunt at a fixed interval until the mechanism is confirmed.",
        "with a fixed-interval hermes cron job whose self-contained prompt carries the hunt state (each tick is a fresh run; see " + H1_PB_CONTRACT + "), removed once the mechanism is confirmed.",
    ),
    (
        "three `delegate_task` calls (role: `leaf`), agent mode (`readonly: false`). Reviewers need MCP access to look up the tickets, chat threads, and observability traces the transcript references, and readonly strips MCPs.",
        "one `delegate_task` call with three tasks, each with `pstack_role` set to its role line below and `access: read-only` (" + H1_CONTRACT + "). Reviewers need MCP access to look up the tickets, chat threads, and observability traces the transcript references; Hermes children keep the parent's MCP tools.",
    ),
    (
        "One `delegate_task` call (role: `leaf`), `model` from the `reflect judgment, divergent, synthesizer` line (default `claude-opus-5-5-xhigh`), agent mode (`readonly: false`). It spot-verifies citations through MCP, and readonly strips MCPs.",
        "One `delegate_task` task with `pstack_role`: `reflect judgment, divergent, synthesizer` (upstream default `claude-opus-5-5-xhigh`) and `access: read-only`. It spot-verifies citations through MCP, which Hermes children keep.",
    ),
    (
        "with `delegate_task` (role: `leaf`), background execution, and the step 3 model, left unset for `inherit-parent`.",
        "in one `delegate_task` call (Hermes always runs it in the background), each task carrying the step 3 `pstack_role`.",
    ),
    (
        "An `inherit-parent` entry in this line means the parent model, so omit `model` for it. If delegate_task rejects a configured entry, run that seat on its family's default and say so. Families go by prefix: `claude-*`, and `grok-*` with `cursor-grok-*` and `composer-*`. A refused `composer-*` entry runs on `grok-4.7-xhigh-fast`. If the family has no default, or delegate_task rejects that default, use the closest valid slug of the same family from its error message, or drop the seat and say so when there is none. If dropped seats leave fewer than two runners, add runners until there are two, or one when the line has one entry. Run each added runner on a remaining runner's model, or omit `model` so it runs on the parent model when none remain, and say so.",
        "Spawn runner n with `pstack_role` set to the runners line (`arena runners`, `architect runners` from architect, or `interrogate reviewers` from interrogate) and `seat: n`. Never pass `model` (" + H1_CONTRACT + "). Families go by prefix: `claude-*`, and `grok-*` with `cursor-grok-*` and `composer-*`; read each runner's family from its result `route` label. If delegate_task refuses a seat, rerun it once without `seat` and `pstack_role` so it runs on the parent model, say so, and fix the panel entry. If refused seats leave fewer than two runners, add runners on the parent model until there are two, or one when the line has one entry, and say so.",
    ),
    (
        "background execution (a steer or resume of a running agent too, because a foreground call holds your turn while other reports wait), agent mode (readonly restricts file writes only; MCP access is unaffected), file pointers not inlined context, explicit model per role",
        "background execution (always, on Hermes; steer a running child with `action: steer`), full tool access (children inherit your toolsets; see `references/hermes-delegation.md`), file pointers not inlined context, explicit `pstack_role` per role",
    ),
    (
        "Run workers on a new cloud VM (local execution) unless the work needs this machine: a `control-ui` or `control-cli` runtime check (from `cursor-team-kit`), local transcripts, a simulator, or auth that exists only here.",
        "Every worker is a local `delegate_task` child; Hermes has no cloud workers. A kickoff names any local state the work needs: a `control-ui` or `control-cli` runtime check (when those skills are installed), session history in the local hermes store, a simulator, or auth that exists only here.",
    ),
    (
        "Every spawn is a read-only `delegate_task` subagent.",
        "Every spawn is a read-only `delegate_task` task: `access: read-only` plus the READ-ONLY `context` line, per the Hermes delegation contract (" + H1_CONTRACT + ").",
    ),
    (
        "`model`: the `why investigators` line, default `grok-4.7-xhigh-fast`.",
        "Set each one's `pstack_role` to `why investigators` (upstream default `grok-4.7-xhigh-fast`).",
    ),
    (
        "Spawn one synthesizer. `model`: the `why synthesizer` line, default `claude-opus-5-5-xhigh`.",
        "Spawn one synthesizer with `pstack_role`: `why synthesizer` (upstream default `claude-opus-5-5-xhigh`).",
    ),
    (
        "Spawn each adversary as a `delegate_task` subagent.",
        "Spawn adversary n as a `delegate_task` task with `pstack_role: interrogate reviewers` and `seat: n` (" + H1_CONTRACT + ").",
    ),
    (
        "Spawn one readonly judge subagent.",
        "Spawn one read-only judge: `pstack_role: arena cross-judge pool`, `seat` set to the chosen entry, and `access: read-only` plus the READ-ONLY `context` line (" + H1_CONTRACT + ").",
    ),
    (
        "If delegate_task rejects an entry, use a valid slug of the same model at the closest level or speed from the error message, or move to the next entry when there is none.",
        "If delegate_task refuses that seat, move to the next entry from another family.",
    ),
    (
        "**Match the model to the job.** Before each spawn, start from the role's line or default and pick the reasoning level and speed the job needs. The `/setup-pstack` budget sets each role's starting level, xhigh by default. Raise it one step, up to max, for the hardest or subtlest work. Lower it one step for judgment and two or more steps for routine work. Use fast mode only when the job is urgent, meaning CI or trunk is red or a person is blocked on the result right now. Otherwise use standard speed, the same slug without `-fast`. Verification is the exception and stays on the `swarm workers` model and speed (`grok-4.7-xhigh-fast` by default). Change only the effort level and whether the slug is fast, and keep the family the role names, so a diverse panel stays diverse. If the family has no slug at that level and speed, use its nearest slug. A role on `inherit-parent` stays on the parent model. If delegate_task rejects the adjusted slug, do not try another adjusted slug. Use the role's line or default and say so. If delegate_task rejects that too, continue with the calling skill's fallback. A slug counts as rejected once a Task error says that the model is not valid, or leaves the slug out of its list of valid slugs. Never pass a rejected slug, and skip any fallback whose slug is already rejected. When the calling skill has no fallback, use the closest valid slug of the role's family from the error's list of valid slugs, or omit `model` so the subagent runs on the parent model when the list has none. When the calling skill's fallback runs out of slugs without saying what to do next, omit `model` too. Say so in both cases.",
        "**Match the model to the job.** Before each spawn, pick the reasoning level and speed the job needs, starting from the role's line or default. The `/setup-pstack` budget sets each role's starting level, xhigh by default. Raise it one step, up to max, for the hardest or subtlest work. Lower it one step for judgment and two or more steps for routine work. Use fast mode only when the job is urgent, meaning CI or trunk is red or a person is blocked on the result right now. Verification is the exception and stays on the `swarm workers` model and speed. Keep the family the role names, so a diverse panel stays diverse. On Hermes a spawn cannot carry its own slug: core drops `model`, and the pstack delegation backend builds each child from the role's panel entry, effort included (`<model>@<effort>` or an explicit route in `pstack-models.json`). So make the adjustment through the role you pick (`hardest tasks` for the hardest work, the playbook's code line for routine work) or through the panel entry, never by passing `model` (`references/hermes-delegation.md`). If delegate_task refuses a role's route, rerun that task once without `pstack_role` so it runs on the parent model, say so, and fix the entry in `pstack-models.json`.",
    ),
    (
        "so put this file's absolute path in every `poteto-agent` prompt.",
        "so put this file's absolute path in the `context` of every poteto-mode delegate (a `delegate_task` child carrying the poteto-mode wrapper instructions).",
    ),
    (
        "Never spawn a `poteto-agent` to run the playbook itself.",
        "Never spawn a poteto-mode delegate to run the playbook itself.",
    ),
    (
        "don't override to `poteto-agent`.",
        "don't override to the poteto-mode wrapper.",
    ),
    (
        "A step you choose not to do stays in the list with a one-line `skip: <reason>`. Match the task",
        "A step you choose not to do stays in the list with a one-line `skip: <reason>`. "
        "A playbook's delegation instruction counts as a step even when it sits in the intro "
        "instead of the numbered list: write its item (`delegate_task` or `skip: <reason>`) "
        "before your first work tool. Match the task",
    ),
]

#: Text the H1 pass must leave nowhere in skills/**/*.md.
H1_FORBIDDEN = (
    "- `readonly`: `true`",
    "`readonly: false`",
    "omit `model`",
    "re-wakes this session",
    "MCP access is unaffected",
)


def apply_h1_contract(out: Path, st: Stats) -> None:
    """Ship the Hermes delegation contract and point the spawn/wake prose at it."""
    asset = SCRIPT_DIR / "assets" / "hermes-delegation.md"
    if not asset.is_file():
        raise ConvertError(f"H1 contract asset missing: {asset}")
    refs = out / "skills" / "poteto-mode" / "references"
    refs.mkdir(parents=True, exist_ok=True)
    copy_file(asset, refs / "hermes-delegation.md", st)
    changed = apply_map(sorted((out / "skills").rglob("*.md")), H1_MAP, map_name="H1_MAP", st=st)
    leftovers = []
    for p in sorted((out / "skills").rglob("*.md")):
        t = p.read_text(encoding="utf-8")
        leftovers.extend((str(p.relative_to(out)), tok) for tok in H1_FORBIDDEN if tok in t)
    if leftovers:
        raise ConvertError(f"H1 delegation contract incomplete: {leftovers}")
    st.fixes.append(
        f"H1: Hermes delegation contract applied across {changed} skill file(s) + "
        "skills/poteto-mode/references/hermes-delegation.md (route by pstack_role via the pstack "
        "delegation backend; model is dropped and readonly refused by core; read-only advisory + "
        "verified; cron ticks are fresh runs)"
    )


def apply_map(
    files: list[Path], mapping: list[tuple[str, str]], *, map_name: str, st: Stats
) -> int:
    """Apply an ordered (old, new) mapping to each file; count per-anchor hits.

    Replacement order within a file follows the mapping order, matching the
    historical inline loops exactly. Occurrences are recorded in
    st.anchor_hits for the post-build dead-anchor audit.
    """
    changed = 0
    for p in files:
        t = p.read_text(encoding="utf-8")
        t0 = t
        for i, (old, new) in enumerate(mapping):
            if old in t:
                st.anchor_hits[(map_name, i)] = st.anchor_hits.get((map_name, i), 0) + t.count(old)
                t = t.replace(old, new)
        if t != t0:
            p.write_bytes(t.encode("utf-8"))
            changed += 1
    return changed


def audit_anchor_hits(maps: dict[str, list[tuple[str, str]]], st: Stats) -> None:
    """Fail loudly when an audited transform anchor never matched upstream.

    Audited anchors are expected at the pinned source: zero hits package-wide
    means the passage the anchor fixes is gone or reworded and the transform
    silently did nothing - the failure mode this prohibits. Dead anchors must
    be pruned from the map (or the map updated) at pin-bump time.
    """
    st.anchor_audited = sum(len(m) for m in maps.values())
    dead = []
    for map_name, mapping in maps.items():
        for i, (old, _new) in enumerate(mapping):
            if st.anchor_hits.get((map_name, i), 0) == 0:
                dead.append(f"{map_name}[{i}] {old[:80]!r}")
    if dead:
        raise ConvertError(
            f"anchor audit: {len(dead)} transform anchor(s) never matched - "
            "upstream drifted; update or prune the map(s):\n  " + "\n  ".join(dead)
        )


def apply_delegation_translation(out: Path, st: Stats) -> None:
    changed = apply_map(
        sorted((out / "skills").rglob("*.md")), DELEGATION_MAP, map_name="DELEGATION_MAP", st=st
    )
    leftovers = []
    for p in sorted((out / "skills").rglob("*.md")):
        t = p.read_text(encoding="utf-8")
        for tok in DELEGATION_FORBIDDEN:
            if tok in t:
                leftovers.append((str(p.relative_to(out)), tok))
    if leftovers:
        raise ConvertError(f"delegation translation incomplete: {leftovers}")
    st.fixes.append(
        f"Phase-2A: delegation translation applied across {changed} skill "
        "files (subagent_type/Task/AskQuestion/run_in_background/cloud -> "
        "delegate_task + clarify + background execution)"
    )


def build_root_manifest(src_manifest_path: Path) -> dict:
    src = json.loads(src_manifest_path.read_text(encoding="utf-8-sig"))
    author = src.get("author")
    if not (
        isinstance(author, dict)
        and all(isinstance(v, str) for v in author.values())
        and set(author) <= {"name", "email", "url"}
    ):
        author = {"name": "Lauren Tan"}
    keywords = src.get("keywords")
    if not (isinstance(keywords, list) and all(isinstance(k, str) for k in keywords)):
        keywords = []
    manifest = {
        "$schema": SCHEMA_URL,
        "name": "pstack",
        "version": str(src.get("version", "0.14.4")),
        "description": str(src.get("description", "")),
        "author": author,
        "homepage": str(src.get("homepage", "")),
        "repository": str(src.get("repository", "")),
        "license": str(src.get("license", "MIT")) or "MIT",
        "keywords": keywords,
    }
    # Hard pre-flight against the loader's own rules (agent_plugins.py:39-41,114-141).
    if not MANIFEST_NAME_RE.match(manifest["name"]) or not (1 <= len(manifest["name"]) <= 64):
        raise ConvertError(f"manifest name fails loader regex: {manifest['name']!r}")
    for key in ("version", "description", "homepage", "repository", "license"):
        if not isinstance(manifest[key], str):
            raise ConvertError(f"manifest field {key} must be a string")
    if not manifest["description"]:
        raise ConvertError("manifest description is empty")
    return manifest


def _converted_at() -> str:
    """Build timestamp; SOURCE_DATE_EPOCH makes builds fully deterministic."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        return datetime.fromtimestamp(int(epoch), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_commit(source: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return out.stdout.strip()
    except Exception as exc:  # git missing, not a repo, ...
        return f"unknown (git rev-parse failed: {exc})"


def source_url(source: Path) -> str:
    """Public origin URL of the source clone (credentials stripped), or local."""
    try:
        out = subprocess.run(
            ["git", "-C", str(source), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        url = out.stdout.strip()
        # strip credentials (user:pass@) if present
        return re.sub(r"^[a-z]+://[^/@]+@", lambda m: m.group(0).split("://")[0] + "://", url)
    except Exception:
        return "(local build; no origin remote)"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0]
        if __doc__
        else "convert pstack to hermes agent-plugins-v1"
    )
    ap.add_argument(
        "--source",
        required=True,
        help="pstack clone root (read-only), e.g. a checkout of github.com/cursor/plugins",
    )
    ap.add_argument(
        "--out", default=str(DEFAULT_OUT), help="package dir to build (rebuilt each run)"
    )
    args = ap.parse_args()

    source = Path(args.source).resolve()
    out = Path(args.out).resolve()
    st = Stats()

    if not source.is_dir():
        raise ConvertError(f"source not found: {source}")
    src_manifest = source / ".cursor-plugin" / "plugin.json"
    if not src_manifest.is_file():
        raise ConvertError(f"source manifest missing: {src_manifest}")
    if out == source or source in out.parents:
        raise ConvertError(f"refusing to write inside the source tree: {out}")
    # Atomic-build pattern: construct in a temp sibling and swap into place only
    # on success, so a mid-build failure never clobbers the last good package.
    final_out = out
    build_dir = out.with_name(out.name + ".tmp-build")
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    out = build_dir

    commit = source_commit(source)

    # (a) skills/ with the grokbot flatten
    skills_src = source / "skills"
    skills_dst = out / "skills"
    flattened: list[str] = []
    excluded_make_bot_ui = False
    for child in sorted(p for p in skills_src.iterdir()):
        if not child.is_dir():
            raise ConvertError(f"unexpected non-directory under skills/: {child.name}")
        if child.name == "grokbot":
            # Study-era layout: make-bot-ui nested in a grokbot container.
            # F-publish: excluded from the hermes package (privilege-escalation
            # scan verdict; F33 deepest vendor coupling). Upstream has since
            # flattened the container themselves - both layouts are handled.
            for inner in sorted(p for p in child.iterdir()):
                if inner.name == "make-bot-ui":
                    excluded_make_bot_ui = True
                    st.fixes.append(
                        "F-publish: skills/grokbot/make-bot-ui excluded "
                        "(Tailscale privilege-escalation scan; F33)"
                    )
                    continue
                raise ConvertError(f"unexpected file under skills/grokbot/: {inner.name}")
            flattened.append("(grokbot container skipped)")
        elif child.name == "make-bot-ui":
            # Current upstream layout: the container is gone; exclude directly.
            excluded_make_bot_ui = True
            st.fixes.append(
                "F-publish: skills/make-bot-ui excluded "
                "(Tailscale privilege-escalation scan; F33; upstream "
                "flattened the grokbot container)"
            )
            flattened.append("(make-bot-ui excluded)")
        else:
            copy_tree(child, skills_dst / child.name, st)
    if flattened:
        st.warnings.append(f"excluded containers: {flattened}")

    # (a2) F-publish replacement: the excluded make-bot-ui slot (webhook-waked
    # control surface) is filled by a hermes-native skill built on the hermes
    # gateway's own stack (webhook subscriptions with X-Webhook-Signature-V2
    # HMAC, `hermes send`, `hermes peer`) - no Tailscale, no third-party bot
    # runtime. The asset lives in this repo so the build stays reproducible
    # without touching the upstream tree.
    if excluded_make_bot_ui:
        asset = SCRIPT_DIR / "assets" / "hermesbot" / "SKILL.md"
        if not asset.is_file():
            raise ConvertError(f"hermesbot replacement asset missing: {asset}")
        hermesbot_dst = out / "skills" / "hermesbot"
        if hermesbot_dst.exists():
            # The slot must contain ONLY repository-owned content: if upstream
            # ever ships its own skills/hermesbot/, the traversal above copied
            # it — remove the whole tree so nothing upstream survives inside
            # the replacement skill.
            shutil.rmtree(hermesbot_dst)
            st.fixes.append(
                "F-publish: upstream skills/hermesbot tree removed; "
                "slot replaced wholesale by the repository-owned asset"
            )
        hermesbot_dst.mkdir(parents=True)
        copy_file(asset, hermesbot_dst / "SKILL.md", st)
        st.fixes.append(
            "F-publish: make-bot-ui slot filled by hermes-native "
            "skills/hermesbot (tools/assets/hermesbot/SKILL.md; "
            "hermes gateway webhook/peer/send stack)"
        )

    # (b) frontmatter name fixes (name == dir name, kebab-case)
    for dirname, (old, new) in FRONTMATTER_FIXES.items():
        p = skills_dst / dirname / "SKILL.md"
        text = p.read_text(encoding="utf-8")  # already normalized by copy_file
        fixed, note = fix_frontmatter_name(text, old, new, f"skills/{dirname}/SKILL.md")
        p.write_bytes(fixed.encode("utf-8"))
        st.fixes.append(note)

    # (b2) Phase-1 hygiene transforms (R1, F16, F10-F12)
    apply_phase1_transforms(out, st)

    # (c) agents/ dropped — Hermes-native only, no Cursor dual-load.
    # (c2) PHASE-1.5 publisher exclusions: automations/benny (8 CRITICAL
    #     persistence findings in the install scanner: copy-instructions
    #     pattern-match) and skills/make-bot-ui (2 HIGH privilege_escalation:
    #     Tailscale curl|sudo sh) are EXCLUDED — hermes' CLI install BLOCKS on
    #     a dangerous verdict and --force cannot override. Both are also the
    #     study's own "what not to port" items; benny is rebuilt as hermes
    #     cron/loop jobs in Phase 4.
    comment_sicko = source / "agents" / "comment-sicko.md"
    if comment_sicko.is_file():
        no_comments_refs = out / "skills" / "no-comments" / "references"
        no_comments_refs.mkdir(parents=True, exist_ok=True)
        copy_file(comment_sicko, no_comments_refs / "comment-sicko.md", st)
        st.fixes.append(
            "Phase-2A: Comment Sicko persona copied into no-comments "
            "references for hermes prompt inlining"
        )
    st.fixes.append(
        "F-publish: automations/benny excluded (scanner persistence verdict) "
        "and skills/make-bot-ui excluded (scanner privilege verdict; F33)"
    )

    # (e) .cursor-plugin/ dropped — Hermes-native only, no Cursor dual-load.

    # (e2) assets/ copied verbatim + manifest-asset validation (fail loud on
    # dangling references: if upstream deletes/renames an asset while the Cursor
    # manifest still references it, the packaged manifest would ship broken).
    cp_manifest = json.loads(src_manifest.read_text(encoding="utf-8"))

    def _local_asset_refs(obj):
        refs = []
        if isinstance(obj, str):
            if re.match(r"^[A-Za-z0-9_\-./]+\.(png|jpg|jpeg|svg|gif|ico|webp)$", obj):
                refs.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                refs += _local_asset_refs(v)
        elif isinstance(obj, list):
            for v in obj:
                refs += _local_asset_refs(v)
        return refs

    manifest_refs = _local_asset_refs(cp_manifest)
    if (source / "assets").is_dir():
        assets_n = copy_tree(source / "assets", out / "assets", st)
        st.fixes.append(f"assets/: {assets_n} file(s) copied verbatim (referenced by the manifest)")
    missing_refs = [r for r in manifest_refs if not (out / r).exists()]
    if missing_refs:
        raise ConvertError("Cursor manifest references missing assets: " + ", ".join(missing_refs))

# (d) root plugin.json: strict whitelisted-field manifest (9 fields; hermes probes root only)
    manifest = build_root_manifest(src_manifest)
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    (out / "plugin.json").write_bytes(manifest_text.encode("utf-8"))
    st.fixes.append(
        "plugin.json: injected exact agent-plugins-v1 $schema at ROOT "
        "(9 whitelisted fields; displayName/category/tags/agents/skills omitted)"
    )

    # (g) LICENSE: upstream MIT + adaptation line
    lic_text = normalize_text((source / "LICENSE").read_bytes(), st)
    if lic_text is None:
        raise ConvertError("source LICENSE is not UTF-8 text")
    if "Lauren Tan" not in lic_text:
        st.warnings.append("source LICENSE does not mention 'Lauren Tan'; verify copyright line")
    body = lic_text.rstrip("\n")
    if ADAPTATION_LINE not in body:
        body += "\n\n" + ADAPTATION_LINE + "\n"
    (out / "LICENSE").write_bytes(body.encode("utf-8"))
    st.files_copied += 1

    # (g) README.md for the package
    discovered = sorted(
        p.name for p in skills_dst.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    )
    principles = [n for n in discovered if n.startswith("principle-")]
    workflow = [n for n in discovered if not n.startswith("principle-")]
    readme = f"""# pstack

*The pstack agent method — go fast by going deep first — packaged for the
Hermes Agent portable plugin path. Unofficial port of
<https://github.com/cursor/plugins/tree/main/pstack> (MIT, © Lauren Tan);
package keeps the upstream identity pstack v{manifest["version"]}.*

Independently maintained by honki12345 at
<https://github.com/honki12345/pstack-hermes>. The converter and tooling
derive from the earlier community port <https://github.com/iap/pstack-hermes>.
Not affiliated with Cursor, Lauren Tan, or iap; see `NOTICE.md` in the
repository.

## Install

Install the plugin:

```sh
hermes plugins install honki12345/pstack-hermes --enable
```

The repository root is the package. Unpinned installs track the default
branch and update with `hermes plugins update pstack`.

For an immutable install, pin a commit (release tags `v*` on `main` mark
tested ones). Pinned installs do not auto-update; move a pin by reinstalling
with the new SHA and `--force`:

```sh
hermes plugins install honki12345/pstack-hermes --ref <sha> --enable
hermes plugins install honki12345/pstack-hermes --ref <new-sha> --force --enable
```

## First run

1. `hermes plugins doctor pstack --ci` — manifest parses, all skills discover.
   Pick the target explicitly: `pstack` is ambiguous (repo root = local
   directory; elsewhere = installed package). Use the name as installed for
   the installed package, or a resolved local path for a branch tree.
2. Load the `setup-pstack` skill once - it writes `pstack-models.json` beside hermes' `config.yaml`, outside this package, so plugin updates do not discard it
   (the 18-role model panel the workflow skills read).
3. Load `poteto-mode` and give it a real task — it classifies the work and
   routes to the right playbook. You do not pick playbooks yourself.

Skills are opt-in on the portable path: load one with
`skill_view` id `agent-plugin-pstack-<digest>:<skill-name>` (digest = first
8 hex of the sha256 of the plugin key, stable while the install dir is named
`pstack`). For `/<name>` slash commands instead, add this package's `skills`
dir to `skills.external_dirs` in the hermes `config.yaml` — trade-off: skills
enter the prompt index with 60-char descriptions and lose the namespace.

## Update

```sh
hermes plugins update pstack
```

Unpinned root installs update in place. Pinned installs must be reinstalled
with a new SHA and `--force` (see [Install](#install)).

Installs created before the root-layout migration (the old
source-subdirectory route), or installed from another fork, cannot update to
this repository in place. Back up local edits, remove the old install, then
install the root package:

```sh
hermes plugins remove pstack
hermes plugins install honki12345/pstack-hermes --enable
```

If the old install was pinned, add `--ref <new-package-SHA>` to the install
command.

## Customizing skills

- **Tweak a line or two** — edit the installed `SKILL.md`, restart the
  gateway, and save a backup outside the plugin before updating. Git-backed
  updates autostash and re-apply local edits (conflicts stay in `git stash`);
  reinstalls replace shipped skills, so in-place edits are temporary.
- **Own several changed skills** — fork `honki12345/pstack-hermes`, commit, install from
  your fork (`hermes plugins install <you>/pstack-hermes --enable`).
- **Add your own new skills alongside** — a skill tap:
  `hermes skills tap add <you>/skills-repo`, then
  `hermes skills install <you>/skills-repo/<skill>`.
- Keep the hermes vocabulary when editing (`delegate_task`, `clarify`,
  `session_search`, `hermes cron`); Cursor primitives will mislead the agent.

Problems: port behaviour → <https://github.com/honki12345/pstack-hermes/issues>;
a hermes defect that reproduces with the plugin disabled →
`NousResearch/hermes-agent`; an upstream pstack bug → `cursor/plugins`.

## What hermes loads

- **{len(discovered)} skills** ({len(workflow)} workflow/mode +
  {len(principles)} `principle-*`), single-level `skills/<dir>/SKILL.md`,
  discovered via the root `plugin.json` (agent-plugins-v1 manifest,
  {len(manifest)} whitelisted fields).
- `skills/hermesbot/` — hermes-native control-surface skill on the gateway
  webhook (`X-Webhook-Signature-V2` HMAC) plus `hermes send` / `hermes peer`;
  fills the excluded `make-bot-ui` slot.
- Skill scripts (e.g. `skills/poteto-mode/scripts/`) ship verbatim; the loader
  never executes them, but skills instruct the agent to run them (`bun`, `gh`).

Not shipped: `automations/benny/` and `skills/make-bot-ui/` (install-scanner
verdicts — persistence / privilege escalation; `--force` cannot override) and
upstream's `docs/guide/` (read it upstream for the guided tour). This tree is
Hermes-native only; for real Cursor-side work install upstream pstack.

## Differences from upstream

| Pass | What changed |
|---|---|
| manifest | root `plugin.json` injected: exact agent-plugins-v1 `$schema`, 9-field whitelist |
| R1 | retired at c742dd6: upstream curates the principles index; the build only checks every entry resolves to a shipped leaf |
| F16 | retired at c47b1284: upstream ships a slug-agnostic lane regex (F16b still rejects a whitespace-only slug) |
| F11 | `worktree-audit.sh` reads worktree paths with spaces whole (F10/F12 GNU/BSD fixes retired: upstream ships them) |
| F-publish | `benny` + `make-bot-ui` excluded (scanner verdicts); `hermesbot` fills the slot; 3 localhost literals neutralized |
| T8/T9/T10 | `setup-pstack` writes the model panel beside hermes' `config.yaml` (outside the package, so updates do not discard it); discovery via `session_search`; hermes tool names |
| Phase-2A | delegation vocabulary → `delegate_task` / `clarify` package-wide |
| T11 | hardcoded Cursor paths → hermes equivalents; upstream guide links → upstream URLs |
| T12 | `/loop` → `hermes cron` wake (each tick a fresh run); check-plan accepts either marker |
| T13 | residual vendor coupling reworded (`cursor-team-kit` skills named as optional, cloud agents → local delegates) |
| P1 | `poteto-help` re-pointed at Hermes: install, no Custom Modes, panel file, `hermesbot` row |
| H1 | spawns route by `pstack_role` / `seat` / `access` (`skills/poteto-mode/references/hermes-delegation.md`); never `model` or `readonly` |

Text is otherwise unchanged; the machine-generated fix register in
`.build-provenance.txt` is the authoritative per-build record, and the
fork-level audit lives in the repository at `docs/fork/AUDIT-ko.md`.

## Known limitations (documented, not drift)

- `deslop`, `control-ui`, `control-cli` are named by playbooks but ship
  outside this package; a verdict without its demanded live lane is
  incomplete, not clean. (`no-comments`, `unslop`, `technical-writing` ship.)
- `statechart` installs TLC and Java with `mise` on its first run (network;
  the TLC jar is checksum-pinned), and its renderer guide installs the npm
  package `pele` outside the repo. Both happen only when the skill runs.
- `poteto-help` links upstream's `docs/guide/` pages, which this package does
  not ship; `/visualize` (named by `ketchup`) is a harness feature, not a
  pstack skill.
- No `mcp.json` shipped: research skills use whichever MCP servers the
  session configures; unreachable sources are recorded as null findings,
  never invented.

---

{ADAPTATION_LINE}
"""
    (out / "README.md").write_bytes(readme.encode("utf-8"))
    st.files_copied += 1
    # First-run guidance lives in README / docs/USING.md — no after-install.md.

    # provenance
    conv_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    # Fixes are derived from st.fixes (what actually ran), never hardcoded —
    # a stale claim here would defeat the provenance's purpose.
    fix_lines = "\n".join(f"  - {f}" for f in st.fixes)
    fix_lines += (
        f"\n  - text normalization: UTF-8 without BOM, LF line endings "
        f"(files normalized: {st.text_normalized}, BOM stripped: {st.bom_stripped}, "
        f"CRLF fixed: {st.crlf_fixed})"
    )
    if st.path_fixes:
        fix_lines += (
            f"\n  - repo-root layout: rewrote stale pstack/skills path(s) in "
            f"{st.path_fixes} file(s)"
        )
    if st.anchor_audited:
        fix_lines += (
            f"\n  - anchor audit: {len(st.anchor_hits)}/{st.anchor_audited} transform "
            f"anchors applied (0 dead; the converter fails loudly on upstream drift)"
        )
    provenance = f"""package: pstack (hermes agent-plugins-v1, Phase-0 conversion)
source: (local clone; public origin in source_url)
source_url: {source_url(source)}
source_commit: {commit}
converted_at: {_converted_at()}
converter: convert.py (sha256[:16]={conv_sha})
schema: {SCHEMA_URL}
fixes:
{fix_lines}
counts:
  skills_loaded: {len(discovered)} ({len(workflow)} workflow/mode + {len(principles)} principle)
  skill_md_on_disk: {len(discovered)} (skills/ only; automations/benny + make-bot-ui excluded for the install scanner)
  agents_files: 0
notes:
  - agents/ and .cursor-plugin/ are not emitted (Hermes-native package; no Cursor dual-load)
  - automations/benny + make-bot-ui excluded (install-scanner verdict); rebuild via Phase 4 / stay Cursor-side
  - hermes probes the root manifest only; no .cursor-plugin/ is shipped
"""
    (out / ".build-provenance.txt").write_bytes(provenance.encode("utf-8"))
    st.files_copied += 1

    # ---- atomic swap: build succeeded, move into place ----
    # When output is the repo root (.), copy files in place instead of
    # renaming the entire directory (which would clobber tooling).
    if final_out == REPO_ROOT:
        pkg_entries = [p.name for p in out.iterdir()]
        backup_dir = final_out.parent / (final_out.name + ".pkg-backup")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir(parents=True)
        backed_up = []
        created = []
        try:
            # Backup existing package entries
            for name in pkg_entries:
                dest = final_out / name
                if dest.exists():
                    bpath = backup_dir / name
                    if dest.is_dir():
                        shutil.copytree(dest, bpath)
                    else:
                        shutil.copy2(dest, bpath)
                    backed_up.append(name)
            # Remove obsolete package entries that might exist
            for name in ["plugin.yaml", "plugin.json.bak", "after-install.md"]:
                d = final_out / name
                if d.exists() and name not in pkg_entries:
                    bpath = backup_dir / name
                    if d.is_dir():
                        shutil.copytree(d, bpath)
                    else:
                        shutil.copy2(d, bpath)
                    backed_up.append(name)
                    if d.is_dir():
                        shutil.rmtree(d)
                    else:
                        d.unlink()
            # Also clean up known package dirs if missing from new build
            for name in ["skills", "config", "assets"]:
                if name not in pkg_entries:
                    d = final_out / name
                    if d.exists():
                        # backup if exists
                        bpath = backup_dir / name
                        if d.is_symlink():
                            bpath.symlink_to(os.readlink(d))
                        elif d.is_dir():
                            shutil.copytree(d, bpath)
                        else:
                            shutil.copy2(d, bpath)
                        backed_up.append(name)
                        if d.is_symlink():
                            d.unlink()
                        elif d.is_dir():
                            shutil.rmtree(d)
                        else:
                            d.unlink()
            # Replace entries
            for item in out.iterdir():
                dest = final_out / item.name
                existed = dest.exists()
                if dest.exists() or dest.is_symlink():
                    if dest.is_symlink():
                        dest.unlink()
                    elif dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                if item.is_symlink():
                    dest.symlink_to(os.readlink(item))
                elif item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
                if not existed:
                    created.append(item.name)
            out = final_out
            shutil.rmtree(build_dir, ignore_errors=True)
            shutil.rmtree(backup_dir, ignore_errors=True)
        except Exception:
            # Rollback: remove newly created entries first, then restore backed up
            for name in created:
                dest = final_out / name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
            for name in backed_up:
                dest = final_out / name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                bpath = backup_dir / name
                if bpath.exists():
                    if bpath.is_dir():
                        shutil.copytree(bpath, dest)
                    else:
                        shutil.copy2(bpath, dest)
            shutil.rmtree(build_dir, ignore_errors=True)
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise
    else:
        prev = final_out.with_name(final_out.name + ".prev-build")
        if prev.exists():
            shutil.rmtree(prev)
        if final_out.exists():
            final_out.rename(prev)
        try:
            out.rename(final_out)
        except OSError:
            if prev.exists():
                prev.rename(final_out)
            raise
        out = final_out
        if prev.exists():
            shutil.rmtree(prev, ignore_errors=True)

    # ---- summary ----
    print("== convert.py summary ==")
    print(f"source: {source} @ {commit[:12]}")
    print(f"out:    {out}")
    print(
        f"files copied: {st.files_copied} (text normalized: {st.text_normalized}, byte-copied: {st.byte_copied}; BOM stripped: {st.bom_stripped}, CRLF fixed: {st.crlf_fixed})"
    )
    print(
        f"skills discovered (portable, immediate children with SKILL.md): {len(discovered)} "
        f"({len(workflow)} workflow/mode + {len(principles)} principle-*)"
    )
    skill_md_on_disk = len(list((out / "skills").glob("*/SKILL.md")))
    print(
        f"SKILL.md on disk: {skill_md_on_disk} (skills/ only; automations/benny + make-bot-ui excluded)"
    )
    print("fixes applied:")
    for f in st.fixes:
        print(f"  - {f}")
    print("warnings:")
    for w in st.warnings or ["(none)"]:
        print(f"  - {w}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConvertError as exc:
        print(f"CONVERT ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
