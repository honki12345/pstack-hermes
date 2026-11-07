# Hermes delegation contract

How pstack's spawn instructions map onto Hermes Agent. Every skill that says
"spawn", "fan out", or names a model role line follows this page. Verified
against Hermes v0.23.0 (`tools/delegate_tool.py`, `tools/delegate_tool_routes.py`).

## What `delegate_task` accepts

A task carries `goal`, `context`, and optionally `output_schema`, `images`, and
`group`. Core has no per-task `model`, `readonly`, `background`, or `toolsets`.
A legacy `role` (`leaf`) is accepted and ignored: depth decides capability.

- `model` is a core-reserved key. Hermes drops it without an error, so the child
  runs on the parent model (or `delegation.model`). A panel built from `model`
  values silently collapses to one model.
- `readonly` (or any other undeclared key) refuses the task: "does not take
  'readonly' ... this task was not started".
- Top-level delegations always run in the background. Results arrive between
  your turns as a new message. Finish what does not depend on them, then end the
  turn. Children inside an orchestrator child run synchronously.
- Children inherit the parent's toolsets. Nothing narrows a child's tools per
  task.

## Route by role, not by slug

With the `pstack` delegation backend active (`hermes-plugin/pstack-delegation`
in the pstack-hermes repository; `delegation.backend: pstack`), `delegate_task`
offers three extra task keys:

| Key | Value |
|---|---|
| `pstack_role` | The role line the skill names, spelled exactly: `how explorer`, `interrogate reviewers`, `feature, refactoring`, ... |
| `seat` | For panel roles (lists), the 0-based entry. Spawn one task per entry. |
| `access` | `read-only` or `write`. |

The backend reads `pstack-models.json` beside `config.yaml`, then
`delegation.backends.pstack.aliases`, then upstream pstack's defaults, and builds
each child on that model, provider, and reasoning effort (or as a named
profile). Never pass `model`. Each result's `route` label names the model the
child actually ran on. Cite the label, not the configured slug.

Without the backend (the schema shows no `pstack_role`), omit `pstack_role`,
`seat`, and `access`; they are refused. Every child then runs on the parent model.
Spawn panel roles anyway, one task per entry, so the fan-out and the independent
reads still happen, and state in the reply that the panel ran single-model.
"Different model family" checks are unmet in that case. Say so instead of
claiming them.

## Read-only is advisory

Hermes cannot strip write tools from one child. For every spawn a skill marks
read-only:

1. Set `access: read-only` when the backend is active.
2. Open `context` with `READ-ONLY: do not edit files, git state, or external
   systems; report findings only.`
3. When the children return, run `git status --porcelain` (and `git stash list`)
   in each worktree they could reach. Revert and report any write.

MCP tools are never stripped from a child, so "agent mode keeps MCP" concerns
from upstream do not apply. Read-only investigators keep their MCP lookups.

## Waking later: cron is a fresh run

`hermes cron create` never resumes this session. Each tick is a new agent run
that knows only its prompt. For a wake loop:

- Write a self-contained tick prompt: plan or `goal.md` path, store path, the
  exact predicate, and what to report.
- Add `--workdir <repo>` for project context and `--continuity` so each run sees
  the previous run's output. Keep cross-run state in the plan store or
  `hermes cron notepad`.
- `--deliver origin` posts the result back to this chat. `bot-chat` wakes the
  profile's canonical Bot Chat, not this session.
- Remove the job (`hermes cron remove <id>`) when the predicate holds, the run
  is stood down, or the operator pauses it. Report the job id when you arm it.
- A tick never merges, force-pushes, or deletes unless the operator authorized
  that action for this run. Arm watch-only ticks by default.

## Live control

`delegate_task` with `action: list` shows running children, `steer` (with
`subagent_id` and `message`) queues a correction into one child, and `stop`
ends one child and returns its partial result. Steer a drifting child instead of
spawning a duplicate. Before you replace an abandoned or wrong-scope child,
`stop` it, so two writers never race on one worktree. `/stop`, `/new`, or a
process exit halts the whole tree.

## Workers are local

Hermes has no cloud workers. Every worker is a local `delegate_task` child that
starts with no conversation history, so briefs inline what the child needs or
point at repo paths. Two writers never share a worktree. Use a separate git
worktree per writer.
