# Contributing

Thanks for helping port and maintain pstack on hermes. The repo is small and
the contract is strict — read this once and the CI will never surprise you.

> [!NOTE]
> Coding agents work from [AGENTS.md](AGENTS.md) (change triage, hermes work
> loop, vocabulary bans). Humans start here. Both describe the same contract;
> that file carries triage and verification orientation; this file carries
> procedures (setup, common tasks, PRs, release).

## Orientation

| Read | For |
|---|---|
| [AGENTS.md](AGENTS.md) | triage (this repo vs hermes vs upstream), work loop, hermes verify, vocabulary |
| [README.md](README.md) | project identity, install, verification summary, naming table |
| [NOTICE.md](NOTICE.md) | where this fork comes from and who maintains it |
| [docs/fork/AUDIT-ko.md](docs/fork/AUDIT-ko.md) | fork audit: what changed versus the original port, and why |

One-sentence model of the repo: **`tools/convert.py` generates the plugin package
from the upstream source; `tools/validate.py` and CI keep that output honest;
everything hand-written exists to serve that pipeline.**

## Setup

```sh
uv sync                                   # pinned CPython 3.11 + dev group (pyyaml, pytest, ruff)
uv run --frozen tools/validate.py         # must exit 0 before you start
uv run --frozen pytest -q                 # unit tests for the tooling
uv run --frozen ruff check tools hermes-plugin  # lint
uv run --frozen python tools/check_docs.py  # docs drift gate
uv run --frozen actionlint                # workflow lint
```

Python is pinned via `.python-version` and `uv.lock`; never commit interpreter
or dependency drift.

