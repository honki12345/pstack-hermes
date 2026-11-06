<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:42b6141d38bacc37685835b7e9b858945dfa08a0743f5c7fe61c096a1dd30784 -->

<!-- coderabbit-ai-generated-section:v1:ebc283da134d7475cca68f35bb3583ad31c7e109d1b7de7f8bf52001bc95c7a6 -->

<!-- coderabbit-ai-generated-section:v1:f07db69766104967cc27b575968211c722f833bde62f42f3048e74bdde0d0575 -->

<!-- coderabbit-ai-generated-section:v1:e35bc2f6b95555feb97dcf319b0170b52dcd6e40c6e8aa7b01ff69a5e847b288 -->

<!-- coderabbit-ai-generated-section:v1:4d11144e6dca943b2c50a4192621fb844dce6ac8084b9f6b30a976eb3574d2a9 -->

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
| [adaptation register](https://github.com/iap/pstack-hermes/issues/94) | the ledger: every conversion pass and its reviewer check |

| [docs/PATCHES.md](docs/PATCHES.md) | historical hermes-fork patches (reference only; not required to install) |

One-sentence model of the repo: **`tools/convert.py` generates the plugin package
from the upstream source; `tools/validate.py` and CI keep that output honest;
everything hand-written exists to serve that pipeline.**

## Setup

```sh
uv sync                                   # pinned CPython 3.11 + dev group (pyyaml, pytest, ruff)
uv run --frozen tools/validate.py         # must exit 0 before you start
uv run --frozen pytest -q                 # unit tests for the tooling
uv run --frozen ruff check tools          # lint
uv run --frozen python tools/check_docs.py  # docs drift gate
```

Python is pinned via `.python-version` and `uv.lock`; never commit interpreter
or dependency drift.

The poteto-mode checker scripts additionally need
[Bun](https://bun.sh) (CI runs the same suite):

```sh
cd skills/poteto-mode/scripts
bun run test                              # deps + `bun test orch watch-pr`
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
   --ci`); for the installed dist package run `hermes plugins doctor pstack
   --ci` from outside the repo. The bare name is ambiguous when run inside the
   checkout.
4. **Publish** — pushes to `master` build and validate the append-only
   `dist` compatibility channel. Only converted_at-only changes are skipped; changes to source_commit or the converter hash still publish.
   Existing unpinned installs can continue fast-forward updates.
   `master` is the intended default branch; drift schedules, labeler and
   Dependabot run directly there. No default-branch scheduler shim is needed.

## Repository contract

- Target `master` for development PRs; keep `dist` append-only for installed
  clones (history is never rewritten, even though each publish replaces the
  tree).

- `plugin.json`, `skills/`, `config/`, `assets/`, `README.md` and
  `.build-provenance.txt` are **generated content** at the repository root —
  never hand-edit; change the converter and rebuild. Provenance
  (`.build-provenance.txt`) must record the pinned upstream SHA at all times.
- **Banned constructs** are defined once in `tools/bans.py` (6 delegation tokens: `subagent_type`, `generalPurpose`, `AskQuestion`, `` `Task` ``, `run_in_background`, `environment: "cloud"`, plus 3 security needles: `tailscale.com/install.sh`, `FOR_AGENTS.md`, `http://127.0.0.1:4173`) and the hermes adaptation bans in `tools/validate.py`. Enforced by `tools/validate.py` and the CI scanner gate (`tools/scanner_gate.py`). Inline legacy model slugs in skill prose (e.g. a superseded default kept as fallback documentation) are intentional fallback documentation, not banned.
- Encoding: LF-only, no BOM (`.gitattributes` enforces; validate.py fails on
  drift).
- Attribution: upstream MIT (© Lauren Tan) is preserved everywhere; new port
  work is © the pstack-hermes-port contributors.
- Fork patches live in `patches/` with documentation in
  [docs/PATCHES.md](docs/PATCHES.md); the drafted upstream PR text is
  [historical proposal](https://github.com/iap/pstack-hermes/issues/95).

## GitHub Actions & workflow policy

The `.github/` tree is hand-written and gated; these rules exist because each
one has already caught (or would have caught) a real defect here.

> [!IMPORTANT]
> **The real-loader gate has its own workflow.** `validate-gold.yml` pins
> `hermes-agent` by commit SHA, builds its environment, and runs
> `tools/validate.py --require-gold`, which exits 3 rather than reporting a green
> package when the required loader checks did not complete. It stays out of
> `ci.yml` on purpose: the network install should not be paid for by the 2-OS
> build matrix. Promote it to a required check in the `Master` ruleset only after
> it has been green across unrelated PRs.

- **Every action is pinned to a full commit SHA** with a trailing `# vN`
  comment. `dependabot.yml` keeps them current: the weekly `github-actions`
  group PR updates both the SHA and the comment. Review SHA bumps like any
  dependency change - the usual CI gates run on the PR. The root entry does
  **not** cover `.github/actions/*/action.yml`, so each composite action with
  its own `uses:` needs a directory-scoped entry there.
- **The repository Actions allowlist is closed.** Settings -> Actions permits
  GitHub-owned actions plus the `astral-sh/setup-uv@*` and `oven-sh/setup-bun@*`
  patterns only, with `sha_pinning_required` on and a read-only default
  `GITHUB_TOKEN` (the policy lives in repository settings, not in this tree).
  A new third-party action fails at `Set up job` with "not allowed" until its
  `owner/repo` is added there - deliberately, so introducing an action is a
  decision rather than an accident. Dependabot keeps already-allowed pins
  current as usual.
- **`UPSTREAM_PIN` moves in lock-step.** It is declared in `ci.yml`,
  `release.yml` and `publish-dist.yml`; `tools/check_pins.py` fails CI when any
  of the three is missing, duplicated, malformed, or out of step. Follow
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
  fix it or add a targeted `# shellcheck disable=` with a comment saying why
  (see `drift-issue-upsert` for the jq `$ENV` case).
- **Reuse: composite action when callers continue afterwards; reusable
  workflow only when the shared thing is a whole job.** Both call sites and
  the rationale are documented in `.github/actions/build-package/action.yml`.
  Shared multi-step logic (build prologue, drift-issue upsert) lives in
  `.github/actions/`, and no workflow re-inlines it.
- **Minimal `permissions:` at workflow level**, widened only per job where a
  step needs it. Checkouts that never push set `persist-credentials: false`.
- **Every job sets `timeout-minutes`.** A hung step otherwise burns the
  6-hour default before anyone notices.
- **`pull_request_target` is allowed only for the labeler**, where no PR
  content is checked out or executed. Never add a PR-head checkout to that
  job; put such work in a `pull_request` workflow instead.

## Common tasks

### Add or change a converter transform

1. Edit the map or pass in `tools/convert.py` (maps like `T8_MAP`,
   `DELEGATION_MAP`; passes register in `st.fixes`).
2. Rebuild: uv run --frozen tools/convert.py --source <pstack-clone>
   — a dead anchor fails here on purpose.
3. Create an adaptation issue linked to the [preserved register](https://github.com/iap/pstack-hermes/issues/94),
   recording the pass, scope, source of truth and **reviewer check**.
4. If the pass has a checkable scope, add a unit test
   (`tools/tests/test_t11_scope.py` is the pattern).
5. `uv run --frozen tools/validate.py` and the full local suite, then PR with
   the rebuilt tree and `rebuilt from pin <sha>` in the body.

### Re-pin upstream

Follow [docs/agent/upstream-drift.md](docs/agent/upstream-drift.md)
verbatim. The one rule people forget: `UPSTREAM_PIN` is declared independently
in **three workflows** — `ci.yml`, `release.yml`, `publish-dist.yml` — and all
three must move in the same PR. Update maps,
never the package. (The build prologue itself now lives once, in the
`.github/actions/build-package` composite action, so `ci.yml`, `release.yml` and
`publish-dist.yml` pass the pin to it as an input rather than each repeating
the clone/verify/convert steps. `tools/check_pins.py` now enforces the
lock-step rule in CI, so a missed file fails fast instead of at release time.)

### Change the model panel

Edit `tools/assets/model-panel.json` (never the shipped
`config/models.json` — Stage-D copies it in and `validate.py` checks
they match). Rebuild, then confirm the weekly
[model-drift-watch](.github/workflows/model-drift-watch.yml) assumptions still
hold: slugs are provider-qualified `vendor/model[:variant]`, array roles hold
no duplicate concrete slugs, and per-role value shapes (string vs array)
match the converter's expectations. Prose-only slug mentions are
informational (`tools/slug_drift.py --prose`).

### Update hermesbot

Edit `tools/assets/hermesbot/SKILL.md` — the shipped
`skills/hermesbot/` copy is converter output. Rebuild and validate.

### Release

1. Merge the release changes to `master`, including the `CHANGELOG.md`
   section for the `v0.4.x` tooling version. Package version stays upstream's.
2. Wait for `publish-dist` to succeed. Create a `v*` tag on that source commit.
3. `release.yml` rebuilds and validates, checks that the package matches the
<!-- coderabbit-ai-generated:v1 -->
<!-- coderabbit-ai-generated-section:v1:9ad9535da30d31f95f6f7b2099158d95227e5463771f36b895a4222c9dcb07df -->
   published `dist` commit, including provenance except `converted_at`, and
   creates a GitHub Release with ZIP, SHA-256 checksum, source SHA and immutable
   package install command.
<!-- /coderabbit-ai-generated:v1 -->
   A lagging or different dist tree fails release creation; rerun after publish.
4. Close the milestone once all acceptance checks pass. Link issues and PRs
   in release notes. Never move an existing release tag.

### Plan and adaptation tracking

Create issues for proposals, implementation plans and adaptation decisions.
Each includes motivation, scope, acceptance criteria and reviewer checks.
Split independent work into native sub-issues and use milestones for outcomes.
A converter PR links its adaptation issue and includes the generated package;
retain the [existing contracts](https://github.com/iap/pstack-hermes/issues/94).
Do not keep parallel plans or adaptation ledgers in `docs/`.

## Pull requests

## Bot review gate (pre-merge mandate)

1. **Qodo [bot]** triggers automatically on PRs. Do not trigger Qodo manually. Triage Qodo findings first (fix or reply with evidence). If Qodo findings are triaged and no new issues exist on the same head, proceed to CodeRabbit without re-triggering Qodo.
2. **CodeRabbit** is not auto-review by default (to reduce noise and bot-to-bot conflict). Trigger `@coderabbitai full review` only after Qodo findings are triaged (fixed or answered with evidence) on this head. Request CodeRabbit again after every fix push: use `full review` after a rebase, force-push, or large diff, and `review` for a small fix push. Confirm the verdict's `commit.oid` equals `headRefOid`. See `docs/agent/bot-reviews.md`.
3. After CodeRabbit reviews, reply in-thread to all findings with evidence (fix or justify). Continue until CodeRabbit's CI flow/check completes successfully. The maintainer or bot resolves the threads.
4. **Merge only when CodeRabbit is clean on the current head commit**, every Qodo finding is triaged (fixed or answered with evidence), every thread is resolved, and CI + local gates pass. Green CI alone is insufficient. Treat "skipping"/"pending" as not clean.
5. Reply to findings with comments explaining what/why (not just resolving). Cross-check for false positives before applying fixes.

Qodo config: optional repo-level Qodo/PR-Agent config may live at `.pr_agent.toml` or `.qodo/pr-agent.toml`; this repo ships none. If added, keep it to a one-line pointer here and in `AGENTS.md`. See `docs/agent/bot-reviews.md`.


> [!TIP]
> **Draft content first.**
> Write draft messages/content in a separate location (local file, branch, or draft PR) before publishing or pushing to the public git provider. Keep it technical, short, and essential — only on-scope context relations. Cross-check and verify the content to ensure no false positive/negative on the post messages (good or bad for public and human/bot reviewer). If posting as an open/draft PR, add a note for the reviewer so they can read it clearly.

The [PR template](.github/PULL_REQUEST_TEMPLATE.md) requires: convert clean, validate
exit 0, doctor exit 0 (install-relevant), banned-construct scan, the
poteto-mode Bun suite (`bun run test` / `bun run typecheck`), provenance
consistency. A PR that changes a generated surface (`skills/`, `config/`,
`assets/`, `README.md`, `LICENSE`, `plugin.json`) must say `rebuilt from pin <sha>` in its
body and name the converter change behind the rebuild — that tree is build
output, and reviewers read it as output. The [PR template](.github/PULL_REQUEST_TEMPLATE.md)
carries the matching "Generated package" fields. CI runs all of it plus the pinned-SHA re-check, unit tests, lint,
and a weekly [model-drift-watch](.github/workflows/model-drift-watch.yml)
re-verifies every configured model slug against the provider catalog
(`tools/slug_drift.py`). Upstream drift is caught by the anchor audit and the
reproducibility gate, not a scheduled watcher (see
[docs/agent/upstream-drift.md](docs/agent/upstream-drift.md)). PRs are
auto-labeled by path (labels are created once via the `labels-bootstrap`
workflow after the repo goes public).

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


<!-- /coderabbit-ai-generated:v1 -->