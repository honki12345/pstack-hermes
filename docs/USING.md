<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:ecd31f90ae3b095a6fc30d12b7919a6ea7c76eaf1ddf65fb6fdf28ec15b3394a -->

<!-- coderabbit-ai-generated-section:v1:4589d1edd22a56a6c706b747698a9dd17a7da3b950efbcf2b21dcd395fe17f67 -->

# Using pstack on hermes

> [!NOTE]
> This page covers **how to invoke and configure** the package on hermes. It
> does not explain the method — that lives in the skills themselves (load
> `poteto-mode` first; it routes to the rest). For the original method
> documentation, see upstream:
> <https://github.com/cursor/plugins/tree/main/pstack>.

## What you installed

A **method package**: 47 instructions-for-the-agent skills (24 workflow/mode +
23 `principle-*`) plus the converter/validator tooling that keeps them faithful
to upstream. It is not a service, a daemon, or an autopilot — it changes how
the agent works on a task you give it.

## Invocation model

Portable-plugin skills are **opt-in**: they do not enter the system-prompt skill
index by default. Two supported routes:

| Route | How | Trade-off |
|---|---|---|
| Namespaced `skill_view` | load a skill by id `agent-plugin-pstack-<digest>:<skill>` | keeps the plugin namespace; nothing appears in listings |
| `skills.external_dirs` | add this package's `skills/` dir to the hermes config | registers `/<name>` slash commands and lists the skills; loses the namespace and adds 60-char descriptions to the prompt index |

Pick one. The namespaced route is the default; `external_dirs` is the
convenience route when you want `/poteto-mode` style commands.

## First run

1. **Install** (see the README for the exact command), then run
   `hermes plugins doctor pstack --ci` — the manifest must parse and all skills
   must be discovered. Pick the target explicitly: `pstack` is ambiguous
   (repo root = local directory; elsewhere = installed package). Use the name
   as installed for the dist package, or a resolved local path for a branch
   tree.
2. **Configure the model panel** — run the `setup-pstack` skill once. It writes
   the model panel **outside** the package (typically `pstack-models.json`
   beside hermes `config.yaml`) so plugin updates do not discard your choices.
   Roles map code, prose, judgment, tooling, cross-judge, and panel roles to
   models available to you. `inherit-parent` means "use the parent chat model".
   The upstream-name → hermes-default mapping (for now) is in
   [MODELS.md](MODELS.md).
   Workflow skills consume those roles; a bad slug shows up later at delegation
   time — not as an upstream bug.
3. **Give it a real task** and load `poteto-mode`. It classifies the work and
   routes to the right playbook; you do not pick the playbook yourself.

## Stack forge (Graphite / GitHub)

When playbooks talk about stacks and frontiers:

- **Default:** Graphite (`gt`) when installed and working.
- **Fallback:** GitHub (`gh`) PR base/head topology when Graphite is absent or
  when the provider is set to `github` / auto-fallback.
- **GitLab (`glab`):** not supported in this port (future / halted).

Missing Graphite is not a hard failure if GitHub can complete the task. Some
operations (e.g. certain restacks) still need Graphite; treat those verdicts as
incomplete when `gt` is unavailable rather than inventing a substitute.

## What to expect

- **The agent follows playbooks, not the docs.** Your leverage is in the task
  statement and in the gates the playbooks impose (verification, decision
  trails) — not in re-reading instructions.
- **External prerequisites stay external.** Some playbooks name tooling this
  package does not ship (a deslop pass, live control-surface drivers) and some
  capabilities are approximated for hermes (see the package README's Known
  limitations and [adaptation register](https://github.com/iap/pstack-hermes/issues/94)). When a demanded live lane
  is unavailable, a verdict is *incomplete*, not clean.
- **MCP-dependent skills adapt.** Research skills draw on whichever MCP servers
  your session has configured; unreachable sources are recorded as null
  findings, not invented.

## Customizing skills

Stock pstack skills are a **versioned product**. Hermes self-improvement
(`skill_manage`, `/learn`, background learning) is for **your** procedural
memory — not for silently rewriting the installed plugin tree.

Pick by what you want to change — one of three durable routes:

| Intent | Route | Notes |
|---|---|---|
<!-- coderabbit-ai-generated:v1 -->
<!-- coderabbit-ai-generated-section:v1:f7796bf073b3cb892a03dc1f85eddabec8170321dfd83d08586e78647488dcb1 -->
| Tweak a line or two (temporary) | Edit installed `SKILL.md` in place, restart gateway | Back up edits outside the plugin first. Git updates re-apply local edits (conflicts stay in `git stash`); reinstalls replace shipped skills, so in-place edits are not durable. |
<!-- /coderabbit-ai-generated:v1 -->
| Own several changed skills, versioned | **Fork** this repo, install from your fork | `hermes plugins install <you>/pstack-hermes --enable`. Root layout: the generated package lives at the repository root (not a `pstack/` subdirectory). Merge upstream when you choose. |
| New skills beside stock pstack | **Skill tap** or local `~/.hermes/skills/` | `hermes skills tap add <you>/skills-repo`, then install; or `skill_manage` / `/learn` into **local** skills / your tap. Stock pstack stays stock. |

Structural changes that must survive upstream re-pins (new adaptations,
model-panel defaults, exclusions) belong in a fork of the development repo
(`pstack-hermes`), via the converter — see its CONTRIBUTING.md. Broadly
useful fixes are welcome as PRs there.

> [!WARNING]
> **Anti-pattern:** using Hermes learning / `skill_manage` to "fix" or evolve
> **stock** pstack skills inside the plugin install (including namespaced
> `agent-plugin-pstack-…` skills). Updates and dist republishes collide with
> those edits. Put improvements on a tap, a fork, or local skills instead.

> [!WARNING]
> Keep the hermes vocabulary when editing skills — `delegate_task`,
> `clarify`, `session_search`, `hermes cron`, `hermesbot`. Nothing on your
> machine enforces this; a skill that instructs Cursor-style `Task`
> subagents or `AskQuestion` will silently mislead the agent on hermes.

## Reporting problems

Read [SECURITY.md](../SECURITY.md) first — it defines what belongs to this port
versus upstream. In short: port behaviour (adaptations, exclusions, config,
install) → this repository's issues; a hermes defect that reproduces with the
plugin disabled → `NousResearch/hermes-agent`; an original Cursor-plugin bug
that reproduces without this port → `cursor/plugins`.

## See also

- [adaptation register](https://github.com/iap/pstack-hermes/issues/94) — port ledger
- [agent/README.md](agent/README.md) — agent deep ops (includes re-pin)
- [PATCHES.md](PATCHES.md) — historical hermes-fork patches
- [AGENTS.md](../AGENTS.md) — always-on repo contract for contributors


<!-- /coderabbit-ai-generated:v1 -->