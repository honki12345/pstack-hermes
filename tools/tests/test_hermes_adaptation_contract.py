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


def test_adaptation_contract_catches_retired_pstack_backend_keys(tmp_path):
    pkg = tmp_path / "pkg"
    _skill(pkg, "swarm/SKILL.md", "Route workers with `pstack_role: swarm workers`.\n")
    _skill(pkg, "how/SKILL.md", "Every spawn sets `access: read-only`.\n")
    _skill(pkg, "arena/SKILL.md", "Spawn runner n with `seat: n`.\n")

    rep = validate.Report()
    validate.check_hermes_adaptation_contract(pkg, rep)

    joined = "\n".join(rep.failed)
    assert "pstack_role" in joined
    assert "access: read-only" in joined
    assert "seat: n" in joined


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
