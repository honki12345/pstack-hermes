"""The dist checkout check must fail when the attributes rule is absent.

A regression test that only ever passes is worthless: these cases pin the two
failure modes the check exists to catch, so a future edit that weakens it (drops
the CRLF assertion, stops comparing binaries, stages before writing the
attributes file) fails here instead of shipping.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dist_checkout_check as checker  # noqa: E402

ATTRS_NAME = checker.ATTRS_NAME


def _package(tmp_path: Path, attrs: str | None) -> Path:
    """Build a minimal dist-like package.

    ``attrs`` is the .gitattributes content the PACKAGE itself carries. The gate
    refuses to install its own copy - that would blind it to a published tree whose
    rule is missing or stale - so a fixture representing something the publisher
    would actually stage must supply one. Pass None to exercise that refusal.
    """
    pkg = tmp_path / "pkg"
    (pkg / "skills" / "demo").mkdir(parents=True)
    (pkg / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\n---\nbody\n", encoding="utf-8", newline=""
    )
    (pkg / "config").mkdir()
    (pkg / "config" / "models.json").write_text('{"a": 1}\n', encoding="utf-8", newline="")
    (pkg / "assets").mkdir()
    # A real PNG header so git's own binary sniffing sees a genuine binary.
    (pkg / "assets" / "logo.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452")
        + b"\x00\x00\x00\x10\x00\x00\x00\x10\x08\x06\x00\x00\x00"
        + b"\x8f\x5f\x3f\x8c\x00\x00\x00\x0aIDATx\x9cc\x00\x01"
        + b"\x00\x00\x05\x00\x01\x0d\x0a-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    if attrs is not None:
        (pkg / ATTRS_NAME).write_text(attrs, encoding="utf-8", newline="")
    return pkg


def _run(package: Path, attrs_source: Path, monkeypatch, *, mirror: bool = True) -> int:
    """Run the gate; ATTRS_SOURCE is monkeypatched to the test rule.

    The package under test must carry the same rule as a published tree would,
    so mirror attrs_source into the package unless a test is exercising the
    "no rule in the tree" refusal.
    """
    tree_attrs = package / checker.ATTRS_NAME
    if mirror and attrs_source.is_file() and package.is_dir() and not tree_attrs.is_file():
        shutil.copy2(attrs_source, tree_attrs)
    monkeypatch.setattr(checker, "ATTRS_SOURCE", attrs_source)
    try:
        return checker.main(["--package", str(package)])
    except RuntimeError:
        # main() normally converts RuntimeError to exit 2 at the entrypoint;
        # call it directly so the refusal is observable here too.
        return 2


def test_clean_tree_passes(tmp_path, monkeypatch):
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n*.png binary\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    assert _run(pkg, attrs, monkeypatch) == 0


def test_checker_reports_crlf_when_the_rule_does_not_pin_lf(tmp_path, monkeypatch, capsys):
    """The pre-fix state must be reported as CRLF, on every host.

    Asserted via the file's actual bytes rather than by relying on the host's
    own autocrlf behaviour: git ignores core.autocrlf=true on POSIX, so on a
    Linux runner a checkout comes out LF whatever the attributes say. The checker
    solves that with a simulated-consumer attributesFile, but a test that depends
    on the *runner* converting line endings would pass vacuously on Linux - which
    is exactly how this case failed its first CI run.
    """
    attrs = tmp_path / "attrs"
    # A real, wrong rule rather than an empty file: `eol=crlf` is what a consumer
    # default degrades to, so the checkout genuinely comes back CRLF everywhere.
    attrs.write_text("* text=auto eol=crlf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    assert _run(pkg, attrs, monkeypatch) == 1
    assert "CRLF" in capsys.readouterr().err


def test_empty_rule_leaves_eol_unspecified(tmp_path, monkeypatch):
    """`git check-attr` is the deterministic half of the contract.

    The published rule must make `eol` explicit for every text file. This does
    not depend on any conversion actually happening, so it holds identically on
    Windows and Linux - which the byte-level assertions above cannot.
    """
    import subprocess

    good = _package(tmp_path, None)
    (good / checker.ATTRS_NAME).write_text(
        "* text=auto eol=lf\n*.png binary\n", encoding="utf-8", newline=""
    )
    bad = tmp_path / "pkg-bad"
    bad.mkdir()
    (bad / "a.md").write_text("x\n", encoding="utf-8", newline="")
    (bad / checker.ATTRS_NAME).write_text("", encoding="utf-8", newline="")

    def eol_for(pkg: Path) -> str:
        subprocess.run(["git", "init", "-q", str(pkg)], capture_output=True, check=False)
        out = subprocess.run(
            ["git", "-C", str(pkg), "check-attr", "eol", "--", "a.md"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return out.strip().rsplit(":", 1)[-1].strip()

    assert eol_for(good) == "lf"
    assert eol_for(bad) == "unspecified"


def test_png_marked_text_is_caught(tmp_path, monkeypatch):
    # The reason *.png is pinned binary: without it, a `text` rule makes git
    # rewrite the bytes and the checkout no longer matches the staged tree.
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n*.png text\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    assert _run(pkg, attrs, monkeypatch) == 1


def test_gate_refuses_a_published_tree_with_no_rule(tmp_path, monkeypatch):
    """The gate must not install its own rule over the tree's.

    This was the reason the gate could not see the regression it exists to catch:
    stage_tree used to copy ATTRS_SOURCE in unconditionally, so a dist tree whose
    publisher step had been deleted still validated clean. Reproduced before the
    fix as exit 0 with no .gitattributes present.
    """
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)  # package carries NO .gitattributes
    assert not (pkg / checker.ATTRS_NAME).is_file()
    # mirror=False: do NOT stage the rule into the tree - that absence is the condition
    # under test, and mirroring it would defeat the assertion.
    assert _run(pkg, attrs, monkeypatch, mirror=False) == 2

def test_gate_refuses_a_published_tree_with_a_stale_rule(tmp_path, monkeypatch):
    """A tree whose rule differs from the tested rule must not be validated."""
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    (pkg / checker.ATTRS_NAME).write_text("* text=auto eol=crlf\n", encoding="utf-8", newline="")
    assert _run(pkg, attrs, monkeypatch, mirror=False) == 2


def test_source_tree_exemption_is_opt_in_not_name_based(tmp_path, monkeypatch):
    """The exemption must be the explicit flag, never the directory name.

    A staged dist checkout is legitimately called "pstack" - that is its published
    name - so a name-based exemption let a published tree with no rule at all
    validate clean. Reproduced before the fix: --package <dir>/pstack with no
    .gitattributes exited 0.
    """
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    # A package directory literally named "pstack", carrying no rule.
    pkg = tmp_path / "pstack"
    (pkg / "skills" / "demo").mkdir(parents=True)
    (pkg / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\n---\n", encoding="utf-8", newline=""
    )
    assert not (pkg / checker.ATTRS_NAME).is_file()
    assert _run(pkg, attrs, monkeypatch, mirror=False) == 2


def test_source_tree_accepts_root_attrs_with_extra_metadata(tmp_path, monkeypatch):
    """The repo-root rule set carries linguist metadata beyond the dist rule.

    --source-tree must use the tree's own rule and not require byte-equality with
    the legacy dist asset, or the CI build job can never pass.
    """
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n*.png binary\n", encoding="utf-8", newline="")
    pkg = _package(
        tmp_path,
        "* text=auto eol=lf\n*.png binary\nREADME.md linguist-generated=true\n",
    )
    monkeypatch.setattr(checker, "ATTRS_SOURCE", attrs)
    assert checker.main(["--package", str(pkg), "--source-tree"]) == 0


def test_source_tree_stages_only_the_published_payload(tmp_path):
    """Tooling, docs and local scratch must not enter the staged package."""
    pkg = _package(tmp_path, "* text=auto eol=lf\n*.png binary\n")
    (pkg / "tools").mkdir()
    (pkg / "tools" / "x.py").write_text("x\n", encoding="utf-8", newline="")
    (pkg / ".venv").mkdir()
    (pkg / ".venv" / "junk").write_text("junk\n", encoding="utf-8", newline="")
    dest = tmp_path / "staged"
    dest.mkdir()
    checker.stage_tree(pkg, dest, source_tree=True)
    assert not (dest / "tools").exists()
    assert not (dest / ".venv").exists()
    assert (dest / "skills").is_dir()
    assert (dest / ATTRS_NAME).is_file()


def test_source_tree_flag_allows_a_package_with_no_rule(tmp_path, monkeypatch):
    """The positive control for the flag: --source-tree stages a throwaway rule."""
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    assert not (pkg / checker.ATTRS_NAME).is_file()
    monkeypatch.setattr(checker, "ATTRS_SOURCE", attrs)
    # stage_tree with source_tree=True must accept it; without the flag it refuses.
    dest = tmp_path / "staged"
    dest.mkdir()
    checker.stage_tree(pkg, dest, source_tree=True)
    assert (dest / checker.ATTRS_NAME).is_file()


def test_untracked_package_file_is_refused(tmp_path, monkeypatch, capsys):
    """A package file git never tracks would never be verified.

    Typically a packaged .gitignore. Reported rather than silently narrowing
    coverage: the checkout would otherwise be declared clean with a file from the
    published tree unchecked.
    """
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    shutil.copy2(attrs, pkg / checker.ATTRS_NAME)
    hidden = pkg / "sub"
    hidden.mkdir()
    (hidden / "a.md").write_text("x\n", encoding="utf-8", newline="")
    (pkg / ".gitignore").write_text("sub/\n", encoding="utf-8", newline="")
    assert _run(pkg, attrs, monkeypatch, mirror=False) == 2
    assert "never be verified" in capsys.readouterr().err

def test_gate_accepts_a_published_tree_carrying_the_rule(tmp_path, monkeypatch):
    """The positive control for the two refusals above."""
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    shutil.copy2(attrs, pkg / checker.ATTRS_NAME)
    assert _run(pkg, attrs, monkeypatch) == 0


def test_run_git_decodes_output_as_utf8_regardless_of_locale():
    """Pin the decoding itself, not the outcome on this host.

    An end-to-end non-ASCII test passes on Linux even without the fix, because
    UTF-8 is the default there - so it cannot catch a regression to the old
    locale-dependent decoding. Assert the subprocess call is configured for
    UTF-8 instead, which holds on every platform.
    """
    captured = {}
    real_run = checker.subprocess.run

    def spy(args, **kwargs):
        captured.update(kwargs)
        return real_run(args, **kwargs)

    checker.subprocess.run = spy
    try:
        checker.run_git(["--version"], Path.cwd())
    finally:
        checker.subprocess.run = real_run
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "surrogateescape"




def test_non_ascii_path_is_not_reported_absent(tmp_path, monkeypatch, capsys):
    """A non-ASCII path must be verified, not skipped as missing.

    Complements the test above: this one is meaningful on Windows (where the bug
    reproduced) and harmless on Linux.
    """
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    shutil.copy2(attrs, pkg / checker.ATTRS_NAME)
    d = pkg / "skills" / "\u65e5\u672c\u8a9e"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: x\ndescription: d\n---\n", encoding="utf-8", newline=""
    )
    assert _run(pkg, attrs, monkeypatch) == 0
    assert "absent from the checkout" not in capsys.readouterr().err


def test_path_with_a_space_is_not_reported_missing(tmp_path, monkeypatch):
    """`ls-files` must be read with -z.

    A plain .split() tears "a b.md" into two entries, and the checker would then
    report a perfectly valid file as absent from the checkout.
    """
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    spaced = pkg / "skills" / "demo" / "a b.md"
    spaced.write_text("spaced\n", encoding="utf-8", newline="")
    assert _run(pkg, attrs, monkeypatch) == 0


def test_forced_checkout_failure_is_a_tool_error(tmp_path, monkeypatch):
    """A failed checkout must not be reported as a clean pass."""
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    monkeypatch.setattr(checker, "ATTRS_SOURCE", attrs)
    monkeypatch.setattr(
        checker,
        "run_git",
        lambda args, cwd: checker.subprocess.CompletedProcess(args, 1, "", "boom"),
    )
    with pytest.raises(RuntimeError):
        checker.main(["--package", str(pkg)])

def test_missing_package_is_tool_error(tmp_path, monkeypatch):
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    assert _run(tmp_path / "absent", attrs, monkeypatch) == 2


def test_shipped_rule_matches_the_asset_the_publisher_copies():
    # The publisher copies tools/assets/dist.gitattributes verbatim; if that file
    # is ever edited by hand in a way that stops pinning LF, say so here.
    body = checker.ATTRS_SOURCE.read_text(encoding="utf-8")
    assert "eol=lf" in body
    assert "*.png binary" in body


def test_extension_less_text_files_are_still_checked(tmp_path, monkeypatch):
    """Every tracked file must land in exactly one branch.

    This package ships extension-less text files - LICENSE and the watch-pr
    launcher. They are in neither TEXT_EXTS nor TEXT_NAMES and carry no NUL byte,
    so an earlier suffix-based classifier called them neither text nor binary and
    silently skipped them: the checker reported a clean tree while never looking
    at 2 of 134 files. Text is now the default and binary requires a NUL byte, so
    the two branches are mutually exclusive and exhaustive.
    """
    staged = tmp_path / "staged"
    (staged / "skills").mkdir(parents=True)
    (staged / "LICENSE").write_bytes(b"MIT License\n\nCopyright\n")
    (staged / "skills" / "watch-pr").write_bytes(b"#!/usr/bin/env bun\n")
    (staged / "assets").mkdir()
    (staged / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02")

    assert checker.is_text_file(staged, "LICENSE")
    assert checker.is_text_file(staged, "skills/watch-pr")
    assert not checker.is_text_file(staged, "assets/logo.png")


def test_extension_less_text_file_is_caught_when_it_comes_back_crlf(tmp_path, monkeypatch, capsys):
    """The end-to-end consequence: a CRLF extension-less file must be reported."""
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=crlf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    (pkg / "LICENSE").write_bytes(b"MIT License\n")
    assert _run(pkg, attrs, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "CRLF" in err
    assert "LICENSE" in err

def test_checker_does_not_mutate_the_package(tmp_path, monkeypatch):
    attrs = tmp_path / "attrs"
    attrs.write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    pkg = _package(tmp_path, None)
    # A publisher would have staged the rule before the gate ran; put it there
    # up front so the baseline below is the tree the gate actually receives.
    shutil.copy2(attrs, pkg / checker.ATTRS_NAME)
    before = subprocess.run(
        ["git", "init", "-q", str(pkg)], capture_output=True, text=True, check=False
    )
    assert before.returncode == 0
    head = subprocess.run(
        ["git", "-C", str(pkg), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    _run(pkg, attrs, monkeypatch)
    after = subprocess.run(
        ["git", "-C", str(pkg), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert head == after
