# poteto-delegation-gate

A Hermes plugin that makes an agent in `/pstack:poteto-mode` write its todolist, including the
delegation decision, before it starts working. It blocks working tools until that todolist exists
and holds a decision item. It never forces delegation. A todolist item that says `skip: <reason>`
satisfies it.

## Why it exists

The poteto-mode playbooks ask the agent to decide which steps go to `delegate_task` before it
works. Without a gate nothing makes it do so, and in the baseline runs of `tools/eval/` the agent
still reaches for a working tool such as `terminal` first, and the gate blocks that call.

## The two hooks

| Hook | What it does |
|---|---|
| `pre_llm_call` | Arms the gate for the session when the user message opens with the skill invocation header that names `poteto-mode`. A later turn without that header disarms it. A subagent turn (one with a parent session) is never armed. Returns nothing, so it injects no context. |
| `pre_tool_call` | While the gate is armed and no decision has been written, blocks every tool except the preparatory ones below. Writing a todolist that holds a decision item through `todo_list`, directly or inside a `tool_call` bridge, unlocks everything for the rest of the turn. |

Tools allowed before the todolist exists are `read_file`, `skill_view`, `skills_list`,
`tool_search`, `tool_describe`, `todo_list`, `delegate_task`, `clarify`, `memory` and the three
`hindsight_*` tools. A `tool_call` bridge is allowed only when every tool inside it is on that list.

A blocked call returns the block message and the agent retries after writing the todolist. Any
exception inside a hook allows the call, so a gate failure never stops a session.

## What counts as a decision

The gate reads the `content` of the items in the `todos` argument of `todo_list`. An item is a
decision when it names `delegate_task`, or holds `skip:` followed by a reason on the same line.
Case does not matter. A bare `skip:` with no reason is not a decision, because it only has the
form of one. The check is a pattern, not a judgment, so it cannot tell a good reason from a weak
one.

| Call | Result |
|---|---|
| `todo_list` with a decision item | Unlocks the turn (`todo_written_now`). |
| `todo_list` with items but no decision item | Allowed so the agent can add the decision, but work stays blocked (`todo_pending_decision`). The next working call is blocked with `todo_without_decision`. |
| `todo_list` with no `todos`, which only reads the list | Changes nothing. |
| Any `todo_list` after the turn is unlocked, `merge: true` or not | Allowed without a check. |

The last row is why a partial update such as `merge: true` with one changed item does not get
blocked again once the decision was written. The turn remembers that it saw a decision, so a later
write that replaces the list is not checked either.

Hermes unwraps a `tool_call` bridge that holds one local tool before it calls the hook, so the
hook usually sees `tool_name` `todo_list` with the unwrapped arguments. When Hermes cannot unwrap
the bridge, the hook sees the bridge arguments instead. The gate reads both forms, including the
older single `name` and `arguments` shape and JSON strings for `calls`, `arguments` or `todos`. A bridge
that holds an undecided todolist next to a working tool is blocked as a whole.

## Release after repeated blocks

If the agent keeps hitting `todo_without_decision`, the gate lets the third working call through
and logs it as `decision_missing_released`. The turn then stays unlocked. This exists because a
gate that blocks without end can stall a session when the agent cannot write the item, for example
when `todo_list` fails or the item format never matches. Two blocks give the agent two messages
that say exactly what to write, and with the first `work_before_todo` block that is at most three
blocks per turn. Blocks with reason `work_before_todo` are not counted, because writing any
todolist ends them, so they cannot loop.

A release means the turn ran without a recorded decision. It is the sign to read the transcript,
and the judge in `tools/eval/` counts it as a failed recovery.

State is kept in process memory per session id. Two sessions in one profile do not affect each
other.

## Install

Install it per profile. The profile's `plugins` directory holds a symlink to this directory, so a
`git pull` of the checkout updates the plugin. Use `~/.hermes` for the default profile or
`~/.hermes/profiles/<name>` for a named one.

