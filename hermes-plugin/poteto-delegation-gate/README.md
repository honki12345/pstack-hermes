# poteto-delegation-gate

A Hermes plugin that makes an agent in `/pstack:poteto-mode` write its todolist, including the
delegation decision, before it starts working. It blocks working tools until that todolist exists
and holds a decision item. It never forces delegation. A todolist item that mentions delegation and
says `skip: <reason>` satisfies it.

## Why it exists

The poteto-mode playbooks ask the agent to decide which steps go to `delegate_task` before it
works. Without a gate nothing makes it do so, and in the baseline runs of `tools/eval/` the agent
still reaches for a working tool such as `terminal` first, and the gate blocks that call.

## The two hooks

| Hook | What it does |
|---|---|
| `pre_llm_call` | Arms the gate for the session when the user message opens with the skill invocation header that names `poteto-mode`. A later turn in the same session without that header keeps the gate on and starts a fresh turn, as described under follow-up turns below. A subagent turn (one with a parent session) is never armed. Returns nothing, so it injects no context. |
| `pre_tool_call` | While the gate is armed and no decision has been written, blocks every tool except the preparatory ones below. Writing a todolist that holds a decision item through `todo_list`, directly or inside a `tool_call` bridge, unlocks everything for the rest of the turn. |

Tools allowed before a decision is written are `read_file`, `skill_view`, `skills_list`,
`tool_search`, `tool_describe`, `todo_list`, `delegate_task`, `clarify`, `memory` and the three
`hindsight_*` tools. A `tool_call` bridge is allowed only when every tool inside it is on that list.

A blocked call returns the block message and the agent retries after writing the decision. Any
exception inside a hook allows the call, so a gate failure never stops a session.

## What counts as a decision

The gate reads the `content` of the items in the `todos` argument of `todo_list`. One item is a
decision when it names `delegate_task`, or when it mentions delegation (the text `deleg`, as in
`delegation` or `delegate`) and also holds `skip:` followed by a reason on the same line. Case does
not matter. The recommended item is `Delegation decision: delegate_task <the steps>` or
`Delegation decision: skip: <reason>`, and both block messages show that form.

These are not decisions, so they do not unlock work.

- A bare `skip:` with no reason, or with the reason on the next line. It only has the form of one.
- A `skip: <reason>` that never mentions delegation, such as `Step 3: open PR. skip: no PR
  requested`. The poteto-mode skill asks for `skip: <reason>` on any playbook step the agent does
  not do, so a skipped step must not read as a delegation decision.

The check is a pattern, not a judgment. It cannot tell a good reason from a weak one. It also
cannot tell a verbatim playbook step that mentions `delegate_task` from a decision about it, so
copying the `multi-phase-plan`, `orchestrate` or `opening-a-pr` step that names `delegate_task`
unlocks work. The tests pin that case.

| Call | Result |
|---|---|
| `todo_list` with a decision item | Unlocks the turn (`todo_written_now`). |
| `todo_list` with items but no decision item | Allowed so the agent can add the decision, but work stays blocked (`todo_pending_decision`). The next working call is blocked with `todo_without_decision`. |
| `todo_list` with no `todos`, which only reads the list | Changes nothing. |
| Any `todo_list` after the turn is unlocked, `merge: true` or not | Allowed without a check. |

The last row is why a partial update such as `merge: true` with one changed item does not get
blocked again once the decision was written. The turn remembers that it saw a decision, so a later
write that replaces the list is not checked either. A turn released after repeated blocks is
unlocked the same way but is logged as `released`, not as a written decision.

Hermes unwraps a `tool_call` bridge that holds one local tool before it calls the hook, so the
hook usually sees `tool_name` `todo_list` with the unwrapped arguments. When Hermes cannot unwrap
the bridge, the hook sees the bridge arguments instead. The gate reads both forms, including the
older single `name` and `arguments` shape and JSON strings for `calls`, `arguments` or `todos`. A bridge
that holds an undecided todolist next to a working tool is blocked as a whole.

## Release after repeated blocks

If the agent keeps hitting `todo_without_decision`, the gate lets the third working call through
and logs it as `decision_missing_released`. The turn then stays unlocked, and its later calls are
logged as `released`. This exists because a gate that blocks without end can stall a session when
the agent cannot write the item, for example when `todo_list` fails or the item format never
matches. Two blocks give the agent two messages that say exactly what to write.

Blocking before any todolist exists (`work_before_todo`) has no limit and is not counted. Writing
any todolist ends it, so it cannot loop on a session whose `todo_list` works. After a todolist
without a decision the gate blocks at most twice per turn.

A release means the turn ran without a recorded decision. It is the sign to read the transcript,
and the judge in `tools/eval/` counts it as a failed recovery.

State is kept in process memory per session id. Two sessions in one profile do not affect each
other.

## Follow-up turns

Once a session has had a `/poteto-mode` turn, the gate stays on for its later turns. Before this,
the first turn without the skill header switched the gate off, so an agent that moved from a
bug fix to a wide investigation in a follow-up never had to record a delegation decision.

