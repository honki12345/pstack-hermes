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


def test_next_plain_turn_in_a_poteto_session_keeps_the_gate():
    turn()
    tool("todo_list", args=DECIDED)
    turn(message="thanks", turn_id="t2")
    assert is_blocked(tool("terminal"))


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
    "delegation decision: skip : one-line edit",
    "Step 2: delegate_task the investigation (pstack_role: feature)",
    "DELEGATE_TASK the fix",
    "skip: delegate_task. The repo is a tiny single-purpose API and the bug is likely a one-function fix, "
    "so a subagent costs more than it saves.",
    "Delegation decision: skip: repo is tiny (single shop-api checkout), cause likely in one function; "
    "a direct read and fix is cheaper than a subagent round trip.",
    "skip delegate_task: repo is tiny, the fix is one function.",
    "Delegation: Skip: trivial edit",
])
def test_a_decision_item_unlocks_work(text):
    turn()
    assert tool("todo_list", args=todos("Step 1", text)) is None
    assert tool("terminal") is None


@pytest.mark.parametrize("text", [
    "skip:", "skip:   ", "skipping the tests", "Step 2: do not delegate", "skip:\nreason",
    "Delegation decision: skip:\nreason",
    "Step 3: open PR. skip: no PR requested",
    "skip: the edit is one line",
    "Skip: trivial",
])
def test_text_that_only_looks_like_a_decision_does_not_unlock(text):
    turn()
    tool("todo_list", args=todos("Step 1", text))
    assert is_blocked(tool("terminal"))


def test_a_skipped_playbook_step_is_not_a_delegation_decision(tmp_path):
    turn()
    tool("todo_list", args=todos("Step 1: reproduce", "Step 3: open PR. skip: no PR requested"))
    assert is_blocked(tool("terminal"))
    assert last_decision(tmp_path)["reason"] == "todo_without_decision"


PLAYBOOKS = Path(__file__).resolve().parents[3] / "skills" / "poteto-mode" / "playbooks"


@pytest.mark.parametrize("name", ["multi-phase-plan", "orchestrate", "opening-a-pr"])
def test_playbook_text_that_names_delegate_task_still_unlocks_known_limit(name):
    """Pins a limit. The gate cannot tell a verbatim playbook step that mentions `delegate_task`
    from a decision about it, so the copy counts. The README lists this under what the check misses."""
    steps = [ln for ln in (PLAYBOOKS / f"{name}.md").read_text().splitlines() if "delegate_task" in ln]
    assert steps
    turn()
    tool("todo_list", args=todos("Step 1", steps[0]))
    assert tool("terminal") is None


def test_a_playbook_step_without_delegate_task_does_not_unlock():
    steps = [
        ln for ln in (PLAYBOOKS / "bug-fix.md").read_text().splitlines()
        if ln.strip() and "deleg" not in ln.lower()
    ]
    turn()
    tool("todo_list", args=todos(*steps[:5]))
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
    assert tool("todo_list", args=todos("Delegation decision: skip: one-line edit", merge=True)) is None
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
    assert "Delegation decision: delegate_task <" in message
    assert "Delegation decision: skip: <reason>" in message


def test_first_block_message_names_the_playbook_sentence():
    turn()
    message = block_message(tool("terminal"))
    assert "delegate_task" in message and "skip: <reason>" in message
    assert "opening sentence" in message and "playbook" in message
    assert "Delegation decision: delegate_task <" in message
    assert "Delegation decision: skip: <reason>" in message


def test_repeated_undecided_blocks_are_released(tmp_path):
    turn()
    tool("todo_list", args=UNDECIDED)
    assert is_blocked(tool("terminal"))
    assert is_blocked(tool("terminal"))
    assert tool("terminal") is None
    record = last_decision(tmp_path)
    assert (record["event"], record["reason"]) == ("allow", "decision_missing_released")
    assert tool("terminal") is None
    assert last_decision(tmp_path)["reason"] == "released"


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


