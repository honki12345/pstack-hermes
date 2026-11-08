# CLAUDE.md

> **Purpose:** Instructions for Claude Code agents working in this repository.
> Read this first, then open the [AGENTS.md](AGENTS.md) router for the full contract.

## Project identity

This is **pstack-hermes** — the unofficial community port of Cursor's *pstack* agent method to the **Hermes Agent** platform (`NousResearch/hermes-agent`). The runtime target is **hermes**. Cursor is upstream attribution only — never the implementation or verification path.

Independently maintained by honki12345 as `honki12345/pstack-hermes` (origins:
[NOTICE.md](NOTICE.md)). Package name: `pstack`. Plugin installed via
`hermes plugins install honki12345/pstack-hermes --enable`.

## The one rule (critical)

> [!WARNING]
> **The package is generated output and lives at the repository root.**
> `plugin.json`, `skills/`, `config/`, `assets/`, `README.md`, `LICENSE` and
> `.build-provenance.txt` are rebuilt from the pinned upstream clone by
> `tools/convert.py`. Hand edits are wiped on the next rebuild and fail the CI
> reproducibility gate. To change the package, change `tools/` or
> `tools/assets/`, rebuild, validate.

Everything below follows from that rule.

## Work discipline

- Keep the diff scoped: touch only files the task or finding requires;
  out-of-scope work becomes an issue or a separate PR.
- Parallelize independent reads/triage/verification; keep one writer per file,
  and never parallelize rebase, rebuild, or push.
- **Never hand-edit a generated surface** (`skills/`, `config/`, `assets/`,
  `plugin.json`, `README.md`, `LICENSE`). Edit `tools/` or `tools/assets/`,
  then rebuild with `uv run --frozen tools/convert.py` and validate.

## Do not

- Rewrite hermes primitives back into Cursor vocabulary
  (`delegate_task`, `clarify`, `session_search`, `hermes cron`,
  `hermesbot`). The bans in `tools/bans.py` and
  `HERMES_ADAPTATION_BANS` catch this.
- Point hermes `skill_manage`, `/learn`, or background learning at **stock**
  plugin skills. Personal procedure → local skills, skill tap, or a fork.
- Implement GitLab (`glab`), or treat missing Graphite as a hard failure while
  GitHub fallback can satisfy the task.
- "Clean up" attribution, or re-add the excluded `benny` / `make-bot-ui`
  slots — that is the scanner / publisher contract.

## Router

| If you need to… | Read |
|---|---|
| Know which paths are generated vs hand-written | [AGENTS.md](AGENTS.md) / [layout.md](docs/agent/layout.md) |
| Change converter maps/assets/bans, rebuild, verify | [docs/agent/convert-adapt.md](docs/agent/convert-adapt.md) |
| Re-pin upstream / resolve dead anchors | [docs/agent/upstream-drift.md](docs/agent/upstream-drift.md) |
| Scope and parallel-work discipline | [docs/agent/dev-ops.md](docs/agent/dev-ops.md) |
| Fork audit and differences from the original port | [docs/fork/AUDIT-ko.md](docs/fork/AUDIT-ko.md) |
| Setup, CI/Actions policy, PR, release | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Invoke or configure the installed package | [docs/USING.md](docs/USING.md) |

## Product policy (do not invert)

| Area | Decision |
|---|---|
| Package identity | Plugin name `pstack`; repository `honki12345/pstack-hermes`, installed from the repo root (pin with `--ref <sha>`); upstream version follows the pin (`plugin.json`) |
| Upstream | Lauren Tan's pstack in `cursor/plugins` via `tools/convert.py`; `iap/pstack-hermes` is attribution only ([NOTICE.md](NOTICE.md)) |
| Releases | Our own `v*` tags on `main`, recorded in `CHANGELOG.md`; never move an existing tag |
| Planning | GitHub issues, native sub-issues and milestones own plans; while issues are disabled on this fork, `docs/fork/BACKLOG-ko.md` holds the backlog; otherwise `docs/` holds instructions only |
| Encoding | LF-only, no BOM, UTF-8 (CI-enforced) |
| Branches | Default `develop`; flow `feature → develop → main` |

## Where to edit

| Path | Kind |
|---|---|
| `tools/`, `tools/assets/` | hand-written source of package truth |
| `docs/`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `NOTICE.md`, `CHANGELOG.md` | hand-written docs |
| `README.md` template in `tools/convert.py` | hand-written source; `README.md` itself is generated |
| `.github/` | hand-written CI; `UPSTREAM_PIN` stays in lock-step across declarers |
| `plugin.json`, `skills/`, `config/`, `assets/`, `README.md`, `LICENSE`, `.build-provenance.txt` | generated — never hand-edit |
| `.venv/`, `.pytest-tmp/`, `.validate-data/`, `pstack.tmp-build/` | local scratch; never commit |

## Hermes-specific vocabulary (must use, not Cursor)

This project ported Cursor's pstack to Hermes Agent. The following vocabulary
mappings are enforced by the converter and validation gates:

