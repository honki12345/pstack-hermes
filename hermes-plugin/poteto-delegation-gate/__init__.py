"""poteto-mode 턴에서 todolist(위임 결정 포함)를 쓰기 전까지 작업 도구 호출을 막는다.

위임을 강제하지 않는다. 막는 것은 "결정 없이 작업부터 시작하는 것"뿐이고, todolist에
`skip: <사유>`를 적는 것도 유효한 결정이다.
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

BLOCK_MESSAGE = (
    "poteto-mode gate: write the todolist before working. Copy the matched playbook's numbered "
    "steps verbatim, and add one item that records the delegation decision: which steps go to "
    "delegate_task (with pstack_role) or `skip: <reason>`. Then retry this call."
)


@dataclass
class _TurnState:
    active: bool = False
    todo_written: bool = False
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


def _bridged_names(args: object) -> list[str]:
    calls = args.get("calls") if isinstance(args, dict) else None
    if not isinstance(calls, list):
        return []
    return [c["name"] for c in calls if isinstance(c, dict) and isinstance(c.get("name"), str)]


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
    names = _bridged_names(args) if tool_name == BRIDGE_TOOL else [tool_name]
    state.delegated += names.count(DELEGATE_TOOL)
    if state.todo_written:
        return "allow", "todo_written"
    if TODO_TOOL in names:
        state.todo_written = True
        return "allow", "todo_written_now"
    if names and all(n in ALLOWED_BEFORE_TODO for n in names):
        return "allow", "preparatory_tool"
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
            return {"action": "block", "message": BLOCK_MESSAGE}
    except Exception:
        logger.debug("poteto-delegation-gate: pre_tool_call failed", exc_info=True)
    return None


def register(ctx):
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