def test_todos_passed_as_a_json_string_are_read():
    turn()
    items = DECIDED["todos"]
    assert tool("todo_list", args={"todos": json.dumps(items)}) is None
    assert tool("terminal") is None


def test_undecided_todos_passed_as_a_json_string_stay_blocked():
    turn()
    tool("todo_list", args={"todos": json.dumps(UNDECIDED["todos"])})
    assert is_blocked(tool("terminal"))


NOTICE = "[ASYNC DELEGATION BATCH COMPLETE — deleg-1]\nTask 1 finished."
QUESTION = "방금 뭐 바꿨어?"
FOLLOWUP = "방금 고친 부분이 다시 깨지지 않도록 확인 코드를 하나 추가해 주세요."


def events(tmp_path, kind=None):
    records = log_records(tmp_path)
    return [r for r in records if kind is None or r["event"] == kind]


def followup(message=FOLLOWUP, turn_id="t2", session="s1"):
    return turn(session=session, message=message, turn_id=turn_id)


def shell(command, **extra):
    return tool("terminal", args={"command": command}, **extra)


def test_followup_turn_is_logged_as_continued_not_disarmed(tmp_path):
    turn()
    tool("todo_list", args=DECIDED)
    followup()
    names = [r["event"] for r in events(tmp_path) if r["event"].startswith("turn_")]
    assert names == ["turn_armed", "turn_continued"]
    assert events(tmp_path, "turn_continued")[0]["turn_id"] == "t2"


def test_followup_turn_resets_the_todolist_and_decision_state():
    turn()
    tool("todo_list", args=DECIDED)
    followup()
    assert is_blocked(tool("patch", args={"path": "x"}))
    assert tool("todo_list", args=DECIDED) is None
    assert tool("patch", args={"path": "x"}) is None


def test_followup_after_a_released_turn_starts_fresh(tmp_path):
    turn()
    tool("todo_list", args=UNDECIDED)
    is_blocked(tool("terminal"))
    is_blocked(tool("terminal"))
    assert tool("terminal") is None
    followup()
    assert is_blocked(tool("patch", args={"path": "x"}))
    assert last_decision(tmp_path)["reason"] == "work_before_todo"
    assert gate._STATES["s1"].released is False


def test_followup_in_the_same_turn_does_not_reset_state():
    turn()
    followup()
    tool("todo_list", args=DECIDED)
    followup()
    assert tool("patch", args={"path": "x"}) is None


def test_session_without_a_poteto_turn_is_unaffected(tmp_path):
    turn(message="fix the typo", turn_id="t1")
    turn(message="and another", turn_id="t2")
    assert tool("terminal") is None
    assert not (tmp_path / "gate.jsonl").exists()


def test_new_marker_turn_rearms_after_a_followup(tmp_path):
    turn()
    tool("todo_list", args=DECIDED)
    followup()
    tool("todo_list", args=DECIDED)
    turn(turn_id="t3")
    assert is_blocked(tool("terminal"))
    assert [r["event"] for r in events(tmp_path) if r["event"].startswith("turn_")] == [
        "turn_armed", "turn_continued", "turn_armed",
    ]
    assert gate._STATES["s1"].followup is False


def test_subagent_turn_does_not_touch_the_parent_state(tmp_path):
    turn()
    tool("todo_list", args=DECIDED)
    turn(session="child", parent_session_id="s1", message="plain", turn_id="c1")
    assert tool("terminal", session="child") is None
    assert tool("terminal") is None
    assert "child" not in gate._STATES


def test_a_different_skill_invocation_ends_the_gate(tmp_path):
    turn()
    followup(message=MARKER.replace("pstack:poteto-mode", "pstack:how"))
    assert tool("terminal") is None
    assert [r["event"] for r in events(tmp_path) if r["event"].startswith("turn_")] == [
        "turn_armed", "turn_disarmed",
    ]


