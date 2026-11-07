#!/usr/bin/env python3
"""Run one fixed poteto-mode prompt against a Hermes profile, then grade it with judge.py.

Everything the agent sees is an ordinary user request in a throwaway shop-api checkout. The run
goes through `hermes -z` first and confirms the gate saw it (`turn_armed` in the profile's gate
log). If the gate never arms, the one-shot is repeated in-process through `hermes_side.py`,
which skips the resident gateway that can hide freshly installed plugins.

  env -i HOME=$HOME PATH=$PATH HERMES_HOME=<profile> \\
      uv run --frozen tools/eval/run_eval.py --prompt p1-bugfix --out <dir>

Costs model calls. Never wired into CI.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import hermes_side
import judge

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"
FIXTURE = HERE / "fixtures" / "shop-api"
PROMPT_IDS = ("p1-bugfix", "p2-why", "p3-oneline", "p4-followup")
# The follow-up only means something after the first turn, in the same process and session.
FOLLOW_UP = {"p4-followup": "p1-bugfix"}
ARM_EVENTS = (b'"turn_armed"', b'"turn_continued"')
CHILD_ENV_KEYS = ("HOME", "PATH", "LANG", "TMPDIR")


class RunError(Exception):
    pass


def resolve_home(arg: str | None, allow_default: bool = False) -> Path:
    raw = arg or os.environ.get("HERMES_HOME")
    if not raw:
        raise RunError("pass --hermes-home or set HERMES_HOME to a throwaway profile")
    home = Path(raw).expanduser().resolve()
    if not (home / "config.yaml").is_file():
        raise RunError(f"{home} is not a Hermes profile (no config.yaml)")
    if home == (Path.home() / ".hermes").resolve() and not allow_default:
        raise RunError(
            "refusing the default profile: a run writes sessions into it. "
            "Create one with `hermes profile create <name> --clone-from <profile>`."
        )
    return home


def child_env(home: Path) -> dict[str, str]:
    env = {key: os.environ[key] for key in CHILD_ENV_KEYS if key in os.environ}
    env["HERMES_HOME"] = str(home)
    return env


def make_sandbox() -> Path:
    """A project-shaped checkout: a path and a repo that read like real work."""
    root = Path(tempfile.mkdtemp(prefix="shop-"))
    repo = root / "shop-api"
    shutil.copytree(FIXTURE, repo)
    git = ["git", "-c", "user.name=shop", "-c", "user.email=shop@example.com"]
    for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "initial import"]):
        subprocess.run([*git, *command], cwd=repo, check=True, capture_output=True)
    return repo


def prompt_text(prompt_id: str) -> str:
    return (PROMPTS / f"{prompt_id}.txt").read_text(encoding="utf-8").strip()


def build_marker(home: Path, prompt_id: str, out: Path) -> Path:
    source = out / f"{prompt_id}.instruction.txt"
    source.write_text(prompt_text(prompt_id) + "\n", encoding="utf-8")
    python = hermes_side.agent_dir() / "venv" / "bin" / "python"
    done = subprocess.run(
        [str(python), str(HERE / "hermes_side.py"), "marker", "--hermes-home", str(home),
         "--prompt-file", str(source)],
        env=child_env(home), capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        raise RunError(f"marker build failed: {done.stderr.strip()[-400:]}")
    marked = out / f"{prompt_id}.message.txt"
    marked.write_text(done.stdout, encoding="utf-8")
    return marked


def gate_log_size(home: Path) -> int:
    log = home / judge.GATE_LOG
    return log.stat().st_size if log.is_file() else 0


def gate_armed_since(home: Path, offset: int) -> bool:
    log = home / judge.GATE_LOG
    if not log.is_file():
        return False
    with log.open("rb") as fh:
        fh.seek(offset)
        added = fh.read()
    return any(mark in added for mark in ARM_EVENTS)


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def run_cli(home: Path, message: Path, repo: Path, out: Path, *, expect_gate: bool,
            timeout: float, arm_timeout: float) -> dict:
    """`hermes -z` with a watch on the gate log; reports why it gave up, if it did."""
    usage = out / "usage-1.json"
    offset = gate_log_size(home)
    started = time.monotonic()
    with (out / "stdout-1.txt").open("w", encoding="utf-8") as so, \
            (out / "stderr-1.txt").open("w", encoding="utf-8") as se:
        proc = subprocess.Popen(
            ["hermes", "-z", message.read_text(encoding="utf-8"), "--usage-file", str(usage)],
            cwd=repo, env=child_env(home), stdout=so, stderr=se, start_new_session=True,
        )
        armed = not expect_gate
        while proc.poll() is None:
            elapsed = time.monotonic() - started
            armed = armed or gate_armed_since(home, offset)
            if not armed and elapsed > arm_timeout:
                _kill(proc)
                return {"ok": False, "reason": f"no turn_armed within {arm_timeout:.0f}s"}
            if elapsed > timeout:
                _kill(proc)
                return {"ok": True, "timed_out": True, "usage": [usage], "exit": None}
            time.sleep(1)
    if not armed and not gate_armed_since(home, offset):
        return {"ok": False, "reason": "process ended without turn_armed"}
    return {"ok": True, "timed_out": False, "usage": [usage], "exit": proc.returncode}


def run_inproc(home: Path, messages: list[Path], repo: Path, out: Path, *, timeout: float) -> dict:
    python = hermes_side.agent_dir() / "venv" / "bin" / "python"
    usages = [out / f"usage-{i}.json" for i in range(1, len(messages) + 1)]
    command = [str(python), str(HERE / "hermes_side.py"), "run", "--hermes-home", str(home)]
    for message, usage in zip(messages, usages, strict=True):
        command += ["--prompt-file", str(message), "--usage-file", str(usage)]
    with (out / "stdout.txt").open("w", encoding="utf-8") as so, \
            (out / "stderr.txt").open("w", encoding="utf-8") as se:
        proc = subprocess.Popen(command, cwd=repo, env=child_env(home), stdout=so, stderr=se,
                                start_new_session=True)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            return {"ok": True, "timed_out": True, "usage": usages, "exit": None}
    return {"ok": True, "timed_out": False, "usage": usages, "exit": proc.returncode}


def read_usage(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def newest_session(home: Path, since: float) -> str | None:
    """Last resort when a timed-out run left no usage file: the root session it started."""
    try:
        with sqlite3.connect(f"file:{home / judge.STATE_DB}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE parent_session_id IS NULL AND started_at >= ? "
                "ORDER BY started_at LIMIT 1", (since,),
            ).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def run(args: argparse.Namespace) -> int:
    home = resolve_home(args.hermes_home, args.allow_default_profile)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    chain = [FOLLOW_UP[args.prompt], args.prompt] if args.prompt in FOLLOW_UP else [args.prompt]
    if len(chain) > 1 and args.runner == "cli":
        raise RunError(f"{args.prompt} continues a session; use --runner auto or inproc")

    messages = [build_marker(home, chain[0], out)]
    for follow in chain[1:]:
        plain = out / f"{follow}.message.txt"
        plain.write_text(prompt_text(follow) + "\n", encoding="utf-8")
        messages.append(plain)

    started_wall = time.time()
    started = time.monotonic()
    repo = make_sandbox()
    runner, fallback = "inproc", None
    result: dict = {}
    if args.runner in {"auto", "cli"} and len(chain) == 1:
        result = run_cli(home, messages[0], repo, out, expect_gate=not args.without_gate,
                         timeout=args.timeout, arm_timeout=args.arm_timeout)
        runner = "cli"
        if not result["ok"]:
            if args.runner == "cli":
                raise RunError(f"hermes -z run unusable: {result['reason']}")
            fallback = result["reason"]
            shutil.rmtree(repo.parent, ignore_errors=True)
            repo = make_sandbox()
    if runner != "cli" or fallback:
        runner = "inproc"
        result = run_inproc(home, messages, repo, out, timeout=args.timeout)

    elapsed = time.monotonic() - started
    usages = [read_usage(p) for p in result["usage"]]
    session_id = (usages[0].get("session_id") if usages else None) or newest_session(
        home, started_wall - 1)
    turns = [
        {"prompt": prompt_id, "turn": number, "session_id": session_id,
         "cost_usd": usage.get("estimated_cost_usd"), "api_calls": usage.get("api_calls"),
         "usage": usage}
        for number, (prompt_id, usage) in enumerate(zip(chain, usages or [{}] * len(chain),
                                                        strict=False), start=1)
    ]
    record = {
        "hermes_home": str(home), "prompt": args.prompt, "runner": runner,
        "fallback_reason": fallback, "sandbox": str(repo), "timed_out": result["timed_out"],
        "exit_code": result["exit"], "elapsed_seconds": round(elapsed, 1),
        "cost_usd": round(sum(t["cost_usd"] or 0 for t in turns), 4), "turns": turns,
    }
    (out / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                  encoding="utf-8")
    code = 0
    if session_id:
        verdicts = judge.judge_run(record)
        (out / "verdict.json").write_text(json.dumps(verdicts, indent=2, ensure_ascii=False)
                                          + "\n", encoding="utf-8")
        for verdict in verdicts:
            print(json.dumps(verdict, ensure_ascii=False))
    else:
        print("no session was recorded; nothing to grade", file=sys.stderr)
        code = 1
    if args.cleanup:
        shutil.rmtree(repo.parent, ignore_errors=True)
    return code or (1 if result["timed_out"] else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--prompt", required=True, choices=PROMPT_IDS)
    parser.add_argument("--out", required=True, help="directory for run.json and transcripts")
    parser.add_argument("--hermes-home", help="profile to run in (default: $HERMES_HOME)")
    parser.add_argument("--runner", choices=("auto", "cli", "inproc"), default="auto")
    parser.add_argument("--timeout", type=float, default=900, help="seconds per run")
    parser.add_argument("--arm-timeout", type=float, default=120,
                        help="seconds to wait for turn_armed before falling back")
    parser.add_argument("--without-gate", action="store_true",
                        help="the profile has the gate off: skip the turn_armed check")
    parser.add_argument("--allow-default-profile", action="store_true")
    parser.add_argument("--cleanup", action="store_true", help="delete the shop-api checkout after")
    try:
        return run(parser.parse_args(argv))
    except RunError as exc:
        print(f"run_eval: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
