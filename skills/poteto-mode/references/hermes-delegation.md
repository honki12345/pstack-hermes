# Hermes delegation contract

How pstack's spawn instructions map onto Hermes Agent. Every skill that says
"spawn", "fan out", or names a model role line follows this page. Verified
against Hermes v0.23.0 (`tools/delegate_tool.py`, `tools/delegate_tool_routes.py`) and
`advanced-delegation` 1.0.0 (catalog pin `e404b68`).

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
  Upstream's "no standing-by reply that waits on a nested agent" maps here: a
  delegate never ends its run to wait (its own spawns return inside the call),
  and the top-level chat's one-line status after dispatching is not a
  standing-by reply. Do not idle or poll; the results wake you.
- Children inherit the parent's toolsets. Nothing narrows a child's tools per
  task.
- A one-shot run (`hermes -z`) and a cron tick cap the run's total children at
  `delegation.oneshot_max_children` (default 2, 0 = unlimited). Each panel round
  spends one per seat, so budget for every round, or the call that crosses the
  cap is refused before any of its children start.

## Route by option, not by slug

pstack routes through the official `advanced-delegation` plugin's model routes
(`delegation.backend: model_map`). Its routes live in
`delegation.backends.model_map.options` of `config.yaml`, the same table every
other delegation in the profile uses. With it active, each `delegate_task` task
takes one extra key, `option`: the name of a route. The schema lists the names
and one-line descriptions the profile configured, and Hermes refuses a name
outside that list before any child starts.

Pick each spawn's `option` from the role the skill names:

| Route | Use | pstack roles |
|---|---|---|
| `implement` | Write or change code: features, refactors, bug and perf fixes, hillclimb loops, swarm work items. | `feature, refactoring`, `bug-fix`, `perf-issue`, `hillclimb`, `swarm workers` |
| `explore` | Read-only code investigation: trace call paths, gather evidence, map how or why something works. | `how explorer`, `why investigators` |
| `research` | Web and document research with cited sources. | none (general work) |
| `quick` | Small mechanical tasks: one-line checks, lookups, tooling probes. | `reflect tooling` |
| `deep` | Hardest problems and judgment: design calls, synthesis, explanations, audits, careful prose. | `judgment and prose`, `hardest tasks`, `how explainer`, `why synthesizer`, `reflect judgment, divergent, synthesizer` |
| `general` | Anything that fits no other route. Default for tasks that pick none. | none (the default route) |

Panel roles (`how critics`, `arena runners`, `arena cross-judge pool`,
`architect runners`, `interrogate reviewers`) use the routes whose names start
with `panel-`, one per seat, in the order the `option` list shows them. Seat n
of a panel is the n-th `panel-` route, and the panel has as many seats as there
are `panel-` routes. Each panel route's description names its model family, so
a skill that needs "another family" can pick by it.

- Never pass `model`. Core drops it, and the route fixes the model, provider,
  and reasoning effort. Change the effort by picking another route (`deep` for
  the hardest work, `quick` for mechanical work), never per task.
- A task without `option` runs on the profile's default route
  (`delegation.backends.model_map.default`, `general` in the generated table),
  else on the parent model.
- If the `option` list lacks the route a role names, omit `option` for that
  task, so it runs on the default route, and say so in the reply.
- If delegate_task refuses a task's route (an unknown name, or a provider the
  profile cannot reach), rerun that task once without `option`, so it runs on
  the default route, say so, and name the route to fix in `config.yaml`.
- Each result's `route` names the route (`label`) and the provider, model, and
  reasoning effort the child actually ran on. Cite that, not the configured
  table.

`setup-pstack` writes this table. The pstack-hermes repository's
`tools/model_map_options.py` builds it from a role panel and keeps routes the
profile added itself.

Without the backend (the schema shows no `option`), omit `option`; a task
carrying it is refused. Every child then runs on the parent model, or on
`delegation.model` when set. Spawn panel roles anyway, one task per seat the
skill asks for, so the fan-out and the independent reads still happen, and
state in the reply that the panel ran single-model. "Different model family"
checks are unmet in that case. Say so instead of claiming them.

## Read-only is advisory

Hermes cannot strip write tools from one child. For every spawn a skill marks
read-only:

1. Open `context` with `READ-ONLY: do not edit files, git state, or external
   systems; report findings only.`
2. Prefer the `explore` route, or the route the skill names.
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