@pytest.mark.parametrize("command", [
    "git diff", "git status --short", "git log --oneline -5", "git -C /repo diff HEAD~1",
    "ls -la", "cat README.md", "rg foo src", "grep -rn foo .", "git diff | head -50",
    "git status && git diff --stat", "cd /repo && git status --short && git diff",
    "cd 'my repo' && ls", "cd /repo && git status --short && echo ---- && git diff",
])
def test_followup_allows_read_only_shell_commands_within_budget(command, tmp_path):
    turn()
    followup(message=QUESTION)
    assert shell(command) is None
    assert last_decision(tmp_path)["reason"] == "followup_read"


@pytest.mark.parametrize("command", [
    "rm -rf build", "git commit -am x", "git push", "git diff > out.patch", "python run.py",
    "cat a; rm a", "echo hi | tee x", "git diff $(rm x)", "ls `rm x`", "find . -delete",
    "find . -exec rm {} +", "sed -i s/a/b/ f", "git checkout main", "curl http://x", "",
    "git diff --output=x.patch", "cat a\nrm a", "git stash", "npm test",
    "cd /repo && rm -rf build", "cd", "cd a b", "cd /repo && make",
])
def test_followup_blocks_shell_commands_that_may_change_things(command):
    turn()
    followup(message=QUESTION)
    assert is_blocked(shell(command))


def test_followup_read_budget_is_three_then_work_needs_a_decision(tmp_path):
    turn()
    followup(message=QUESTION)
    assert shell("git status") is None
    assert shell("git diff") is None
    assert tool("search_files", args={"pattern": "x"}) is None
    assert is_blocked(shell("git log"))
    assert last_decision(tmp_path)["reason"] == "work_before_todo"
    assert tool("todo_list", args=DECIDED) is None
    assert shell("git log") is None


def test_followup_wide_read_batch_over_budget_is_blocked():
    turn()
    followup()
    calls = [{"name": "search_files", "arguments": {}} for _ in range(4)]
    assert is_blocked(tool("tool_call", args={"calls": calls}))


def test_followup_read_batch_within_budget_counts_each_call(tmp_path):
    turn()
    followup()
    calls = [{"name": "session_search", "arguments": {}}, {"name": "read_file", "arguments": {}}]
    assert tool("tool_call", args={"calls": calls}) is None
    assert gate._STATES["s1"].free_reads == 1


def test_followup_write_tools_are_blocked_without_a_todolist(tmp_path):
    turn()
    followup()
    for name in ("write_file", "patch", "execute_code", "web_search", "skill_manage"):
        assert is_blocked(tool(name))


def test_followup_read_allowance_closes_after_the_first_block():
    turn()
    followup()
    assert is_blocked(tool("patch", args={"path": "x"}))
    assert is_blocked(shell("git diff"))


def test_followup_read_allowance_closes_once_an_undecided_todolist_exists():
    turn()
    followup()
    tool("todo_list", args=UNDECIDED)
    assert is_blocked(shell("git diff"))


def test_marker_turn_has_no_read_allowance():
    turn()
    assert is_blocked(shell("git diff"))


def test_followup_question_turn_blocks_at_most_twice(tmp_path):
    turn()
    tool("todo_list", args=DECIDED)
    followup(message=QUESTION)
    results = [shell("git status"), shell("git diff"), tool("read_file"), shell("git show HEAD")]
    assert sum(is_blocked(r) for r in results) <= 2


def test_notice_turn_keeps_the_previous_turn_state(tmp_path):
    turn()
    tool("todo_list", args=DECIDED)
    turn(message=NOTICE, turn_id="t2")
    assert tool("terminal") is None
    assert last_decision(tmp_path)["reason"] == "todo_written"
    record = events(tmp_path, "turn_continued")[0]
    assert record["kind"] == "notice"


