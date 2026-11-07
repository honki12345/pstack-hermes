"""Grading of poteto-mode runs from fixed gate logs and sqlite rows. No model is called."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
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


class Transcript:
    """Builds a state.db and a gate log with a monotonically advancing clock."""

    def __init__(self, tmp_path: Path):
        self.home = tmp_path
        (tmp_path / "logs").mkdir()
        self.db = sqlite3.connect(tmp_path / "state.db")
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO sessions (id, started_at) VALUES (?, 0)", (SID,))
        self.clock = 1000.0
        self.events: list[dict] = []
        self.call_count = 0

    def tick(self) -> float:
        self.clock += 1
        return self.clock

    def user(self, text="do the thing"):
        self.db.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
            (SID, text, self.tick()),
        )

    def gate(self, event, **fields):
        self.events.append({"ts": self.tick(), "event": event, "session_id": SID, **fields})

    def call(self, name, args=None, *, blocked=False, bridged=False):
        self.call_count += 1
        call_id = f"call_{self.call_count}"
        if bridged:
            name, args = "tool_call", {"calls": [{"name": name, "arguments": args or {}}]}
        stored = [{"id": call_id, "function": {"name": name, "arguments": json.dumps(args or {})}}]
        self.db.execute(
            "INSERT INTO messages (session_id, role, tool_calls, timestamp) "
            "VALUES (?, 'assistant', ?, ?)", (SID, json.dumps(stored), self.tick()),
        )
        self.db.execute(
            "INSERT INTO messages (session_id, role, content, tool_call_id, tool_name, timestamp) "
            "VALUES (?, 'tool', ?, ?, ?, ?)",
            (SID, BLOCK_RESULT if blocked else "{}", call_id, name, self.tick()),
        )

    def todo(self, *items, **kwargs):
        todos = [{"id": str(i), "content": text, "status": "pending"} for i, text in enumerate(items)]
        self.call("todo_list", {"todos": todos}, **kwargs)

    def child(self, cost=0.1):
        self.db.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, model, estimated_cost_usd) "
            "VALUES (?, ?, ?, 'child-model', ?)", (f"child_{self.tick()}", SID, self.clock, cost),
        )

    def grade(self, prompt, turn=1):
        self.db.commit()
        log = self.home / judge.GATE_LOG
        log.write_text("".join(json.dumps(e) + "\n" for e in self.events), encoding="utf-8")
        return judge.judge(db_path=self.home / judge.STATE_DB, gate_log=log, session_id=SID,
                           prompt=prompt, turn=turn)


@pytest.fixture
def run(tmp_path):
    t = Transcript(tmp_path)
    t.user()
    t.gate("turn_armed", turn_id="t1")
    return t


def test_g1_passes_when_the_decision_todolist_precedes_work(run):
    run.call("read_file")
    run.todo("Step 1", DECIDED)
    run.call("terminal", {"command": "python3 checks/run.py"})
    verdict = run.grade("p1-bugfix")
    assert verdict["G1"] is True
    assert verdict["evidence"]["first_work_tool"] == "terminal"


def test_g1_passes_for_a_todolist_written_through_the_bridge(run):
    run.todo("skip: one-line edit", bridged=True)
    run.call("patch", {"path": "README.md"})
    assert run.grade("p3-oneline")["G1"] is True


def test_g1_fails_when_work_is_attempted_before_the_todolist(run):
    run.call("terminal", {"command": "ls"}, blocked=True)
    run.todo("Step 1", DECIDED)
    verdict = run.grade("p1-bugfix")
    assert verdict["G1"] is False
    assert verdict["counts"]["blocks"] == 0


def test_g1_fails_when_the_todolist_has_no_delegation_decision(run):
    run.todo("Reproduce the bug", "Fix the bug")
    run.call("terminal", {"command": "ls"})
    verdict = run.grade("p1-bugfix")
    assert verdict["G1"] is False
    assert verdict["evidence"]["todo_written_before_work"] is True
    assert verdict["evidence"]["todo_decision_before_work"] is False


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
    run.gate("allow", tool_name="todo_list", reason="todo_written_now")
    assert run.grade("p1-bugfix")["G2"] is None


def test_g2_passes_when_the_next_real_step_writes_the_todolist(run):
    run.gate("block", tool_name="terminal", reason="work_before_todo")
    run.gate("allow", tool_name="read_file", reason="preparatory_tool")
    run.gate("allow", tool_name="todo_list", reason="todo_written_now")
    assert run.grade("p1-bugfix")["G2"] is True


def test_g2_fails_when_the_agent_is_blocked_twice(run):
    run.gate("block", tool_name="terminal", reason="work_before_todo")
    run.gate("block", tool_name="terminal", reason="work_before_todo")
    run.gate("allow", tool_name="todo_list", reason="todo_written_now")
    assert run.grade("p1-bugfix")["G2"] is False


def test_g2_fails_when_the_agent_never_recovers(run):
    run.gate("block", tool_name="terminal", reason="work_before_todo")
    assert run.grade("p3-oneline")["G2"] is False


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


def test_g5_passes_for_a_read_only_run(run):
    run.todo("Trace the total")
    run.call("read_file", {"path": "shop/pricing.py"})
    run.call("terminal", {"command": "grep -n '>' shop/*.py | cat; python3 checks/run.py 2>&1"})
    verdict = run.grade("p2-why")
    assert verdict["G5"] is True
    assert verdict["counts"]["mutating_calls"] == 0


@pytest.mark.parametrize("name, args", [
    ("write_file", {"path": "x.py", "content": ""}),
    ("patch", {"path": "x.py"}),
    ("terminal", {"command": "sed -i 's/a/b/' shop/pricing.py"}),
    ("terminal", {"command": "echo fixed > shop/pricing.py"}),
    ("terminal", {"command": "git commit -am done"}),
    ("execute_code", {"code": "open('x.py', 'w').write('')"}),
])
def test_g5_fails_on_each_kind_of_mutation(run, name, args):
    run.todo("Explain")
    run.call(name, args)
    verdict = run.grade("p2-why")
    assert verdict["G5"] is False
    assert verdict["counts"]["mutating_calls"] == 1


def test_g5_ignores_a_mutation_the_gate_refused(run):
    run.call("terminal", {"command": "rm -rf build"}, blocked=True)
    assert run.grade("p2-why")["G5"] is True


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
    assert first["evidence"]["gate_events"] == ["turn_armed"]
    assert first["G1"] is True


def test_every_verdict_carries_all_six_metrics(run):
    verdict = run.grade("p1-bugfix")
    assert [k for k in verdict if re.fullmatch(r"G\d", k)] == [f"G{i}" for i in range(1, 7)]


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


def test_gate_arming_is_read_only_from_new_log_bytes(tmp_path):
    log = tmp_path / judge.GATE_LOG
    log.parent.mkdir()
    log.write_text('{"event": "turn_armed", "session_id": "old"}\n', encoding="utf-8")
    offset = run_eval.gate_log_size(tmp_path)
    assert run_eval.gate_armed_since(tmp_path, offset) is False
    with log.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "turn_armed", "session_id": "new"}\n')
    assert run_eval.gate_armed_since(tmp_path, offset) is True
