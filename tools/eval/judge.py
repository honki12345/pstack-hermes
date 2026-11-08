#!/usr/bin/env python3
"""Grade a poteto-mode run from the delegation gate log and the profile's state.db.

Deterministic on purpose: a model grader would add cost and bias for a question the tool-call
order already answers. Reads only the gate log (`logs/poteto-delegation-gate.jsonl`) and, from
`state.db`, `messages` and `sessions`, plus the sandbox state run_eval.py recorded. Nothing is
written.

Metrics (null when the prompt does not exercise them):
  G1 decision first      a todo_list holding a delegation decision precedes the first work tool
  G2 recovery            after a gate block, the agent writes the todolist without being blocked
                         again in a later assistant message (parallel blocks are one attempt)
  G3 delegation happens  p1: at least one delegate_task executed and not rejected
  G4 no over-delegation  p3: no delegate_task, no child session, and a `skip:` reason is written in a decision item
  G5 reroute             p2: the sandbox ends as it began (same HEAD, same porcelain status) and
                         no file-writing tool ran; null without a recorded before/after state
  G6 follow-up turn      p4: the gate stays active on the second turn of the same session
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

GATE_LOG = Path("logs") / "poteto-delegation-gate.jsonl"
STATE_DB = "state.db"

TODO_TOOL = "todo_list"
BRIDGE_TOOL = "tool_call"
DELEGATE_TOOL = "delegate_task"
# Mirrors the gate's ALLOWED_BEFORE_TODO. Kept as a copy so the judge works on logs written by
# any gate version.
PREPARATORY_TOOLS = frozenset({
    "read_file", "skill_view", "skills_list", "skill_manage", "tool_search", "tool_describe",
    TODO_TOOL, DELEGATE_TOOL, "clarify", "memory", "hindsight_recall", "hindsight_reflect",
    "hindsight_retain",
})
# Hermes' background skill and memory review runs a fork under the parent's session id, and the fork's
# messages never reach state.db. The gate logs its calls anyway, so an event for one of these tools
# that no stored call owns is the fork's, not a mismatch. Tools the parent shares with the fork
# (read_file, search_files) are not listed, so a real unpaired event of those still shows.
REVIEW_FORK_TOOLS = frozenset({"skill_view", "skills_list", "skill_manage", "memory"})
# Hermes' file-writing tools. Shell and code tools are judged by their effect on the sandbox.
# `skill_manage` is in PREPARATORY_TOOLS too, and both are intended. The gate lets it through before a
# decision, so G1 does not count it as work, and G5 still counts it as a file write.
FILE_WRITE_TOOLS = frozenset({"write_file", "patch", "skill_manage"})

# What a bad path, a missing state.db or a malformed run.json raise while grading.
GRADE_ERRORS = (OSError, ValueError, KeyError, TypeError, sqlite3.Error)

# Same criterion as the gate's _is_decision: one todo item either names `delegate_task`, or mentions
# delegation (`deleg`) and holds `skip:` with a same-line reason. A `skip:` with no mention of
# delegation is how the playbook marks a skipped step, so it is not a delegation decision.
DECISION_RE = re.compile(r"delegate_task", re.IGNORECASE)
DELEG_RE = re.compile(r"deleg", re.IGNORECASE)
SKIP_REASON_RE = re.compile(r"skip[ \t]*:[ \t]*\S", re.IGNORECASE)
# Same line only: a reason on the next line would be the next todo item's text. `skip delegate_task:`
# is the form agents write; the gate accepts it through `delegate_task`, so the reason is read here.
SKIP_RE = re.compile(r"skip(?:[ \t]+delegate_task)?[ \t]*:[ \t]*([^\n]*)", re.IGNORECASE)


def is_decision(text: str) -> bool:
    if DECISION_RE.search(text):
        return True
    return bool(DELEG_RE.search(text) and SKIP_REASON_RE.search(text))


@dataclass
class Call:
    """One assistant tool call; a `tool_call` bridge is one Call with several inner names."""

    id: str
    tool: str
    names: list[str]
    args: list[dict]
    message: int = 0
    result: str | None = None
    ts: float = 0.0
    refused: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.refused = [False] * len(self.names)

    @property
    def preparatory(self) -> bool:
        return bool(self.names) and all(n in PREPARATORY_TOOLS for n in self.names)

    @property
    def work(self) -> bool:
        return not self.preparatory


@dataclass
class Turn:
    number: int
    start: float
    end: float | None
    calls: list[Call] = field(default_factory=list)


def _json(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _inner_calls(name: str, args: object) -> list[tuple[str, dict]]:
    parsed = _json(args)
    parsed = parsed if isinstance(parsed, dict) else {}
    if name != BRIDGE_TOOL:
        return [(name, parsed)]
    inner = []
    for call in parsed.get("calls") or []:
        if isinstance(call, dict) and isinstance(call.get("name"), str):
            inner_args = _json(call.get("arguments"))
            inner.append((call["name"], inner_args if isinstance(inner_args, dict) else {}))
    return inner


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_turns(db_path: Path, session_id: str) -> list[Turn]:
    """Split the session into turns at each user message and list the tool calls of each."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, role, content, tool_call_id, tool_calls, timestamp FROM messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    results = {row["tool_call_id"]: row["content"] for row in rows if row["role"] == "tool"}
    turns: list[Turn] = []
    for row in rows:
        if row["role"] == "user":
            if turns:
                turns[-1].end = row["timestamp"]
            turns.append(Turn(number=len(turns) + 1, start=row["timestamp"], end=None))
        elif row["role"] == "assistant" and row["tool_calls"] and turns:
            stored = _json(row["tool_calls"])
            for call in stored if isinstance(stored, list) else []:
                function = call.get("function") or {}
                name = function.get("name")
                if not isinstance(name, str):
                    continue
                pairs = _inner_calls(name, function.get("arguments"))
                call_id = str(call.get("id") or call.get("call_id") or "")
                turns[-1].calls.append(Call(
                    id=call_id,
                    tool=name,
                    names=[n for n, _ in pairs],
                    args=[a for _, a in pairs],
                    message=row["id"],
                    result=results.get(call_id),
                    ts=row["timestamp"],
                ))
    return turns


