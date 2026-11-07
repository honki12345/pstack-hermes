"""poteto-mode 턴에서 위임 결정이 든 todolist를 쓰기 전까지 작업 도구 호출을 막는다.

위임을 강제하지 않는다. 막는 것은 "결정 없이 작업부터 시작하는 것"뿐이고, todolist에
`skip: <사유>`를 적는 것도 유효한 결정이다. 결정 항목이 없는 todolist는 작업을 풀지 못한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_NAME = "poteto-mode"
# agent/skill_commands.py 의 _SKILL_INVOCATION_PREFIX 와 바이트 단위로 같아야 한다.
_INVOCATION_PREFIX = "[IMPORTANT: The user has invoked the "
_SKILL_NAME_RE = re.compile(re.escape(_INVOCATION_PREFIX) + r'"([^"]*)"')

TODO_TOOL = "todo_list"
BRIDGE_TOOL = "tool_call"
DELEGATE_TOOL = "delegate_task"
ALLOWED_BEFORE_TODO = frozenset({
    "read_file", "skill_view", "skills_list", "tool_search", "tool_describe", TODO_TOOL,
    DELEGATE_TOOL, "clarify", "memory", "hindsight_recall", "hindsight_reflect", "hindsight_retain",
})

# 결정 항목의 형식. 판정기(tools/eval/judge.py)의 DECISION_RE 와 같되, `skip:` 뒤에 같은 줄 사유가
# 있어야 한다. 사유 없는 `skip:` 은 결정을 적은 것이 아니라 형식만 맞춘 것이다.
_DECISION_RE = re.compile(r"delegate_task|skip[ \t]*:[ \t]*\S", re.IGNORECASE)

# 결정 없는 todolist 뒤에 작업 호출이 이만큼 막히면 다음 호출부터 푼다. 에이전트가 끝내 결정 항목을
# 쓰지 못하는 턴(todo_list 가 실패하거나 항목 형식을 못 맞추는 경우)에서 관문이 세션을 마비시키지
# 않게 하는 안전장치다. 첫 차단(work_before_todo)과 합쳐 턴당 차단은 3회를 넘지 않는다.
# todolist 를 아예 쓰지 않은 차단(work_before_todo)에는 세지 않는다. 그때는 todolist 하나로 풀리므로
# 교착이 아니다.
MAX_DECISION_BLOCKS = 2

BLOCK_MESSAGE = (
    "poteto-mode gate: write the todolist before working. Copy the matched playbook's numbered "
    "steps verbatim as items. Add the playbook's opening sentence about delegation as an item "
    "too, then add one item that records the delegation decision: either `delegate_task` for the "
    "named steps (with pstack_role) or `skip: <reason>`. Then retry this call."
)

DECISION_MESSAGE = (
    "poteto-mode gate: your todolist has no delegation decision, so working tools stay blocked. "
    "Add an item that names `delegate_task` (which steps go to a child, with pstack_role) or reads "
    "`skip: <reason>` with the reason on the same line, and add the playbook's opening sentence "
    "about delegation as an item. todo_list with merge: true adds the item without rewriting the "
    "list. Then retry this call."
)


@dataclass
class _TurnState:
    active: bool = False
    # 이 턴에서 위임 결정 항목이 든 todolist 쓰기를 본 적이 있다. 한 번 서면 턴 끝까지 유지해서,
    # `merge: true` 부분 갱신이나 목록 교체가 결정 항목 없이 와도 다시 막지 않는다.
    todo_written: bool = False
    # 결정 항목 없는 todolist 쓰기를 본 적이 있다. 작업 차단 사유를 가르고 교착 해제를 센다.
    todo_pending: bool = False
    decision_blocks: int = 0
    delegated: int = 0
    turn_id: str = ""


_STATES: dict[str, _TurnState] = {}
_LOCK = threading.Lock()


def _log_path() -> Path:
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home / "logs" / "poteto-delegation-gate.jsonl"


def _audit(event: str, **fields) -> None:
    """결정만 기록한다. 사용자 메시지 원문과 도구 인자는 남기지 않는다."""
    try:
        record = {"ts": round(time.time(), 3), "event": event, **fields}
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("poteto-delegation-gate: audit write failed", exc_info=True)


def _invoked_skill_tokens(user_message: object) -> list[str]:
    if not isinstance(user_message, str) or not user_message.startswith(_INVOCATION_PREFIX):
        return []
    match = _SKILL_NAME_RE.match(user_message)
    # 번들 헤더는 "/a /b" 처럼 공백으로 이어진 이름을 담는다.
    return match.group(1).split() if match else []


def _is_poteto_invocation(user_message: object) -> bool:
    return any(t.lstrip("/").rsplit(":", 1)[-1] == SKILL_NAME for t in _invoked_skill_tokens(user_message))


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _entries(tool_name: str, args: object) -> list[tuple[str, dict]]:
    """(이름, 인자) 목록. Hermes 는 로컬 `tool_call` 한 건을 훅 전에 풀어 `tool_name="todo_list"` 와
    풀린 인자로 넘기지만, 풀지 못한 경우(커넥터 묶음, 범위·스키마 검사 실패)에는 bridge 인자
    (`calls`, 옛 단일 형태 `name`/`arguments`, JSON 문자열)를 그대로 넘긴다. 두 형태를 모두 읽는다.
    """
    if tool_name != BRIDGE_TOOL:
        return [(tool_name, args if isinstance(args, dict) else {})]
    if not isinstance(args, dict):
        return []
    calls = _json_value(args["calls"]) if "calls" in args else [
        {"name": args.get("name"), "arguments": args.get("arguments")}
    ]
    if isinstance(calls, dict):
        calls = [calls]
    if not isinstance(calls, list):
        return []
    entries = []
    for call in calls:
        if isinstance(call, dict) and isinstance(call.get("name"), str):
            inner = _json_value(call.get("arguments"))
            entries.append((call["name"], inner if isinstance(inner, dict) else {}))
    return entries


def _bridged_names(args: object) -> list[str]:
    return [name for name, _ in _entries(BRIDGE_TOOL, args)]


def _item_texts(args: dict) -> list[str]:
    todos = _todos(args)
    if not isinstance(todos, list):
        return []
    return [i["content"] for i in todos if isinstance(i, dict) and isinstance(i.get("content"), str)]


def _todos(args: dict) -> object:
    # 모델이 배열을 JSON 문자열로 넘기는 경우가 있고 Hermes 는 이를 받아 준다.
    return _json_value(args.get("todos"))


def _writes_todos(args: dict) -> bool:
    """인자 없는 todo_list 는 목록을 읽을 뿐이다."""
    return isinstance(_todos(args), list)


def _has_decision(args: dict) -> bool:
    return any(_DECISION_RE.search(text) for text in _item_texts(args))


def _on_pre_llm_call(**kwargs):
    try:
        if kwargs.get("parent_session_id"):
            return None
        session_id = kwargs.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        turn_id = str(kwargs.get("turn_id") or "")
        marked = _is_poteto_invocation(kwargs.get("user_message"))
        with _LOCK:
            state = _STATES.get(session_id)
            if marked:
                # 같은 턴의 재호출이 todolist 기록을 지우면 안 된다.
                if not (state and state.active and turn_id and state.turn_id == turn_id):
                    _STATES[session_id] = _TurnState(active=True, turn_id=turn_id)
                    _audit("turn_armed", session_id=session_id, turn_id=turn_id)
            elif state and state.active:
                state.active = False
                _audit("turn_disarmed", session_id=session_id, turn_id=turn_id)
    except Exception:
        logger.debug("poteto-delegation-gate: pre_llm_call failed", exc_info=True)
    return None


def _decide(state: _TurnState, tool_name: str, args: object) -> tuple[str, str]:
    """(decision, reason). decision 은 allow | block. state 를 변경하므로 락 안에서 호출한다."""
    entries = _entries(tool_name, args)
    names = [name for name, _ in entries]
    state.delegated += names.count(DELEGATE_TOOL)
    todo_args = [a for name, a in entries if name == TODO_TOOL]
    if state.todo_written:
        return "allow", "todo_written"
    if any(_has_decision(a) for a in todo_args):
        state.todo_written = True
        return "allow", "todo_written_now"
    undecided_write = any(_writes_todos(a) for a in todo_args)
    if names and all(n in ALLOWED_BEFORE_TODO for n in names):
        # 결정 없는 todolist 쓰기는 통과시킨다. 에이전트가 `merge: true` 로 결정 항목만 더할 수 있어야
        # 한다. 풀어 주지는 않는다.
        if undecided_write:
            state.todo_pending = True
            return "allow", "todo_pending_decision"
        return "allow", "preparatory_tool"
    if state.todo_pending or undecided_write:
        if state.decision_blocks >= MAX_DECISION_BLOCKS:
            state.todo_written = True
            return "allow", "decision_missing_released"
        state.decision_blocks += 1
        return "block", "todo_without_decision"
    return "block", "work_before_todo"


def _on_pre_tool_call(**kwargs):
    try:
        session_id = kwargs.get("session_id")
        tool_name = kwargs.get("tool_name")
        if not isinstance(session_id, str) or not isinstance(tool_name, str):
            return None
        with _LOCK:
            state = _STATES.get(session_id)
            if state is None or not state.active:
                return None
            decision, reason = _decide(state, tool_name, kwargs.get("args"))
            delegated = state.delegated
        names = _bridged_names(kwargs.get("args")) if tool_name == BRIDGE_TOOL else []
        # 풀린 tool_call bridge는 로그와 세션 DB의 도구 이름이 달라서, 읽는 쪽이 이름 대신 id로 짝짓게 한다.
        _audit(decision, session_id=session_id, tool_name=tool_name, bridged=names, reason=reason,
               delegated=delegated, tool_call_id=kwargs.get("tool_call_id") or None)
        if decision == "block":
            message = DECISION_MESSAGE if reason == "todo_without_decision" else BLOCK_MESSAGE
            return {"action": "block", "message": message}
    except Exception:
        logger.debug("poteto-delegation-gate: pre_tool_call failed", exc_info=True)
    return None


def register(ctx):
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
