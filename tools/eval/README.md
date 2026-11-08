# Delegation behaviour harness

Measures whether an agent in `/pstack:poteto-mode` records its delegation decision before it
starts working, using real agent runs. It exists so that every change to the delegation gate or
the delegation wording is judged on before/after behaviour, not on unit tests alone.

It spends model calls, so CI never runs it. CI only runs the judge's unit tests
(`tools/tests/test_eval_judge.py`).

## Pieces

| File | Role |
|---|---|
| `prompts/p1-bugfix.txt` | a reproducible bug report; routes to the bug-fix playbook |
| `prompts/p2-why.txt` | a "how and why does this work" question; routes to investigation |
| `prompts/p3-oneline.txt` | a one-line README fix; delegating it would be over-delegation |
| `prompts/p4-followup.txt` | a follow-up in the p1 session, sent without the skill marker |
| `fixtures/shop-api/` | small service with one pricing bug; copied to a fresh git checkout per run |
| `run_eval.py` | builds the marked message, runs it in a profile, writes `run.json`, calls the judge |
| `judge.py` | grades a run from the gate log and `state.db`; no model involved |
| `hermes_side.py` | the two steps that need Hermes' own interpreter (marker, in-process run) |

Prompts and the fixture read like ordinary project work. They must not contain words that name
the measurement (`test`, `score`, `benchmark`, `probe`, ...), because the agent sees them; the
unit tests enforce this.

## Run

Use a throwaway profile. A run writes sessions and gate-log lines into it, and the harness
refuses the default profile.

```sh
hermes profile create pstacklane-demo --clone-from <profile> --no-alias
env -i HOME=$HOME PATH=$PATH HERMES_HOME=$HOME/.hermes/profiles/pstacklane-demo \
  uv run --frozen tools/eval/run_eval.py --prompt p1-bugfix --out /tmp/lane/p1-$(date +%H%M%S)
hermes profile delete pstacklane-demo -y
```

- `<profile>` is an existing profile that already has the `pstack` plugin and
  `poteto-delegation-gate` enabled; the clone inherits them.
- `env -i` matters: a `HERMES_HOME` left in a parent shell otherwise decides which profile
  runs. The harness rebuilds the child environment from `HOME`, `PATH`, `LANG`, `TMPDIR` and the
  profile path only, and ignores everything else.
- `--prompt p4-followup` runs p1 and then the follow-up as two turns of one session in one
  process, because the gate keeps its turn state in memory.