def load_children(db_path: Path, session_id: str, start: float, end: float | None) -> list[dict]:
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT id, started_at, model, estimated_cost_usd FROM sessions "
                "WHERE parent_session_id = ? ORDER BY started_at",
                (session_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT id, started_at, NULL AS model, NULL AS estimated_cost_usd FROM sessions "
                "WHERE parent_session_id = ? ORDER BY started_at",
                (session_id,),
            ).fetchall()
    return [
        dict(row) for row in rows
        if row["started_at"] >= start - 1 and (end is None or row["started_at"] < end)
    ]


def load_gate_events(path: Path, session_id: str, start: float, end: float | None) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("session_id") != session_id:
            continue
        ts = event.get("ts")
        if isinstance(ts, (int, float)) and ts >= start and (end is None or ts < end):
            events.append(event)
    return events


def _todo_texts(call: Call) -> list[str]:
    texts = []
    for name, args in zip(call.names, call.args, strict=True):
        if name != TODO_TOOL:
            continue
        for item in args.get("todos") or []:
            if isinstance(item, dict) and isinstance(item.get("content"), str):
                texts.append(item["content"])
    return texts


def _decision_first(calls: list[Call]) -> tuple[bool, str | None, bool]:
    """(todo with a decision precedes the first work tool, first work tool, any todo written)."""
    first_work = next((c for c in calls if c.work), None)
    if first_work is None:
        before = calls
    else:
        # The gate treats a todo_list anywhere in a bridge as written before its other tools.
        end = calls.index(first_work) + (1 if TODO_TOOL in first_work.names else 0)
        before = calls[:end]
    texts = [t for c in before for t in _todo_texts(c)]
    todo_written = any(TODO_TOOL in c.names for c in before)
    decided = any(is_decision(t) for t in texts)
    first_tool = (
        next((n for n in first_work.names if n not in PREPARATORY_TOOLS), None)
        if first_work else None
    )
    return decided, first_tool, todo_written


def _tool_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("event") in {"block", "allow"} and e.get("tool_name")]


@dataclass
class Pairing:
    """The gate decisions that belong to this turn's calls, and the message each belongs to."""

    mode: str  # "id" or "name"
    events: list[dict]
    owner: list[int]
    unpaired: int


def pair_gate_events(calls: list[Call], events: list[dict]) -> Pairing:
    """Match each tool call to the gate decision logged for it and mark the blocked ones.

    A log whose events carry `tool_call_id` is paired by id, exactly. An older log is paired by
    name and order, which is an approximation.
    """
    tool_events = _tool_events(events)
    if any(isinstance(e.get("tool_call_id"), str) and e["tool_call_id"] for e in tool_events):
        return _pair_by_id(calls, tool_events)
    return _pair_by_name(calls, tool_events)


