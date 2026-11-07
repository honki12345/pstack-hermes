import importlib.util
import json
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("poteto_gate", Path(__file__).parent.parent / "__init__.py")
gate = importlib.util.module_from_spec(_spec)
sys.modules["poteto_gate"] = gate
_spec.loader.exec_module(gate)

MARKER = (
    '[IMPORTANT: The user has invoked the "pstack:poteto-mode" skill, indicating they want you to '
    "follow its instructions. The full skill content is loaded below.]\n\n# Poteto mode"
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    gate._STATES.clear()
    monkeypatch.setattr(gate, "_log_path", lambda: tmp_path / "gate.jsonl")


def turn(session="s1", message=MARKER, turn_id="t1", **extra):
    return gate._on_pre_llm_call(session_id=session, user_message=message, turn_id=turn_id, **extra)


def tool(name, session="s1", args=None):
    return gate._on_pre_tool_call(tool_name=name, args=args or {}, session_id=session)


def is_blocked(result):
    return isinstance(result, dict) and result.get("action") == "block" and bool(result.get("message"))


def test_turn_hook_never_injects_context():
    assert turn() is None


def test_terminal_is_blocked_on_marker_turn():
    turn()
    assert is_blocked(tool("terminal", args={"command": "ls"}))


@pytest.mark.parametrize("name", ["execute_code", "search_files", "patch", "write_file", "web_search"])
def test_other_working_tools_are_blocked(name):
    turn()
    assert is_blocked(tool(name))


@pytest.mark.parametrize("name", sorted(gate.ALLOWED_BEFORE_TODO - {"todo_list"}))
def test_preparatory_tools_pass_before_todo(name):
    turn()
    assert tool(name) is None
    assert is_blocked(tool("terminal"))


def test_direct_todo_list_unlocks_work():
    turn()
    assert tool("todo_list") is None
    assert tool("terminal") is None


def test_bridged_todo_list_unlocks_work():
    turn()
    assert tool("tool_call", args={"calls": [{"name": "todo_list", "arguments": {}}]}) is None
    assert tool("terminal") is None


def test_bridged_working_tool_is_blocked():
    turn()
    assert is_blocked(tool("tool_call", args={"calls": [{"name": "process_manage", "arguments": {}}]}))
    assert is_blocked(tool("tool_call", args={"calls": [{"name": "tool_search"}, {"name": "computer_use"}]}))


def test_bridged_preparatory_tool_passes():
    turn()
    assert tool("tool_call", args={"calls": [{"name": "tool_search", "arguments": {}}]}) is None


def test_bridge_without_calls_is_blocked():
    turn()
    assert is_blocked(tool("tool_call", args={}))


def test_no_marker_turn_is_unaffected():
    turn(message="just fix the typo")
    assert tool("terminal") is None


def test_subagent_turn_is_unaffected():
    turn(session="child", parent_session_id="s1")
    assert tool("terminal", session="child") is None


def test_sessions_are_independent():
    turn(session="a")
    turn(session="b", message="plain", turn_id="t9")
    assert is_blocked(tool("terminal", session="a"))
    assert tool("terminal", session="b") is None
    tool("todo_list", session="a")
    assert tool("terminal", session="a") is None


def test_unknown_session_is_unaffected():
    assert tool("terminal", session="never-seen") is None


def test_next_plain_turn_disarms_the_gate():
    turn()
    tool("todo_list")
    turn(message="thanks", turn_id="t2")
    assert tool("terminal") is None


def test_next_marker_turn_rearms_the_gate():
    turn()
    tool("todo_list")
    turn(turn_id="t2")
    assert is_blocked(tool("terminal"))


def test_repeat_hook_in_same_turn_keeps_todo_state():
    turn()
    tool("todo_list")
    turn()
    assert tool("terminal") is None


def test_delegate_task_is_counted_before_and_after_todo():
    turn()
    tool("delegate_task")
    tool("tool_call", args={"calls": [{"name": "delegate_task"}]})
    tool("todo_list")
    tool("delegate_task")
    assert gate._STATES["s1"].delegated == 3


def test_marker_must_open_the_message():
    turn(message="please note " + MARKER)
    assert tool("terminal") is None


def test_other_skill_marker_is_ignored():
    turn(message=MARKER.replace("pstack:poteto-mode", "pstack:how"))
    assert tool("terminal") is None


@pytest.mark.parametrize("name", ["poteto-mode", "/poteto-mode", "pstack:poteto-mode", "/pstack:poteto-mode"])
def test_marker_name_forms(name):
    turn(message=MARKER.replace("pstack:poteto-mode", name))
    assert is_blocked(tool("terminal"))


def test_bundle_marker_with_poteto_arms_the_gate():
    turn(message=MARKER.replace('"pstack:poteto-mode"', '"/clean /poteto-mode"'))
    assert is_blocked(tool("terminal"))


def test_non_string_inputs_are_ignored():
    assert turn(message=None) is None
    assert gate._on_pre_tool_call(tool_name=None, session_id="s1") is None
    assert gate._on_pre_tool_call() is None


def test_callback_exception_allows_the_call(monkeypatch):
    turn()
    monkeypatch.setattr(gate, "_decide", lambda *a, **k: 1 / 0)
    assert tool("terminal") is None


def test_audit_failure_does_not_change_decision(monkeypatch):
    turn()
    monkeypatch.setattr(gate, "_log_path", lambda: Path("/proc/nope/x.jsonl"))
    assert is_blocked(tool("terminal"))


def test_audit_log_names_the_tool_call_it_decided(tmp_path):
    turn()
    gate._on_pre_tool_call(tool_name="terminal", args={}, session_id="s1", tool_call_id="call-7")
    gate._on_pre_tool_call(tool_name="todo_list", args={}, session_id="s1")
    records = [json.loads(line) for line in (tmp_path / "gate.jsonl").read_text().splitlines()]
    decisions = [r for r in records if r["event"] in {"block", "allow"}]
    assert [r.get("tool_call_id") for r in decisions] == ["call-7", None]


def test_audit_log_has_decisions_but_not_message_text(tmp_path):
    turn(message=MARKER + "\nSECRET-USER-TEXT")
    tool("terminal", args={"command": "SECRET-COMMAND"})
    text = (tmp_path / "gate.jsonl").read_text()
    assert '"event": "block"' in text and "SECRET" not in text
