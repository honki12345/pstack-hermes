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
`skill_manage`, `tool_search`, `tool_describe`, `todo_list`, `delegate_task`, `clarify`, `memory` and
the three `hindsight_*` tools. A `tool_call` bridge is allowed only when every tool inside it is on
that list. In a follow-up turn `read_file` is on the list only for skill and playbook files, as
described under follow-up turns below.

`skill_manage` is there because Hermes' background skill and memory review runs a fork that shares
the parent session id, and the hook arguments carry nothing that tells the fork from the parent. A
review that ran after a turn with no decision would otherwise be blocked and the skill would not be
saved. It sits with `memory` as a learning tool, not repository work, and it never counts as a
decision. The cost is that, being preparatory, it can write a skill file on a marker turn before any
decision exists. The eval judge still counts a `skill_manage` call as a file write (G5), which is
how a question turn that edits skills shows up.

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
| Hermes notice, recognised by its header or by the gateway footer at its end (see below) | Continues the previous turn. The todolist, the decision, the block count and a release stay as they were, so the agent can read a child's result with the tools it already had. | `turn_continued`, `kind: "notice"` |
| Any other message in the same session, such as a question or a new instruction | A new turn. The todolist and decision state start empty, and the read allowance below applies. | `turn_continued`, `kind: "followup"` |
| Any message in a session with no `poteto-mode` turn, and any subagent turn | Not gated. | none |

A repeat of the hook inside one turn (same `turn_id`) changes nothing, so a todolist written in
that turn is not forgotten.

**Notices.** Two signals mark a message as a Hermes notice, and either one is enough. The first is
the header, after optional leading whitespace.

| Header | Source in Hermes |
|---|---|
| `[ASYNC DELEGATION ...` (complete, batch complete, task failed), and the older `[ASYNC COMPLETE` and `[ASYNC BATCH COMPLETE` | `tools/process_registry_notifications.py` |
| `[IMPORTANT: Background process ...` (completed, matched a watch pattern) | `tools/process_registry_notifications.py` |
| `[Background process <id> heartbeat #<n>` | `tools/process_registry_notifications.py` |
| `[IMPORTANT: Watch patterns disabled for process ...`, `[IMPORTANT: Watch-pattern overflow`, `[IMPORTANT: Watch-pattern notifications resumed` | `tools/process_registry_notifications.py` |
| `[IMPORTANT: <n> background processes ...` (batch and coalesced) | `tools/process_registry_notifications.py`, `gateway/run_notifications.py` |
| `[IMPORTANT: <n> background subagent delegations ...` | `gateway/run_notifications.py` |
| `[IMPORTANT: <n> background process results arrived while this session was busy` | `gateway/session_automation.py` |
| `[Continuing toward your standing goal]`, and the same header with a dash and a reason before the closing bracket, as in the quality-gate form | `hermes_cli/goals.py`. The goal loop sends one of its four continuation templates between turns of one standing goal, so the decision made when the goal was set still applies. |

The second is the footer `[INTERNAL NOTIFICATION ... generated by the Hermes gateway ...]` that the
gateway appends to the end of every notification it delivers, after a blank line. It covers a new
notification kind the header list does not know yet. It is not a replacement for the headers,
because the CLI path delivers the same notifications without a footer. Only the last footer block
is looked at, found from the end of the message in one pass. It counts when it holds the text
`generated by the Hermes gateway`, ends with the closing bracket, and has no other closing bracket
in it. A message that quotes the footer in the middle, or adds text after it, is a normal follow-up.

**Read allowance.** A follow-up turn can use up to three read-only calls before it needs a
decision. A question such as "what did you just change?" usually ends after `git status` and
`git diff`, and asking for a todolist first would block the user's turn for nothing. The calls that
count as read-only are `search_files`, `session_search`, `read_file`, and a `terminal` command made
only of the pieces below. A `read_file` of a skill or playbook file, meaning a `.md` file whose normalised path has
a `skills` directory in it, is a preparatory tool and does not count. A path with a `..` part is
never a skill file. Any other `read_file` counts, so reading
files one by one is not free. A marker turn is unchanged. There every `read_file` is preparatory.