- `--runner inproc` (default) runs the prompt in a process of its own (`hermes_side.py`, in
  Hermes' interpreter). Stopping it stops the model calls, and it reads the plugin code as it is
  on disk.
- `--runner cli` runs `hermes -z`, which always hands the session to the shared gateway. Stopping
  the client does not stop the gateway: its run continues to the end, and the cost keeps coming.
  A profile the gateway has already served may keep the plugin code it loaded until the gateway
  restarts, while a profile it serves for the first time reads the code on disk. Read the
  verdict's `pairing` before trusting a `cli` run: `"name"` means the gate that ran did not log
  call ids. Pick `cli` only to measure the path a person's `hermes -z` takes. A stopped `cli` run
  (Ctrl-C, SIGTERM, SIGHUP or `--timeout`) prints one warning line on stderr; Ctrl-C also prints
  Python's traceback after it. It cannot continue a session, so `p4-followup` needs `inproc`.
- A signal that unwinds the harness (Ctrl-C, SIGTERM, SIGHUP) kills the child and its processes
  and deletes the sandbox, including when it arrives while the sandbox is being built. SIGKILL
  cannot be handled.
- `--timeout` bounds one run (default 900 s). `--cleanup` removes the `shop-api` checkout.

Output in `--out`: `run.json`, `verdict.json` (an array, one verdict per graded turn), the
marked messages, stdout/stderr of the run, and one verdict per line on stdout. Use a new path
for every run: a non-empty `--out` ends with one `run_eval: ...` line and exit code 2, so a
run never reads the usage files of an earlier one.

`run.json` records the profile, runner, elapsed seconds, per-turn usage, the sandbox's `git
rev-parse HEAD` and `git status --porcelain --untracked-files=all` before and after the run
(`sandbox_state`), and the cost. A turn whose usage file is missing is unknown, not free:
`cost_usd` is `null` when no cost is known, and `cost_complete` is `false` whenever any part is
missing. A run that fails (`run_eval: ...`, exit 2) deletes its sandbox; a finished run
keeps it for inspection unless `--cleanup` is given.

Grade an existing run again, or any session, without a model call:

```sh
uv run --frozen tools/eval/judge.py --run-dir /tmp/lane/p1-<time>
uv run --frozen tools/eval/judge.py --hermes-home <profile> --session-id <id> --prompt p1-bugfix --pretty
```

## Verdict

`verdict.json` holds an array with one verdict per graded turn. The judge prints each element on
its own line.

```json
[
  {
    "prompt": "p2-why",
    "session_id": "20260101_000000_aaaaaa",
    "turn": 1,
    "pairing": "id",
    "G1": null,
    "G2": null,
    "G3": null,
    "G4": null,
    "G5": true,
    "G6": null,
    "counts": {
      "blocks": 0,
      "block_messages": 0,
      "delegate_task_calls": 0,
      "delegate_task_rejected": 0,
      "child_sessions": 0,
      "write_tool_calls": 0,
      "tool_calls": 3
    },
    "evidence": {
      "gate_log_found": true,
      "turn_armed": true,
      "gate_events": ["turn_armed", "allow", "allow"],
      "unpaired_events": 0,
      "first_work_tool": "terminal",
      "todo_written_before_work": true,
      "todo_decision_before_work": false,
      "skip_reasons": [],
      "delegate_roles": [],
      "write_tools": [],
      "sandbox": {"head_changed": false, "status_changed": false},
      "child_models": [],
      "child_cost_usd": 0.0
    }
  }
]
```

`null` means the prompt does not exercise that metric (or, for G2, that nothing was blocked).

| Metric | Prompt | True when |
|---|---|---|
| G1 decision first | p1, p3 | the gate armed, and a `todo_list` with a decision item (one item naming `delegate_task`, or mentioning delegation and holding `skip:` with a same-line reason, the gate's criterion) came before the first work tool; a `todo_list` inside the same bridge as work counts as first, as the gate allows it; a work call that the gate had to block counts against it |
| G2 recovery | all | after a gate block, the agent writes the todolist without a second block. Blocks that come from one assistant message are one attempt; only a block in a later message fails it |
| G3 delegation happens | p1 | at least one `delegate_task` executed and was not rejected; a rejected call counts only if a child session exists |
| G4 no over-delegation | p3 | no `delegate_task`, no child session, and a decision item with a `skip:` that has a non-blank reason on the same line (also read from `skip delegate_task: <reason>`) in the todolist |
| G5 reroute | p2 | the sandbox ends as it began (same `HEAD`, same porcelain status, new files included) and no file-writing tool (`write_file`, `patch`, `skill_manage`) ran. `null` when `run.json` has no recorded before/after state, e.g. when grading a bare session with `--session-id` |
| G6 follow-up turn | p4 | the second turn of the session still has gate events and no `turn_disarmed` |

The judge reads the gate log (`logs/poteto-delegation-gate.jsonl`), `messages` and `sessions`
from `state.db`, and the sandbox state in `run.json`. "Work tool" is anything outside the
gate's preparatory set (`read_file`, `skill_view`, `tool_search`, `todo_list`, `delegate_task`,
...). `tool_call` bridges are unpacked to the tools they carry. A call is "blocked" when the
gate log has a `block` event for it. The judge never reads the result text.

**Pairing.** The gate log's `allow`/`block` rows carry the `tool_call_id` Hermes passed to the
hook (`null` if the hook got none), and `state.db` keeps the same id per call
(`tool_calls[].id`). When the turn's rows have ids the judge pairs by id and reports
`"pairing": "id"`:

- a call with no row never reached the gate (bad arguments, a scope block) and counts as executed;
- a row whose id matches no call is ignored and counted in `evidence.unpaired_events`, so a clean
  run reads `0`;
- a rejected `tool_call` batch keeps its outer id and is refused as a whole, whatever order
  parallel calls reached the hook in.

A log written before the gate recorded ids is paired by tool name and order and reports
`"pairing": "name"`. That is an approximation: Hermes unwraps a one-entry `tool_call` bridge
before the hook, parallel calls may log in any order, and a call the gate never saw can take the
next call's row, so G2, G5 and the delegation counts can be wrong on such logs. Trust a number
only from an `"id"` run.

G5 does not interpret shell or code strings: whatever a command did to the sandbox, including
from a delegated child session, shows up in the porcelain status.

A bad path or a profile without a `state.db` ends `judge.py` with one `judge: cannot grade: ...`
line and exit code 2. `run_eval.py` does the same when grading its own run fails, after
writing `run.json`, so the run can be graded again with `judge.py --run-dir`.

## Reading results

Model output varies run to run. Judge a change on the pass rate over several runs of the same
prompt, before and after, not on one verdict. `run.json` carries `elapsed_seconds` and
`cost_usd` (the parent sessions' estimate; `evidence.child_cost_usd` adds delegated sessions),
and `usage-*.json` the raw token counts. One pass over all four prompts is budgeted at 20
minutes and 3 USD.