The poteto-mode checker scripts additionally need
[Bun](https://bun.sh) (CI runs the same suite):

```sh
cd skills/poteto-mode/scripts
bun run test                              # deps + `bun test watch-pr check-plan.test.ts`
bun run typecheck                         # deps + tsc --noEmit --strict
```

## The pipeline

1. **Convert** — `tools/convert.py` rebuilds the package at the repository
   root from any pstack clone (or a fresh upstream clone at the pinned SHA).
   Builds are byte-reproducible under `SOURCE_DATE_EPOCH`; out-of-tree builds
   stage in a temp sibling, but `--out` at the repo root rewrites entries in
   place (see [rollback](docs/agent/upstream-drift.md#rollback)). Every transform anchor in the
   T8/T9/T10/T11/delegation maps is audited: an anchor that matches nothing in
   the upstream build fails the build loudly (upstream drift must be resolved
   by updating or pruning the map, never ignored).
2. **Validate** — `tools/validate.py` runs the four-stage ladder (static →
   repo YAML → gold manifest → gold load). The static stage includes the
   **hermes adaptation contract** (no inert `persona:` blocks, no stale
   runtime claims, no Cursor tool names, no transcript paths, no cloud-worker
   wording, no Cursor-style auto selectors in shipped skills), the
   banned-construct scan, and the model-panel checks (panel == shipped
   `config/models.json`, provider-qualified slugs, per-role value shapes,
   no duplicate concrete slugs inside an array role). The expected skill
   count is **derived from the package**, not hardcoded — structural changes
   flow through automatically. Dependency installs (`node_modules/`) created
   by script verification are exempt from the scans.
3. **Doctor** — `hermes plugins doctor` for install-relevant changes
   (maintainer machine; needs the hermes venv). Select the target explicitly:
   from the repo root pass the branch tree (`hermes plugins doctor "$PWD"
   --ci`); for the installed package run `hermes plugins doctor pstack
   --ci` from outside the repo. The bare name is ambiguous when run inside the
   checkout.
4. **Ship** — the committed repository root *is* the install: users run
   `hermes plugins install honki12345/pstack-hermes`, optionally pinned with
   `--ref <sha>`. There is no separate publish branch; CI proves the committed
   package is byte-identical to a fresh build from the pin.

## Repository contract

- Branch flow is `feature → develop → main`. `develop` is the default branch
  and the target for feature PRs; `main` receives promotions from `develop`
  and carries release tags. Never rewrite published history on either.

- `plugin.json`, `skills/`, `config/`, `assets/`, `README.md` and
  `.build-provenance.txt` are **generated content** at the repository root —
  never hand-edit; change the converter and rebuild. Provenance
  (`.build-provenance.txt`) must record the pinned upstream SHA at all times.
- **Banned constructs** are defined once in `tools/bans.py` (6 delegation tokens: `subagent_type`, `generalPurpose`, `AskQuestion`, `` `Task` ``, `run_in_background`, `environment: "cloud"`, plus 3 security needles: `tailscale.com/install.sh`, `FOR_AGENTS.md`, `http://127.0.0.1:4173`) and the hermes adaptation bans in `tools/validate.py`. Enforced by `tools/validate.py` and the CI scanner gate (`tools/scanner_gate.py`). Inline legacy model slugs in skill prose (e.g. a superseded default kept as fallback documentation) are intentional fallback documentation, not banned.
- Encoding: LF-only, no BOM (`.gitattributes` enforces; validate.py fails on
  drift).
- Attribution: upstream MIT (© Lauren Tan) is preserved everywhere; new port
  work is © the pstack-hermes-port contributors. Fork origins are recorded in
  [NOTICE.md](NOTICE.md).
- No patches to Hermes itself are kept or shipped here. A Hermes defect is
  reported to `NousResearch/hermes-agent`, never worked around by patching
  its scanner or loader.

## GitHub Actions & workflow policy

The `.github/` tree is hand-written and gated; these rules exist because each
one has already caught (or would have caught) a real defect here.

> [!IMPORTANT]
> **The real-loader gate has its own workflow.** `validate-gold.yml` pins
> `hermes-agent` by commit SHA, builds its environment, and runs
> `tools/validate.py --require-gold`, which exits 3 rather than reporting a green
> package when the required loader checks did not complete. It stays out of
> `ci.yml` on purpose: the network install should not be paid for by the 2-OS
> build matrix. Promote it to a required check in the default-branch ruleset
> only after it has been green across unrelated PRs.

- **Every action is pinned to a full commit SHA** with a trailing `# vN`
  comment. `dependabot.yml` keeps them current: the weekly `github-actions`
  group PR updates both the SHA and the comment. Review SHA bumps like any
  dependency change - the usual CI gates run on the PR. The root entry does
  **not** cover `.github/actions/*/action.yml`, so each composite action with
  its own `uses:` needs a directory-scoped entry there.
- **Keep the repository Actions allowlist closed.** Settings -> Actions should
  permit GitHub-owned actions plus the `astral-sh/setup-uv@*` and `oven-sh/setup-bun@*`
  patterns only, with `sha_pinning_required` on and a read-only default
  `GITHUB_TOKEN` (the policy lives in repository settings, not in this tree).
  A new third-party action fails at `Set up job` with "not allowed" until its
  `owner/repo` is added there - deliberately, so introducing an action is a
  decision rather than an accident. Dependabot keeps already-allowed pins
  current as usual.
- **`UPSTREAM_PIN` moves in lock-step.** It is declared in `ci.yml`;
  `tools/check_pins.py` fails CI when a declaration is missing, duplicated,
  malformed, or out of step. Follow
  [docs/agent/upstream-drift.md](docs/agent/upstream-drift.md).
- **`HERMES_PIN` is the hermes-agent commit pinned by `validate-gold.yml`.**
  It is a full lowercase 40-char SHA, declared exactly once, and moved
  deliberately like `UPSTREAM_PIN` (treat a bump as an upstream re-pin).
- **Workflows must pass `actionlint`** (with `shellcheck`) - the `Lint
  workflows` CI step, runnable locally as `uv run --frozen actionlint`.
- **Action inputs must be declared by the action they target.** `tools/`
  `check_action_inputs.py` fails on a `with:` key the pinned action does not
  declare - the runner only warns and silently ignores such a key (the
  `syncLabels` / `sync-labels` defect), and actionlint cannot see it because it
  does not read remote action metadata.
- **Composite actions must pass `tools/check_action_shell.py`.** actionlint
  does not lint `.github/actions/*/action.yml` steps, so that file's run
  blocks get their own shellcheck pass in CI. If the gate flags something,
  fix it or add a targeted `# shellcheck disable=` with a comment saying why.
- **Reuse: composite action when callers continue afterwards; reusable
  workflow only when the shared thing is a whole job.** The rationale is
  documented in `.github/actions/build-package/action.yml`.
  Shared multi-step logic (the build prologue) lives in `.github/actions/`,
  and no workflow re-inlines it.
- **Minimal `permissions:` at workflow level**, widened only per job where a
  step needs it. Checkouts that never push set `persist-credentials: false`.
- **Every job sets `timeout-minutes`.** A hung step otherwise burns the
  6-hour default before anyone notices.
- **No `pull_request_target`.** Every PR workflow uses `pull_request`, so PR
  code never runs with a write-capable token.

## Common tasks

### Add or change a converter transform

1. Edit the map or pass in `tools/convert.py` (maps like `T8_MAP`,
   `DELEGATION_MAP`; passes register in `st.fixes`).
2. Rebuild: uv run --frozen tools/convert.py --source <pstack-clone>
   — a dead anchor fails here on purpose.
3. Create an adaptation issue recording the pass, scope, source of truth and
   **reviewer check** (see [docs/fork/AUDIT-ko.md](docs/fork/AUDIT-ko.md) for
   the current fork-level record).
4. If the pass has a checkable scope, add a unit test
   (`tools/tests/test_t11_scope.py` is the pattern).
5. `uv run --frozen tools/validate.py` and the full local suite, then PR with
   the rebuilt tree and `rebuilt from pin <sha>` in the body.

### Re-pin upstream

Follow [docs/agent/upstream-drift.md](docs/agent/upstream-drift.md)
verbatim. `UPSTREAM_PIN` is declared in `ci.yml`, which passes it to the
`.github/actions/build-package` composite action. Update maps, never the
package; `tools/check_pins.py` fails CI if a declaration is missing or out of
step.

### Change the model panel

Edit `tools/assets/model-panel.json` (never the shipped
`config/models.json` — Stage-D copies it in and `validate.py` checks
they match). Rebuild, then confirm the panel still holds: slugs are
provider-qualified `vendor/model[:variant]`, array roles hold no duplicate
concrete slugs, and per-role value shapes (string vs array) match the
converter's expectations. `uv run --frozen tools/slug_drift.py` re-checks every
configured slug against the live catalog by hand (`--prose` also reports slug
mentions in skill text).

### Update hermesbot

Edit `tools/assets/hermesbot/SKILL.md` — the shipped
`skills/hermesbot/` copy is converter output. Rebuild and validate.

### Release

1. Promote `develop` to `main` (PR), including the `CHANGELOG.md` section for
   the tooling version. The package version stays upstream's.
2. Tag the `main` merge commit with an annotated `vX.Y.Z` tag and push it.
   Users pin to the tag's commit with `hermes plugins install
   honki12345/pstack-hermes --ref <sha> --enable`.
3. Never move or delete an existing release tag.

### Plan and adaptation tracking

Create issues for proposals, implementation plans and adaptation decisions.
Each includes motivation, scope, acceptance criteria and reviewer checks.
Split independent work into native sub-issues and use milestones for outcomes.
A converter PR links its adaptation issue and includes the generated package.
Do not keep parallel plans or adaptation ledgers in `docs/`; the one fork-level
audit record is [docs/fork/AUDIT-ko.md](docs/fork/AUDIT-ko.md). While issues
are disabled on this fork, plans and the backlog live only in
[docs/fork/BACKLOG-ko.md](docs/fork/BACKLOG-ko.md) and move to issues once
issues are enabled.

## Pull requests

The [PR template](.github/PULL_REQUEST_TEMPLATE.md) requires: convert clean, validate
exit 0, doctor exit 0 (install-relevant), banned-construct scan, the
poteto-mode Bun suite (`bun run test` / `bun run typecheck`), provenance
consistency. A PR that changes a generated surface (`skills/`, `config/`,
`assets/`, `README.md`, `LICENSE`, `plugin.json`) must say `rebuilt from pin <sha>` in its
body and name the converter change behind the rebuild — that tree is build
output, and reviewers read it as output. The [PR template](.github/PULL_REQUEST_TEMPLATE.md)
carries the matching "Generated package" fields. CI runs all of it plus the
pinned-SHA re-check, unit tests and lint. Upstream drift is caught by the
anchor audit and the reproducibility gate, not a scheduled watcher (see
[docs/agent/upstream-drift.md](docs/agent/upstream-drift.md)). Feature PRs
target `develop`.

## Issue reports

Use the templates — the bug form requires the affected skill or surface and how
the plugin is installed; `hermes plugins doctor pstack --ci` output is the most
useful evidence you can attach.

## Document style

GitHub renders **alert callouts** in markdown, so use them in this repo's
hand-written docs (`CONTRIBUTING.md`, `README.md`, `SECURITY.md`, the PR
template, issue bodies). They carry more weight than a plain `> Note.` block.

```
> [!NOTE]
> Required context, or a non-obvious "yes, this is intended".

> [!IMPORTANT]
> A decision or constraint that changes what a correct change looks like.

> [!WARNING]
> Something that will silently break or mislead if skipped.

> [!TIP]
> A shortcut worth knowing, not required for correctness.
```

- One callout per point. Don't stack them; write the prose instead.
- `[!CAUTION]` is for *destructive or irreversible* steps — use it sparingly,
  and only where a reader could actually hit the action.
- Keep the first line short; the body carries the detail. GitHub truncates
  callouts to a preview in some views.

### Where alerts do **not** apply

- **Commit messages.** GitHub does not render markdown (including alerts) in
  commit messages — they stay plain text. Say it in the subject line or the
  body as normal prose. The `type(scope): summary` convention already carries
  the what/why; add a second line for the why if it doesn't fit.
- **The generated package.** `skills/`, `config/`, `assets/`, `README.md`,
  `LICENSE` and
  `plugin.json` are rebuilt by `tools/convert.py` from upstream and are never
  hand-edited. Don't add a
  callout convention there as a hand-edit rule — it would be wiped on the next
  build. If alerts belong in package prose, that's a converter change (and an
  upstream-drift consideration), not a CONTRIBUTING note.
