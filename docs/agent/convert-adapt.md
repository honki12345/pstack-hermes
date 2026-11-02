# Convert, adapt, verify

Single home for changing the port. Layout paths:
[layout.md](layout.md). Re-pin: [upstream-drift.md](upstream-drift.md).

## Work loop

```sh
uv sync
# 1. Edit tools/, tools/assets/, docs/, …
# 2. Rebuild (default --out = repo root):
uv run --frozen tools/convert.py --source <pstack-clone>
# 3. Must exit 0:
uv run --frozen tools/validate.py
uv run --frozen pytest -q
uv run --frozen ruff check tools
uv run --frozen python tools/check_docs.py
```

Then:

1. Converter / maps / assets changed → adaptation issue linked to
   [#94](https://github.com/iap/pstack-hermes/issues/94) with a **reviewer
   check**; PR includes rebuilt package (`rebuilt from pin <sha>`).
2. Install-relevant → [Verify](#verify).
3. Re-pin only via [upstream-drift.md](upstream-drift.md).

Poteto checkers: see [CONTRIBUTING Setup](../../CONTRIBUTING.md#setup)
(`bun run test` / `typecheck` under `skills/poteto-mode/scripts`).

## Converter transform

1. Edit map/pass in `tools/convert.py` (`T8_MAP`, `DELEGATION_MAP`, `st.fixes`, …).
2. Rebuild — dead anchors fail on purpose.
3. Adaptation issue → [#94](https://github.com/iap/pstack-hermes/issues/94).
4. Checkable scope → unit test (`tools/tests/test_t11_scope.py` pattern).
5. Validate + suite; PR with `rebuilt from pin <sha>`.

| Want | Edit | Not |
|---|---|---|
| Model panel | `tools/assets/model-panel.json` | `config/models.json` |
| hermesbot | `tools/assets/hermesbot/SKILL.md` | `skills/hermesbot/` |

Panel slug shape / model-drift assumptions: weekly
`model-drift-watch` + `tools/slug_drift.py` (see CONTRIBUTING Actions policy).

## Hermes vocabulary

Speak **hermes** in converter output, docs, and tooling. Enforced by
`tools/bans.py` + `HERMES_ADAPTATION_BANS` in `validate.py`
([#94](https://github.com/iap/pstack-hermes/issues/94) §2).

| Use | Banned alias |
|---|---|
| `delegate_task` | `Task` / `subagent_type` / `run_in_background` / `environment: "cloud"` |
| `clarify` | `AskQuestion` |
| `session_search` | `agent-transcripts/*.jsonl` |
| `hermes cron` wake | terminal `/loop` as the mechanism |
| `goal.md` in agent store | armed Cursor `/goal` |
| `hermesbot` | `grokbot` / `make-bot-ui` / Tailscale bot |
| `skill_manage` (local/tap only) | Cursor `create-skill`; never stock plugin mutation |
| panel ← `tools/assets/model-panel.json` | hand-tuned shipped `config/models.json` |

## Verify

Prove the **built package** on hermes — not Cursor IDE dual-load.

1. Rebuild (when package-shaped) + `validate.py` — always.
2. Doctor: branch `hermes plugins doctor "$PWD" --ci`; installed
   `hermes plugins doctor pstack --ci` **outside** this repo.
3. Optional: load `poteto-mode` per [USING.md](../USING.md). Clean doctor +
   stock package failure → hermes harness issue, not a silent skill rewrite.
4. Publish (CI): `master` maintains append-only `dist`; converted_at-only may
   skip; `source_commit` / converter hash changes still publish.

Convert is atomic and `SOURCE_DATE_EPOCH`-reproducible; unmatched transform
anchors fail the build (update or prune maps — never ignore). Validate runs
static → repo YAML → gold manifest → gold load, including the hermes
adaptation contract and model-panel consistency.
