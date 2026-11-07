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


def tool(name, session="s1", args=None, **extra):
    return gate._on_pre_tool_call(tool_name=name, args=args or {}, session_id=session, **extra)


def todos(*contents, **extra):
    items = [{"id": str(i), "content": text, "status": "pending"} for i, text in enumerate(contents)]
    return {"todos": items, **extra}


DECIDED = todos("Step 1: reproduce", "Delegation decision: skip: one-line edit")
UNDECIDED = todos("Step 1: reproduce", "Step 2: fix", "Step 3: verify")


def bridged(name, arguments):
    return {"calls": [{"name": name, "arguments": arguments}]}


def log_records(tmp_path):
    return [json.loads(line) for line in (tmp_path / "gate.jsonl").read_text().splitlines()]


def last_decision(tmp_path):
    return [r for r in log_records(tmp_path) if r["event"] in {"block", "allow"}][-1]


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
    assert tool("todo_list", args=DECIDED) is None
    assert tool("terminal") is None


def test_bridged_todo_list_unlocks_work():
    turn()
    assert tool("tool_call", args=bridged("todo_list", DECIDED)) is None
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
    tool("todo_list", session="a", args=DECIDED)
    assert tool("terminal", session="a") is None


def test_unknown_session_is_unaffected():
    assert tool("terminal", session="never-seen") is None


def test_next_plain_turn_disarms_the_gate():
    turn()
    tool("todo_list", args=DECIDED)
    turn(message="thanks", turn_id="t2")
    assert tool("terminal") is None


def test_next_marker_turn_rearms_the_gate():
    turn()
    tool("todo_list", args=DECIDED)
    turn(turn_id="t2")
    assert is_blocked(tool("terminal"))


def test_repeat_hook_in_same_turn_keeps_todo_state():
    turn()
    tool("todo_list", args=DECIDED)
    turn()
    assert tool("terminal") is None


def test_delegate_task_is_counted_before_and_after_todo():
    turn()
    tool("delegate_task")
    tool("tool_call", args={"calls": [{"name": "delegate_task"}]})
    tool("todo_list", args=DECIDED)
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
    gate._on_pre_tool_call(tool_name="todo_list", args=DECIDED, session_id="s1")
    decisions = [r for r in log_records(tmp_path) if r["event"] in {"block", "allow"}]
    assert [r.get("tool_call_id") for r in decisions] == ["call-7", None]


def test_audit_log_has_decisions_but_not_message_text(tmp_path):
    turn(message=MARKER + "\nSECRET-USER-TEXT")
    tool("terminal", args={"command": "SECRET-COMMAND"})
    text = (tmp_path / "gate.jsonl").read_text()
    assert '"event": "block"' in text and "SECRET" not in text


def block_message(result):
    assert is_blocked(result)
    return result["message"]


def test_todolist_without_a_decision_keeps_work_blocked(tmp_path):
    turn()
    assert tool("todo_list", args=UNDECIDED) is None
    assert is_blocked(tool("terminal", args={"command": "ls"}))
    record = last_decision(tmp_path)
    assert (record["event"], record["reason"]) == ("block", "todo_without_decision")


def test_the_undecided_todo_write_itself_is_allowed_and_logged(tmp_path):
    turn()
    tool("todo_list", args=UNDECIDED, tool_call_id="call-1")
    record = last_decision(tmp_path)
    assert (record["event"], record["reason"]) == ("allow", "todo_pending_decision")
    assert record["tool_call_id"] == "call-1"


def test_work_with_no_todolist_still_reports_work_before_todo(tmp_path):
    turn()
    assert is_blocked(tool("terminal"))
    assert last_decision(tmp_path)["reason"] == "work_before_todo"


@pytest.mark.parametrize("text", [
    "skip: the edit is one line",
    "Skip: trivial",
    "delegation decision: skip : one-line edit",
    "Step 2: delegate_task the investigation (pstack_role: feature)",
    "DELEGATE_TASK the fix",
])
def test_a_decision_item_unlocks_work(text):
    turn()
    assert tool("todo_list", args=todos("Step 1", text)) is None
    assert tool("terminal") is None


@pytest.mark.parametrize("text", ["skip:", "skip:   ", "skipping the tests", "Step 2: do not delegate", "skip:\nreason"])
def test_text_that_only_looks_like_a_decision_does_not_unlock(text):
    turn()
    tool("todo_list", args=todos("Step 1", text))
    assert is_blocked(tool("terminal"))


def test_decision_in_any_item_counts():
    turn()
    tool("todo_list", args=todos("a", "b", "c", "skip: nothing to delegate"))
    assert tool("terminal") is None


def test_malformed_todos_do_not_unlock():
    turn()
    for bad in ({"todos": "skip: yes"}, {"todos": [None, 3, {"id": "1"}]}, {"todos": {"content": "skip: x"}}):
        tool("todo_list", args=bad)
    assert is_blocked(tool("terminal"))


