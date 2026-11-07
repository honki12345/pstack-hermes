"""Grading of poteto-mode runs from fixed gate logs and sqlite rows. No model is called."""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(EVAL_DIR))

import judge  # noqa: E402
import run_eval  # noqa: E402

SID = "20260101_000000_aaaaaa"
BLOCK_RESULT = json.dumps({"error": "poteto-mode gate: write the todolist before working."})
DECIDED = "Delegation decision: delegate_task for the investigation, skip: edit is one line"
SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, started_at REAL,
                       model TEXT, estimated_cost_usd REAL);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
                       content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
                       timestamp REAL);
"""
HEAD = "a" * 40
UNCHANGED = {"before": {"head": HEAD, "status": ""}, "after": {"head": HEAD, "status": ""}}
DIRTY = {"before": {"head": HEAD, "status": ""},
         "after": {"head": HEAD, "status": " M shop/pricing.py\n"}}
COMMITTED = {"before": {"head": HEAD, "status": ""}, "after": {"head": "b" * 40, "status": ""}}


class Transcript:
    """Builds a state.db and a gate log the way a gated session writes them.

    Every tool call logs the decision the real gate would make: a work call before the todolist
    is blocked, the first todo_list is `todo_written_now`, and so on. Events carry the call id the
    hook received; `ids=False` writes the older log shape without it.
    """

    def __init__(self, tmp_path: Path, ids: bool = True):
        self.ids = ids
        self.home = tmp_path
        (tmp_path / "logs").mkdir()
        self.db = sqlite3.connect(tmp_path / "state.db")
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO sessions (id, started_at) VALUES (?, 0)", (SID,))
        self.clock = 1000.0
        self.events: list[dict] = []
        self.call_count = 0
        self.armed = False
        self.todo_written = False

    def tick(self) -> float:
        self.clock += 1
        return self.clock

    def user(self, text="do the thing"):
        self.db.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
            (SID, text, self.tick()),
        )

    def gate(self, event, **fields):
        if event == "turn_armed":
            self.armed, self.todo_written = True, False
        elif event == "turn_disarmed":
            self.armed = False
        self.events.append({"ts": self.tick(), "event": event, "session_id": SID, **fields})

    def _decide(self, top, names, forced_block):
        if forced_block:
            return "block", "work_before_todo"
        if not self.armed:
            return None
        if self.todo_written:
            return "allow", "todo_written"
        if judge.TODO_TOOL in names:
            self.todo_written = True
            return "allow", "todo_written_now"
        if all(n in judge.PREPARATORY_TOOLS for n in names):
            return "allow", "preparatory_tool"
        return "block", "work_before_todo"

    @staticmethod
    def spec(name, args=None, *, blocked=False, bridged=False, result=None, unseen=False,
             decision=None):
        """`unseen`: the call never reached the gate (bad arguments, scope block), so no event.
        `decision`: the (event, reason) the gate logged, for the decisions `_decide` does not model
        (a todolist without a delegation decision, a release)."""
        return {"name": name, "args": args or {}, "blocked": blocked, "bridged": bridged,
                "result": result, "unseen": unseen, "batch": None, "decision": decision}

    @staticmethod
    def batch(*inner, result=None):
        """A `tool_call` bridge holding several local entries. Hermes rejects it before the hook
        unwraps it, so the gate sees (and blocks) the wrapper name."""
        args = {"calls": [{"name": n, "arguments": a} for n, a in inner]}
        rejected = result or json.dumps({"error": "tool_call takes exactly one entry for local tools"})
        return {"name": "tool_call", "args": args, "blocked": True, "bridged": False,
                "result": rejected, "unseen": False, "batch": [n for n, _ in inner],
                "decision": None}

    def parallel(self, *specs, log_order=None):
        """One assistant message carrying several tool calls.

        `log_order` lists the indexes of `specs` in the order the gate logged them; parallel calls
        reach the hook in any order.
        """
        stored, decisions = [], []
        for spec in specs:
            self.call_count += 1
            call_id = f"call_{self.call_count}"
            name, args = spec["name"], spec["args"]
            inner = name
            if spec["bridged"]:
                name, args = "tool_call", {"calls": [{"name": inner, "arguments": args}]}
            stored.append({"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}})
            decisions.append((call_id, name, inner, spec))
        self._insert(stored, decisions, log_order or range(len(decisions)))

    def _insert(self, stored, decisions, log_order):
        self.db.execute(
            "INSERT INTO messages (session_id, role, tool_calls, timestamp) "
            "VALUES (?, 'assistant', ?, ?)", (SID, json.dumps(stored), self.tick()),
        )
        logged = {}
        for index, (call_id, stored_name, inner, spec) in enumerate(decisions):
            names = spec["batch"] or [inner]
            decision = spec["decision"] or (
                None if spec["unseen"] else self._decide(inner, names, spec["blocked"]))
            if decision:
                event, reason = decision
                # Hermes unwraps a single-entry local `tool_call` bridge before pre_tool_call, so
                # the gate logs the inner name while state.db keeps `tool_call`.
                logged[index] = {
                    "ts": 0, "event": event, "session_id": SID, "tool_name": inner,
                    "bridged": [], "reason": reason,
                    **({"tool_call_id": call_id} if self.ids else {})}
            decisions[index] = (call_id, stored_name, inner, spec, decision)
        for index in log_order:
            if index in logged:
                self.events.append({**logged[index], "ts": self.tick()})
        for call_id, stored_name, _, spec, decision in decisions:
            content = spec["result"]
            if content is None:
                content = BLOCK_RESULT if decision and decision[0] == "block" else "{}"
            self.db.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, tool_name, "
                "timestamp) VALUES (?, 'tool', ?, ?, ?, ?)",
                (SID, content, call_id, stored_name, self.tick()),
            )

    def call(self, name, args=None, **kwargs):
        self.parallel(self.spec(name, args, **kwargs))

    def todo(self, *items, **kwargs):
        todos = [{"id": str(i), "content": text, "status": "pending"} for i, text in enumerate(items)]
        self.call("todo_list", {"todos": todos}, **kwargs)

    def child(self, cost=0.1):
        self.db.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, model, estimated_cost_usd) "
            "VALUES (?, ?, ?, 'child-model', ?)", (f"child_{self.tick()}", SID, self.clock, cost),
        )

    def grade(self, prompt, turn=1, sandbox=None):
        self.db.commit()
        log = self.home / judge.GATE_LOG
        log.write_text("".join(json.dumps(e) + "\n" for e in self.events), encoding="utf-8")
        extra = {"sandbox": sandbox} if sandbox else {}
        return judge.judge(db_path=self.home / judge.STATE_DB, gate_log=log, session_id=SID,
                           prompt=prompt, turn=turn, **extra)


@pytest.fixture
def run(tmp_path, request):
    t = Transcript(tmp_path, ids=getattr(request, "param", True))
    t.user()
    t.gate("turn_armed", turn_id="t1")
    return t


# A test marked with either one runs against a gate log with call ids or the older one without.
both_log_shapes = pytest.mark.parametrize("run", [True, False], ids=["id", "name"], indirect=True)
old_log_shape = pytest.mark.parametrize("run", [False], ids=["name"], indirect=True)


def test_g1_passes_when_the_decision_todolist_precedes_work(run):
    run.call("read_file")
    run.todo("Step 1", DECIDED)
    run.call("terminal", {"command": "python3 checks/run.py"})
    verdict = run.grade("p1-bugfix")
    assert verdict["G1"] is True
    assert verdict["evidence"]["first_work_tool"] == "terminal"


def test_g1_passes_for_a_todolist_written_through_the_bridge(run):
    run.call("todo_list", {"todos": [{"id": "1", "content": "Delegation decision: skip: one-line edit"}]}, bridged=True)
    run.call("patch", {"path": "README.md"})
    assert run.grade("p3-oneline")["G1"] is True


def test_g1_fails_when_work_is_attempted_before_the_todolist(run):
    run.call("terminal", {"command": "ls"})
    run.todo("Step 1", DECIDED)
    verdict = run.grade("p1-bugfix")
    assert verdict["G1"] is False
    assert verdict["counts"]["blocks"] == 1


def test_g1_fails_when_the_todolist_has_no_delegation_decision(run):
    run.todo("Reproduce the bug", "Fix the bug")
    run.call("terminal", {"command": "ls"})
    verdict = run.grade("p1-bugfix")
    assert verdict["G1"] is False
    assert verdict["evidence"]["todo_written_before_work"] is True
    assert verdict["evidence"]["todo_decision_before_work"] is False


REAL_RUN_DECISIONS = [
    "skip: delegate_task. The repo is a tiny single-purpose API and the bug is likely a one-function "
    "fix, so a subagent costs more than it saves.",
    "Delegation decision: skip: repo is tiny (single shop-api checkout), cause likely in one "
    "function; a direct read and fix is cheaper than a subagent round trip.",
    "skip delegate_task: repo is tiny, the fix is one function.",
]


@pytest.mark.parametrize("item", REAL_RUN_DECISIONS)
def test_g1_accepts_the_decision_items_agents_actually_wrote(run, item):
    run.todo("Step 1", item)
    run.call("terminal", {"command": "ls"})
    assert run.grade("p1-bugfix")["G1"] is True


@pytest.mark.parametrize("item", [
    "Step 3: open PR. skip: no PR requested", "skip: trivial", "skip:", "Delegation decision: skip:",
])
def test_g1_rejects_a_skip_that_is_not_a_delegation_decision(run, item):
    run.todo("Step 1", item)
    run.call("terminal", {"command": "ls"})
    verdict = run.grade("p1-bugfix")
    assert verdict["G1"] is False
    assert verdict["evidence"]["todo_decision_before_work"] is False


def test_g4_reads_the_reason_of_a_skip_delegate_task_item(run):
    run.todo("Step 1", "skip delegate_task: repo is tiny, the fix is one function.")
    run.call("patch", {"path": "README.md"})
    verdict = run.grade("p3-oneline")
    assert verdict["G4"] is True
    assert verdict["evidence"]["skip_reasons"] == ["repo is tiny, the fix is one function."]


def test_g4_ignores_a_playbook_step_skip_that_is_not_a_decision(run):
    run.todo("Step 3: open PR. skip: no PR requested")
    run.call("patch", {"path": "README.md"})
    verdict = run.grade("p3-oneline")
    assert verdict["G4"] is False
    assert verdict["evidence"]["skip_reasons"] == []


def test_g1_fails_when_the_gate_never_armed(tmp_path):
    t = Transcript(tmp_path)
    t.user()
    t.todo(DECIDED)
    t.call("terminal", {"command": "ls"})
    verdict = t.grade("p1-bugfix")
    assert verdict["G1"] is False
    assert verdict["evidence"]["turn_armed"] is False


def test_g1_fails_without_a_gate_log(tmp_path):
    t = Transcript(tmp_path)
    t.user()
    t.todo(DECIDED)
    t.call("terminal", {"command": "ls"})
    t.db.commit()
    verdict = judge.judge(db_path=tmp_path / judge.STATE_DB, gate_log=tmp_path / "missing.jsonl",
                          session_id=SID, prompt="p1-bugfix")
    assert verdict["G1"] is False
    assert verdict["evidence"]["gate_log_found"] is False


def test_g2_is_not_applicable_without_a_block(run):
    run.todo(DECIDED)
    assert run.grade("p1-bugfix")["G2"] is None


def test_g2_passes_when_the_next_real_step_writes_the_todolist(run):
    run.call("terminal", {"command": "ls"})
    run.call("read_file")
    run.todo(DECIDED)
    assert run.grade("p1-bugfix")["G2"] is True


@both_log_shapes
def test_g2_passes_after_a_parallel_block_in_one_message(run):
    run.parallel(run.spec("terminal", {"command": "ls"}), run.spec("session_search"))
    run.call("tool_search")
    run.todo(DECIDED)
    verdict = run.grade("p1-bugfix")
    assert verdict["counts"]["blocks"] == 2
    assert verdict["counts"]["block_messages"] == 1
    assert verdict["G2"] is True


@both_log_shapes
def test_g2_passes_when_a_bridged_call_is_blocked_with_a_plain_one_in_the_same_message(run):
    run.parallel(run.spec("terminal", {"command": "ls"}),
                 run.spec("session_search", {"query": "x"}, bridged=True))
    run.todo(DECIDED)
    verdict = run.grade("p1-bugfix")
    assert verdict["counts"]["blocks"] == 2
    assert verdict["counts"]["block_messages"] == 1
    assert verdict["G2"] is True


@both_log_shapes
def test_g2_fails_when_a_bridged_call_is_blocked_again_in_the_next_message(run):
    run.call("terminal", {"command": "ls"})
    run.call("process_manage", {"action": "poll"}, bridged=True)
    run.todo(DECIDED)
    assert run.grade("p1-bugfix")["G2"] is False


def test_g2_fails_when_the_agent_is_blocked_again_in_the_next_message(run):
    run.call("terminal", {"command": "ls"})
    run.call("terminal", {"command": "ls -la"})
    run.todo(DECIDED)
    verdict = run.grade("p1-bugfix")
    assert verdict["counts"]["block_messages"] == 2
    assert verdict["G2"] is False


def test_g2_fails_when_a_parallel_block_is_followed_by_another_block(run):
    run.parallel(run.spec("terminal", {"command": "ls"}), run.spec("session_search"))
    run.call("terminal", {"command": "ls"})
    run.todo(DECIDED)
    assert run.grade("p1-bugfix")["G2"] is False


UNDECIDED_WRITE = ("allow", "todo_pending_decision")


def test_g2_passes_when_an_undecided_todolist_is_followed_by_the_decision(run):
    run.call("terminal", {"command": "ls"})
    run.call("todo_list", {"todos": [{"id": "1", "content": "Step 1"}]}, decision=UNDECIDED_WRITE)
    run.call("todo_list", {"todos": [{"id": "2", "content": DECIDED}], "merge": True})
    assert run.grade("p1-bugfix")["G2"] is True


def test_g2_fails_when_the_undecided_todolist_is_followed_by_a_block(run):
    run.call("terminal", {"command": "ls"})
    run.call("todo_list", {"todos": [{"id": "1", "content": "Step 1"}]}, decision=UNDECIDED_WRITE)
    run.call("terminal", {"command": "ls"}, decision=("block", "todo_without_decision"),
             result=BLOCK_RESULT)
    run.call("todo_list", {"todos": [{"id": "2", "content": DECIDED}], "merge": True})
    verdict = run.grade("p1-bugfix")
    assert verdict["counts"]["blocks"] == 2
    assert verdict["G2"] is False


def test_g2_fails_when_the_gate_releases_the_turn_without_a_decision(run):
    run.call("terminal", {"command": "ls"})
    run.call("todo_list", {"todos": [{"id": "1", "content": "Step 1"}]}, decision=UNDECIDED_WRITE)
    run.call("terminal", {"command": "ls"}, decision=("allow", "decision_missing_released"))
    verdict = run.grade("p1-bugfix")
    assert verdict["G1"] is False
    assert verdict["G2"] is False


def test_g2_fails_when_the_agent_never_recovers(run):
    run.call("terminal", {"command": "ls"})
    assert run.grade("p3-oneline")["G2"] is False


def test_a_block_is_read_from_the_gate_log_not_from_the_result_text(run):
    run.todo(DECIDED)
    run.call("delegate_task", {"goal": "x"}, blocked=True, result="{}")
    verdict = run.grade("p1-bugfix")
    assert verdict["G3"] is False
    assert verdict["counts"]["blocks"] == 1


def test_a_result_that_quotes_the_block_message_is_not_a_block(run):
    run.todo(DECIDED)
    run.call("delegate_task", {"goal": "x"},
             result="notes/gate.md:3: poteto-mode gate: write the todolist before working.")
    verdict = run.grade("p1-bugfix")
    assert verdict["G3"] is True
    assert verdict["counts"]["blocks"] == 0


def test_g3_counts_executed_delegations(run):
    run.todo(DECIDED)
    run.call("delegate_task", {"pstack_role": "investigate", "goal": "find it"})
    run.call("delegate_task", {"pstack_role": "feature", "goal": "fix it"}, bridged=True)
    verdict = run.grade("p1-bugfix")
    assert verdict["G3"] is True
    assert verdict["counts"]["delegate_task_calls"] == 2
    assert verdict["evidence"]["delegate_roles"] == ["investigate", "feature"]


def test_g3_fails_without_a_delegation(run):
    run.todo(DECIDED)
    run.call("terminal", {"command": "ls"})
    assert run.grade("p1-bugfix")["G3"] is False


def test_g3_ignores_a_delegation_the_gate_refused(run):
    run.todo(DECIDED)
    run.call("delegate_task", {"goal": "x"}, blocked=True)
    assert run.grade("p1-bugfix")["G3"] is False


def test_g3_ignores_a_delegation_that_failed_without_starting_a_child(run):
    run.todo(DECIDED)
    run.call("delegate_task", {"goal": "x", "pstack_role": "feature"},
             result=json.dumps({"error": "oneshot_max_children exceeded"}))
    verdict = run.grade("p1-bugfix")
    assert verdict["G3"] is False
    assert verdict["counts"]["delegate_task_calls"] == 0
    assert verdict["counts"]["delegate_task_rejected"] == 1


def test_g3_ignores_a_bridged_delegation_that_failed(run):
    run.todo(DECIDED)
    failed = {"results": [{"index": 0, "name": "delegate_task", "error": {"code": "REJECTED"}}],
              "success_count": 0, "error_count": 1, "total_count": 1}
    run.call("delegate_task", {"goal": "x"}, bridged=True, result=json.dumps(failed))
    assert run.grade("p1-bugfix")["G3"] is False


def test_g3_trusts_a_child_session_over_an_error_result(run):
    run.todo(DECIDED)
    run.call("delegate_task", {"goal": "x"}, result=json.dumps({"error": "timed out"}))
    run.child()
    verdict = run.grade("p1-bugfix")
    assert verdict["G3"] is True
    assert verdict["counts"]["child_sessions"] == 1


def test_g4_passes_for_a_skip_reason_and_no_delegation(run):
    run.todo("Edit the title", "skip: single-line edit, delegation costs more than it saves")
    run.call("patch", {"path": "README.md"})
    verdict = run.grade("p3-oneline")
    assert verdict["G4"] is True
    assert verdict["evidence"]["skip_reasons"] == [
        "single-line edit, delegation costs more than it saves"
    ]


def test_g4_fails_when_the_agent_delegates(run):
    run.todo("skip: none")
    run.call("delegate_task", {"goal": "edit the title"})
    assert run.grade("p3-oneline")["G4"] is False


def test_g4_fails_when_a_child_session_exists(run):
    run.todo("skip: trivial")
    run.child()
    verdict = run.grade("p3-oneline")
    assert verdict["G4"] is False
    assert verdict["counts"]["child_sessions"] == 1
    assert verdict["evidence"]["child_cost_usd"] == 0.1


def test_g4_fails_without_a_skip_reason(run):
    run.todo("Edit the title")
    run.call("patch", {"path": "README.md"})
    assert run.grade("p3-oneline")["G4"] is False


@pytest.mark.parametrize("item", ["skip:", "skip:   ", "Skip :\t", "skip:\nEdit the title"])
def test_g4_fails_when_the_skip_has_no_reason(run, item):
    run.todo(item)
    run.call("patch", {"path": "README.md"})
    verdict = run.grade("p3-oneline")
    assert verdict["G4"] is False
    assert verdict["evidence"]["skip_reasons"] == []


@pytest.mark.parametrize("name, args", [
    ("terminal", {"command": "grep -n '>' shop/*.py | cat; python3 checks/run.py 2>&1"}),
    ("terminal", {"command": "git merge-base HEAD main; git stash list"}),
    ("terminal", {"command": 'grep -n "cp " shop/*.py'}),
    ("execute_code", {"code": "import shop.pricing as p\nif p.total(5, 3) > 100:\n    print('big')"}),
    ("execute_code", {"code": "print(open('shop/api.py').read())"}),
    ("execute_code", {"code": "import sys\nsys.stdout.write('x')"}),
])
def test_g5_passes_when_the_sandbox_is_unchanged_whatever_the_command_looks_like(run, name, args):
    run.todo("Trace the total")
    run.call(name, args)
    verdict = run.grade("p2-why", sandbox=UNCHANGED)
    assert verdict["G5"] is True
    assert verdict["evidence"]["sandbox"] == {"head_changed": False, "status_changed": False}


@pytest.mark.parametrize("name, args", [
    ("terminal", {"command": "git -C . commit -am done"}),
    ("terminal", {"command": "curl -o pricing.py http://example.invalid/y"}),
    ("terminal", {"command": "python3 -c \"import pathlib;getattr(pathlib.Path('f'),'write_'+'text')('x')\""}),
    ("execute_code", {"code": "from pathlib import Path\nPath('shop/pricing.py').unlink()"}),
    ("execute_code", {"code": "m = 'w'\nopen('x.py', mode=m)"}),
])
@pytest.mark.parametrize("after", [DIRTY, COMMITTED], ids=["dirty-tree", "new-commit"])
def test_g5_fails_when_the_sandbox_changed_whatever_the_command_looks_like(run, name, args, after):
    run.todo("Explain")
    run.call(name, args)
    verdict = run.grade("p2-why", sandbox=after)
    assert verdict["G5"] is False


@pytest.mark.parametrize("name", ["write_file", "patch", "skill_manage"])
def test_g5_fails_on_a_file_write_tool_even_if_the_sandbox_looks_unchanged(run, name):
    run.todo("Explain")
    run.call(name, {"path": "x.py"})
    verdict = run.grade("p2-why", sandbox=UNCHANGED)
    assert verdict["G5"] is False
    assert verdict["counts"]["write_tool_calls"] == 1


def test_g5_is_null_without_a_recorded_sandbox_state(run):
    run.todo("Explain")
    run.call("read_file", {"path": "shop/pricing.py"})
    assert run.grade("p2-why")["G5"] is None
    half = {"before": UNCHANGED["before"], "after": None}
    assert run.grade("p2-why", sandbox=half)["G5"] is None


def test_g5_ignores_a_write_the_gate_refused(run):
    run.call("write_file", {"path": "x.py"})
    assert run.grade("p2-why", sandbox=UNCHANGED)["G5"] is True


@both_log_shapes
def test_g5_ignores_writes_the_gate_refused_after_a_refused_bridged_call(run):
    run.call("session_search", {"query": "x"}, bridged=True)
    run.call("write_file", {"path": "x.py"})
    run.todo("Explain")
    verdict = run.grade("p2-why", sandbox=UNCHANGED)
    assert verdict["G5"] is True
    assert verdict["counts"]["write_tool_calls"] == 0
    assert verdict["counts"]["tool_calls"] == 1


@both_log_shapes
def test_g5_sees_an_executed_write_that_follows_a_refused_bridged_call(run):
    # The refused write is a different tool so a mispaired block cannot hide behind the executed one.
    run.call("session_search", {"query": "x"}, bridged=True)
    run.call("patch", {"path": "x.py"})
    run.todo("Explain")
    run.parallel(run.spec("write_file", {"path": "/tmp/outside.txt"}), run.spec("read_file"))
    verdict = run.grade("p2-why", sandbox=UNCHANGED)
    assert verdict["G5"] is False
    assert verdict["evidence"]["write_tools"] == ["write_file"]


@both_log_shapes
def test_a_refused_bridged_call_is_not_counted_as_executed(run):
    run.call("session_search", {"query": "x"}, bridged=True)
    run.todo(DECIDED)
    run.call("session_search", {"query": "x"}, bridged=True)
    verdict = run.grade("p1-bugfix")
    assert verdict["counts"]["blocks"] == 1
    assert verdict["counts"]["tool_calls"] == 2


# --- pairing by tool_call_id --------------------------------------------------------------------


def test_events_with_call_ids_are_paired_by_id(run):
    run.todo(DECIDED)
    assert run.grade("p1-bugfix")["pairing"] == "id"


@old_log_shape
def test_events_without_call_ids_are_paired_by_name(run):
    run.todo(DECIDED)
    assert run.grade("p1-bugfix")["pairing"] == "name"


def test_a_rejected_multi_entry_bridge_does_not_hide_a_later_blocked_write(run):
    # The wrapper is blocked under its own name; both writes it held never ran.
    run.parallel(run.batch(("write_file", {"path": "a"}), ("write_file", {"path": "b"})))
    run.call("write_file", {"path": "c"}, blocked=True)
    run.call("patch", {"path": "d"}, blocked=True)
    run.todo("Explain")
    verdict = run.grade("p2-why", sandbox=UNCHANGED)
    assert verdict["G5"] is True
    assert verdict["counts"]["write_tool_calls"] == 0
    assert verdict["counts"]["tool_calls"] == 1
    assert verdict["pairing"] == "id"


def test_a_call_the_gate_never_saw_does_not_take_the_next_messages_block(run):
    # Bad arguments or a scope block stop a call before the hook, so it has no event.
    run.todo(DECIDED)
    run.call("delegate_task", {"goal": "a", "pstack_role": "unseen"}, unseen=True)
    run.call("delegate_task", {"goal": "b", "pstack_role": "refused"}, blocked=True)
    verdict = run.grade("p1-bugfix")
    assert verdict["evidence"]["delegate_roles"] == ["unseen"]
    assert verdict["evidence"]["unpaired_events"] == 0


def test_parallel_calls_of_one_name_are_paired_whatever_order_the_gate_logged_them(run):
    run.todo(DECIDED)
    run.parallel(
        run.spec("delegate_task", {"goal": "a", "pstack_role": "refused"}, blocked=True),
        run.spec("delegate_task", {"goal": "b", "pstack_role": "ran"}),
        log_order=[1, 0],
    )
    verdict = run.grade("p1-bugfix")
    assert verdict["evidence"]["delegate_roles"] == ["ran"]
    assert verdict["counts"]["delegate_task_calls"] == 1


def test_two_blocked_writes_of_one_name_in_one_message_are_one_attempt(run):
    run.parallel(run.spec("write_file", {"path": "a"}), run.spec("write_file", {"path": "b"}))
    run.todo("Explain")
    verdict = run.grade("p2-why", sandbox=UNCHANGED)
    assert verdict["counts"]["blocks"] == 2
    assert verdict["counts"]["block_messages"] == 1
    assert verdict["G2"] is True
    assert verdict["G5"] is True


def test_an_event_no_call_claims_is_ignored(run):
    run.todo(DECIDED)
    run.gate("block", tool_name="write_file", reason="work_before_todo", tool_call_id="ghost")
    verdict = run.grade("p1-bugfix")
    assert verdict["counts"]["blocks"] == 0
    assert verdict["G2"] is None
    assert verdict["evidence"]["unpaired_events"] == 1


def test_an_event_with_no_id_is_ignored_when_the_log_carries_ids(run):
    run.call("write_file", {"path": "a"}, blocked=True)
    run.gate("block", tool_name="write_file", reason="work_before_todo", tool_call_id=None)
    verdict = run.grade("p2-why", sandbox=UNCHANGED)
    assert verdict["pairing"] == "id"
    assert verdict["counts"]["blocks"] == 1
    assert verdict["evidence"]["unpaired_events"] == 1


def test_a_clean_id_run_has_no_unpaired_events(run):
    run.todo(DECIDED)
    run.call("terminal", {"command": "ls"})
    verdict = run.grade("p1-bugfix")
    assert verdict["evidence"]["unpaired_events"] == 0


def test_g5_sees_a_change_even_when_a_result_quotes_the_block_message(run):
    run.todo("Explain")
    run.call("terminal", {"command": "sed -i 's/a/b/' shop/pricing.py"},
             result="plugins/x.py:12: poteto-mode gate: write the todolist")
    assert run.grade("p2-why", sandbox=DIRTY)["G5"] is False


@pytest.fixture
def two_turns(run):
    run.todo(DECIDED)
    run.call("terminal", {"command": "ls"})
    run.user("add a check for what you fixed")
    return run


def test_g6_passes_when_the_gate_stays_active_on_the_second_turn(two_turns):
    two_turns.gate("turn_continued", turn_id="t2")
    two_turns.gate("allow", tool_name="todo_list", reason="todo_written_now")
    verdict = two_turns.grade("p4-followup", turn=2)
    assert verdict["G6"] is True
    assert verdict["turn"] == 2


def test_g6_fails_when_the_second_turn_disarms_the_gate(two_turns):
    two_turns.gate("turn_disarmed", turn_id="t2")
    assert two_turns.grade("p4-followup", turn=2)["G6"] is False


def test_g6_fails_when_there_is_no_second_turn(run):
    verdict = run.grade("p4-followup", turn=2)
    assert verdict["G6"] is None
    assert verdict["evidence"] == {"turn_found": False, "turns_in_session": 1}


def test_turns_are_graded_from_their_own_events(two_turns):
    two_turns.gate("turn_disarmed", turn_id="t2")
    first = two_turns.grade("p1-bugfix", turn=1)
    assert first["evidence"]["gate_events"] == ["turn_armed", "allow", "allow"]
    assert first["G1"] is True


def test_every_verdict_carries_all_six_metrics(run):
    verdict = run.grade("p1-bugfix")
    assert [k for k in verdict if re.fullmatch(r"G\d", k)] == [f"G{i}" for i in range(1, 7)]


def write_run_json(run, name="out", **extra):
    run_dir = run.home / name
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "hermes_home": str(run.home),
        "turns": [{"prompt": "p2-why", "session_id": SID, "turn": 1}],
        **extra,
    }), encoding="utf-8")
    return run_dir


def test_cli_prints_one_json_line_per_verdict(run, capsys):
    run.todo(DECIDED)
    run.call("delegate_task", {"goal": "x"})
    run.grade("p1-bugfix")
    run_dir = run.home / "out"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "hermes_home": str(run.home),
        "turns": [{"prompt": "p1-bugfix", "session_id": SID, "turn": 1}],
    }), encoding="utf-8")
    assert judge.main(["--run-dir", str(run_dir)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["prompt"] == "p1-bugfix"
    assert json.loads(lines[0])["G3"] is True


@pytest.mark.parametrize("sandbox, expected", [
    (UNCHANGED, True), (DIRTY, False), (COMMITTED, False), (None, None),
])
def test_cli_grades_g5_from_the_sandbox_state_in_run_json(run, capsys, sandbox, expected):
    run.todo("Explain")
    run.call("read_file", {"path": "shop/pricing.py"})
    run.grade("p2-why")
    extra = {"sandbox_state": sandbox} if sandbox else {}
    assert judge.main(["--run-dir", str(write_run_json(run, **extra))]) == 0
    assert json.loads(capsys.readouterr().out)["G5"] is expected


def test_cli_reports_a_missing_run_dir_in_one_line(tmp_path, capsys):
    assert judge.main(["--run-dir", str(tmp_path / "nope")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("judge:") and len(err.strip().splitlines()) == 1


def test_cli_reports_an_unreadable_run_json_in_one_line(tmp_path, capsys):
    (tmp_path / "run.json").write_text("{not json", encoding="utf-8")
    assert judge.main(["--run-dir", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("judge:") and len(err.strip().splitlines()) == 1


def test_cli_reports_a_profile_without_state_db_in_one_line(run, capsys):
    run_dir = write_run_json(run, hermes_home=str(run.home / "no-such-profile"))
    assert judge.main(["--run-dir", str(run_dir)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("judge:") and len(err.strip().splitlines()) == 1
    assert judge.main(["--hermes-home", str(run.home / "gone"), "--session-id", SID,
                       "--prompt", "p1-bugfix"]) == 2


BLINDING_WORDS = re.compile(
    r"\b(eval|evals|evaluation|test|tests|testing|judge|experiment|rubric|score|compare|"
    r"benchmark|candidate|arena|probe)\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("path", sorted(
    p for p in (EVAL_DIR / "prompts").rglob("*") if p.is_file()
) + sorted(p for p in (EVAL_DIR / "fixtures").rglob("*") if p.is_file()), ids=lambda p: p.name)
def test_nothing_the_agent_sees_names_the_measurement(path):
    found = BLINDING_WORDS.findall(path.read_text(encoding="utf-8"))
    assert not found, f"{path.name} leaks measurement words: {found}"


def test_the_fixture_ships_the_bug_the_prompts_describe():
    pricing = (EVAL_DIR / "fixtures" / "shop-api" / "shop" / "pricing.py").read_text("utf-8")
    assert '"shipping": shipping(amount)' in pricing


def test_readme_documents_what_the_judge_prints(run):
    readme = (EVAL_DIR / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Verdict", 1)[1]
    example = json.loads(re.search(r"```json\n(.*?)\n```", section, re.DOTALL).group(1))
    actual = run.grade("p2-why", sandbox=UNCHANGED)
    assert isinstance(example, list)
    assert list(example[0]) == list(actual)
    assert list(example[0]["counts"]) == list(actual["counts"])
    assert list(example[0]["evidence"]) == list(actual["evidence"])
    assert "--clone-from pstacklab" not in readme


def test_run_eval_refuses_the_default_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    default = tmp_path / ".hermes"
    default.mkdir()
    (default / "config.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(run_eval.RunError, match="default profile"):
        run_eval.resolve_home(str(default))
    assert run_eval.resolve_home(str(default), allow_default=True) == default.resolve()


def test_run_eval_requires_an_explicit_profile(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    with pytest.raises(run_eval.RunError, match="HERMES_HOME"):
        run_eval.resolve_home(None)


def test_run_eval_child_env_is_rebuilt_from_scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", "/somewhere/else")
    monkeypatch.setenv("OPENAI_API_KEY", "leak")
    env = run_eval.child_env(tmp_path)
    assert env["HERMES_HOME"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in env


# --- run_eval: sandbox, sessions, cost -------------------------------------------------------


@pytest.fixture
def sandboxes(monkeypatch, tmp_path):
    """Point the sandbox temp dir at tmp_path so leftovers are visible."""
    root = tmp_path / "sandboxes"
    root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(root))
    return root


def test_make_sandbox_commits_under_a_signing_global_config(monkeypatch, tmp_path, sandboxes):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /usr/bin/false\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    repo = run_eval.make_sandbox()
    commit = subprocess.run(["git", "cat-file", "-p", "HEAD"], cwd=repo, capture_output=True,
                            text=True, check=True).stdout
    assert "gpgsig" not in commit
    assert "author shop <shop@example.com>" in commit


def test_make_sandbox_leaves_nothing_behind_when_the_commit_fails(monkeypatch, tmp_path, sandboxes):
    empty = tmp_path / "empty-fixture"
    empty.mkdir()
    monkeypatch.setattr(run_eval, "FIXTURE", empty)
    with pytest.raises(run_eval.RunError):
        run_eval.make_sandbox()
    assert list(sandboxes.iterdir()) == []


def test_sandbox_state_changes_with_a_new_file_but_not_with_python_caches(sandboxes):
    repo = run_eval.make_sandbox()
    before = run_eval.git_state(repo)
    assert before is not None and before["status"] == ""
    subprocess.run([sys.executable, "-B", "-c", "pass"], cwd=repo, check=True)
    (repo / "shop" / "__pycache__").mkdir()
    (repo / "shop" / "__pycache__" / "api.cpython-311.pyc").write_bytes(b"x")
    assert run_eval.git_state(repo) == before
    (repo / "notes.md").write_text("x", encoding="utf-8")
    after = run_eval.git_state(repo)
    assert after["head"] == before["head"]
    assert "?? notes.md" in after["status"]


def fake_profile(tmp_path, *, sessions=(), messages=(), events=()):
    home = tmp_path / "profile"
    (home / "logs").mkdir(parents=True)
    (home / "config.yaml").write_text("{}", encoding="utf-8")
    db = sqlite3.connect(home / "state.db")
    db.executescript(SCHEMA)
    for sid, started in sessions:
        db.execute("INSERT INTO sessions (id, started_at) VALUES (?, ?)", (sid, started))
    for sid, role, tool_calls, ts in messages:
        db.execute("INSERT INTO messages (session_id, role, tool_calls, timestamp) "
                   "VALUES (?, ?, ?, ?)", (sid, role, json.dumps(tool_calls) if tool_calls else None, ts))
    db.commit()
    db.close()
    (home / judge.GATE_LOG).write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return home


def stub_run(tmp_path, monkeypatch, sandboxes, *, cost=0.1, runner=None, during_run=None,
             usage_session=None):
    """One run with both runners faked: they write a usage file and leave a session to grade."""
    now = time.time()
    todo = [{"id": "c1", "function": {"name": "todo_list", "arguments": json.dumps(
        {"todos": [{"id": "1", "content": DECIDED, "status": "pending"}]})}}]
    work = [{"id": "c2", "function": {"name": "terminal", "arguments": "{}"}}]
    home = fake_profile(
        tmp_path,
        sessions=[("earlier-session", now + 5), ("run-session", now + 30)],
        messages=[("run-session", "user", None, now + 31),
                  ("run-session", "assistant", todo, now + 32),
                  ("run-session", "assistant", work, now + 33)],
        events=[{"ts": now + 31.5, "event": "turn_armed", "session_id": "run-session"},
                {"ts": now + 32.5, "event": "allow", "session_id": "run-session",
                 "tool_name": "todo_list", "reason": "todo_written_now"},
                {"ts": now + 33.5, "event": "allow", "session_id": "run-session",
                 "tool_name": "terminal", "reason": "todo_written"}],
    )
    started: list[str] = []

    def usage_file(out, name):
        path = out / name
        if cost is not None:
            path.write_text(json.dumps({"estimated_cost_usd": cost, "api_calls": 3,
                                        "session_id": usage_session}), encoding="utf-8")
        return path

    def fake_marker(home, prompt_id, out):
        path = out / f"{prompt_id}.message.txt"
        path.write_text("go\n", encoding="utf-8")
        return path

    def fake_cli(home, message, repo, out, **kwargs):
        started.append("cli")
        if during_run:
            during_run(home)
        return {"timed_out": False, "exit": 0,
                "usage": [usage_file(out, "usage-cli-1.json")]}

    def fake_inproc(home, messages, repo, out, *, timeout):
        started.append("inproc")
        if during_run:
            during_run(home)
        return {"timed_out": False, "exit": 0,
                "usage": [usage_file(out, "usage-1.json")]}

    monkeypatch.setattr(run_eval, "build_marker", fake_marker)
    monkeypatch.setattr(run_eval, "run_cli", fake_cli)
    monkeypatch.setattr(run_eval, "run_inproc", fake_inproc)
    out = tmp_path / "out"
    options = ["--runner", runner] if runner else []
    code = run_eval.main(["--prompt", "p1-bugfix", "--out", str(out), "--hermes-home", str(home),
                          "--cleanup", *options])
    record = json.loads((out / "run.json").read_text(encoding="utf-8")) \
        if (out / "run.json").exists() else None
    return code, record, out, started


def test_the_default_runner_is_inproc(tmp_path, monkeypatch, sandboxes):
    code, record, _, started = stub_run(tmp_path, monkeypatch, sandboxes)
    assert code == 0
    assert started == ["inproc"]
    assert record["runner"] == "inproc"


def test_the_cli_runner_is_still_selectable(tmp_path, monkeypatch, sandboxes):
    _, record, _, started = stub_run(tmp_path, monkeypatch, sandboxes, runner="cli")
    assert started == ["cli"]
    assert record["runner"] == "cli"


def test_there_is_no_auto_runner(tmp_path, monkeypatch, sandboxes, capsys):
    with pytest.raises(SystemExit) as stopped:
        stub_run(tmp_path, monkeypatch, sandboxes, runner="auto")
    assert stopped.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_a_follow_up_prompt_needs_the_inproc_runner(tmp_path, monkeypatch, sandboxes, capsys):
    home = fake_profile(tmp_path)
    code = run_eval.main(["--prompt", "p4-followup", "--runner", "cli", "--hermes-home",
                          str(home), "--out", str(tmp_path / "out")])
    err = capsys.readouterr().err.strip().splitlines()
    assert code == 2
    assert len(err) == 1 and "--runner inproc" in err[0]


def test_a_run_grades_the_newest_session(tmp_path, monkeypatch, sandboxes):
    code, _, out, _ = stub_run(tmp_path, monkeypatch, sandboxes)
    assert code == 0
    verdicts = json.loads((out / "verdict.json").read_text(encoding="utf-8"))
    assert [v["session_id"] for v in verdicts] == ["run-session"]
    assert verdicts[0]["G1"] is True


def test_cost_is_the_runs_usage(tmp_path, monkeypatch, sandboxes):
    _, record, _, _ = stub_run(tmp_path, monkeypatch, sandboxes, cost=0.3)
    assert record["cost_usd"] == 0.3
    assert record["cost_complete"] is True


def test_cost_is_null_not_zero_when_the_usage_file_is_missing(tmp_path, monkeypatch, sandboxes):
    _, record, _, _ = stub_run(tmp_path, monkeypatch, sandboxes, cost=None)
    assert record["cost_usd"] is None
    assert record["cost_complete"] is False


def test_a_failed_run_removes_its_sandbox(tmp_path, monkeypatch, sandboxes):
    def refuse(home):
        raise run_eval.RunError("hermes is not installed")

    code, record, _, _ = stub_run(tmp_path, monkeypatch, sandboxes, during_run=refuse)
    assert code == 2
    assert record is None
    assert list(sandboxes.iterdir()) == []


def test_run_json_records_the_sandbox_before_and_after(tmp_path, monkeypatch, sandboxes):
    _, record, _, _ = stub_run(tmp_path, monkeypatch, sandboxes)
    state = record["sandbox_state"]
    assert re.fullmatch(r"[0-9a-f]{40}", state["before"]["head"])
    assert state["before"] == state["after"]


# --- run_eval: interrupted runs ----------------------------------------------------------------

FAKE_HERMES = """#!/bin/sh
echo $$ > {dir}/parent.pid
sleep 300 &
echo $! > {dir}/child.pid
wait
"""


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_for(condition, seconds=5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


@pytest.fixture
def contained_signals():
    """A signal the test sends must not kill pytest if the harness fails to handle it."""

    def unhandled(signum, frame):
        raise AssertionError(f"signal {signum} reached the test process unhandled")

    watched = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    saved = {sig: signal.getsignal(sig) for sig in watched}
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, unhandled)
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


@pytest.fixture
def stuck_hermes(monkeypatch, tmp_path, contained_signals):
    """A `hermes` that never finishes and leaves a child behind, for both runners."""
    fake = tmp_path / "fake"
    for script in (fake / "bin" / "hermes", fake / "agent" / "venv" / "bin" / "python"):
        script.parent.mkdir(parents=True)
        script.write_text(FAKE_HERMES.format(dir=fake), encoding="utf-8")
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(run_eval.hermes_side, "agent_dir", lambda: fake / "agent")

    def marker(home, prompt_id, out):
        path = out / "m.txt"
        path.write_text("go\n", encoding="utf-8")
        return path

    monkeypatch.setattr(run_eval, "build_marker", marker)
    yield fake
    for name in ("parent.pid", "child.pid"):
        path = fake / name
        if path.exists() and path.read_text().strip():
            try:
                os.killpg(int(path.read_text()), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def signal_when_started(fake: Path, sig: int) -> None:
    def send() -> None:
        child = fake / "child.pid"
        wait_for(lambda: child.exists() and child.read_text().strip() != "")
        os.kill(os.getpid(), sig)

    threading.Thread(target=send, daemon=True).start()


@pytest.mark.parametrize("runner", ["cli", "inproc"])
@pytest.mark.parametrize(("sig", "raised"), [
    (signal.SIGINT, KeyboardInterrupt), (signal.SIGTERM, SystemExit),
    (signal.SIGHUP, SystemExit)])
def test_an_interrupted_run_leaves_no_child_process_and_no_sandbox(
        tmp_path, stuck_hermes, sandboxes, runner, sig, raised):
    home = fake_profile(tmp_path)
    signal_when_started(stuck_hermes, sig)
    with pytest.raises(raised):
        run_eval.main(["--prompt", "p2-why", "--runner", runner, "--hermes-home", str(home),
                       "--out", str(tmp_path / "out2"), "--timeout", "30"])
    parent = int((stuck_hermes / "parent.pid").read_text())
    child = int((stuck_hermes / "child.pid").read_text())
    assert wait_for(lambda: not pid_alive(parent) and not pid_alive(child))
    assert list(sandboxes.iterdir()) == []


@pytest.mark.parametrize(("runner", "told"), [("cli", 1), ("inproc", 0)])
def test_only_an_interrupted_cli_run_says_the_gateway_keeps_going(
        tmp_path, stuck_hermes, sandboxes, capsys, runner, told):
    home = fake_profile(tmp_path)
    signal_when_started(stuck_hermes, signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        run_eval.main(["--prompt", "p2-why", "--runner", runner, "--hermes-home", str(home),
                       "--out", str(tmp_path / "out2"), "--timeout", "30"])
    notes = [line for line in capsys.readouterr().err.splitlines() if "gateway" in line]
    assert len(notes) == told
    assert all(line.startswith("run_eval:") for line in notes)


def test_a_timed_out_cli_run_says_the_gateway_keeps_going(
        tmp_path, stuck_hermes, sandboxes, capsys):
    home = fake_profile(tmp_path)
    run_eval.main(["--prompt", "p2-why", "--runner", "cli", "--hermes-home", str(home),
                   "--out", str(tmp_path / "out2"), "--timeout", "0.5"])
    notes = [line for line in capsys.readouterr().err.splitlines() if "gateway" in line]
    assert len(notes) == 1


@pytest.mark.parametrize(("sig", "raised"), [
    (signal.SIGINT, KeyboardInterrupt), (signal.SIGTERM, SystemExit),
    (signal.SIGHUP, SystemExit)])
def test_a_signal_while_the_sandbox_is_built_leaves_no_sandbox(
        tmp_path, monkeypatch, sandboxes, contained_signals, sig, raised):
    real_git = run_eval._git

    def signalled_git(repo, *command):
        os.kill(os.getpid(), sig)
        time.sleep(1)
        return real_git(repo, *command)

    def marker(home, prompt_id, out):
        path = out / "m.txt"
        path.write_text("go\n", encoding="utf-8")
        return path

    monkeypatch.setattr(run_eval, "_git", signalled_git)
    monkeypatch.setattr(run_eval, "build_marker", marker)
    with pytest.raises(raised):
        run_eval.main(["--prompt", "p2-why", "--hermes-home", str(fake_profile(tmp_path)),
                       "--out", str(tmp_path / "out2")])
    assert list(sandboxes.iterdir()) == []


# --- run_eval: --out reuse and grading failures ---------------------------------------------


def test_a_second_run_into_the_same_out_is_refused_and_keeps_the_first(
        tmp_path, monkeypatch, sandboxes, capsys):
    code, record, out, _ = stub_run(tmp_path, monkeypatch, sandboxes)
    assert code == 0
    first = (out / "run.json").read_bytes()
    capsys.readouterr()
    again = run_eval.main(["--prompt", "p1-bugfix", "--out", str(out),
                           "--hermes-home", str(tmp_path / "profile"), "--timeout", "1"])
    err = capsys.readouterr().err
    assert again == 2
    assert len(err.strip().splitlines()) == 1
    assert "not an empty directory" in err
    assert (out / "run.json").read_bytes() == first


def test_an_existing_empty_out_directory_is_accepted(tmp_path, monkeypatch, sandboxes):
    (tmp_path / "out").mkdir()
    code, record, _, _ = stub_run(tmp_path, monkeypatch, sandboxes)
    assert code == 0
    assert record is not None


def test_an_out_that_is_a_file_is_refused(tmp_path, monkeypatch, sandboxes, capsys):
    target = tmp_path / "out"
    target.write_text("x", encoding="utf-8")
    code, _, _, _ = stub_run(tmp_path, monkeypatch, sandboxes)
    assert code == 2
    assert "run_eval:" in capsys.readouterr().err
    assert list(sandboxes.iterdir()) == []


def test_a_grading_failure_ends_with_one_line_and_keeps_run_json(
        tmp_path, monkeypatch, sandboxes, capsys):
    code, record, out, _ = stub_run(
        tmp_path, monkeypatch, sandboxes, usage_session="run-session",
        during_run=lambda home: (home / judge.STATE_DB).unlink())
    err = capsys.readouterr().err.strip().splitlines()
    assert code == 2
    assert [line for line in err if "cannot grade" in line] != []
    assert not any("Traceback" in line for line in err)
    assert record["turns"]
    assert not (out / "verdict.json").exists()
