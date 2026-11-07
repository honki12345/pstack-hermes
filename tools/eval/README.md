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
hermes profile create pstacklane-demo --clone-from pstacklab --no-alias
env -i HOME=$HOME PATH=$PATH HERMES_HOME=$HOME/.hermes/profiles/pstacklane-demo \
  uv run --frozen tools/eval/run_eval.py --prompt p1-bugfix --out /tmp/lane/p1
hermes profile delete pstacklane-demo
```

- The profile needs the `pstack` plugin and `poteto-delegation-gate` enabled.
- `env -i` matters: a `HERMES_HOME` left in a parent shell otherwise decides which profile
  runs. The harness rebuilds the child environment from `HOME`, `PATH`, `LANG`, `TMPDIR` and the
  profile path only, and ignores everything else.
- `--prompt p4-followup` runs p1 and then the follow-up as two turns of one session in one
  process, because the gate keeps its turn state in memory.
- `--runner auto` (default) tries `hermes -z` and watches the gate log for `turn_armed`. If it
  does not appear within `--arm-timeout` seconds, the run is repeated in-process. Use
  `--without-gate` for a profile whose gate is off, so the harness does not wait for it.
- `--timeout` bounds one run (default 900 s). `--cleanup` removes the `shop-api` checkout.

Output in `--out`: `run.json` (profile, runner, elapsed seconds, per-turn usage and cost),
`verdict.json`, the marked messages, stdout/stderr of the run, and one verdict per line on
stdout.

Grade an existing run again, or any session, without a model call:

```sh
uv run --frozen tools/eval/judge.py --run-dir /tmp/lane/p1
uv run --frozen tools/eval/judge.py --hermes-home <profile> --session-id <id> --prompt p1-bugfix --pretty
```

## Verdict

```json
{"prompt": "p1-bugfix", "session_id": "...", "turn": 1,
 "G1": true, "G2": null, "G3": true, "G4": null, "G5": null, "G6": null,
 "counts": {"blocks": 0, "delegate_task_calls": 2, "child_sessions": 2,
            "mutating_calls": 1, "tool_calls": 14},
 "evidence": {"gate_log_found": true, "turn_armed": true, "first_work_tool": "terminal", "...": "..."}}
```

`null` means the prompt does not exercise that metric (or, for G2, that nothing was blocked).

| Metric | Prompt | True when |
|---|---|---|
| G1 decision first | p1, p3 | the gate armed, and a `todo_list` containing `delegate_task` or `skip:` came before the first work tool; a work call that the gate had to block counts against it |
| G2 recovery | all | after a gate block, the next non-preparatory step is the `todo_written_now` allow |
| G3 delegation happens | p1 | at least one `delegate_task` call executed |
| G4 no over-delegation | p3 | no `delegate_task`, no child session, and a `skip:` reason in the todolist |
| G5 reroute | p2 | no mutating call: `write_file`, `patch`, or a `terminal`/`execute_code` command that writes |
| G6 follow-up turn | p4 | the second turn of the session still has gate events and no `turn_disarmed` |

The judge reads the gate log (`logs/poteto-delegation-gate.jsonl`) and, from `state.db`, the
`messages` and `sessions` tables. "Work tool" is anything outside the gate's preparatory set
(`read_file`, `skill_view`, `tool_search`, `todo_list`, `delegate_task`, ...). `tool_call`
bridges are unpacked to the tools they carry. Mutation detection of shell commands is a
pattern heuristic (redirects, `rm`/`mv`/`sed -i`, `git commit`, installers, file-writing
code); a false positive shows up in `evidence.mutating_tools`.

## Reading results

Model output varies run to run. Judge a change on the pass rate over several runs of the same
prompt, before and after, not on one verdict. `run.json` carries `elapsed_seconds` and
`cost_usd` (the parent session's estimate; `evidence.child_cost_usd` adds delegated sessions),
and `usage-*.json` the raw token counts. One pass over all four prompts is budgeted at 20
minutes and 3 USD.