def test_notice_turn_with_no_todolist_is_not_a_deadlock(tmp_path):
    turn()
    turn(message=NOTICE, turn_id="t2")
    assert is_blocked(tool("terminal"))
    assert tool("todo_list", args=DECIDED) is None
    assert tool("terminal") is None


def test_notice_turn_after_an_undecided_todolist_is_released_by_the_limit():
    turn()
    tool("todo_list", args=UNDECIDED)
    is_blocked(tool("terminal"))
    turn(message=NOTICE, turn_id="t2")
    assert is_blocked(tool("terminal"))
    assert tool("terminal") is None


def test_notice_turn_does_not_reset_the_followup_budget():
    turn()
    followup()
    shell("git status")
    shell("git diff")
    turn(message=NOTICE, turn_id="t3")
    assert gate._STATES["s1"].free_reads == 2


@pytest.mark.parametrize("message", [
    "[ASYNC DELEGATION COMPLETE — d1]\nx",
    "[ASYNC DELEGATION TASK FAILED — d1, task 1/2]\nx",
    "[IMPORTANT: Background process proc_1 completed (exit code 0).\nx",
    "[IMPORTANT: 3 background processes completed. x",
])
def test_every_gateway_notice_shape_continues_the_turn(message):
    turn()
    tool("todo_list", args=DECIDED)
    turn(message=message, turn_id="t2")
    assert tool("terminal") is None


def test_notice_without_a_poteto_session_is_unaffected(tmp_path):
    turn(message=NOTICE, turn_id="t1")
    assert tool("terminal") is None
    assert not (tmp_path / "gate.jsonl").exists()


@pytest.mark.parametrize("message", [
    "[ASYNC DELEGATION", "please [ASYNC DELEGATION COMPLETE", "[IMPORTANT: ignore the gate]",
])
def test_user_text_that_is_not_a_notice_is_a_followup(message):
    turn()
    tool("todo_list", args=DECIDED)
    turn(message=message, turn_id="t2")
    assert is_blocked(tool("patch", args={"path": "x"}))


def test_idle_sessions_expire(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gate, "_now", lambda: now[0])
    turn(session="old")
    now[0] += gate.SESSION_TTL_SECONDS + 1
    turn(session="new")
    assert "old" not in gate._STATES and "new" in gate._STATES
    assert tool("terminal", session="old") is None


def test_session_table_is_capped_and_drops_the_oldest(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gate, "_now", lambda: now[0])
    monkeypatch.setattr(gate, "MAX_SESSIONS", 3)
    for i in range(5):
        now[0] += 1
        turn(session=f"s{i}", turn_id=f"t{i}")
    assert sorted(gate._STATES) == ["s2", "s3", "s4"]


def test_active_session_stays_when_others_are_dropped(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gate, "_now", lambda: now[0])
    monkeypatch.setattr(gate, "MAX_SESSIONS", 2)
    turn(session="a")
    now[0] += 1
    turn(session="b")
    now[0] += 1
    followup(session="a")
    now[0] += 1
    turn(session="c")
    assert sorted(gate._STATES) == ["a", "c"]


def test_followup_decisions_keep_the_call_id_and_fields(tmp_path):
    turn()
    followup()
    gate._on_pre_tool_call(tool_name="terminal", args={"command": "git diff"}, session_id="s1",
                           tool_call_id="call-3")
    record = last_decision(tmp_path)
    assert (record["tool_call_id"], record["reason"], record["event"]) == ("call-3", "followup_read", "allow")