| Cursor term | Hermes equivalent |
|---|---|
| `delegate_task` (backtick) | `delegate_task` |
| `role: leaf` | `delegate_task` (role: `leaf`) |
| `clarify` | `clarify` |
| `hermes runtime` | `local execution` (or omit) |
| `background execution: true` | `background execution` |
| `/loop` | `hermes cron` wake |
| `/goal` | write `goal.md` in the agent store |
| `poteto-agent` | Do not override — respect what the skill prescribes |
| `readonly` (Ask mode) | No per-task `readonly` on Hermes (core refuses the key). `access: read-only` via the pstack delegation backend, a READ-ONLY `context` line, and a parent `git status` check; children keep MCP. See `skills/poteto-mode/references/hermes-delegation.md`. |

## Skill discovery and loading

- **58 skills** (34 workflow/mode + 24 `principle-*`), single-level
  `skills/<dir>/SKILL.md`, discovered via the root `plugin.json`
  (agent-plugins-v1 manifest, 9 whitelisted fields).
- Skills are opt-in on the portable path: load one with
  `skill_view` id `agent-plugin-pstack-<digest>:<skill-name>` (digest = first
  8 hex of the sha256 of the plugin key).
- For `/<name>` slash commands instead, add this package's `skills` dir to
  `skills.external_dirs` in the hermes `config.yaml` — trade-off: skills
  enter the prompt index with 60-char descriptions and lose the namespace.
- `skills/hermesbot/` — hermes-native control-surface skill on the gateway
  webhook (`X-Webhook-Signature-V2` HMAC) plus `hermes send` / `hermes peer`;
  fills the excluded `make-bot-ui` slot.
- Skill scripts (e.g. `skills/poteto-mode/scripts/`) ship verbatim; the loader
  never executes them, but skills instruct the agent to run them (`bun`, `gh`).
- Not shipped: `automations/benny/` and `skills/make-bot-ui/` (install-scanner
  verdicts — persistence / privilege escalation; `--force` cannot override).

## Critical adapter contract checks

The converter and validation gate enforce these hermes adaptation bans. If any
appear in skill prose or config, the build fails:

| Ban | Why it matters |
|---|---|
| `persona:` | delegate personas are inert in hermes portable plugins; inline the prompt |
| `Read, Grep, Glob` | Cursor tool names survived in hermes-facing prose — must be reworded |
| `<ABSOLUTE_PATH>` | Reflect reviewers must use a session id or digest, not a transcript path |
| `parallel cloud workers` | Delegate_task background work is process-local, not a cloud fleet |
| `cloud concurrency limit` | Hermes swarm sizing is worker count, not cloud concurrency |
| `inherit-parent` or `auto` | Hermes has no Cursor-style `auto` selector; the parent chat model IS the inherit-parent semantic |
| `~/.cursor/projects` | Cursor transcript path — hermes sessions live in SQLite; query via `session_search` |
| `~/.cursor/rules/pstack-models.mdc` | Panel written inside the plugin directory is lost on `plugins update`; write `pstack-models.json` beside hermes' `config.yaml` outside the package instead |

## Installation and first run

```sh
hermes plugins install honki12345/pstack-hermes --enable
```

1. `hermes plugins doctor pstack --ci` — manifest parses, all skills discover.
   Pick the target explicitly: `pstack` is ambiguous (repo root = local directory;
   elsewhere = installed package). Use the name as installed for the installed
   package, or a resolved local path for a branch tree.
2. Load the `setup-pstack` skill once - it writes `pstack-models.json` beside
   hermes' `config.yaml`, outside this package, so plugin updates do not discard it
   (the 18-role model panel the workflow skills read).
3. Load `poteto-mode` and give it a real task — it classifies the work and
   routes to the right playbook. You do not pick playbooks yourself.

## Customizing skills

- **Tweak a line or two** — edit the installed `SKILL.md`, restart the
  gateway, and save a backup outside the plugin before updating. Reinstalls
  replace shipped skills; in-place edits are temporary.
- **Own several changed skills** — fork `honki12345/pstack-hermes`, commit, install
  from your fork (`hermes plugins install <you>/pstack-hermes --enable`).
- **Add your own new skills alongside** — a skill tap:
  `hermes skills tap add <you>/skills-repo`, then
  `hermes skills install <you>/skills-repo/<skill>`.
- Keep the hermes vocabulary when editing (`delegate_task`, `clarify`,
  `session_search`, `hermes cron`); Cursor primitives will mislead the agent.

## Known limitations (documented, not drift)

- `deslop`, `control-ui`, `control-cli` are named by playbooks but ship
  outside this package; a verdict without its demanded live lane is
  incomplete, not clean. (`no-comments`, `unslop`, `technical-writing` ship.)
- `statechart` installs TLC and Java with `mise` on its first run (network;
  the TLC jar is checksum-pinned); its renderer guide installs npm `pele`
  outside the repo.
- No `mcp.json` shipped: research skills use whichever MCP servers the
  session configures; unreachable sources are recorded as null findings,
  never invented.

## Verification

```sh
uv sync
uv run --frozen tools/validate.py
uv run --frozen pytest -q
uv run --frozen ruff check tools hermes-plugin
uv run --frozen python tools/check_docs.py
uv run --frozen actionlint
```

Docs-only changes need no rebuild. Package-shaped changes rebuild from a clone
at the pinned SHA (`uv run --frozen tools/convert.py --source <pstack-clone>`,
default `--out` is the repo root) before the block above.

Install-relevant changes (manifest, skill discovery, package layout) also run
`hermes plugins doctor "$PWD" --ci`.
