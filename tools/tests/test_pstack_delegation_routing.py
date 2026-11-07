"""Behavior of the pstack delegation backend's pure routing module (no Hermes import)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROUTING = Path(__file__).resolve().parents[2] / "hermes-plugin" / "pstack-delegation" / "routing.py"
_spec = importlib.util.spec_from_file_location("pstack_delegation_routing", ROUTING)
assert _spec is not None and _spec.loader is not None
routing = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = routing
_spec.loader.exec_module(routing)

CONFIG = {"reasoning_effort": "high", "max_iterations": 250, "backend": "pstack", "backends": {}}


def route(role, *, seat=None, access=None, panel=None, settings=None, config=CONFIG):
    return routing.resolve(role=role, seat=seat, access=access, panel=panel or {},
                           settings=settings or {}, config=config)


def test_panel_seat_routes_each_reviewer_to_its_own_model():
    panel = {"interrogate reviewers": ["claude-opus-5-5@max", {"provider": "openai-codex", "model": "gpt-5.6-sol"},
                                       "inherit-parent"]}
    first = route("interrogate reviewers", seat=0, panel=panel)
    second = route("interrogate reviewers", seat=1, panel=panel)
    third = route("interrogate reviewers", seat=2, panel=panel)
    assert first.route == {"reasoning_effort": "max", "model": "claude-opus-5-5"}
    assert first.label == "interrogate reviewers[0] -> claude-opus-5-5 @max"
    assert second.route == {"reasoning_effort": "high", "provider": "openai-codex", "model": "gpt-5.6-sol"}
    assert third.route == {"reasoning_effort": "high"}
    assert third.label == "interrogate reviewers[2] -> parent"


def test_seat_past_the_panel_is_refused_with_the_entry_count():
    with pytest.raises(routing.RoutingError, match=r"seat 3 is out of range .* has 3 entries \(seats 0..2\)"):
        route("interrogate reviewers", seat=3, panel={"interrogate reviewers": ["a", "b", "c"]})


def test_unmapped_upstream_default_runs_on_parent_and_says_so():
    result = route("how explorer")
    assert result.route == {"reasoning_effort": "high"}
    assert result.label == "how explorer -> parent (unmapped default grok-4.7-xhigh-fast)"


def test_unmapped_upstream_default_can_be_refused():
    with pytest.raises(routing.RoutingError, match="no panel line and its upstream default"):
        route("how explorer", settings={"unmapped_defaults": "refuse"})


def test_alias_maps_an_upstream_slug_to_a_real_route():
    settings = {"aliases": {"grok-4.7-xhigh-fast": {"provider": "xai", "model": "grok-4.7-fast",
                                                    "reasoning_effort": "xhigh"}}}
    result = route("swarm workers", settings=settings)
    assert result.route == {"reasoning_effort": "xhigh", "provider": "xai", "model": "grok-4.7-fast"}
    assert result.label == "swarm workers -> xai/grok-4.7-fast @xhigh"


def test_provider_pin_drops_the_default_endpoint():
    config = {"base_url": "http://127.0.0.1:9/v1", "api_key": "k", "model": "local", "reasoning_effort": "low"}
    result = route("bug-fix", panel={"bug-fix": {"provider": "openrouter", "model": "z-ai/glm-5.2"}}, config=config)
    assert result.route == {"reasoning_effort": "low", "provider": "openrouter", "model": "z-ai/glm-5.2"}


def test_profile_entry_routes_the_child_as_that_profile_only():
    result = route("why synthesizer", panel={"why synthesizer": "profile:reviewer"}, access="read-only")
    assert result.route == {"profile": "reviewer"}
    assert result.label == "why synthesizer -> profile reviewer [read-only: advisory]"


def test_task_without_role_takes_the_default_route():
    assert route(None).route == {"reasoning_effort": "high"}
    with pytest.raises(routing.RoutingError, match="seat needs pstack_role"):
        route(None, seat=1)


def test_single_role_rejects_a_nonzero_seat():
    with pytest.raises(routing.RoutingError, match="single entry; omit seat"):
        route("bug-fix", seat=1, panel={"bug-fix": "m"})


def test_entry_with_unknown_key_or_effort_is_refused():
    with pytest.raises(routing.RoutingError, match=r"unsupported keys \['api_key'\]"):
        route("bug-fix", panel={"bug-fix": {"model": "m", "api_key": "x"}})
    with pytest.raises(routing.RoutingError, match="unknown reasoning_effort 'turbo'"):
        route("bug-fix", panel={"bug-fix": {"model": "m", "reasoning_effort": "turbo"}})


def test_load_panel_reads_roles_and_reports_bad_json(tmp_path):
    good = tmp_path / "pstack-models.json"
    good.write_text(json.dumps({"budget": "unlimited", "roles": {"bug-fix": "m"}}), encoding="utf-8")
    assert routing.load_panel(good) == {"bug-fix": "m"}
    assert routing.load_panel(tmp_path / "absent.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(routing.RoutingError, match="not valid JSON"):
        routing.load_panel(bad)


def test_every_role_has_an_upstream_default_and_panel_roles_are_lists():
    assert set(routing.UPSTREAM_DEFAULTS) == set(routing.ROLES)
    assert {r for r, v in routing.UPSTREAM_DEFAULTS.items() if isinstance(v, tuple)} == set(routing.PANEL_ROLES)


def test_check_panel_lists_every_spawn_time_problem():
    panel = {
        "interrogate reviewers": "single-string",
        "arena runners": ["ok-model", {"model": "m", "api_key": "x"}],
        "bug-fix": "m@max",
        "not a role": "m",
    }
    problems = routing.check_panel(panel, {})
    assert problems[0].startswith("'not a role': unknown role")
    assert problems[1:] == [
        "arena runners[1]: pstack panel entry has unsupported keys ['api_key']; allowed: "
        "['fallback_providers', 'model', 'profile', 'provider', 'reasoning_effort', 'service_tier'].",
        "interrogate reviewers: a panel role takes a list (one subagent per entry)",
    ]
    assert routing.check_panel({"bug-fix": "m@max", "arena runners": ["a", "inherit-parent"]}, {}) == []


def test_check_cli_reports_ok_and_failure(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"roles": {"swarm workers": "m"}}), encoding="utf-8")
    assert routing._main(["--check", str(good)]) == 0
    assert capsys.readouterr().out == "panel ok\n"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"roles": {"arena runners": []}}), encoding="utf-8")
    assert routing._main(["--check", str(bad)]) == 1
    assert capsys.readouterr().out.splitlines() == ["arena runners: empty list", "1 problem(s)"]