# Notice fixtures. The first group is the output of Hermes' own formatters, run once and pasted here
# so the tests need no Hermes install. Source: tools/process_registry_notifications.py
# (format_process_notification, ProcessNotificationBatch.render) at hermes-agent 2bd378ed1b.
_BG_COMPLETE = (
    "[IMPORTANT: Background process proc_abc123 completed normally (exit code 0).\n"
    "Command: sleep 60\nOutput:\nhello]"
)
_BG_HEARTBEAT = (
    "[Background process proc_abc123 heartbeat #2 \u2014 still running after 2m (next in 60s when "
    "there is new output; you will also be told when it exits).\n"
    "Command: sleep 60\nOutput since last heartbeat:\nhello]"
)
_BG_WATCH_MATCH = (
    '[IMPORTANT: Background process proc_abc123 matched watch pattern "ERROR".\n'
    "Command: sleep 60\nMatched output:\nhello]"
)
_BG_WATCH_DISABLED = (
    "[IMPORTANT: Watch patterns disabled for process proc_abc123 \u2014 too many matches. Falling "
    "back to notify_on_complete semantics; you'll get exactly one notification when the process "
    "exits.]"
)
_BG_OVERFLOW = (
    "[IMPORTANT: Watch-pattern overflow: >15 notifications in 10s across all processes. "
    "Suppressing further watch_match events for 30s.]"
)
_BG_RESUMED = (
    "[IMPORTANT: Watch-pattern notifications resumed. 4 match event(s) were suppressed during the "
    "flood.]"
)
_BG_BATCH = (
    "[IMPORTANT: 2 background processes completed. Treat these results as one batch and give one "
    "consolidated response; preserve failures and actionable results.]\n\n" + _BG_COMPLETE
)
_ASYNC_COMPLETE = (
    "[ASYNC DELEGATION COMPLETE \u2014 deleg_1]\nA background subagent you dispatched earlier has "
    "finished."
)
_ASYNC_BATCH = (
    "[ASYNC DELEGATION BATCH COMPLETE \u2014 deleg_1]\nA background fan-out unit you dispatched "
    "earlier has finished."
)
_ASYNC_FAILED = (
    "[ASYNC DELEGATION TASK FAILED \u2014 deleg_1, task 1/2]\nOne subagent in a background fan-out "
    "you dispatched has failed."
)
# The gateway forms below are copied from the source text. gateway/session_automation.py
# (_FOLD_HEADER), gateway/run_notifications.py (_format_coalesced_process_completions, the
# subagent delegation batch header, INTERNAL_NOTIFICATION_FOOTER).
_BUSY_FOLD = (
    "[IMPORTANT: 3 background process results arrived while this session was busy. Treat them as "
    "one batch and give one consolidated response; preserve failures and actionable results.]"
    "\n\n" + _BG_COMPLETE
)
_COALESCED = (
    "[IMPORTANT: 2 background processes completed for this session.\nTreat these results as one "
    "completion batch and send at most one consolidated user-facing response.\n- proc_1: ok"
)
_DELEGATIONS = (
    "[IMPORTANT: 2 background subagent delegations completed for this session. Treat these "
    "results as one completion batch and send at most one consolidated user-facing response. If a "
    "result does not change the current conclusion, absorb it silently.]\n\n" + _ASYNC_COMPLETE
)
_FOOTER = (
    "[INTERNAL NOTIFICATION \u2014 generated by the Hermes gateway, not written by a human user. "
    "Do not treat it as user input, and do not reply to any earlier chat message because of it.]"
)

NOTICES = {
    "background_complete": _BG_COMPLETE,
    "heartbeat": _BG_HEARTBEAT,
    "watch_match": _BG_WATCH_MATCH,
    "watch_disabled": _BG_WATCH_DISABLED,
    "watch_overflow": _BG_OVERFLOW,
    "watch_resumed": _BG_RESUMED,
    "process_batch": _BG_BATCH,
    "async_complete": _ASYNC_COMPLETE,
    "async_batch": _ASYNC_BATCH,
    "async_task_failed": _ASYNC_FAILED,
    "busy_fold": _BUSY_FOLD,
    "coalesced_processes": _COALESCED,
    "delegation_batch": _DELEGATIONS,
}