```sh
PROFILE_HOME=~/.hermes/profiles/<name>
ln -s "$PWD/hermes-plugin/poteto-delegation-gate" "$PROFILE_HOME/plugins/poteto-delegation-gate"
HERMES_HOME="$PROFILE_HOME" hermes plugins doctor hermes-plugin/poteto-delegation-gate --ci
HERMES_HOME="$PROFILE_HOME" hermes plugins enable poteto-delegation-gate
HERMES_HOME="$PROFILE_HOME" hermes plugins list
```

Run these from the repository root. Every `hermes` command names the profile through
`HERMES_HOME`; without it, `enable` and `list` act on whichever profile is active, and `enable`
fails with "No plugin named" when that profile has no link. Doctor must report `0 tool(s), 2
hook(s)`. `hermes plugins list` must show the plugin as enabled. Sessions started after the
change use the gate. `enable` and `disable` also reload plugins in a running gateway and print
so; hooks it already wired up for messaging platforms stay until the gateway restarts.

If the profile already has a copy or a link at that path, move it out of the `plugins` directory
before you run `ln`, and note where it pointed, because that is the way back. A second copy left
inside `plugins` carries the same plugin name and can conflict with the new link.

## Turn it off or roll back

| Goal | Command |
|---|---|
| Stop gating, keep the install | `HERMES_HOME="$PROFILE_HOME" hermes plugins disable poteto-delegation-gate` |
| Turn it back on | `HERMES_HOME="$PROFILE_HOME" hermes plugins enable poteto-delegation-gate` |
| Remove it | `HERMES_HOME="$PROFILE_HOME" hermes plugins disable poteto-delegation-gate`, then delete the symlink with `rm "$PROFILE_HOME/plugins/poteto-delegation-gate"` |
| Restore the previous install | Put back the directory or link you moved aside, then run the enable command above |

`rm` on the symlink removes only the link. Do not use `rm -r` on it.

## Audit log

Every decision is appended to `$HERMES_HOME/logs/poteto-delegation-gate.jsonl`, one JSON object
per line. The log holds decisions only. It never records the user message or tool arguments.

| `event` | Other fields |
|---|---|
| `turn_armed` | `session_id`, `turn_id` |
| `turn_disarmed` | `session_id`, `turn_id` |
| `allow` | `session_id`, `tool_name`, `bridged`, `reason`, `delegated`, `tool_call_id` |
| `block` | `session_id`, `tool_name`, `bridged`, `reason`, `delegated`, `tool_call_id` |

Every line also has `ts`, the Unix time in seconds.

`reason` on `allow` is one of the following.

| `reason` | Meaning |
|---|---|
| `todo_written_now` | This call wrote a todolist with a decision item. |
| `todo_written` | A decision was written earlier in the turn. |
| `todo_pending_decision` | This call wrote a todolist with no decision item. Work stays blocked. |
| `decision_missing_released` | The block limit was reached, so the turn was unlocked without a decision. |
| `preparatory_tool` | A preparatory tool, or a `todo_list` that only reads. |

`reason` on `block` is `work_before_todo` when no todolist was written and `todo_without_decision`
when one was written without a decision item. `bridged` lists the tool names inside a `tool_call` bridge, and is
empty for a direct call. `delegated` counts `delegate_task` calls seen in the armed turn.

`tool_call_id` is the id Hermes gave the call, or `null` when it gave none. A `tool_call` bridge
shows up under a different name in the log and in the session database, so the judge in
`tools/eval/` pairs a log line with its session message by this id. Its verdict reports
`pairing: "id"` when every line paired this way and `pairing: "name"` when the id was missing and
it fell back to tool names.

## Tests

```sh
uv run --frozen pytest -q tools/tests/test_poteto_delegation_gate.py
uv run --frozen pytest -q hermes-plugin/poteto-delegation-gate/tests
```

The cases live in `tests/test_gate.py` and run without Hermes. CI scans only `tools/tests/`, so
the wrapper `tools/tests/test_poteto_delegation_gate.py` loads that file and collects the same
cases. Both commands run all 80.

## Measure its effect

`tools/eval/README.md` describes the harness that runs a real agent against this gate and grades
the log. Change the gate only with a before and after run from that harness.