def _pair_by_id(calls: list[Call], tool_events: list[dict]) -> Pairing:
    """A call with no event never reached the gate; an event no call owns is ignored.

    Hermes hands the hook the id of the outer call, so a `tool_call` bridge keeps its own id
    however many entries it held, and a rejected batch is refused as a whole.
    """
    by_id = {call.id: call for call in calls if call.id}
    kept: list[dict] = []
    owner: list[int] = []
    unpaired = 0
    for event in tool_events:
        call = by_id.pop(event["tool_call_id"], None) if isinstance(
            event.get("tool_call_id"), str) else None
        if call is None:
            unpaired += event.get("tool_name") not in REVIEW_FORK_TOOLS
            continue
        kept.append(event)
        owner.append(call.message)
        call.refused = [event["event"] == "block"] * len(call.names)
    return Pairing("id", kept, owner, unpaired)


def _pair_by_name(calls: list[Call], tool_events: list[dict]) -> Pairing:
    """The gate logs one allow/block per tool call it sees, in call order; calls it never saw
    (gate off, hook failure) have none. Parallel calls of one assistant message may log in any
    order, so a call is matched within the window of its message. An event no call claims is its
    own message.
    """
    owner = [-(i + 1) for i in range(len(tool_events))]
    free = list(range(len(tool_events)))
    messages: dict[int, list[Call]] = {}
    for call in calls:
        messages.setdefault(call.message, []).append(call)
    for message, group in messages.items():
        # Hermes unwraps a `tool_call` bridge before the hook, so the gate logs each inner name.
        units = [(call, i, name) for call in group for i, name in enumerate(call.names)]
        window = free[: len(units)]
        used: list[int] = []
        for call, index, name in units:
            hit = next((i for i in window
                        if i not in used and tool_events[i]["tool_name"] == name), None)
            if hit is None:
                continue
            used.append(hit)
            owner[hit] = message
            call.refused[index] = tool_events[hit]["event"] == "block"
        if used:
            # Whatever precedes a matched event belongs to calls this turn never recorded.
            free = [i for i in free if i > max(used)]
    unpaired = sum(1 for o, e in zip(owner, tool_events, strict=True)
                   if o < 0 and e.get("tool_name") not in REVIEW_FORK_TOOLS)
    return Pairing("name", tool_events, owner, unpaired)


def _recovered(pairing: Pairing) -> bool | None:
    """None without a block; else whether the agent got past the gate without a second block.

    Blocks from one assistant message are one attempt. Only a block in a later message is a
    failed recovery.
    """
    tool_events, owner = pairing.events, pairing.owner
    first = next((i for i, e in enumerate(tool_events) if e["event"] == "block"), None)
    if first is None:
        return None
    for i in range(first + 1, len(tool_events)):
        event = tool_events[i]
        written = event["event"] == "allow" and event.get("reason") == "todo_written_now"
        if owner[i] == owner[first]:
            if written:
                return True
            continue
        # A todolist written without a delegation decision is a step toward the todolist, not a miss.
        if event.get("reason") in {"preparatory_tool", "todo_pending_decision"}:
            continue
        return written
    return False


def _follow_up_kept(events: list[dict]) -> bool:
    kept = {"turn_continued", "turn_armed", "block", "allow"}
    seen = {e.get("event") for e in events}
    return "turn_disarmed" not in seen and bool(seen & kept)


def _rejected(call: Call, index: int) -> bool:
    """Whether the tool result says the inner call at `index` failed."""
    parsed = _json(call.result)
    if not isinstance(parsed, dict):
        return False
    if call.tool != BRIDGE_TOOL:
        return bool(parsed.get("error"))
    entries = parsed.get("results")
    entry = entries[index] if isinstance(entries, list) and index < len(entries) else None
    return isinstance(entry, dict) and bool(entry.get("error"))


def _state(snapshot: object) -> tuple[str, str] | None:
    if not isinstance(snapshot, dict):
        return None
    head, status = snapshot.get("head"), snapshot.get("status")
    return (head, status) if isinstance(head, str) and isinstance(status, str) else None


def _sandbox_change(sandbox: object) -> dict | None:
    """What the run did to the sandbox, or None when no before/after state was recorded."""
    if not isinstance(sandbox, dict):
        return None
    before, after = _state(sandbox.get("before")), _state(sandbox.get("after"))
    if before is None or after is None:
        return None
    return {"head_changed": before[0] != after[0], "status_changed": before[1] != after[1]}