def test_reading_the_list_changes_nothing():
    turn()
    assert tool("todo_list", args={}) is None
    assert is_blocked(tool("terminal"))


def test_bridged_undecided_todolist_keeps_work_blocked(tmp_path):
    turn()
    assert tool("tool_call", args=bridged("todo_list", UNDECIDED)) is None
    assert is_blocked(tool("terminal"))
    assert last_decision(tmp_path)["reason"] == "todo_without_decision"


@pytest.mark.parametrize("args", [
    bridged("todo_list", DECIDED),
    {"calls": [{"name": "todo_list", "arguments": json.dumps(DECIDED)}]},
    {"calls": json.dumps([{"name": "todo_list", "arguments": DECIDED}])},
    {"name": "todo_list", "arguments": DECIDED},
    {"calls": {"name": "todo_list", "arguments": DECIDED}},
], ids=["list", "json-arguments", "json-calls", "legacy-single", "single-dict"])
def test_every_bridge_shape_is_read_for_the_decision(args):
    turn()
    assert tool("tool_call", args=args) is None
    assert tool("terminal") is None


def test_bridge_batch_with_an_undecided_todo_and_work_is_blocked():
    turn()
    both = {"calls": [{"name": "todo_list", "arguments": UNDECIDED}, {"name": "terminal", "arguments": {}}]}
    assert is_blocked(tool("tool_call", args=both))
    assert is_blocked(tool("terminal"))


def test_bridge_batch_with_decision_todo_unlocks():
    turn()
    batch = {"calls": [{"name": "todo_list", "arguments": DECIDED}, {"name": "read_file", "arguments": {}}]}
    assert tool("tool_call", args=batch) is None
    assert tool("terminal") is None


def test_partial_merge_after_the_decision_is_not_blocked_again():
    turn()
    tool("todo_list", args=DECIDED)
    update = todos("Step 1: reproduce (done)", merge=True)
    assert tool("todo_list", args=update) is None
    assert tool("tool_call", args=bridged("todo_list", update)) is None
    assert tool("terminal") is None


def test_replace_write_after_the_decision_is_not_blocked_again():
    turn()
    tool("todo_list", args=DECIDED)
    tool("todo_list", args=UNDECIDED)
    assert tool("terminal") is None


def test_merge_that_adds_the_decision_unlocks():
    turn()
    tool("todo_list", args=UNDECIDED)
    assert is_blocked(tool("terminal"))
    assert tool("todo_list", args=todos("Decision: skip: one-line edit", merge=True)) is None
    assert tool("terminal") is None


def test_merge_without_a_decision_does_not_unlock():
    turn()
    assert tool("todo_list", args=todos("Step 1", merge=True)) is None
    assert is_blocked(tool("terminal"))


def test_a_new_marker_turn_forgets_the_decision():
    turn()
    tool("todo_list", args=DECIDED)
    turn(turn_id="t2")
    tool("todo_list", args=todos("only a merge", merge=True))
    assert is_blocked(tool("terminal"))


def test_undecided_block_message_says_what_to_write():
    turn()
    tool("todo_list", args=UNDECIDED)
    message = block_message(tool("terminal"))
    assert "delegate_task" in message and "skip: <reason>" in message
    assert "playbook" in message and "merge" in message
    assert "no delegation decision" in message


def test_first_block_message_names_the_playbook_sentence():
    turn()
    message = block_message(tool("terminal"))
    assert "delegate_task" in message and "skip: <reason>" in message
    assert "opening sentence" in message and "playbook" in message


def test_repeated_undecided_blocks_are_released(tmp_path):
    turn()
    tool("todo_list", args=UNDECIDED)
    assert is_blocked(tool("terminal"))
    assert is_blocked(tool("terminal"))
    assert tool("terminal") is None
    record = last_decision(tmp_path)
    assert (record["event"], record["reason"]) == ("allow", "decision_missing_released")
    assert tool("terminal") is None
    assert last_decision(tmp_path)["reason"] == "todo_written"


def test_release_is_counted_per_turn():
    turn()
    tool("todo_list", args=UNDECIDED)
    is_blocked(tool("terminal"))
    turn(turn_id="t2")
    tool("todo_list", args=UNDECIDED)
    assert is_blocked(tool("terminal"))
    assert is_blocked(tool("terminal"))
    assert tool("terminal") is None


def test_blocks_before_any_todolist_never_release(tmp_path):
    turn()
    for _ in range(6):
        assert is_blocked(tool("terminal"))
    assert last_decision(tmp_path)["reason"] == "work_before_todo"


def test_undecided_block_keeps_the_call_id_and_delegated_count(tmp_path):
    turn()
    tool("todo_list", args=UNDECIDED)
    tool("terminal", tool_call_id="call-9")
    record = last_decision(tmp_path)
    assert (record["tool_call_id"], record["tool_name"], record["bridged"]) == ("call-9", "terminal", [])
    assert record["delegated"] == 0
