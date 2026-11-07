"""Load pstack-delegation through Hermes' real plugin discovery and assert routes per task.

Run with Hermes' own interpreter from its checkout, e.g.::

    cd ~/.hermes/hermes-agent && .venv/bin/python -m pytest -q -p no:cacheprovider \
        <fork>/hermes-plugin/pstack-delegation/tests/test_hermes_integration.py

Skipped when Hermes is not importable. Children are never started: the child builder and
runner are replaced with recorders, exactly like Hermes' tests/tools/test_delegation_backends.py.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

pytest.importorskip("agent.delegation_provider")
yaml = pytest.importorskip("yaml")

PLUGIN_SRC = Path(__file__).resolve().parents[1]


def _home(tmp_path, monkeypatch, delegation, panel):
    import hermes_cli.plugins as plugins_mod
    from hermes_cli.plugins import PluginManager

    home = tmp_path / ".hermes"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "pstack-delegation").symlink_to(PLUGIN_SRC, target_is_directory=True)
    (home / "config.yaml").write_text(yaml.safe_dump(
        {"plugins": {"enabled": ["pstack-delegation"]}, "delegation": delegation}), encoding="utf-8")
    if panel is not None:
        (home / "pstack-models.json").write_text(json.dumps({"budget": "unlimited", "roles": panel}),
                                                 encoding="utf-8")
    hermes_src = Path(plugins_mod.__file__).resolve().parents[1]
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "delegation").symlink_to(hermes_src / "plugins" / "delegation", target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path / "os-home"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugins_mod, "get_bundled_plugins_dir", lambda: bundled)
    manager = PluginManager()
    manager.discover_and_load()
    return manager


@pytest.fixture
def recorded(monkeypatch):
    from tools import delegate_tool as dt
    built = []

    def build(**kwargs):
        built.append(kwargs)
        return types.SimpleNamespace(tool_progress_callback=None, model=kwargs.get("model"))

    monkeypatch.setattr(dt, "_build_child_preserving_parent_tools", build)
    monkeypatch.setattr(dt, "_run_single_child", lambda task_index, goal, child=None, parent_agent=None, **kw: {
        "task_index": task_index, "status": "completed", "summary": "ok", "api_calls": 1, "duration_seconds": 0})
    return built


def _parent():
    return types.SimpleNamespace(session_id="root", model="parent-model", provider="custom",
                                 base_url="http://127.0.0.1:9/v1", api_key="parent-key",
                                 tool_progress_callback=None, _delegate_spinner=None, request_overrides={},
                                 _safe_print=lambda line: None)


ENDPOINT = {"base_url": "http://127.0.0.1:9/v1", "api_key": "k"}
DELEGATION = {"backend": "pstack", **ENDPOINT, "backends": {"pstack": {}}}


def test_schema_declares_pstack_parameters_and_is_byte_stable(tmp_path, monkeypatch):
    manager = _home(tmp_path, monkeypatch, DELEGATION, panel=None)
    try:
        import tools.delegate_tool  # noqa: F401  registers delegate_task
        from tools.registry import registry
        definition = registry.get_definitions({"delegate_task"})[0]["function"]
        props = definition["parameters"]["properties"]["tasks"]["items"]["properties"]
        assert props["pstack_role"]["enum"][0] == "feature, refactoring"
        assert len(props["pstack_role"]["enum"]) == 18
        assert props["access"]["enum"] == ["read-only", "write"]
        assert props["seat"]["type"] == "integer"
        assert "never pass `model`" in definition["description"]
        assert json.dumps(registry.get_definitions({"delegate_task"})[0]["function"]) == json.dumps(definition)
    finally:
        manager.unload()


def test_panel_seats_route_three_reviewers_to_three_models(tmp_path, monkeypatch, recorded):
    panel = {"interrogate reviewers": ["model-a@max", "model-b", "inherit-parent"]}
    manager = _home(tmp_path, monkeypatch, DELEGATION, panel)
    try:
        from tools.delegate_tool import delegate_task
        out = json.loads(delegate_task(tasks=[
            {"goal": "review the diff for correctness bugs", "pstack_role": "interrogate reviewers", "seat": 0, "access": "read-only"},
            {"goal": "review the diff for boundary mistakes", "pstack_role": "interrogate reviewers", "seat": 1, "access": "read-only"},
            {"goal": "review the diff for missing tests", "pstack_role": "interrogate reviewers", "seat": 2, "access": "read-only"},
            {"goal": "seat past the panel", "pstack_role": "interrogate reviewers", "seat": 3},
        ], parent_agent=_parent()))
        assert "results" in out, out
        statuses = [r["status"] for r in out["results"]]
        assert statuses == ["completed", "completed", "completed", "error"]
        assert "seat 3 is out of range" in out["results"][3]["error"]
        a, b, c = recorded
        assert (a["model"], a["override_reasoning_effort"]) == ("model-a", "max")
        assert b["model"] == "model-b"
        assert c["model"] is None
        from tools.delegate_tool_config import _load_config
        from tools.delegate_tool_routes import resolve_task_routes
        routes, error = resolve_task_routes([
            {"goal": "x", "pstack_role": "interrogate reviewers", "seat": n, "access": "read-only"} for n in range(3)
        ], _load_config(), _parent())
        assert error is None
        assert [r.report() for r in routes] == [{"backend": "pstack", "label": r.label} for r in routes]
        labels = [r.label for r in routes]
        assert labels == [
            "interrogate reviewers[0] -> model-a @max [read-only: advisory]",
            "interrogate reviewers[1] -> model-b [read-only: advisory]",
            "interrogate reviewers[2] -> parent [read-only: advisory]",
        ]
    finally:
        manager.unload()


def test_model_key_is_still_core_reserved_and_readonly_key_is_refused(tmp_path, monkeypatch, recorded):
    manager = _home(tmp_path, monkeypatch, DELEGATION, panel={})
    try:
        from tools.delegate_tool import delegate_task
        out = json.loads(delegate_task(tasks=[
            {"goal": "legacy readonly reviewer task", "readonly": True},
            {"goal": "summarize the module layout plainly"},
        ], parent_agent=_parent()))
        assert "results" in out, out
        assert [r["status"] for r in out["results"]] == ["error", "completed"]
        assert "does not take 'readonly'" in out["results"][0]["error"]
    finally:
        manager.unload()
