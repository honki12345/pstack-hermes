---
name: reflect
description: Spawn three parallel review subagents over the active transcript, surface learnings, and route each to a concrete edit on an existing skill. Use when the user says reflect.
disable-model-invocation: true
---

# Reflect

Mine the current conversation for durable learnings, then route them into skill edits.

Invoke when the user says "reflect" or "/reflect". Skip when the conversation is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings.

## 1. Locate the active transcript

The parent locates the current session via `session_search` (hermes stores sessions in its SQLite store; there are no JSONL transcript files). Query for the active conversation and take the most recent matching session id. If the exact session cannot be resolved, write a tight digest of the conversation and pass that instead.

## 2. Spawn three reviewers in parallel

One message, one `delegate_task` call with three tasks, each with `pstack_role` set to its role line below and `access: read-only` (`../poteto-mode/references/hermes-delegation.md`). Reviewers need MCP access to look up the tickets, chat threads, and observability traces the transcript references; Hermes children keep the parent's MCP tools.

Each reviewer and the synthesizer name a role line in the pstack model panel (`pstack-models.json` in the hermes config dir next to `config.yaml`) and a default. Route each spawn per the Hermes delegation contract (`../poteto-mode/references/hermes-delegation.md`): set `pstack_role` to that line's name when `delegate_task` offers it, and never pass `model`, which Hermes drops. Without the pstack delegation backend every spawn runs on the parent model; say so in the reply. Pick each subagent's reasoning level and fast or standard speed for the job, per the Subagents section of the **poteto-mode** skill.

| Lens | Role line | Default `model` | Prompt template |
|---|---|---|---|
| Judgment | `reflect judgment, divergent, synthesizer` | the line's value, else the parent chat model | `references/judgment-reviewer.md` |
| Tooling | `reflect tooling` | the line's value, else the parent chat model | `references/tooling-reviewer.md` |
| Divergent | `reflect judgment, divergent, synthesizer` | the line's value, else the parent chat model | `references/divergent-reviewer.md` |

Pass each template verbatim, substituting the Hermes session id or digest where marked. Reviewers return findings in the `delegate_task` response body.

## 3. Synthesize

One `delegate_task` task with `pstack_role`: `reflect judgment, divergent, synthesizer` (upstream default `claude-opus-5-5-xhigh`) and `access: read-only`. It spot-verifies citations through MCP, which Hermes children keep. Pass `references/synthesizer.md` verbatim, with each reviewer's full output inlined where marked. It returns an Accepted / Rejected / Backlog list.

## 4. Structural enforcement check

Move any Accepted item that a lint rule, script, metadata flag, or runtime check would enforce more reliably to Backlog. See the **encode-lessons-in-structure** principle skill.

## 5. Apply

Present the synthesizer's full Accepted / Rejected / Backlog output and wait for explicit approval before applying any Accepted edit. The user picks the subset and may redirect routings. Skill changes affect every future agent in the org. Do not auto-apply.

File each Backlog item to your team's devex or backlog tracker without waiting. Only the Accepted list waits for approval.

Never edit a skill inside an installed plugin, this package included, because `hermes plugins update` replaces it. Before approval, show its destination, a local skill under `~/.hermes/skills/<skill>/` or the project's own skills directory. After approval, copy the skill folder there and edit the copy. Never open a PR or push.

Follow each approved row's Routing exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): the parent does it directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to the hermes skill-authoring flow (the hermes-agent skill's guidance, or `skill_manage(action='create')`) and run its draft / test / iterate loop.
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to the hermes skill-authoring flow's description pass.
- `new skill: <kebab-name>`: hand creation to the hermes skill-authoring flow. Do not invent the shape ad hoc.

If your environment ships a SKILL.md validator, run it on every touched skill before declaring done.

## 6. Summarize for the user

Short list, no preamble:

- Edits applied: `<skill path>`. What changed, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog filed to the devex tracker: `<issue title>` (`<tags>`). One line each.
- Dropped: one line per rejected finding + reason from the synthesizer.
