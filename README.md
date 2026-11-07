# pstack

*The pstack agent method — go fast by going deep first — packaged for the
Hermes Agent portable plugin path. Unofficial port of
<https://github.com/cursor/plugins/tree/main/pstack> (MIT, © Lauren Tan);
package keeps the upstream identity pstack v0.15.36.*

Independently maintained by honki12345 at
<https://github.com/honki12345/pstack-hermes>. The converter and tooling
derive from the earlier community port <https://github.com/iap/pstack-hermes>.
Not affiliated with Cursor, Lauren Tan, or iap; see `NOTICE.md` in the
repository.

## Install

Install the plugin:

```sh
hermes plugins install honki12345/pstack-hermes --enable
```

The repository root is the package. Unpinned installs track the default
branch and update with `hermes plugins update pstack`.

For an immutable install, pin a commit (release tags `v*` on `main` mark
tested ones). Pinned installs do not auto-update; move a pin by reinstalling
with the new SHA and `--force`:

```sh
hermes plugins install honki12345/pstack-hermes --ref <sha> --enable
hermes plugins install honki12345/pstack-hermes --ref <new-sha> --force --enable
```

## First run

1. `hermes plugins doctor pstack --ci` — manifest parses, all skills discover.
   Pick the target explicitly: `pstack` is ambiguous (repo root = local
   directory; elsewhere = installed package). Use the name as installed for
   the installed package, or a resolved local path for a branch tree.
2. Load the `setup-pstack` skill once - it writes `pstack-models.json` beside hermes' `config.yaml`, outside this package, so plugin updates do not discard it
   (the 18-role model panel the workflow skills read).
3. Load `poteto-mode` and give it a real task — it classifies the work and
   routes to the right playbook. You do not pick playbooks yourself.

Skills are opt-in on the portable path: load one with
`skill_view` id `agent-plugin-pstack-<digest>:<skill-name>` (digest = first
8 hex of the sha256 of the plugin key, stable while the install dir is named
`pstack`). For `/<name>` slash commands instead, add this package's `skills`
dir to `skills.external_dirs` in the hermes `config.yaml` — trade-off: skills
enter the prompt index with 60-char descriptions and lose the namespace.

## Update

```sh
hermes plugins update pstack
```

Unpinned root installs update in place. Pinned installs must be reinstalled
with a new SHA and `--force` (see [Install](#install)).

Installs created before the root-layout migration (the old
source-subdirectory route), or installed from another fork, cannot update to
this repository in place. Back up local edits, remove the old install, then
install the root package:

```sh
hermes plugins remove pstack
hermes plugins install honki12345/pstack-hermes --enable
```

If the old install was pinned, add `--ref <new-package-SHA>` to the install
command.

## Customizing skills

- **Tweak a line or two** — edit the installed `SKILL.md`, restart the
  gateway, and save a backup outside the plugin before updating. Git-backed
  updates autostash and re-apply local edits (conflicts stay in `git stash`);
  reinstalls replace shipped skills, so in-place edits are temporary.
- **Own several changed skills** — fork `honki12345/pstack-hermes`, commit, install from
  your fork (`hermes plugins install <you>/pstack-hermes --enable`).
- **Add your own new skills alongside** — a skill tap:
  `hermes skills tap add <you>/skills-repo`, then
  `hermes skills install <you>/skills-repo/<skill>`.
- Keep the hermes vocabulary when editing (`delegate_task`, `clarify`,
  `session_search`, `hermes cron`); Cursor primitives will mislead the agent.

Problems: port behaviour → <https://github.com/honki12345/pstack-hermes/issues>;
a hermes defect that reproduces with the plugin disabled →
`NousResearch/hermes-agent`; an upstream pstack bug → `cursor/plugins`.

## What hermes loads

- **58 skills** (34 workflow/mode +
  24 `principle-*`), single-level `skills/<dir>/SKILL.md`,
  discovered via the root `plugin.json` (agent-plugins-v1 manifest,
  9 whitelisted fields).
- `skills/hermesbot/` — hermes-native control-surface skill on the gateway
  webhook (`X-Webhook-Signature-V2` HMAC) plus `hermes send` / `hermes peer`;
  fills the excluded `make-bot-ui` slot.
- Skill scripts (e.g. `skills/poteto-mode/scripts/`) ship verbatim; the loader
  never executes them, but skills instruct the agent to run them (`bun`, `gh`).

Not shipped: `automations/benny/` and `skills/make-bot-ui/` (install-scanner
verdicts — persistence / privilege escalation; `--force` cannot override) and
upstream's `docs/guide/` (read it upstream for the guided tour). This tree is
Hermes-native only; for real Cursor-side work install upstream pstack.

## Differences from upstream

| Pass | What changed |
|---|---|
| manifest | root `plugin.json` injected: exact agent-plugins-v1 `$schema`, 9-field whitelist |
| R1 | retired at c742dd6: upstream curates the principles index; the build only checks every entry resolves to a shipped leaf |
| F16 | retired at c47b1284: upstream ships a slug-agnostic lane regex (F16b still rejects a whitespace-only slug) |
| F11 | `worktree-audit.sh` reads worktree paths with spaces whole (F10/F12 GNU/BSD fixes retired: upstream ships them) |
| F-publish | `benny` + `make-bot-ui` excluded (scanner verdicts); `hermesbot` fills the slot; 3 localhost literals neutralized |
| T8/T9/T10 | `setup-pstack` writes the model panel beside hermes' `config.yaml` (outside the package, so updates do not discard it); discovery via `session_search`; hermes tool names |
| Phase-2A | delegation vocabulary → `delegate_task` / `clarify` package-wide |
| T11 | hardcoded Cursor paths → hermes equivalents; upstream guide links → upstream URLs |
| T12 | `/loop` → `hermes cron` wake (each tick a fresh run); check-plan accepts either marker |
| T13 | residual vendor coupling reworded (`cursor-team-kit` skills named as optional, cloud agents → local delegates) |
| P1 | `poteto-help` re-pointed at Hermes: install, no Custom Modes, panel file, `hermesbot` row |
| H1 | spawns route by `pstack_role` / `seat` / `access` (`skills/poteto-mode/references/hermes-delegation.md`); never `model` or `readonly` |

Text is otherwise unchanged; the machine-generated fix register in
`.build-provenance.txt` is the authoritative per-build record, and the
fork-level audit lives in the repository at `docs/fork/AUDIT-ko.md`.

## Known limitations (documented, not drift)

- `deslop`, `control-ui`, `control-cli` are named by playbooks but ship
  outside this package; a verdict without its demanded live lane is
  incomplete, not clean. (`no-comments`, `unslop`, `technical-writing` ship.)
- `statechart` installs TLC and Java with `mise` on its first run (network;
  the TLC jar is checksum-pinned), and its renderer guide installs the npm
  package `pele` outside the repo. Both happen only when the skill runs.
- `poteto-help` links upstream's `docs/guide/` pages, which this package does
  not ship; `/visualize` (named by `ketchup`) is a harness feature, not a
  pstack skill.
- No `mcp.json` shipped: research skills use whichever MCP servers the
  session configures; unreachable sources are recorded as null findings,
  never invented.

---

Adapted for Hermes Agent plugin compatibility from github.com/cursor/plugins pstack.