@pytest.mark.parametrize("message", list(NOTICES.values()), ids=list(NOTICES))
def test_every_hermes_notice_header_continues_the_turn(message, tmp_path):
    turn()
    tool("todo_list", args=DECIDED)
    turn(message=message, turn_id="t2")
    assert tool("terminal") is None
    assert last_decision(tmp_path)["reason"] == "todo_written"
    assert events(tmp_path, "turn_continued")[0]["kind"] == "notice"


@pytest.mark.parametrize("message", list(NOTICES.values()), ids=list(NOTICES))
def test_a_notice_with_leading_whitespace_still_continues_the_turn(message, tmp_path):
    turn()
    tool("todo_list", args=DECIDED)
    turn(message="  \n" + message, turn_id="t2")
    assert events(tmp_path, "turn_continued")[0]["kind"] == "notice"


@pytest.mark.parametrize("body", ["a notification the headers do not know", "ping", _BG_COMPLETE])
def test_the_gateway_footer_marks_a_notice_whatever_its_header(body, tmp_path):
    turn()
    tool("todo_list", args=DECIDED)
    turn(message=body + "\n\n" + _FOOTER, turn_id="t2")
    assert events(tmp_path, "turn_continued")[0]["kind"] == "notice"


def test_a_footer_with_other_wording_after_gateway_is_still_a_notice(tmp_path):
    turn()
    turn(message="x\n\n[INTERNAL NOTIFICATION \u2014 generated by the Hermes gateway]", turn_id="t2")
    assert events(tmp_path, "turn_continued")[0]["kind"] == "notice"


def test_a_footer_in_the_middle_of_user_text_is_not_a_notice(tmp_path):
    turn()
    turn(message=_FOOTER + "\n\nplease explain this footer", turn_id="t2")
    assert events(tmp_path, "turn_continued")[0]["kind"] == "followup"


@pytest.mark.parametrize("message", [
    "[Background process proc_1 is great]", "[Background process]", "[IMPORTANT: Watch out]",
    "[IMPORTANT: 3 background tasks]", "[IMPORTANT: Watch-pattern]", "[ASYNC]", "ASYNC DELEGATION",
])
def test_text_that_only_resembles_a_header_is_a_followup(message, tmp_path):
    turn()
    turn(message=message, turn_id="t2")
    assert events(tmp_path, "turn_continued")[0]["kind"] == "followup"


def test_heartbeat_after_a_decided_turn_does_not_ask_for_a_new_decision():
    turn()
    tool("todo_list", args=DECIDED)
    turn(message=_BG_HEARTBEAT, turn_id="t2")
    assert tool("process_manage") is None


# Flags that write a file or run another program. Checked against the man pages of each allowed
# command (file, ripgrep, git, find). A command with one of them is never read-only.
@pytest.mark.parametrize("command", [
    "file -C", "file --compile", "file -C -m /tmp/magic", "file --comp x", "file -bC x",
    "file -f list.txt", "file --files-from list.txt",
    "rg --hostname-bin=/tmp/x foo", "rg --hostname-bin /tmp/x foo",
    "rg --pre=cmd foo", "rg --pre cmd foo", "rg --pre-glob '*.pdf' foo",
    "git diff --ext-di", "git diff --ext-diff", "git log -p --ext", "git show --textconv HEAD",
    "git diff -O/tmp/orderfile", "git diff --out=x.patch", "git log --output=x",
    "find . -fprint /tmp/x", "find . -fls /tmp/x", "find . -fprintf /tmp/x %p",
])
def test_flags_that_write_or_execute_are_not_read_only(command):
    assert gate._is_read_only_shell(command) is False


@pytest.mark.parametrize("command", [
    "file x", "file -b x", "file -bi x", "file -L --mime-type a b", "file --brief --mime-encoding x",
    "rg -z foo f", "rg --files", "rg --debug foo", "rg -n --glob '*.py' foo",
    "git log --pretty=format:%h", "git log --prefix", "git diff --no-ext-diff",
])
def test_the_same_commands_without_those_flags_stay_read_only(command):
    assert gate._is_read_only_shell(command) is True
