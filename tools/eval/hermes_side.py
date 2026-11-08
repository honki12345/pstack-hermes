#!/usr/bin/env python3
"""Entry points that must run inside Hermes' own interpreter (`run_eval.py` shells out to this).

`marker` prints the skill-invocation message the gate recognises. `run` executes one or two
one-shot turns in a single process, which is the only way the gate's in-memory turn state can
carry from a first turn into a follow-up.

Hermes is imported lazily and only after HERMES_HOME is pinned, so importing this module is
harmless anywhere else.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def agent_dir() -> Path:
    return Path(os.environ.get("HERMES_AGENT_DIR") or Path.home() / ".hermes" / "hermes-agent")


def _boot(hermes_home: str) -> None:
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        os.environ.pop(name, None)
    sys.path.insert(0, str(agent_dir()))
    # The attribute makes hermes_bootstrap keep the profile we set below instead of the default.
    sys._hermes_pin_default_home = True  # type: ignore[attr-defined]
    import hermes_bootstrap  # noqa: F401

    os.environ["HERMES_HOME"] = hermes_home


def build_marker(hermes_home: str, skill: str, instruction: str) -> str:
    _boot(hermes_home)
    from agent.skill_commands import build_skill_invocation_message, scan_skill_commands
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    scan_skill_commands()
    message = build_skill_invocation_message(skill, instruction, task_id="shop-api")
    if not message:
        raise SystemExit(f"skill {skill} is not available in {hermes_home}")
    return message


def run_turns(hermes_home: str, prompts: list[str], usage_files: list[str]) -> int:
    _boot(hermes_home)
    from hermes_cli.oneshot import run_oneshot

    exit_code = 0
    session_id: str | None = None
    for prompt, usage_file in zip(prompts, usage_files, strict=True):
        exit_code = run_oneshot(prompt, usage_file=usage_file, resume=session_id) or exit_code
        try:
            session_id = json.loads(Path(usage_file).read_text(encoding="utf-8"))["session_id"]
        except (OSError, ValueError, KeyError):
            return exit_code or 1
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    marker = sub.add_parser("marker")
    marker.add_argument("--hermes-home", required=True)
    marker.add_argument("--skill", default="/pstack:poteto-mode")
    marker.add_argument("--prompt-file", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--hermes-home", required=True)
    run.add_argument("--prompt-file", type=Path, action="append", required=True)
    run.add_argument("--usage-file", action="append", required=True)
    args = parser.parse_args(argv)

    if args.command == "marker":
        text = args.prompt_file.read_text(encoding="utf-8").strip()
        sys.stdout.write(build_marker(args.hermes_home, args.skill, text))
        return 0
    prompts = [p.read_text(encoding="utf-8") for p in args.prompt_file]
    code = run_turns(args.hermes_home, prompts, args.usage_file)
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
