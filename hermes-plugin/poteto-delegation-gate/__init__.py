"""poteto-mode 턴에서 위임 결정이 든 todolist를 쓰기 전까지 작업 도구 호출을 막는다.

위임을 강제하지 않는다. 막는 것은 "결정 없이 작업부터 시작하는 것"뿐이고, 위임을 가리키는 항목에
`skip: <사유>`를 적는 것도 유효한 결정이다. 결정 항목이 없는 todolist는 작업을 풀지 못한다.

poteto 턴이 있었던 세션에서는 마커 없는 후속 턴도 새 턴으로 보고 같은 관문을 건다. 짧은 읽기 조회
몇 번까지는 결정 없이 통과시키고, 그 너머의 조사나 쓰기부터 결정을 요구한다. 게이트웨이가 넣는
비동기 완료 알림 턴은 새 작업이 아니므로 앞선 턴의 상태를 이어 간다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
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

# 결정 항목의 형식. 한 항목 안에서 다음 둘 중 하나여야 한다.
#   1. `delegate_task` 를 쓰겠다는 내용
#   2. 위임을 가리키는 말(`deleg`, 예: delegation, delegate)과 같은 줄 사유가 있는 `skip:`
# 위임을 가리키는 말 없는 `skip: <사유>` 는 playbook 단계를 건너뛴다는 표시(SKILL.md 의 `skip:`)일
# 수 있어 결정으로 읽지 않는다. tools/eval/judge.py 의 is_decision 과 같은 기준이어야 한다.
_DELEGATE_TASK_RE = re.compile(r"delegate_task", re.IGNORECASE)
_DELEG_RE = re.compile(r"deleg", re.IGNORECASE)
_SKIP_REASON_RE = re.compile(r"skip[ \t]*:[ \t]*\S", re.IGNORECASE)


def _is_decision(text: str) -> bool:
    if _DELEGATE_TASK_RE.search(text):
        return True
    return bool(_DELEG_RE.search(text) and _SKIP_REASON_RE.search(text))


# 결정 없는 todolist 뒤에 작업 호출이 이만큼 막히면 다음 호출부터 푼다. 에이전트가 끝내 결정 항목을
# 쓰지 못하는 턴(todo_list 가 실패하거나 항목 형식을 못 맞추는 경우)에서 관문이 세션을 마비시키지
# 않게 하는 안전장치다. todolist 를 쓰기 전의 차단(work_before_todo)은 횟수 제한이 없고 여기에
# 세지 않는다. 그때는 todolist 하나로 풀리므로 교착이 아니다. 결정 없는 todolist 뒤에는 최대 이
# 횟수만큼만 막는다.
MAX_DECISION_BLOCKS = 2

# 마커 없는 후속 턴에서 결정 없이 통과시키는 읽기 조회의 상한. "방금 뭐 바꿨어?" 같은 질문은 보통
# `git status`와 `git diff` 정도로 끝난다. 이를 넘는 조사는 넓은 조사의 시작으로 보고 막는다.
# 첫 차단이나 결정 없는 todolist 쓰기가 있으면 이 허용은 닫혀서, 이후 흐름은 마커 턴과 같다.
FOLLOWUP_FREE_READS = 3

# 세션별 상태는 프로세스 메모리에만 있다. 오래 쉰 세션은 만료시키고 개수에도 상한을 둬서 무한히
# 쌓이지 않게 한다. 밀려난 세션은 poteto 이력이 없는 세션처럼 다룬다.
SESSION_TTL_SECONDS = 24 * 60 * 60
MAX_SESSIONS = 256

# 이름만으로 읽기 전용인 도구. 쉘 명령은 아래 규칙으로 따로 가린다.
READ_ONLY_TOOLS = frozenset({"search_files", "session_search"})
_READ_ONLY_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "wc", "pwd", "grep", "rg", "stat", "file", "which", "du", "find",
    "echo",
})
_READ_ONLY_GIT = frozenset({
    "diff", "log", "status", "show", "blame", "shortlog", "rev-parse", "ls-files", "describe",
})
# 이 인자가 하나라도 있으면 읽기 전용으로 보지 않는다. 파일을 바꾸거나 다른 명령을 실행한다.
# 각 명령의 man 페이지에서 쓰기와 실행을 하는 플래그를 골랐다. find 의 -exec 계열과 파일 출력 동작.
_UNSAFE_ARGS = frozenset({
    "-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls",
})
# 앞부분이 같으면 막는 플래그. 명령마다 따로 둔다. 공통으로 두면 `git log --pretty` 가 rg 의 `--pre`
# 에 걸린다. git 은 옵션 줄임 표기를 받으므로(`--ext-di` 는 `--ext-diff`) 줄임까지 막으려고 앞부분만 쓴다.
#   rg  --pre, --pre-glob  전처리 프로그램 실행. --hostname-bin  프로그램 실행.
#   git --output  파일에 쓴다. --ext-diff, --textconv  설정된 외부 프로그램 실행. -O  순서 파일.
_UNSAFE_PREFIXES = {
    "rg": ("--pre", "--hostname-bin"),
    "git": ("--out", "--ext", "--text", "-O"),
}
# `file` 은 `-C`(--compile)가 파일을 쓴다. GNU getopt 의 줄임 표기도 받아서 막는 쪽 목록으로는 새는
# 구멍이 생긴다. 그래서 허용 플래그만 통과시킨다.
_FILE_SHORT_FLAGS = frozenset("biIkLhnNsp")
_FILE_LONG_FLAGS = frozenset({
    "--brief", "--mime", "--mime-type", "--mime-encoding", "--extension", "--keep-going",
    "--dereference", "--no-dereference", "--no-buffer", "--no-pad", "--special-files",
    "--preserve-date",
})


def _file_args_are_read_only(args: list[str]) -> bool:
    for arg in args:
        if arg == "--":
            continue
        if arg.startswith("--"):
            if arg not in _FILE_LONG_FLAGS:
                return False
        elif arg.startswith("-") and len(arg) > 1 and not set(arg[1:]) <= _FILE_SHORT_FLAGS:
            return False
    return True


# 리다이렉션, 명령 치환, `;`, 줄바꿈, 백슬래시, 괄호, 중괄호, 단독 `&`(백그라운드 실행).
_SHELL_META_RE = re.compile(r"[;<>`$\n\r\\(){}]|(?<!&)&(?!&)")

# Hermes 가 에이전트 턴으로 끼워 넣는 알림. 사용자가 새로 시킨 작업이 아니다. 신호는 둘이고 하나만
# 맞아도 알림이다.
#   1. 머리글. tools/process_registry_notifications.py(프로세스 완료, 감시 일치, 감시 해제와 넘침,
#      heartbeat, 비동기 위임), gateway/run_notifications.py(묶음 완료 두 종류),
#      gateway/session_automation.py(바쁜 동안 모인 결과)가 만든다. 앞 공백은 허용한다.
#   2. 끝의 게이트웨이 푸터. gateway/run_notifications.py 의 INTERNAL_NOTIFICATION_FOOTER 를
#      게이트웨이 경로의 모든 알림 끝에 붙인다. 머리글 목록에 없는 새 알림도 잡지만 CLI 경로의
#      알림에는 붙지 않는다. 그래서 머리글도 함께 본다.
_NOTICE_HEAD_RE = re.compile(
    r"\s*\[(?:"
    r"ASYNC (?:DELEGATION |(?:BATCH )?COMPLETE\b)"
    r"|Background process \S+ heartbeat #"
    r"|IMPORTANT: (?:"
    r"Background process "
    r"|\d+ background (?:processes |process results |subagent delegations )"
    r"|Watch patterns disabled for process "
    r"|Watch-pattern (?:overflow|notifications resumed)"
    r"))"
)
_NOTICE_FOOTER_RE = re.compile(
    r"\n\n\[INTERNAL NOTIFICATION\b[^\]]*generated by the Hermes gateway[^\]]*\]\s*\Z"
)

_FORMAT_HINT = (
    "Recommended item: `Delegation decision: delegate_task <the steps that go to a child>` (with "
    "pstack_role) or `Delegation decision: skip: <reason>` with the reason on the same line."
)

BLOCK_MESSAGE = (
    "poteto-mode gate: write the todolist before working. Copy the matched playbook's numbered "
    "steps verbatim as items. Add the playbook's opening sentence about delegation as an item "
    "too, then add one item that records the delegation decision. " + _FORMAT_HINT + " A `skip:` "
    "on a playbook step does not count. Then retry this call."
)

DECISION_MESSAGE = (
    "poteto-mode gate: your todolist has no delegation decision, so working tools stay blocked. "
    "Add one item that names `delegate_task`, or that mentions delegation and reads `skip: <reason>` "
    "with the reason on the same line. " + _FORMAT_HINT + " A `skip:` on a playbook step does not "
    "count. Add the playbook's opening sentence about delegation as an item too. todo_list with "
    "merge: true adds the item without rewriting the list. Then retry this call."
)


@dataclass
class _TurnState:
    active: bool = False
    # 이 턴에서 위임 결정 항목이 든 todolist 쓰기를 본 적이 있다. 한 번 서면 턴 끝까지 유지해서,
    # `merge: true` 부분 갱신이나 목록 교체가 결정 항목 없이 와도 다시 막지 않는다.
    todo_written: bool = False
    # 결정 없이 교착 해제로 풀린 턴. 결정이 쓰인 턴(todo_written)과 로그에서 구분한다.
    released: bool = False
    # 결정 항목 없는 todolist 쓰기를 본 적이 있다. 작업 차단 사유를 가르고 교착 해제를 센다.
    todo_pending: bool = False
    decision_blocks: int = 0
    delegated: int = 0
    turn_id: str = ""
    # 마커 없는 후속 턴이다. 읽기 조회 허용(free_reads)은 이 턴에서만 쓴다.
    followup: bool = False
    free_reads: int = 0
    # 이 턴에서 한 번이라도 막았다. 막힌 뒤에는 읽기 조회 허용을 닫는다.
    blocked: bool = False
    # 마지막으로 이 세션을 본 시각. 만료와 개수 상한에 쓴다.
    seen: float = 0.0


_STATES: dict[str, _TurnState] = {}
_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


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


def _is_notice(user_message: object) -> bool:
    return isinstance(user_message, str) and bool(
        _NOTICE_HEAD_RE.match(user_message) or _NOTICE_FOOTER_RE.search(user_message)
    )


def _shell_segment_is_read_only(segment: str) -> bool:
    try:
        words = shlex.split(segment)
    except ValueError:
        return False
    if not words:
        return False
    head, rest = words[0], words[1:]
    if any(w in _UNSAFE_ARGS or w.startswith(_UNSAFE_PREFIXES.get(head, ())) for w in words):
        return False
    if head == "file":
        return _file_args_are_read_only(rest)
    if head == "cd":
        # 에이전트는 `cd <저장소> && git diff` 형태로 조회한다. 경로 하나만 읽기 전용으로 본다.
        return len(rest) == 1
    if head == "git":
        if rest[:1] == ["-C"] and len(rest) >= 3:
            rest = rest[2:]
        return bool(rest) and rest[0] in _READ_ONLY_GIT
    return head in _READ_ONLY_COMMANDS


def _is_read_only_shell(command: object) -> bool:
    """읽기만 하는 쉘 명령이면 참. 리다이렉션, 치환, `;`, 백그라운드 실행이 있으면 거짓이다.
    `|` 와 `&&` 로 이은 명령은 조각마다 읽기 전용일 때만 참이다."""
    if not isinstance(command, str) or not command.strip() or _SHELL_META_RE.search(command):
        return False
    segments = [part for chunk in command.split("&&") for part in chunk.split("|")]
    return all(_shell_segment_is_read_only(seg) for seg in segments)


def _read_cost(entries: list[tuple[str, dict]]) -> int | None:
    """준비 도구를 뺀 나머지가 모두 읽기 전용이면 그 호출 수, 하나라도 아니면 None."""
    cost = 0
    for name, args in entries:
        if name in ALLOWED_BEFORE_TODO:
            continue
        if name in READ_ONLY_TOOLS or (name == "terminal" and _is_read_only_shell(args.get("command"))):
            cost += 1
        else:
            return None
    return cost


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
    return any(_is_decision(text) for text in _item_texts(args))


def _expire(now: float, keep: str) -> None:
    """락 안에서 호출한다. 오래 쉰 세션을 지우고, 상한을 넘으면 가장 오래된 것부터 지운다."""
    for sid in [k for k, v in _STATES.items() if k != keep and now - v.seen > SESSION_TTL_SECONDS]:
        del _STATES[sid]
    while len(_STATES) > MAX_SESSIONS:
        oldest = min((k for k in _STATES if k != keep), key=lambda k: _STATES[k].seen, default=None)
        if oldest is None:
            break
        del _STATES[oldest]


def _on_pre_llm_call(**kwargs):
    try:
        if kwargs.get("parent_session_id"):
            return None
        session_id = kwargs.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        turn_id = str(kwargs.get("turn_id") or "")
        message = kwargs.get("user_message")
        marked = _is_poteto_invocation(message)
        now = _now()
        with _LOCK:
            state = _STATES.get(session_id)
            # 같은 턴의 재호출이 todolist 기록을 지우면 안 된다.
            same_turn = bool(state and turn_id and state.turn_id == turn_id)
            if marked:
                if not (state and state.active and same_turn):
                    _STATES[session_id] = _TurnState(active=True, turn_id=turn_id, seen=now)
                    _audit("turn_armed", session_id=session_id, turn_id=turn_id)
            elif state and state.active and not same_turn:
                if _invoked_skill_tokens(message):
                    # 사용자가 다른 스킬을 직접 불렀다. poteto 를 이어 가지 않는다.
                    state.active = False
                    _audit("turn_disarmed", session_id=session_id, turn_id=turn_id)
                elif _is_notice(message):
                    # 알림은 새 작업이 아니다. 앞선 턴의 todolist, 결정, 차단 횟수를 그대로 둔다.
                    state.turn_id = turn_id
                    _audit("turn_continued", session_id=session_id, turn_id=turn_id, kind="notice")
                else:
                    _STATES[session_id] = _TurnState(active=True, followup=True, turn_id=turn_id, seen=now)
                    _audit("turn_continued", session_id=session_id, turn_id=turn_id, kind="followup")
            current = _STATES.get(session_id)
            if current:
                current.seen = now
            _expire(now, session_id)
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
    if state.released:
        return "allow", "released"
    undecided_write = any(_writes_todos(a) for a in todo_args)
    if names and all(n in ALLOWED_BEFORE_TODO for n in names):
        # 결정 없는 todolist 쓰기는 통과시킨다. 에이전트가 `merge: true` 로 결정 항목만 더할 수 있어야
        # 한다. 풀어 주지는 않는다.
        if undecided_write:
            state.todo_pending = True
            return "allow", "todo_pending_decision"
        return "allow", "preparatory_tool"
    reads = _read_cost(entries)
    if (state.followup and reads is not None and not state.blocked and not state.todo_pending
            and not undecided_write and state.free_reads + reads <= FOLLOWUP_FREE_READS):
        state.free_reads += reads
        return "allow", "followup_read"
    state.blocked = True
    if state.todo_pending or undecided_write:
        if state.decision_blocks >= MAX_DECISION_BLOCKS:
            state.released = True
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
            state.seen = _now()
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