The gate cannot read intent. It does not try to tell a follow-up that continues the work from a
new task. It sorts the input by its shape and gives every markerless user turn the same rule.

| Input | What the gate does | Log |
|---|---|---|
| Message that opens with the `poteto-mode` header | Arms a fresh turn. Nothing from the earlier turn carries over. | `turn_armed` |
| Message that opens with the header of another skill | Turns the gate off for the session. The user chose a different skill on purpose. | `turn_disarmed` |
| Gateway notice that opens with `[ASYNC DELEGATION` or `[IMPORTANT: Background process` or `[IMPORTANT: <n> background processes` | Continues the previous turn. The todolist, the decision, the block count and a release stay as they were, so the agent can read a child's result with the tools it already had. | `turn_continued`, `kind: "notice"` |
| Any other message in the same session, such as a question or a new instruction | A new turn. The todolist and decision state start empty, and the read allowance below applies. | `turn_continued`, `kind: "followup"` |
| Any message in a session with no `poteto-mode` turn, and any subagent turn | Not gated. | none |

A repeat of the hook inside one turn (same `turn_id`) changes nothing, so a todolist written in
that turn is not forgotten.

**Read allowance.** A follow-up turn can use up to three read-only calls before it needs a
decision. A question such as "what did you just change?" usually ends after `git status` and
`git diff`, and asking for a todolist first would block the user's turn for nothing. The calls that
count as read-only are `search_files`, `session_search`, and a `terminal` command made of
`ls`, `cat`, `head`, `tail`, `wc`, `pwd`, `grep`, `rg`, `stat`, `file`, `which`, `du`, `find`, `cd` with one path, or `git`
with `diff`, `log`, `status`, `show`, `blame`, `shortlog`, `rev-parse`, `ls-files` or `describe`.
Pipes and `&&` are fine when every part is read-only. A redirection, a substitution, `;`, a lone
`&`, a newline, `find -exec` or `-delete`, `git diff --output` and anything not on the list are not
read-only. A `tool_call` bridge counts each read-only call inside it. Preparatory tools do not count.
The allowance is logged as `followup_read`.

The fourth read-only call, any other working tool, and any call after the first block need the
decision first. That is the original failure, an agent that starts a broad investigation or a
change in a follow-up without a decision, and it is blocked like a marker turn is (`work_before_todo`,
then `todo_without_decision`, then release after two blocks). Once a block or an undecided
todolist has happened in the turn, the allowance is closed, so the log reads as a block followed
by a recovery.

What this costs the user: a question turn that stays within three read-only calls is never
blocked. A follow-up that edits files is blocked once, then goes ahead after the todolist.
Blocks per follow-up turn are bounded by the same rule as a marker turn, one before the
todolist and at most two after an undecided one.

**Limits.**

- A message that arrives with no `poteto-mode` turn earlier in the same process is not gated. State
  lives in process memory, so a restart of Hermes or of the gateway forgets which sessions had a
  `poteto-mode` turn. Send the `/pstack:poteto-mode` header again to arm the session.
- The gate stays on for the rest of the session. To work without it, start a new session or
  disable the plugin. A message that opens with another skill's header also turns it off.
- The read allowance is a pattern over the command string. It does not run the command and does
  not understand every shell. It can be wrong in both directions, and a read-only command can
  still read a lot, so the cap of three calls is what keeps an investigation from starting for free.
- Notice detection reads the first characters of the message. A user who types text that begins
  with one of those headers is treated as a notice.
- Memory is bounded. A session that has been idle for 24 hours is dropped, and when more than 256
  sessions are tracked the least recently used one is dropped. A dropped session behaves like a
  session that never had a `poteto-mode` turn.

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
| `turn_continued` | `session_id`, `turn_id`, `kind` (`followup` or `notice`) |
| `turn_disarmed` | `session_id`, `turn_id` (the user invoked another skill) |
| `allow` | `session_id`, `tool_name`, `bridged`, `reason`, `delegated`, `tool_call_id` |
| `block` | `session_id`, `tool_name`, `bridged`, `reason`, `delegated`, `tool_call_id` |

Every line also has `ts`, the Unix time in seconds.

`reason` on `allow` is one of the following.

| `reason` | Meaning |
|---|---|
| `todo_written_now` | This call wrote a todolist with a decision item. |
| `todo_written` | A decision was written earlier in the turn. Later calls of a released turn do not use it. |
| `todo_pending_decision` | This call wrote a todolist with no decision item. Work stays blocked. |
| `decision_missing_released` | The block limit was reached, so this call unlocked the turn without a decision. |
| `released` | Every later call in a turn that was unlocked without a decision. A log reader can tell this turn from one that has `todo_written`. |
| `preparatory_tool` | A preparatory tool, or a `todo_list` that only reads. |
| `followup_read` | A read-only call inside the read allowance of a follow-up turn. |

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
cases. Both commands run all 158.

## Measure its effect

`tools/eval/README.md` describes the harness that runs a real agent against this gate and grades
the log. Change the gate only with a before and after run from that harness.