def judge(*, db_path: Path, gate_log: Path, session_id: str, prompt: str, turn: int = 1,
          sandbox: dict | None = None) -> dict:
    turns = load_turns(db_path, session_id)
    current = next((t for t in turns if t.number == turn), None)
    gate_found = gate_log.is_file()
    verdict: dict = {"prompt": prompt, "session_id": session_id, "turn": turn, "pairing": None}
    gate_keys = [f"G{i}" for i in range(1, 7)]
    if current is None:
        verdict.update(dict.fromkeys(gate_keys))
        verdict["evidence"] = {"turn_found": False, "turns_in_session": len(turns)}
        return verdict

    events = (
        load_gate_events(gate_log, session_id, current.start, current.end) if gate_found else []
    )
    pairing = pair_gate_events(current.calls, events)
    children = load_children(db_path, session_id, current.start, current.end)
    executed = [
        (c, i, n, a)
        for c in current.calls
        for i, (n, a) in enumerate(zip(c.names, c.args, strict=True))
        if not c.refused[i]
    ]
    delegates = [(c, i, a) for c, i, n, a in executed if n == DELEGATE_TOOL]
    # A rejected delegation counts only when a child session proves it started anyway.
    delegate_calls = [a for c, i, a in delegates if children or not _rejected(c, i)]
    writes = [n for _, _, n, _ in executed if n in FILE_WRITE_TOOLS]
    change = _sandbox_change(sandbox)
    decided, first_work, todo_written = _decision_first(current.calls)
    armed = any(e.get("event") in {"turn_armed", "turn_continued"} for e in events)
    todo_text = [t for c in current.calls for t in _todo_texts(c)]
    skip_reasons = [
        reason
        for t in todo_text
        if is_decision(t)
        for m in SKIP_RE.finditer(t)
        if (reason := m.group(1).strip())
    ]
    blocks = sum(1 for e in pairing.events if e["event"] == "block")
    block_messages = len({
        o for e, o in zip(pairing.events, pairing.owner, strict=True) if e["event"] == "block"
    })

    flags: dict[str, bool | None] = dict.fromkeys(gate_keys)
    flags["G2"] = _recovered(pairing)
    if prompt in {"p1-bugfix", "p3-oneline"}:
        flags["G1"] = gate_found and armed and decided
    if prompt == "p1-bugfix":
        flags["G3"] = len(delegate_calls) >= 1
    if prompt == "p3-oneline":
        flags["G4"] = not delegate_calls and not children and bool(skip_reasons)
    if prompt == "p2-why" and change is not None:
        flags["G5"] = not (change["head_changed"] or change["status_changed"] or writes)
    if prompt == "p4-followup":
        flags["G6"] = gate_found and _follow_up_kept(events)
    verdict.update(flags, pairing=pairing.mode)
    verdict["counts"] = {
        "blocks": blocks,
        "block_messages": block_messages,
        "delegate_task_calls": len(delegate_calls),
        "delegate_task_rejected": len(delegates) - len(delegate_calls),
        "child_sessions": len(children),
        "write_tool_calls": len(writes),
        "tool_calls": len(executed),
    }
    verdict["evidence"] = {
        "gate_log_found": gate_found,
        "turn_armed": armed,
        "gate_events": [e.get("event") for e in events],
        "unpaired_events": pairing.unpaired,
        "first_work_tool": first_work,
        "todo_written_before_work": todo_written,
        "todo_decision_before_work": decided,
        "skip_reasons": skip_reasons,
        "delegate_roles": [
            str(a.get("pstack_role")) for a in delegate_calls if a.get("pstack_role")
        ],
        "write_tools": writes,
        "sandbox": change,
        "child_models": [c["model"] for c in children],
        "child_cost_usd": round(sum(c["estimated_cost_usd"] or 0 for c in children), 4),
    }
    return verdict


def judge_run(run: dict) -> list[dict]:
    """Grade every turn a `run.json` from run_eval.py recorded."""
    home = Path(run["hermes_home"])
    return [
        judge(
            db_path=home / STATE_DB,
            gate_log=home / GATE_LOG,
            session_id=entry["session_id"],
            prompt=entry["prompt"],
            turn=entry["turn"],
            sandbox=run.get("sandbox_state"),
        )
        for entry in run["turns"]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--run-dir", type=Path, help="directory holding run.json from run_eval.py")
    parser.add_argument("--hermes-home", type=Path, help="profile home (gate log and state.db)")
    parser.add_argument("--session-id")
    parser.add_argument("--prompt", help="p1-bugfix, p2-why, p3-oneline or p4-followup")
    parser.add_argument("--turn", type=int, default=1, help="1-based turn within the session")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.run_dir:
            record = json.loads((args.run_dir / "run.json").read_text(encoding="utf-8"))
            verdicts = judge_run(record)
        elif args.hermes_home and args.session_id and args.prompt:
            verdicts = [judge(
                db_path=args.hermes_home / STATE_DB,
                gate_log=args.hermes_home / GATE_LOG,
                session_id=args.session_id,
                prompt=args.prompt,
                turn=args.turn,
            )]
        else:
            parser.error("give --run-dir, or --hermes-home with --session-id and --prompt")
    except GRADE_ERRORS as exc:
        print(f"judge: cannot grade: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for verdict in verdicts:
        print(json.dumps(verdict, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
