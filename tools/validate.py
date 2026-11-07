#!/usr/bin/env python3
"""Validation harness for the converted pstack -> hermes agent-plugins-v1 package.

Layers:
  [1] Static manifest + structure checks (pure stdlib; mirrors hermes_cli/agent_plugins.py rules)
  [2] Skill frontmatter checks (name == dir name, kebab-case, description 1..1024)
  [3] Encoding checks (no BOM, no CRLF in any normalized text file)
  [4] GOLD check: the REAL hermes loader (read_agent_plugin_manifest) via the hermes venv python
  [5] GOLD+ check: full load_agent_plugin() discovery (counts skills, surfaces per-skill diagnostics)
  [6] READ-ONLY CLI attempt: `hermes.exe plugins doctor <pkg> --ci` (verbatim capture; errors are
      findings to record, not package failures — the doctor signature is verified in parallel)

Usage: python validate.py [--package <package-dir>] [--require-gold]
Exit codes: 0 = all hard checks passed (the loader checks are skipped only when hermes is
absent and --require-gold was not passed); 1 = hard static failure; 2 = the loader ran and
did not accept the package, or, without --require-gold, the loader environment exists but is
broken; 3 = --require-gold was passed and the loader checks did not complete because the
loader was absent or broken. CI passes the flag so a green run can never mean "the real
loader was never asked".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent  # tools/
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_PACKAGE = REPO_ROOT

sys.path.insert(0, str(SCRIPT_DIR))
from bans import (  # noqa: E402
    DELEGATION_VOCAB_BANS,
    find_violations,
    is_generated_dependency_path,
    is_package_path,
)
from convert import MANIFEST_NAME_RE, SCHEMA_URL, SKILL_NAME_RE, TEXT_EXTS  # noqa: E402


def _hermes_home_default() -> str:
    if sys.platform == "win32":
        return str(Path.home() / "AppData" / "Local" / "hermes")
    return str(Path.home() / ".hermes")


# NOTE (issue #130 item 1): --no-install-project relies on agent/hermes_platform/
# hermes_cli being root-level packages in the hermes tree. If upstream moves to
# a src/ layout or installed distributions, this gate fails with ImportError.
# HERMES_HOME must point at a hermes checkout that has a .venv; CI uses
# --require-gold so an absent loader is never mistaken for a pass.
_hermes_home = os.environ.get("HERMES_HOME") or _hermes_home_default()
HERMES_SRC = Path(_hermes_home) / "hermes-agent"
if sys.platform == "win32":
    VENV_PY = HERMES_SRC / ".venv" / "Scripts" / "python.exe"
    VENV_HERMES = HERMES_SRC / ".venv" / "Scripts" / "hermes.exe"
else:
    VENV_PY = HERMES_SRC / ".venv" / "bin" / "python"
    VENV_HERMES = HERMES_SRC / ".venv" / "bin" / "hermes"


def expected_count(pkg: Path) -> int:
    """Expected skill count per the converter's discovery rule:
    immediate children of <pkg>/skills/ containing a SKILL.md."""
    return sum(1 for d in (pkg / "skills").iterdir()
               if d.is_dir() and (d / "SKILL.md").is_file())


def expected_principles(pkg: Path) -> int:
    return sum(1 for d in (pkg / "skills").iterdir()
               if d.is_dir() and d.name.startswith("principle-")
               and (d / "SKILL.md").is_file())

# The loader TOLERATES one more manifest field than convert.py emits ("extensions");
# this is validate's superset whitelist, distinct from the converter's 9-field output set.
LOADER_WHITELIST = {"$schema", "name", "version", "description", "author", "homepage",
                    "repository", "license", "keywords", "extensions"}
# Filenames the encoding check touches even without a text suffix (dotfiles carry none).
TEXT_NAMES = {".gitignore", ".build-provenance.txt"}
# Model slugs in the repo-owned panel must be provider-qualified and lowercase
# (vendor/model[:variant], e.g. z-ai/glm-5.2 or …:free) or the inherit-parent
# selector. Catches hand-edit slips like a missing vendor prefix or `-free`
# where the catalog's free suffix is `:free`.
MODEL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*(:[a-z0-9_-]+)?$")
ROLE_VALUE_SHAPES = {
    "feature, refactoring": str,
    "bug-fix": str,
    "perf-issue": str,
    "hillclimb": str,
    "judgment and prose": str,
    "hardest tasks": str,
    "how explorer": str,
    "how explainer": str,
    "how critics": list,
    "why investigators": str,
    "why synthesizer": str,
    "reflect tooling": str,
    "reflect judgment, divergent, synthesizer": str,
    "arena runners": list,
    "arena cross-judge pool": list,
    "swarm workers": str,
    "architect runners": list,
    "interrogate reviewers": list,
}
HERMES_ADAPTATION_BANS = {
    "persona:": "delegate personas are inert in hermes portable plugins; inline the prompt",
    "read-only delegates lose MCP access": "readonly-MCP rationale is stale for hermes",
    "Read, Grep, Glob": "Cursor tool names survived in hermes-facing prose",
    "<ABSOLUTE_PATH>": "reflect reviewers must use a session id or digest, not a transcript path",
    "parallel cloud workers": "delegate_task background work is process-local, not a cloud fleet",
    "cloud concurrency limit": "hermes swarm sizing is worker count, not cloud concurrency",
    "`inherit-parent` or `auto`": "hermes has no Cursor-style auto selector",
}


class Report:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.notes: list[str] = []

    def ok(self, msg: str) -> None:
        self.passed.append(msg)

    def fail(self, msg: str) -> None:
        self.failed.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def parse_frontmatter(text: str):
    """Minimal stdlib frontmatter reader: returns (fields|None, error|None)."""
    if not text.startswith("---\n"):
        return None, "no frontmatter block at file start"
    end = text.find("\n---", 4)
    if end == -1:
        return None, "frontmatter block unterminated"
    lines = text[4:end].split("\n")
    fields: dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = re.match(r"^([A-Za-z0-9_-]+):(.*)$", lines[i])
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        if rest in (">", ">-", "|", "|+", "|-"):
            i += 1
            buf: list[str] = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or lines[i] == ""):
                if lines[i].strip():
                    buf.append(lines[i].strip())
                i += 1
            fields[key] = (" " if rest.startswith(">") else "\n").join(buf)
            continue
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "'\"":
            rest = rest[1:-1]
        fields[key] = rest
        i += 1
    return fields, None


def check_manifest(pkg: Path, rep: Report) -> dict:
    p = pkg / "plugin.json"
    if not p.is_file():
        rep.fail("root plugin.json missing")
        return {}
    if (pkg / "plugin.yaml").exists():
        rep.fail("stray plugin.yaml present (agent plugins require plugin.json only)")
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        rep.fail(f"root {p.name} has a UTF-8 BOM")
    if b"\r\n" in raw:
        rep.fail(f"root {p.name} contains CRLF")
    try:
        if p.suffix == ".json":
            m = json.loads(raw.decode("utf-8"))
        else:
            m = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:
        rep.fail(f"root {p.name} is not valid UTF-8 {p.suffix.lstrip('.')}: {exc}")
        return {}
    if not isinstance(m, dict):
        rep.fail(f"root {p.name} is not an object")
        return {}
    if m.get("$schema") != SCHEMA_URL:
        rep.fail(f"$schema must be exactly {SCHEMA_URL!r}, got {m.get('$schema')!r}")
    else:
        rep.ok("$schema exact-match agent-plugins.org v1")
    unknown = sorted(set(m) - LOADER_WHITELIST)
    if unknown:
        rep.fail(f"manifest has non-whitelisted fields (loader would emit ignored-field diagnostics): {unknown}")
    else:
        rep.ok(f"manifest keys subset of the 10-field loader whitelist (fields present: {len(m)})")
    name = m.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not MANIFEST_NAME_RE.match(name):
        rep.fail(f"manifest name fails loader rules: {name!r}")
    else:
        rep.ok(f"manifest name valid: {name!r}")
    for k in ("version", "description", "homepage", "repository", "license"):
        if k in m and not isinstance(m[k], str):
            rep.fail(f"manifest {k} must be a string, got {type(m[k]).__name__}")
    if not m.get("description"):
        rep.note("manifest description empty/absent (loader tolerates but docs suffer)")
    kw = m.get("keywords")
    if kw is not None and not (isinstance(kw, list) and all(isinstance(x, str) for x in kw)):
        rep.fail("manifest keywords must be an array of strings")
    au = m.get("author")
    if au is not None and not (isinstance(au, dict) and set(au) <= {"name", "email", "url"}
                               and all(isinstance(v, str) for v in au.values())):
        rep.fail("manifest author must be an object with only string name/email/url")
    if isinstance(au, dict) and au.get("name") == "Lauren Tan":
        rep.ok("manifest author preserved: Lauren Tan")
    return m


def check_skills(pkg: Path, rep: Report) -> None:
    skills_dir = pkg / "skills"
    if not skills_dir.is_dir():
        rep.fail("skills/ directory missing")
        return
    children = sorted(d for d in skills_dir.iterdir() if d.is_dir())
    no_skill_md = [d.name for d in children if not (d / "SKILL.md").is_file()]
    if no_skill_md:
        rep.fail(f"skills/ immediate children without SKILL.md (loader would skip them): {no_skill_md}")
    nested = sorted(str(p.relative_to(pkg)) for p in skills_dir.glob("*/*/SKILL.md"))
    if nested:
        rep.fail(f"nested SKILL.md found (loader sees immediate children only): {nested}")
    else:
        rep.ok("no nested skill directories under skills/")
    loaded, bad_name, bad_desc = [], [], []
    principles = []
    seen_casefold: dict[str, str] = {}
    for d in children:
        if not (d / "SKILL.md").is_file():
            continue
        key = d.name.casefold()
        if key in seen_casefold:
            rep.fail(f"case-insensitive dir collision on Windows: {d.name} vs {seen_casefold[key]}")
        seen_casefold[key] = d.name
        fm, err = parse_frontmatter((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        if fm is None:
            rep.fail(f"skills/{d.name}/SKILL.md: {err}")
            continue
        if fm.get("name") != d.name or not SKILL_NAME_RE.match(fm.get("name", "")):
            bad_name.append(f"{d.name}: name={fm.get('name')!r}")
        desc = fm.get("description", "")
        if not desc or not (1 <= len(desc) <= 1024):
            bad_desc.append(f"{d.name}: description length={len(desc)}")
        loaded.append(d.name)
        if d.name.startswith("principle-"):
            principles.append(d.name)
    if bad_name:
        rep.fail(f"frontmatter name != dir name / not kebab-case: {bad_name}")
    else:
        rep.ok(f"all {len(loaded)} skills: frontmatter name == dir name, kebab-case")
    if bad_desc:
        rep.fail(f"description missing or >1024 chars: {bad_desc}")
    else:
        rep.ok(f"all {len(loaded)} skills: description present, 1..1024 chars")
    workflow = [n for n in loaded if not n.startswith("principle-")]
    rep.note(f"skills discovered: {len(loaded)} "
             f"({len(workflow)} workflow/mode + {len(principles)} principle-*)")
    expected, exp_principles = expected_count(pkg), expected_principles(pkg)
    if len(loaded) != expected or len(principles) != exp_principles or len(workflow) != expected - exp_principles:
        rep.fail(f"expected {expected} loaded skills ({expected - exp_principles} workflow/mode + "
                 f"{exp_principles} principle), got {len(loaded)} ({len(workflow)} + {len(principles)})")
    benny = sorted(str(p.relative_to(pkg)) for p in (pkg / "automations").glob("benny/skills/*/SKILL.md")) if (pkg / "automations").is_dir() else []
    if benny or (pkg / "automations").exists():
        rep.fail("F-publish: automations/ present — excluded by publisher contract (install-scanner persistence verdict)")
    else:
        rep.ok("F-publish: automations/benny excluded (install-scanner persistence verdict)")
    if "make-bot-ui" in loaded:
        rep.fail("F-publish: make-bot-ui present — excluded by publisher contract (privilege-escalation scan, F33)")
    else:
        rep.ok("F-publish: make-bot-ui excluded (privilege-escalation scan, F33)")
    total_skill_md = len(list(pkg.glob("skills/*/SKILL.md")))
    rep.note(f"SKILL_MD files on disk: {total_skill_md} (expected {expected})")
    if total_skill_md != expected:
        rep.fail(f"expected {expected} SKILL.md on disk, found {total_skill_md}")

    # Issue #107: inline-shell false trigger in TS patterns
    bad_ts = []
    for p in (pkg / "skills").rglob("*.md"):
        try:
            t = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if "The tells are `!`" in t:
            bad_ts.append(str(p.relative_to(pkg)))
    if bad_ts:
        rep.fail(f"inline-shell trigger `!` in TS patterns (issue #107): {bad_ts}")
    else:
        rep.ok("issue #107: no inline-shell `!` trigger in TS patterns")


def check_encoding(pkg: Path, rep: Report) -> None:
    bom_files, crlf_files, checked = [], [], 0
    for p in pkg.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(pkg)
        if not is_package_path(rel):
            continue
        if is_generated_dependency_path(rel):
            continue
        if p.suffix.lower() not in TEXT_EXTS and p.name not in TEXT_NAMES:
            continue
        data = p.read_bytes()
        checked += 1
        if data.startswith(b"\xef\xbb\xbf"):
            bom_files.append(str(rel))
        if b"\r\n" in data:
            crlf_files.append(str(rel))
    if bom_files:
        rep.fail(f"files with UTF-8 BOM: {bom_files}")
    else:
        rep.ok(f"no BOM in any of {checked} text files")
    if crlf_files:
        rep.fail(f"files with CRLF: {crlf_files}")
    else:
        rep.ok(f"no CRLF in any of {checked} text files")


def check_layout(pkg: Path, rep: Report) -> None:
    for rel, label in [("README.md", "README.md"),
                       (".build-provenance.txt", ".build-provenance.txt"),
                       ("plugin.json", "plugin.json")]:
        if (pkg / rel).exists():
            rep.ok(f"present: {label}")
        else:
            rep.fail(f"missing: {label}")
    if (pkg / "plugin.yaml").exists():
        rep.fail("stray plugin.yaml present (agent plugins require plugin.json only)")
    # LICENSE moved to the repo root (repo layout v2); either location satisfies.
    if (pkg / "LICENSE").exists():
        rep.ok("present: LICENSE (package)")
    elif (REPO_ROOT / "LICENSE").exists():
        rep.ok("present: LICENSE (repo root)")
    else:
        rep.fail("missing: LICENSE")
    if (pkg / "automations").exists():
        rep.fail("F-publish: automations/ present (must be excluded)")


def check_phase1(pkg: Path, rep: Report) -> None:
    """Phase-1 hygiene checks: R1 index generation, F16 lane-shape invariant, F10-F12 portability."""
    # R1: the shipped index must equal what the leaves generate
    try:
        from convert import build_principles_index  # noqa: PLC0415
        generated, drift = build_principles_index(pkg / "skills")
        if drift:
            rep.fail(f"principles index generation reported: {drift}")
        pm = pkg / "skills" / "poteto-mode" / "SKILL.md"
        text = pm.read_text(encoding="utf-8")
        start = text.find("## Principles\n\nRead the leaf skill")
        end = text.find("## Autonomy", start)
        if start == -1 or end == -1:
            rep.fail("poteto-mode/SKILL.md: principles block anchors missing")
        elif text[start:end].rstrip("\n") != generated.rstrip("\n"):
            rep.fail("R1: shipped principles index does NOT match the leaves (drift reintroduced)")
        else:
            rep.ok("R1: poteto-mode principles index == leaf-generated (single source of truth)")
    except Exception as exc:  # import or generation failure is itself a finding
        rep.fail(f"R1 check could not run: {exc}")

    # F16 (retired transform): the converter no longer rewrites these - the
    # checks assert the upstream c47b1284 shapes survive conversion.
    cp = pkg / "skills" / "poteto-mode" / "scripts" / "check-plan.mjs"
    if cp.is_file():
        cp_text = cp.read_text(encoding="utf-8")
        lanes = ("const LANES = /Ten lanes on `(?=[^`<>]*[^\\s`<>])[^`<>]+`"
                 " at the PR head/;")
        if lanes in cp_text:
            rep.ok("F16: check-plan.mjs carries the exact slug-agnostic LANES "
                   "declaration (F16b form)")
        else:
            rep.fail("F16: check-plan.mjs LANES declaration is not the exact "
                     "slug-agnostic F16b regex")

    # F16 companion: the template keeps its lane-shape sentence.
    mp = pkg / "skills" / "poteto-mode" / "playbooks" / "multi-phase-plan.md"
    if mp.is_file():
        mp_text = mp.read_text(encoding="utf-8")
        if "Ten lanes on" in mp_text and "at the PR head" in mp_text:
            rep.ok("F16: multi-phase-plan keeps the lane-shape sentence")
        else:
            rep.fail("F16: multi-phase-plan lost the lane-shape sentence")

    # F10-F12: worktree-audit.sh must use portable helpers, no unguarded BSD forms
    wa = pkg / "skills" / "poteto-mode" / "scripts" / "worktree-audit.sh"
    if wa.is_file():
        wa_text = wa.read_text(encoding="utf-8")
        bad = [s for s in ("stat -f '%m %N'", 'date -r "$last_ts"', "awk '/^worktree /{print $2}") if s in wa_text]
        # The GNU/BSD date discriminator must be an output-comparing epoch probe:
        # BSD `date -d` exists with unrelated (kernel DST) semantics, so a plain
        # exit-status probe on `-d @0` can misdetect and silently pick GNU syntax.
        ambiguous_probe = "date -d @0 '+%Y-%m-%d'" in wa_text
        # Must be the output-comparing form: an exit-status-only probe on
        # `date -d @0 '+%s'` would still misdetect on BSD and pass a bare
        # substring check, so the command substitution AND the `= "0"` test
        # are both required.
        strict_probe = ("\"$(date -d @0 '+%s'" in wa_text
                        and '= "0" ]' in wa_text)
        good = [s for s in ("stat_mtime", "date_epoch") if s in wa_text]
        if bad:
            rep.fail(f"F10-F12: BSD-only constructs survived in worktree-audit.sh: {bad}")
        elif ambiguous_probe or not strict_probe:
            rep.fail("F10-F12: date_epoch uses an ambiguous GNU/BSD probe (exit-status "
                     "on 'date -d @0'); the epoch-comparing probe ('+%s' == 0) is required")
        elif len(good) < 2:
            rep.fail("F10-F12: portable helpers missing from worktree-audit.sh")
        else:
            rep.ok("F10-F12: worktree-audit.sh portable (stat_mtime/date_epoch, full-path awk)")


    # Phase-2A: delegation translation must be complete
    # (DELEGATION_VOCAB_BANS is the canonical set, imported from bans.py)
    stragglers = []
    for p in (pkg / "skills").rglob("*.md"):
        t = p.read_text(encoding="utf-8", errors="replace")
        for tok in DELEGATION_VOCAB_BANS:
            if tok in t:
                stragglers.append(f"{p.relative_to(pkg)}:{tok}")
    if stragglers:
        rep.fail(f"Phase-2A: Cursor delegation tokens remain: {stragglers}")
    else:
        rep.ok("Phase-2A: no Cursor delegation tokens in any skill")
    pm_text = (pkg / "skills" / "poteto-mode" / "SKILL.md").read_text(encoding="utf-8")
    if "delegate_task" in pm_text:
        rep.ok("Phase-2A: poteto-mode Subagents section uses delegate_task")
    else:
        rep.fail("Phase-2A: poteto-mode has no delegate_task phrasing")

    # T8: factual fixes from the hermes deep review
    t8_bad = []
    t8_patterns = {"strips MCP": "readonly-MCP rationale",
                   'environment: "local"': "swarm cloud param",
                   "cloud_base_branch": "swarm cloud param",
                   "Use Glob": "Cursor tool name",
                   "../principle-guard-the-context-window/SKILL.md)": "relative skill link"}
    for p in (pkg / "skills").rglob("*.md"):
        t = p.read_text(encoding="utf-8", errors="replace")
        for tok, label in t8_patterns.items():
            if tok in t:
                t8_bad.append(f"{p.relative_to(pkg)}:{label}")
    sp_note = (pkg / "skills" / "why" / "references" / "source-playbook.md")
    if sp_note.is_file() and "Cursor MCP examples" not in sp_note.read_text(encoding="utf-8"):
        t8_bad.append("why/references/source-playbook.md:missing adaptation note")
    if t8_bad:
        rep.fail(f"T8: unresolved constructs: {t8_bad}")
    else:
        rep.ok("T8: readonly-MCP rationale, swarm params, tool names, doc links all fixed")

    # T9/T10: hermes-native discovery + config
    t9_bad = []
    t9_toks = {"~/.cursor/projects": "Cursor transcript path",
               "available-tools map": "Cursor MCP discovery",
               "agent-transcripts": "Cursor transcripts dir",
               "ls -t <": "Cursor ls recipe",
               "create-skill": "Cursor skill-creation ref"}
    for p in (pkg / "skills").rglob("*.md"):
        t = p.read_text(encoding="utf-8", errors="replace")
        for tok, label in t9_toks.items():
            if tok in t:
                t9_bad.append(f"{p.relative_to(pkg)}:{tok} ({label})")
    if t9_bad:
        rep.fail(f"T9: Cursor discovery tokens remain: {t9_bad}")
    else:
        rep.ok("T9: discovery sections hermes-native (no Cursor tokens)")
    cfg = pkg / "config" / "models.json"
    if not cfg.is_file():
        rep.fail("T10: config/models.json missing")
    else:
        try:
            roles = json.loads(cfg.read_text(encoding="utf-8")).get("roles", {})
            def _ok_value(v):
                vals = v if isinstance(v, list) else [v]
                return bool(vals) and all(isinstance(x, str) and x.strip() for x in vals)
            if len(roles) == 18 and all(_ok_value(v) for v in roles.values()):
                rep.ok(f"T10: config/models.json present, {len(roles)} roles, string/array values")
            else:
                rep.fail("T10: config/models.json shape unexpected")
        except Exception as exc:
            rep.fail(f"T10: config/models.json unparseable: {exc}")

    # F-publish: no banned constructs anywhere in the shipped package
    # (definitions shared with the CI scanner gate via tools/bans.py)
    violations = find_violations(pkg)
    if violations:
        for v in violations:
            rep.fail(f"F-publish: banned construct present: {v}")
    else:
        rep.ok("F-publish: no banned constructs in any decodable package file "
                "(security bans package-wide, delegation vocab hermes-facing)")


def check_conflict_markers(pkg: Path, rep: Report) -> None:
    """No unresolved VCS conflict markers may survive into generated output.

    Only the unambiguous markers are checked: `<<<<<<<`/`>>>>>>>`/`|||||||`
    line starts. A bare `=======` line is a legal setext heading underline in
    markdown and is therefore not flagged.
    """
    markers = ("<<<<<<<", ">>>>>>>", "|||||||")
    bad = []
    for p in sorted(pkg.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(pkg)
        if not is_package_path(rel):
            continue
        if is_generated_dependency_path(rel):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.startswith(markers):
                bad.append(f"{rel}:{lineno}:{line[:12]}")
    if bad:
        rep.fail(f"conflict markers present in generated output: {bad}")
    else:
        rep.ok("no VCS conflict markers in package")
        rep.ok("conflict markers: none in any package file")


def check_hermes_adaptation_contract(pkg: Path, rep: Report) -> None:
    """Hermes-specific instruction contract checks for generated skills."""
    bad = []
    for p in (pkg / "skills").rglob("*.md"):
        t = p.read_text(encoding="utf-8", errors="replace")
        for tok, label in HERMES_ADAPTATION_BANS.items():
            if tok in t:
                bad.append(f"{p.relative_to(pkg)}:{tok} ({label})")
    if bad:
        rep.fail(f"Hermes adaptation contract: unresolved constructs: {bad}")
    else:
        rep.ok("Hermes adaptation contract: no inert personas, stale runtime claims, "
               "Cursor tool names, transcript paths, cloud-worker wording, or auto aliases")


def check_model_panel(pkg: Path, rep: Report, asset: Path | None = None) -> None:
    """Stage-D consistency: shipped config/models.json == the repo-owned panel."""
    if asset is None:
        asset = SCRIPT_DIR / "assets" / "model-panel.json"
    if not asset.is_file():
        rep.note("no repo-owned model panel asset present; shipped config not compared")
        return
    cfg = pkg / "config" / "models.json"
    if not cfg.is_file():
        rep.fail("Stage-D: config/models.json missing while a repo model panel exists")
        return
    try:
        panel_roles = json.loads(asset.read_text(encoding="utf-8")).get("roles", {})
        cfg_roles = json.loads(cfg.read_text(encoding="utf-8")).get("roles", {})
    except Exception as exc:
        rep.fail(f"Stage-D: model panel / config not parseable JSON: {exc}")
        return

    def bad_slugs(roles: dict) -> list[str]:
        bad = []
        for role, value in roles.items():
            for slug in value if isinstance(value, list) else [value]:
                # Non-string JSON values (numbers, nulls) are reported, never
                # handed to the regex — a TypeError here would abort the whole
                # validation instead of returning the malformed config.
                if not isinstance(slug, str) or not (
                    slug == "inherit-parent" or MODEL_SLUG_RE.match(slug)
                ):
                    bad.append(f"{role}: {slug!r}")
        return bad

    slug_problems = [f"panel {s}" for s in bad_slugs(panel_roles)] + \
                    [f"config {s}" for s in bad_slugs(cfg_roles)]
    if slug_problems:
        rep.fail("Stage-D: model slugs not provider-qualified lowercase "
                 f"vendor/model[:variant]: {slug_problems}")

    shape_problems = []
    for source, roles in (("panel", panel_roles), ("config", cfg_roles)):
        for role, expected_type in ROLE_VALUE_SHAPES.items():
            if role not in roles:
                continue
            value = roles[role]
            if not isinstance(value, expected_type):
                shape = "array" if expected_type is list else "string"
                shape_problems.append(f"{source} '{role}' must be {shape}")
    if shape_problems:
        rep.fail(f"Stage-D: role value shape mismatch: {shape_problems}")

    # One subagent runs per array entry, so a duplicate concrete slug inside a
    # role spawns an identical lane — always an accident (e.g. two spellings
    # of the same model normalized to one slug), never a real fan-out choice.
    # The "inherit-parent" selector is exempt: repeated selector entries are a
    # deliberate multi-lane configuration (each entry still spawns its own lane
    # on the parent's model), and the token is not a provider slug.
    dup_problems = []
    for source, roles in (("panel", panel_roles), ("config", cfg_roles)):
        for role, value in roles.items():
            if isinstance(value, list):
                strs = [s for s in value
                        if isinstance(s, str) and s != "inherit-parent"]
                dupes = sorted({s for s in strs if strs.count(s) > 1})
                if dupes:
                    dup_problems.append(f"{source} '{role}': {dupes}")
    if dup_problems:
        rep.fail(f"Stage-D: duplicate slugs inside a role array: {dup_problems}")

    if panel_roles != cfg_roles:
        details = []
        only_panel = sorted(set(panel_roles) - set(cfg_roles))
        only_cfg = sorted(set(cfg_roles) - set(panel_roles))
        differing = sorted(k for k in set(panel_roles) & set(cfg_roles)
                           if panel_roles[k] != cfg_roles[k])
        if only_panel:
            details.append(f"roles only in panel: {only_panel}")
        if only_cfg:
            details.append(f"roles only in config: {only_cfg}")
        if differing:
            details.append(f"values differ: {differing}")
        rep.fail("Stage-D: shipped config/models.json does not match the repo model "
                 "panel (" + "; ".join(details) + ")")
    else:
        rep.ok(f"Stage-D: config/models.json == repo model panel ({len(cfg_roles)} roles)")


def subprocess_env() -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # never write .pyc into the hermes install dir
    return env


# Printed by each loader subprocess immediately after the import succeeds, so the
# parent can tell "the loader was never reached" from "the loader ran and refused
# the package" without guessing from exception names. A SyntaxError or a
# module-level RuntimeError during import is neither an ImportError nor a
# rejection, and inferring from the last traceback line got that wrong.
LOADER_IMPORT_MARKER = "hermes-loader-imported"


def _loader_failure_status(stderr: str) -> str:
    """Classify a loader subprocess that produced no JSON.

    "import-failed"  the loader was never reached - a broken environment
    "call-failed"    the import completed and the loader then raised on the
                     package: a rejection, not a harness problem
    "no-json-stdout" no readable exception; unknown, treated as broken
    """
    if "Traceback" not in stderr:
        return "no-json-stdout"
    return "call-failed" if LOADER_IMPORT_MARKER in stderr else "import-failed"


def gold_manifest_check(pkg: Path) -> dict:
    """[4] Run the REAL hermes loader manifest validation via its venv python."""
    if not VENV_PY.is_file():
        return {"status": "unavailable", "reason": f"venv python not found: {VENV_PY}"}
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {json.dumps(str(HERMES_SRC))})\n"
        "from hermes_cli.agent_plugins import read_agent_plugin_manifest\n"
        f"print({LOADER_IMPORT_MARKER!r}, file=sys.stderr, flush=True)\n"
        f"d, diag = read_agent_plugin_manifest({json.dumps(str(pkg))})\n"
        "print(json.dumps({'data': d, 'diagnostics': [getattr(x, 'message', str(x)) for x in diag]}, indent=2, default=str))\n"
    )
    try:
        run = subprocess.run([str(VENV_PY), "-c", code], capture_output=True,
                             text=True, timeout=120, env=subprocess_env())
    except Exception as exc:
        return {"status": "error", "reason": f"subprocess failed: {exc}"}
    result: dict = {"cmd": [str(VENV_PY), "-c", "<read_agent_plugin_manifest snippet>"],
                    "exit_code": run.returncode, "stderr_tail": run.stderr[-2000:]}
    try:
        parsed = json.loads(run.stdout)
        result["parsed"] = parsed
        result["status"] = "ok" if run.returncode == 0 else "nonzero-exit"
    except Exception:
        result["stdout_tail"] = run.stdout[-2000:]
        result["status"] = _loader_failure_status(run.stderr)
    return result


def gold_load_check(pkg: Path, data_root: Path) -> dict:
    """[5] Full load_agent_plugin(): discovers skills + mcp, returns per-skill diagnostics."""
    if not VENV_PY.is_file():
        return {"status": "unavailable", "reason": f"venv python not found: {VENV_PY}"}
    data_root.mkdir(parents=True, exist_ok=True)
    code = (
        "import json, sys, dataclasses\n"
        f"sys.path.insert(0, {json.dumps(str(HERMES_SRC))})\n"
        "from hermes_cli.agent_plugins import load_agent_plugin\n"
        f"print({LOADER_IMPORT_MARKER!r}, file=sys.stderr, flush=True)\n"
        f"p = load_agent_plugin({json.dumps(str(pkg))}, {json.dumps(str(data_root))})\n"
        "skills = getattr(p, 'skills', []) or []\n"
        "try:\n"
        "    payload = dataclasses.asdict(p)\n"
        "except Exception:\n"
        "    payload = {k: str(v) for k, v in vars(p).items()}\n"
        "diag = [str(getattr(x, 'scope', '')) + ': ' + str(getattr(x, 'message', str(x)))\n"
        "        for x in (getattr(p, 'diagnostics', None) or [])]\n"
        "print(json.dumps({'name': getattr(p, 'name', None), 'skill_count': len(skills),\n"
        "                  'skill_names': sorted(str(getattr(s, 'name', s)) for s in skills),\n"
        "                  'mcp_server_count': len(getattr(p, 'mcp_servers', []) or []),\n"
        "                  'diagnostics': diag, 'data': payload}, indent=2, default=str))\n"
    )
    try:
        run = subprocess.run([str(VENV_PY), "-c", code], capture_output=True,
                             text=True, timeout=120, env=subprocess_env())
    except Exception as exc:
        return {"status": "error", "reason": f"subprocess failed: {exc}"}
    result: dict = {"cmd": [str(VENV_PY), "-c", "<load_agent_plugin snippet>"],
                    "exit_code": run.returncode, "stderr_tail": run.stderr[-4000:]}
    try:
        parsed = json.loads(run.stdout)
        result["parsed"] = parsed
        result["status"] = "ok" if run.returncode == 0 else "nonzero-exit"
    except Exception:
        result["stdout_tail"] = run.stdout[-2000:]
        result["status"] = _loader_failure_status(run.stderr)
    return result


def cli_doctor(pkg: Path) -> dict:
    """[6] READ-ONLY attempt at the hermes plugins doctor CLI. Errors = findings, not failures."""
    if not VENV_HERMES.is_file():
        return {"status": "unavailable", "reason": f"hermes.exe not found: {VENV_HERMES}"}
    cmd = [str(VENV_HERMES), "plugins", "doctor", str(pkg), "--ci"]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=subprocess_env())
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "cmd": cmd, "note": "killed after 60s"}
    except Exception as exc:
        return {"status": "error", "cmd": cmd, "reason": str(exc)}
    return {"status": "ran", "cmd": cmd, "exit_code": run.returncode,
            "stdout": run.stdout, "stderr": run.stderr}


# "call-failed" is a rejection: the loader imported and then raised on the
# package, so it ran. The other two are "the loader executed".
LOADER_RAN_STATUSES = ("ok", "nonzero-exit", "call-failed")
LOADER_ABSENT_STATUSES = ("unavailable", "skipped")


def loader_state(gold: dict, load: dict) -> str:
    """Classify the two loader checks as "ran", "broken" or "unavailable".

    "ran"         both checks executed against the package, accepted or not
    "broken"      the harness could not be trusted: an import failure, a crashed
                  subprocess, unparseable output, or one check running while the
                  other did not. A partial run is never a skip.
    "unavailable" neither check ran because there is no hermes environment

    The distinction is the point: a broken environment used to be reported as
    "the loader ran and rejected the package", which sends a reader hunting a
    package defect that does not exist.
    """
    statuses = (gold.get("status"), load.get("status"))
    if all(s in LOADER_RAN_STATUSES for s in statuses):
        return "ran"
    if all(s in LOADER_ABSENT_STATUSES for s in statuses):
        return "unavailable"
    return "broken"


def resolve_exit_code(
    *,
    static_failed: bool,
    loader_state: str,
    loader_ok: bool,
    require_gold: bool,
) -> int:
    """Map finished check results to the documented exit code.

    Pure on purpose: the --require-gold policy is the one thing that must be
    testable without a hermes install, because its whole job is to make "the
    real loader never ran" impossible to mistake for a pass.
    """
    if static_failed:
        return 1
    if loader_state == "ran":
        return 0 if loader_ok else 2
    if require_gold:
        return 3
    # Without the flag a broken environment still has to fail - silently
    # skipping it is how the loader layer went unenforced in the first place -
    # but an absent one stays a no-op for contributors without hermes.
    return 2 if loader_state == "broken" else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", default=str(DEFAULT_PACKAGE))
    ap.add_argument(
        "--require-gold",
        action="store_true",
        help="fail (exit 3) when the real hermes loader is unavailable, instead of "
        "degrading silently to the static mirror",
    )
    args = ap.parse_args()
    pkg = Path(args.package).resolve()
    rep = Report()

    print(f"== validate.py ==\npackage: {pkg}\n")
    gold: dict = {"status": "unavailable", "reason": "package dir missing"}
    load: dict = {"status": "skipped", "reason": "package dir missing"}
    gold_ok = load_ok = False
    if not pkg.is_dir():
        rep.fail(f"package dir not found: {pkg}")
    else:
        check_manifest(pkg, rep)
        check_skills(pkg, rep)
        check_encoding(pkg, rep)
        check_layout(pkg, rep)
        check_phase1(pkg, rep)
        check_hermes_adaptation_contract(pkg, rep)
        check_conflict_markers(pkg, rep)
        check_model_panel(pkg, rep)

        print("-- [1..3] static checks (stdlib replication of agent_plugins.py rules) --")
        for msg in rep.passed:
            print(f"  PASS  {msg}")
        for msg in rep.notes:
            print(f"  NOTE  {msg}")
        for msg in rep.failed:
            print(f"  FAIL  {msg}")

        print("\n-- [4] GOLD check: real hermes loader read_agent_plugin_manifest --")
        gold = gold_manifest_check(pkg)
        print(json.dumps(gold, indent=2, default=str))
        gold_ok = gold.get("status") == "ok" and not (gold.get("parsed", {}).get("diagnostics"))
        if gold_ok:
            print("  GOLD: manifest validated by the real loader; ZERO diagnostics.")
        elif gold.get("status") == "unavailable":
            print(f"  GOLD unavailable -> static fallback only ({gold.get('reason')})")
        else:
            print("  GOLD: see output above (diagnostics/errors recorded as findings)")

        print("\n-- [5] GOLD+ full load_agent_plugin (skill discovery) --")
        load = gold_load_check(pkg, SCRIPT_DIR / ".validate-data")
        load_ok = False
        if load.get("status") == "ok":
            parsed = load["parsed"]
            print(f"  skill_count={parsed.get('skill_count')} mcp={parsed.get('mcp_server_count')} "
                  f"diagnostics={parsed.get('diagnostics')}")
            data = parsed.get("data") or {}
            print(f"  manifest round-trip: name={data.get('name')!r} version={data.get('version')!r} "
                  f"root={data.get('root')}")
            names = parsed.get("skill_names") or []
            if parsed.get("skill_count") == expected_count(pkg) and not parsed.get("diagnostics"):
                print(f"  GOLD+: all {expected_count(pkg)} skills discovered by the real loader, "
                      "zero component diagnostics.")
            else:
                print("  GOLD+: UNEXPECTED — see skill_names/diagnostics above")
            print(f"  skill names: {names}")
        else:
            print(json.dumps(load, indent=2, default=str))

        print("\n-- [6] READ-ONLY CLI attempt: hermes plugins doctor <pkg> --ci --")
        doc = cli_doctor(pkg)
        print(json.dumps(doc, indent=2, default=str))
        if doc.get("status") == "ran" and doc.get("exit_code") != 0:
            rep.note(f"plugins doctor exited {doc.get('exit_code')} — recorded as finding (signature "
                     "being verified in parallel), not a package failure")

        load_ok = load.get("status") == "ok" \
            and load.get("parsed", {}).get("skill_count") == expected_count(pkg) \
            and not load.get("parsed", {}).get("diagnostics")

        # Optional: repo YAML structural check (only when pyyaml is available,
        # e.g. via `uv sync` dev group or CI). The package itself ships no YAML;
        # this validates the repo's CI configs, dependabot, and issue templates.
        try:
            import yaml  # type: ignore
        except ImportError:
            yaml = None
        if yaml is not None:
            repo = REPO_ROOT
            gh = repo / ".github"
            ymls = sorted(gh.rglob("*.yml")) if gh.is_dir() else []
            bad_yml = []
            for y in ymls:
                try:
                    yaml.safe_load(y.read_text(encoding="utf-8"))
                except Exception as exc:
                    bad_yml.append(f"{y.name}: {str(exc)[:100]}")
            if ymls:
                print(f"\n-- [R] Repo YAML structural check ({len(ymls)} files) --")
                for b in bad_yml:
                    print(f"  YAML FAIL: {b}")
                    rep.failed.append(f"repo YAML invalid: {b}")
                if not bad_yml:
                    print(f"  PASS: all {len(ymls)} .github YAML files parse clean")

        print("\n== verdict ==")
        if rep.failed:
            print(f"HARD FAILURES: {len(rep.failed)}")
            for m in rep.failed:
                print(f"  - {m}")
        state = loader_state(gold, load)
        if state == "ran":
            print(f"static: {'PASS' if not rep.failed else 'FAIL'}; "
                  f"gold manifest: {'PASS' if gold_ok else 'FAIL'}; "
                  f"gold load: {'PASS' if load_ok else 'FAIL'}")
        elif state == "broken":
            # The environment is present but could not drive the loader. Say so:
            # it is a harness failure, never a package verdict.
            print(f"static: {'PASS' if not rep.failed else 'FAIL'}; "
                  "gold: BROKEN (the hermes environment exists but the loader "
                  "could not be driven - see the status blocks above)")
        elif args.require_gold:
            # --require-gold exists so a CI run can never go green after silently
            # skipping the real loader. An absent hermes is a harness failure, not
            # a package verdict, and it must not be reported as one.
            print(f"static: {'PASS' if not rep.failed else 'FAIL'}; "
                  "gold: REQUIRED but UNAVAILABLE (point HERMES_HOME at a hermes "
                  "checkout that has a .venv, or drop --require-gold)")
        else:
            # hermes not present (e.g. a contributor machine): gold checks cannot
            # run -> skip them instead of failing. Static + repo YAML gates remain
            # authoritative; the maintainer machine supplies gold evidence per
            # CONTRIBUTING.md.
            print(f"static: {'PASS' if not rep.failed else 'FAIL'}; "
                  "gold: SKIPPED (hermes not available on this machine)")
        return resolve_exit_code(
            static_failed=bool(rep.failed),
            loader_state=state,
            loader_ok=gold_ok and load_ok,
            require_gold=args.require_gold,
        )


if __name__ == "__main__":
    sys.exit(main())
