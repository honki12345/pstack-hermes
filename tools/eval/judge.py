#!/usr/bin/env python3
"""Grade a poteto-mode run from the delegation gate log and the profile's state.db.

Deterministic on purpose: a model grader would add cost and bias for a question the tool-call
order already answers. Reads only the gate log (`logs/poteto-delegation-gate.jsonl`) and, from
`state.db`, `messages` and `sessions`. Nothing is written.

Metrics (null when the prompt does not exercise them):
  G1 decision first      a todo_list holding a delegation decision precedes the first work tool
  G2 recovery            after a gate block, the next non-preparatory step writes the todolist
  G3 delegation happens  p1: at least one delegate_task executed
  G4 no over-delegation  p3: no delegate_task, no child session, and a `skip:` reason is written
  G5 reroute             p2: zero mutating tool calls
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
    "read_file", "skill_view", "skills_list", "tool_search", "tool_describe", TODO_TOOL,
    DELEGATE_TOOL, "clarify", "memory", "hindsight_recall", "hindsight_reflect", "hindsight_retain",
})
FILE_WRITE_TOOLS = frozenset({"write_file", "patch", "skill_manage"})
SHELL_TOOLS = frozenset({"terminal", "execute_code"})

BLOCK_MARK = "poteto-mode gate:"
DECISION_RE = re.compile(r"delegate_task|skip\s*:", re.IGNORECASE)
SKIP_RE = re.compile(r"skip\s*:\s*([^\n]*)", re.IGNORECASE)

# Output redirections to /dev/null and fd duplication write nothing a repository would notice.
_HARMLESS_REDIRECT_RE = re.compile(r"\d*>>?\s*/dev/null|&>\s*/dev/null|\d*>&\d+")
_MUTATING_SHELL_RE = re.compile(
    r"(?<![\w-])(?:rm|mv|cp|touch|mkdir|rmdir|chmod|chown|ln|tee|truncate|dd|patch)\s"
    r"|\bsed\s+(?:-[a-zA-Z]*i|--in-place)"
    r"|\bperl\s+-[a-zA-Z]*i"
    r"|\bgit\s+(?:add|commit|checkout|restore|reset|apply|rebase|merge|stash|clean|rm|mv"
    r"|push|cherry-pick|switch)\b"
    r"|\b(?:pip3?|npm|yarn|bun|uv|brew)\s+(?:install|add|remove|uninstall)\b"
    r"|\bwrite_text\(|\bwrite_bytes\(|\.write\(|open\([^)]*[\"'][wax+]"
    r"|\bos\.(?:remove|rename|replace|unlink|makedirs)\b|\bshutil\.|write_file\(|\bpatch\("
)
_REDIRECT_RE = re.compile(r"(?<![<>&\d=-])>>?(?![&=])")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")


@dataclass
class Call:
    """One assistant tool call; a `tool_call` bridge is one Call with several inner names."""

    id: str
    names: list[str]
    args: list[dict]
    blocked: bool = False
    ts: float = 0.0

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


def is_mutating(name: str, args: dict) -> bool:
    if name in FILE_WRITE_TOOLS:
        return True
    if name not in SHELL_TOOLS:
        return False
    text = str(args.get("command") or args.get("code") or "")
    unquoted = _QUOTED_RE.sub(" ", _HARMLESS_REDIRECT_RE.sub(" ", text))
    return bool(_MUTATING_SHELL_RE.search(text) or _REDIRECT_RE.search(unquoted))


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
    blocked_ids = {
        row["tool_call_id"]
        for row in rows
        if row["role"] == "tool" and BLOCK_MARK in (row["content"] or "")
    }
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
                    names=[n for n, _ in pairs],
                    args=[a for _, a in pairs],
                    blocked=call_id in blocked_ids,
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
    before = calls[: calls.index(first_work)] if first_work else calls
    texts = [t for c in before for t in _todo_texts(c)]
    todo_written = any(TODO_TOOL in c.names for c in before)
    decided = any(DECISION_RE.search(t) for t in texts)
    return decided, (first_work.names[0] if first_work else None), todo_written


def _recovered(events: list[dict]) -> bool | None:
    blocks = [i for i, e in enumerate(events) if e.get("event") == "block"]
    if not blocks:
        return None
    for event in events[blocks[0] + 1:]:
        if event.get("reason") == "preparatory_tool":
            continue
        return event.get("event") == "allow" and event.get("reason") == "todo_written_now"
    return False


def _follow_up_kept(events: list[dict]) -> bool:
    kept = {"turn_continued", "turn_armed", "block", "allow"}
    seen = {e.get("event") for e in events}
    return "turn_disarmed" not in seen and bool(seen & kept)


def judge(*, db_path: Path, gate_log: Path, session_id: str, prompt: str, turn: int = 1) -> dict:
    turns = load_turns(db_path, session_id)
    current = next((t for t in turns if t.number == turn), None)
    gate_found = gate_log.is_file()
    verdict: dict = {"prompt": prompt, "session_id": session_id, "turn": turn}
    gate_keys = [f"G{i}" for i in range(1, 7)]
    if current is None:
        verdict.update(dict.fromkeys(gate_keys))
        verdict["evidence"] = {"turn_found": False, "turns_in_session": len(turns)}
        return verdict

    events = (
        load_gate_events(gate_log, session_id, current.start, current.end) if gate_found else []
    )
    children = load_children(db_path, session_id, current.start, current.end)
    executed = [(n, a) for c in current.calls if not c.blocked for n, a in zip(c.names, c.args,
                                                                               strict=True)]
    delegate_calls = [a for n, a in executed if n == DELEGATE_TOOL]
    mutating = [n for n, a in executed if is_mutating(n, a)]
    decided, first_work, todo_written = _decision_first(current.calls)
    armed = any(e.get("event") in {"turn_armed", "turn_continued"} for e in events)
    todo_text = [t for c in current.calls for t in _todo_texts(c)]
    skip_reasons = [m.group(1).strip() for t in todo_text for m in SKIP_RE.finditer(t)]
    blocks = sum(1 for e in events if e.get("event") == "block")

    flags: dict[str, bool | None] = dict.fromkeys(gate_keys)
    flags["G2"] = _recovered(events)
    if prompt in {"p1-bugfix", "p3-oneline"}:
        flags["G1"] = gate_found and armed and decided
    if prompt == "p1-bugfix":
        flags["G3"] = len(delegate_calls) >= 1
    if prompt == "p3-oneline":
        flags["G4"] = not delegate_calls and not children and bool(skip_reasons)
    if prompt == "p2-why":
        flags["G5"] = not mutating
    if prompt == "p4-followup":
        flags["G6"] = gate_found and _follow_up_kept(events)
    verdict.update(flags)
    verdict["counts"] = {
        "blocks": blocks,
        "delegate_task_calls": len(delegate_calls),
        "child_sessions": len(children),
        "mutating_calls": len(mutating),
        "tool_calls": len(executed),
    }
    verdict["evidence"] = {
        "gate_log_found": gate_found,
        "turn_armed": armed,
        "gate_events": [e.get("event") for e in events],
        "first_work_tool": first_work,
        "todo_written_before_work": todo_written,
        "todo_decision_before_work": decided,
        "skip_reasons": skip_reasons,
        "delegate_roles": [
            str(a.get("pstack_role")) for a in delegate_calls if a.get("pstack_role")
        ],
        "mutating_tools": mutating,
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

    if args.run_dir:
        verdicts = judge_run(json.loads((args.run_dir / "run.json").read_text(encoding="utf-8")))
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
    for verdict in verdicts:
        print(json.dumps(verdict, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
