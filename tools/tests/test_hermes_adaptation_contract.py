"""Hermes adaptation regressions that must not survive generated output."""

import json

import validate


def _skill(pkg, rel, text):
    path = pkg / "skills" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_adaptation_contract_catches_inert_personas_and_stale_runtime_claims(tmp_path):
    pkg = tmp_path / "pkg"
    _skill(
        pkg,
        "poteto-mode/SKILL.md",
        "Use `delegate_task` (role: `leaf`, persona: poteto-agent).\n"
        "Defaults: read-only delegates lose MCP access.\n",
    )
    _skill(pkg, "no-comments/SKILL.md", "delegate_task persona: Comment Sicko\n")

    rep = validate.Report()
    validate.check_hermes_adaptation_contract(pkg, rep)

    joined = "\n".join(rep.failed)
    assert "persona:" in joined
    assert "read-only delegates lose MCP access" in joined


def test_adaptation_contract_catches_cursor_tool_names_auto_and_transcript_paths(tmp_path):
    pkg = tmp_path / "pkg"
    _skill(pkg, "interrogate/SKILL.md", "If configured value is `inherit-parent` or `auto`.\n")
    _skill(pkg, "interrogate/references/rubric.md", "Use the tools available to you (Read, Grep, Glob).\n")
    _skill(pkg, "reflect/references/judgment-reviewer.md", "Read the active transcript at <ABSOLUTE_PATH>.\n")
    _skill(pkg, "swarm/SKILL.md", "Fan out N parallel cloud workers. Not the cloud concurrency limit.\n")

    rep = validate.Report()
    validate.check_hermes_adaptation_contract(pkg, rep)

    joined = "\n".join(rep.failed)
    assert "`auto`" in joined
    assert "Read, Grep, Glob" in joined
    assert "<ABSOLUTE_PATH>" in joined
    assert "cloud workers" in joined


def test_model_panel_role_shapes_are_validated(tmp_path):
    payload = {
        "roles": {
            "reflect judgment, divergent, synthesizer": ["z-ai/glm-5.2"],
            "swarm workers": ["meituan/longcat-2.0"],
        }
    }
    asset = tmp_path / "panel.json"
    asset.write_text(json.dumps(payload), encoding="utf-8")
    pkg = tmp_path / "pkg"
    (pkg / "config").mkdir(parents=True)
    (pkg / "config" / "models.json").write_text(json.dumps(payload), encoding="utf-8")

    rep = validate.Report()
    validate.check_model_panel(pkg, rep, asset=asset)

    joined = "\n".join(rep.failed)
    assert "reflect judgment, divergent, synthesizer" in joined
    assert "swarm workers" in joined


UPSTREAM_PRINCIPLE_SHA256 = {
    "principle-fix-root-causes": "8df3c8c3fe57eb0055a5c8efab8ab144557fdf301da70b1cdc77a3a3ef3f3052",
    "principle-test-behavior-not-implementation": "f7223698623b6a26fea49c751e466b38e6691b1f32ce841d3983e7ffbe2b74a6",
}
DELEGATION_DECISION_SENTENCE = (
    "Record every delegation instruction a playbook gives, preamble included, "
    "as a todolist item: a `delegate_task` item, or `skip: <reason>` when you do not delegate."
)


def test_h1_contract_records_delegation_decisions_in_the_todolist():
    asset = validate.REPO_ROOT / "tools" / "assets" / "hermes-delegation.md"
    shipped = validate.REPO_ROOT / "skills" / "poteto-mode" / "references" / "hermes-delegation.md"

    assert DELEGATION_DECISION_SENTENCE in asset.read_text(encoding="utf-8")
    assert shipped.read_bytes() == asset.read_bytes()


def test_rebuild_leaves_upstream_principle_files_byte_identical():
    import hashlib

    for name, digest in UPSTREAM_PRINCIPLE_SHA256.items():
        path = validate.REPO_ROOT / "skills" / name / "SKILL.md"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
