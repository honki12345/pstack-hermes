# AGENTS.md

> [!IMPORTANT]
> This repository is **pstack-hermes** — the unofficial community port of
> Cursor's *pstack* agent method to the **Hermes Agent** platform
> (`NousResearch/hermes-agent`). The runtime target is **hermes**. Cursor is
> upstream attribution only — never the implementation or verification path.
> Independently maintained by honki12345 as `honki12345/pstack-hermes`;
> origins and attribution are in [NOTICE.md](NOTICE.md).

This file is the always-on contract: read it, then open **one** row of the
router. Humans contributing start at [CONTRIBUTING.md](CONTRIBUTING.md); end
users at [docs/USING.md](docs/USING.md).

## The one rule

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
- Parallelize independent reads/triage/verification with `delegate_task`
  workers; keep one writer per file, and never parallelize rebase, rebuild, or
  push. Details: [docs/agent/dev-ops.md](docs/agent/dev-ops.md).

## Do not

- Hand-edit a generated surface (root `skills/`, `config/`, `assets/`,
  `README.md`, `plugin.json`, `LICENSE`, `.build-provenance.txt`). Edit `tools/assets/` or
  the converter template, then rebuild.
- Rewrite hermes primitives back into Cursor vocabulary (`delegate_task`,
  `clarify`, `session_search`, `hermes cron`, `hermesbot`). The bans in
  `tools/bans.py` and `HERMES_ADAPTATION_BANS` catch this.
- Point hermes `skill_manage`, `/learn`, or background learning at **stock**
  plugin skills. Personal procedure → local skills, skill tap, or a fork.
- Implement GitLab (`glab`), or treat missing Graphite as a hard failure while
  GitHub fallback can satisfy the task.
- "Clean up" attribution, or re-add the excluded `benny` / `make-bot-ui`
  slots — that is the scanner / publisher contract.

## Router

| If you need to… | Read |
|---|---|
| Know which paths are generated vs hand-written | [docs/agent/layout.md](docs/agent/layout.md) |
| Change converter maps/assets/bans, rebuild, verify | [docs/agent/convert-adapt.md](docs/agent/convert-adapt.md) |
| Re-pin upstream / resolve dead anchors | [docs/agent/upstream-drift.md](docs/agent/upstream-drift.md) |
| Scope and parallel-work discipline | [docs/agent/dev-ops.md](docs/agent/dev-ops.md) |
| Fork audit and differences from the original port | [docs/fork/AUDIT-ko.md](docs/fork/AUDIT-ko.md) |
| Setup, CI/Actions policy, PR, release | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Invoke or configure the installed package | [docs/USING.md](docs/USING.md) |

**Scale rule:** one owner per fact. A new deep-ops topic gets a router row plus
a focused file under `docs/agent/`. Do not grow this file.

## Verify

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
`hermes plugins doctor`:

- branch-local tree, from the repo root: `hermes plugins doctor "$PWD" --ci`
- installed package, from outside the repo: `hermes plugins doctor pstack --ci`

Poteto-mode checkers (CI runs these):

```sh
cd skills/poteto-mode/scripts
bun run test        # deps + bun test orch watch-pr
bun run typecheck   # deps + tsc --noEmit --strict
```

## Product policy (do not invert)

| Area | Decision |
|---|---|
| Stack provider | Graphite (`gt`) default when available; GitHub (`gh`) fallback; `local` (git-native) explicit only; GitLab (`glab`) halted |
| Package identity | Plugin name `pstack`; repository `honki12345/pstack-hermes`, installed from the repo root (pin with `--ref <sha>`); upstream version follows the pin (`plugin.json`); update mechanics live in the package README |
| Upstream | Lauren Tan's pstack in `cursor/plugins`, tracked through `tools/convert.py` at `UPSTREAM_PIN`; `iap/pstack-hermes` is attribution only ([NOTICE.md](NOTICE.md)) |
| Releases | Our own `v*` tags on `main`, recorded in `CHANGELOG.md`; never move an existing tag |
| Planning | GitHub issues, native sub-issues and milestones own plans; `docs/` holds instructions only |
| Encoding | LF-only, no BOM, UTF-8 (CI-enforced) |
| Branches | Default `develop`; flow `feature → develop → main` |

## Where to edit

| Path | Kind |
|---|---|
| `tools/`, `tools/assets/` | hand-written source of package truth |
| `docs/`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `NOTICE.md`, `CHANGELOG.md` | hand-written docs |
| `README.md` template in `tools/convert.py` | hand-written source; `README.md` itself is generated |
| `.github/` | hand-written CI; `UPSTREAM_PIN` stays in lock-step across the declarers listed by `tools/check_pins.py`; action pins are bumped by dependabot, not by hand |
| `plugin.json`, `skills/`, `config/`, `assets/`, `README.md`, `LICENSE`, `.build-provenance.txt` | generated — never hand-edit |
| `.venv/`, `.pytest-tmp/`, `.validate-data/`, `pstack.tmp-build/` | local scratch; never commit |