| Piece | Read-only form |
|---|---|
| `ls`, `cat`, `head`, `tail`, `wc`, `pwd`, `grep`, `rg`, `stat`, `which`, `du`, `echo`, `cut` | Any arguments, except `rg --pre`, `rg --pre-glob` and `rg --hostname-bin`, which run a program. |
| `date` | Only `+<format>`, `-u` (`--utc`, `--universal`) and the output-format flags `-I`, `-R`, `--iso-8601` and `--rfc-*`, optionally after `TZ=<zone>`. `-s`, `--set` and a bare time operand set the clock, so every other argument is refused. `TZ=` is the only assignment allowed in front of a command, and only in front of `date`, because other variables can change what a command runs. |
| `find` | Without `-exec`, `-execdir`, `-ok`, `-okdir`, `-delete` and the `-fprint`, `-fprint0`, `-fprintf` and `-fls` actions. |
| `file` | Only the flags `-b -i -I -k -L -h -n -N -s -p` (alone or combined) and their long forms. `-C` writes a file, so every other flag is refused. |
| `cd` | One path. |
| `git` | `diff`, `log`, `status`, `show`, `blame`, `shortlog`, `rev-parse`, `ls-files` or `describe`, after optional `-C <path>` and `--no-pager`. Also `branch` with only the listing flags `--show-current`, `-a`, `-r`, `-v` and `-vv`, and `remote`, `remote -v` and `remote get-url <name>`, because the other forms of those two create, rename and delete. Any flag that starts `--out`, `--ext`, `--text` or `-O` is refused, which covers `--output`, `--ext-diff`, `--textconv` and their abbreviations. |
| `sed` | `sed -n '<address>p'` only, where the address is a line number, `$`, a `/regex/` with no backslash and no `[`, or a range of those. A script with `w`, `e`, `s` or a second command, a backslash anywhere in it, and any of `-i`, `-e`, `-f`, is refused. |
| `gh` | `pr view`, `pr list`, `pr checks`, `pr diff`, `issue view`, `issue list`, `run view`, `run list`, `repo view` and `auth status`. Each takes only the read flags listed for it in the code, taken from the gh manual, for example `--json <fields>`, `-R/--repo`, `--comments`, `--state`, `--limit`, `--author`, `--base`, `--head`, `--search`, `--log`, `--log-failed` and `--job`. Any other flag is refused, which covers `--web`, `-w`, `--jq`, `-q`, `--template`, `-t`, `--watch`, every `--flag=value` form and every bundle of short flags. `auth status` takes no flag. `gh api` is not allowed. |

Pieces are joined by `|`, `&&` or `;`, and the whole command is read-only when every piece is. The
splitter understands quotes. Inside single quotes everything is text, so `grep -E 'a|b;c'` is one
piece. Inside double quotes `$`, a backtick and a backslash still count as syntax. Anywhere in the
command, even inside quotes, a `#`, a newline and any other control character make it not
read-only, because a comment can end at a newline inside a quote and let the next line run. A
command such as `git log --format='%h #%s'` is refused for this reason. Outside quotes a
redirection other than the two below, a substitution, a variable, a backslash, a parenthesis, a
brace, `||`, a lone `&` and a quote that never closes make the command not read-only. The
two redirections that pass are the ones that discard or merge output, `>/dev/null` (also `2>` and
`&>`) and `2>&1`. `sqlite3` is not allowed because its dot commands run a shell and write files.
A `tool_call` bridge counts each read-only call inside it. Preparatory tools do not count. The
allowance is logged as `followup_read`.

The fourth read-only call, any other working tool, and any call after the first block need the
decision first. That is the original failure, an agent that starts a broad investigation or a
change in a follow-up without a decision, and it is blocked like a marker turn is (`work_before_todo`,
then `todo_without_decision`, then release after two blocks). Once a block or an undecided
todolist has happened in the turn, the allowance is closed, so the log reads as a block followed
by a recovery.

The first block of a follow-up turn uses its own message. It says the message is a new request,
asks the agent to choose the playbook that fits it again or to write that none fits, and asks for
the `Delegation decision:` item. The marker turn message asks the agent to copy a playbook's
numbered steps, which only makes sense for the request that named the playbook.

What this costs the user: a question turn that stays within three read-only calls is never
blocked. A follow-up that edits files is blocked on its first working call, then goes ahead after the
todolist. Before any todolist exists the gate has no block limit, so a follow-up that keeps trying
working tools without writing one keeps being blocked. Writing any todolist ends that. After an
undecided todolist the gate blocks at most twice, the same as a marker turn.

**Limits.**

- A message that arrives with no `poteto-mode` turn earlier in the same process is not gated. State
  lives in process memory, so a restart of Hermes or of the gateway forgets which sessions had a
  `poteto-mode` turn. Send the `/pstack:poteto-mode` header again to arm the session.
- The gate stays on for the rest of the session. To work without it, start a new session or
  disable the plugin. A message that opens with another skill's header also turns it off.
- The read allowance is a pattern over the command string. It does not run the command and does
  not understand every shell. It can be wrong in both directions, and a read-only command can
  still read a lot, so the cap of three calls is what keeps an investigation from starting for free.
  The allowance reduces friction and is not a security boundary, since an agent can always write one
  todolist line to get past the gate. A shape that is unclear is refused, so the allowed set stays
  small. A refusal costs one block and one todolist.
- Notice detection reads the start of the message for a header and the end of it for the footer. A
  user who types text that begins with one of those headers, or ends with the footer, is treated as a
  notice.
- Memory is bounded. A session that has been idle for 24 hours is dropped, and when more than 256
  sessions are tracked the least recently used one is dropped. A dropped session behaves like a
  session that never had a `poteto-mode` turn. The 24 hours count for the session that is
  speaking too, so a session that comes back alone after a day is treated the same as one that
  comes back while others are running.
- The background review fork is not told apart from the parent. Its `read_file` and `search_files`
  calls are judged with the parent turn's state, so a review that runs while the parent has used up
  its read allowance can see them blocked. Its `skill_view`, `skill_manage` and `memory` calls
  always pass.

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
cases. Both commands run all 618. The notice cases carry strings that Hermes' own formatters
produce, copied in with their source named in a comment, so they need no Hermes install.

## Measure its effect

`tools/eval/README.md` describes the harness that runs a real agent against this gate and grades
the log. Change the gate only with a before and after run from that harness.
