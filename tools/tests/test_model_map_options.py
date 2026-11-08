"""The model_map options generator: pstack role panel -> `delegation.backends.model_map`.

These tests pin three things the migration to the advanced-delegation `model_map` backend relies
on: every pstack role has a route, a real panel converts without changing any role's model or
effort, and the route table the shipped contract shows the model is the table the generator writes.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_map_options as mmo  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ROUTING = REPO / "hermes-plugin" / "pstack-delegation" / "routing.py"
CONTRACT = REPO / "tools" / "assets" / "hermes-delegation.md"

PROVIDER = "claude-subscription-directsdk-experimental"
PARENT = "claude-opus-5-5[1m]"
OPUS = "claude-opus-5-5[1m]@xhigh"
SONNET = "claude-sonnet-5-5[1m]@high"
GPT = {"provider": "openai-codex", "model": "gpt-6-astra-900k", "reasoning_effort": "xhigh"}
FABLE = "claude-fable-5-1[1m]@high"
SEATS = [OPUS, GPT, FABLE]

# The shape of the operator's pstack-models.json when this migration was designed.
LIVE_PANEL = {
    "feature, refactoring": SONNET,
    "bug-fix": SONNET,
    "perf-issue": SONNET,
    "hillclimb": SONNET,
    "judgment and prose": OPUS,
    "hardest tasks": OPUS,
    "how explorer": SONNET,
    "how explainer": OPUS,
    "how critics": SEATS,
    "why investigators": SONNET,
    "why synthesizer": OPUS,
    "reflect tooling": "claude-sonnet-5-5[1m]@medium",
    "reflect judgment, divergent, synthesizer": OPUS,
    "arena runners": SEATS,
    "arena cross-judge pool": SEATS,
    "swarm workers": SONNET,
    "architect runners": SEATS,
    "interrogate reviewers": SEATS,
}


def _routing():
    spec = importlib.util.spec_from_file_location("pstack_routing_for_mmo", ROUTING)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def _build(panel=None, **kwargs):
    kwargs.setdefault("provider", PROVIDER)
    kwargs.setdefault("parent_model", PARENT)
    return mmo.build(LIVE_PANEL if panel is None else panel, **kwargs)


def _by_name(block):
    return {o["name"]: o for o in block["options"]}


def test_every_pstack_role_has_exactly_one_route():
    assert set(mmo.ROLE_ROUTES) == set(_routing().ROLES)
    assert set(mmo.ROLE_ROUTES.values()) <= {r.name for r in mmo.ROUTES} | {mmo.PANEL}


def test_live_panel_keeps_every_role_on_its_model_and_effort():
    block = _build()
    options = _by_name(block)
    assert [o["name"] for o in block["options"]] == [
        "implement", "explore", "research", "quick", "deep", "general",
        "panel-opus", "panel-gpt", "panel-fable",
    ]
    assert block["default"] == "general"
    routing = _routing()
    panel_names = [o["name"] for o in block["options"] if o["name"].startswith(mmo.PANEL_PREFIX)]
    for role, route in mmo.ROLE_ROUTES.items():
        entries = LIVE_PANEL[role] if isinstance(LIVE_PANEL[role], list) else [LIVE_PANEL[role]]
        names = panel_names if route == mmo.PANEL else [route]
        assert len(names) == len(entries), role
        for name, entry in zip(names, entries, strict=True):
            expected, _ = routing._entry_route(entry, {})
            got = options[name]
            assert got["model"] == expected["model"], (role, name)
            assert got.get("reasoning_effort") == expected.get("reasoning_effort"), (role, name)
            assert got["provider"] == expected.get("provider", PROVIDER), (role, name)


def test_general_route_reproduces_todays_roleless_route():
    options = _by_name(_build(default_effort="high"))
    assert options["general"] == {
        "name": "general", "description": options["general"]["description"],
        "provider": PROVIDER, "model": PARENT, "reasoning_effort": "high",
    }


def test_research_copies_the_explore_route():
    options = _by_name(_build())
    for key in ("provider", "model", "reasoning_effort"):
        assert options["research"][key] == options["explore"][key]


def test_every_option_is_one_the_backend_accepts():
    for option in _build()["options"]:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", option["name"])
        assert option["description"] and "\n" not in option["description"]
        assert option["model"] and option["provider"]
        assert set(option) <= {"name", "description", "provider", "model", "reasoning_effort",
                               "service_tier", "fallback_providers"}


def test_roles_sharing_a_route_must_agree():
    panel = {**LIVE_PANEL, "bug-fix": OPUS}
    with pytest.raises(mmo.GenerateError, match="implement") as info:
        _build(panel)
    assert "bug-fix" in str(info.value) and "feature, refactoring" in str(info.value)


def test_panel_roles_must_share_one_seat_list():
    panel = {**LIVE_PANEL, "how critics": [OPUS]}
    with pytest.raises(mmo.GenerateError, match="how critics"):
        _build(panel)


def test_missing_roles_take_their_route_from_the_roles_that_have_a_line():
    panel = {k: v for k, v in LIVE_PANEL.items() if k != "bug-fix"}
    assert _by_name(_build(panel))["implement"]["model"] == "claude-sonnet-5-5[1m]"


def test_a_route_with_no_line_at_all_runs_on_the_parent_model():
    panel = {k: v for k, v in LIVE_PANEL.items() if k != "reflect tooling"}
    quick = _by_name(_build(panel, default_effort="high"))["quick"]
    assert (quick["model"], quick["reasoning_effort"]) == (PARENT, "high")


def test_inherit_parent_entries_use_the_parent_model():
    panel = {role: (["inherit-parent"] * 3 if isinstance(v, list) else "inherit-parent")
             for role, v in LIVE_PANEL.items()}
    block = _build(panel, default_effort="medium")
    names = [o["name"] for o in block["options"]]
    assert names[-3:] == ["panel-opus", "panel-opus-2", "panel-opus-3"]
    assert {o["model"] for o in block["options"]} == {PARENT}


def test_profile_entries_are_refused():
    with pytest.raises(mmo.GenerateError, match="profile_routes"):
        _build({**LIVE_PANEL, "hardest tasks": "profile:builder"})


def test_unknown_roles_are_refused():
    with pytest.raises(mmo.GenerateError, match="unknown role"):
        _build({**LIVE_PANEL, "how critic": SEATS})


def test_parent_model_is_required_only_when_a_route_needs_it():
    block = mmo.build(LIVE_PANEL, provider=PROVIDER, parent_model=None, default_effort="high")
    assert "general" not in _by_name(block)
    assert "default" not in block  # no default: unrouted tasks keep the parent route


def test_config_supplies_provider_model_and_effort_and_keeps_user_routes(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n  default: claude-opus-5-5[1m]\n"
        "  provider: claude-subscription-directsdk-experimental\n"
        "delegation:\n  reasoning_effort: high\n  backends:\n    model_map:\n"
        "      default: local\n      options:\n"
        "        - {name: local, description: Private model., base_url: 'http://localhost:8080/v1',"
        " model: qwen3-coder}\n"
        "        - {name: deep, description: stale, provider: x, model: y}\n",
        encoding="utf-8",
    )
    panel = tmp_path / "pstack-models.json"
    panel.write_text(json.dumps({"budget": "large", "roles": LIVE_PANEL}), encoding="utf-8")
    block = mmo.build_from_files(panel=panel, config=config)
    options = _by_name(block)
    assert options["general"]["model"] == PARENT
    assert options["deep"]["model"] == "claude-opus-5-5[1m]"  # generated routes replace stale ones
    assert options["local"]["base_url"] == "http://localhost:8080/v1"
    assert [o["name"] for o in block["options"]][-1] == "local"
    assert block["default"] == "local"  # a user default that still names a route is kept


def test_cli_prints_a_json_block_and_fails_with_exit_2(tmp_path, capsys):
    panel = tmp_path / "pstack-models.json"
    panel.write_text(json.dumps({"roles": LIVE_PANEL}), encoding="utf-8")
    assert mmo.main(["--panel", str(panel), "--provider", PROVIDER, "--parent-model", PARENT]) == 0
    block = json.loads(capsys.readouterr().out)
    assert block["default"] == "general" and len(block["options"]) == 9
    panel.write_text(json.dumps({"roles": {**LIVE_PANEL, "bug-fix": OPUS}}), encoding="utf-8")
    assert mmo.main(["--panel", str(panel), "--provider", PROVIDER, "--parent-model", PARENT]) == 2
    assert "implement" in capsys.readouterr().err


def _contract_rows() -> dict[str, tuple[str, str]]:
    """`| `name` | description | roles |` rows of the contract's route table."""
    rows = {}
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([a-z][a-z0-9-]*)` \| (.+?) \| (.*?) \|$", line)
        if m:
            rows[m.group(1)] = (m.group(2), m.group(3))
    return rows


def test_contract_route_table_is_the_generator_table():
    rows = _contract_rows()
    assert list(rows) == [r.name for r in mmo.ROUTES]
    for route in mmo.ROUTES:
        description, roles = rows[route.name]
        assert description == route.description, route.name
        expected = sorted(role for role, name in mmo.ROLE_ROUTES.items() if name == route.name)
        assert sorted(re.findall(r"`([^`]+)`", roles)) == expected, route.name


def test_contract_names_the_panel_prefix_and_every_panel_role():
    text = CONTRACT.read_text(encoding="utf-8")
    assert f"`{mmo.PANEL_PREFIX}`" in text
    for role, route in mmo.ROLE_ROUTES.items():
        if route == mmo.PANEL:
            assert f"`{role}`" in text, role
