# Bot reviews (SSOT)

Single source of truth for the Qodo + CodeRabbit review gate on `iap/pstack-hermes`.
Operational command reference (GraphQL/REST, thread enumeration, SHA checks) lives in
[`docs/agent/review-ops.md`](review-ops.md); this file owns the **flow, gate, and policy**.
`CONTRIBUTING.md` § "Bot review gate (pre-merge mandate)" is the contributor-facing
short form and must stay consistent with this file.

## Bot order

1. **Qodo [bot]** reviews the open PR automatically (on open / reopen / ready-for-review).
   Do **not** trigger Qodo manually. Triage its findings first; do not wait for a fresh
   Qodo verdict after a fix push.
2. **CodeRabbit** is manual (`.coderabbit.yaml` sets `auto_review.enabled: false`).
   Trigger `@coderabbitai full review` only after Qodo findings are triaged
   (fixed or answered with evidence) on this head.
   Use `@coderabbitai review` for a small fix push within the same round.

## Gate (merge requires ALL)

- CodeRabbit clean on the **current head commit** (`commit.oid == headRefOid`).
- Every Qodo finding fixed or answered (with evidence).
- Every review thread resolved.
- CI green + local gates pass.
- CodeRabbit check is **not** `skipping` and not `pending`.

Green CI alone is insufficient. `skipping` = never requested (not clean). `pending` = not clean.

## SHA-bound verdicts

A CodeRabbit verdict on an older commit is not clean. Compare `commit.oid` with
`headRefOid` before believing it. Qodo's open-PR review is recorded at its own
commit and is never treated as a current-head verdict.

## "No verdict" states (CodeRabbit)

- `skipping` — never requested.
- `in_progress` that never moves — **paused**, not slow. After a run of new commits
  CodeRabbit posts a "Reviews paused" note and stops. Send `@coderabbitai resume`,
  then `@coderabbitai review`. A paused review never times out. Prefer fewer, larger
  fix pushes over one commit per finding.

One request per head SHA. Do not re-send because a verdict is slow.

## Round loop

1. Open PR; wait for Qodo's initial automatic review.
2. Triage every Qodo finding (matrix below). Fix valid ones; reply on the rest.
   Do not wait for Qodo to re-review a later head.
3. Request CodeRabbit on the current head (`full review` first pass / rebase /
   force-push / large diff; `review` for small fix push). Confirm `commit.oid == headRefOid`.
4. Triage CodeRabbit. Every fix push requires a new CodeRabbit request; Qodo does
   not re-run. Return to step 3.
5. Merge only when all gate conditions hold.

## Triage matrix (adjudication)

| Situation | Action | Evidence in the reply |
|---|---|---|
| Valid, in scope | Minimal fix + test that fails before / passes after | commit SHA, `file:line`, test name, gate output |
| Valid, out of scope | Do not widen the PR; open an issue and link it | issue link + why out of scope |
| False positive | No code change; correct the bot | counter-evidence: code path, test, or contract quote |
| Already fixed | No code change | commit SHA + current diff hunk |
| Outdated hunk | Re-check against head; reply on current code | head SHA + current `file:line` |
| Duplicate | Point at the canonical thread | thread / comment link |
| Blocked / product decision | Stop and ask the maintainer | the conflict, the options, the contract clause |

A finding names a symptom; the "Recommended Fix" is advisory. Reproduce the symptom
first, then choose the smallest correct fix. Reject a recommendation that conflicts
with the contract (hand-editing generated output, reintroducing Cursor vocabulary,
adding a lint ignore) even when the symptom is real.

## Reply format

Reply in the bot's thread, not as a standalone post. Open with `<sha>: <verdict>`
(`accepted` / `rejected` / `already fixed` / `out of scope`), then mechanism and
evidence. Include: **What** (`file:line` + short quote), **Why** (contract/diff/
outdated hunk), **Evidence** (SHA, test, gate output, counterexample),
**Verification** (commands run; honest `not run` + cause if blocked).

Tone: no thanks, no emoji, no encouragement, no hedging. Address the claim, not
the author.

## Loop budget and escalation

- Three rounds per bot with no new information → stop and ask the maintainer.
- Contradictory demands (bot A wants X, bot B wants not-X) → the contract
  decides; escalate rather than oscillate.
- A bot re-raising a resolved root cause on a new SHA → reply with the prior SHA
  and fix; do not re-fix.
- Never merge to escape the loop.

## Common false pressures

| Bot nudge | Contract response |
|---|---|
| Hand-edit generated `skills/` / `config/` / package README | Rebuild from converter / assets instead |
| Reintroduce Cursor tool names | Banned — use Hermes vocabulary |
| Clean up / modernize `agents/` or re-add excluded `benny` / `make-bot-ui` | Leave as-is (scanner / dual-load contract) |
| Implement GitLab stack provider because docs mention `glab` | Halted |
| Treat missing Graphite as a hard CI/product failure | GitHub fallback is intentional |
| Mutate installed stock skills via learning / `skill_manage` | Forbidden as durable source of truth |
| Broaden a check or add `# noqa` | Fix the input, not the gate |
| Bump an action/dependency or reformat "while here" | Separate PR — keep the diff scoped |

## Qodo configuration

Qodo is app-configured (automatic on open/reopen/ready-for-review). Optional
repo-level Qodo config may live at `.pr_agent.toml` or `.qodo/pr-agent.toml`; this
repo does not ship one. If added, keep it to a one-line pointer in `CONTRIBUTING.md`
/ `AGENTS.md` and do not duplicate the flow here.

## Verification before merge (local)

`uv run --frozen ruff check tools` · `uv run --frozen pytest -q` ·
`uv run --frozen tools/validate.py` · `python tools/check_docs.py` ·
`python tools/bans.py` (via validate). See `CONTRIBUTING.md` and `AGENTS.md`.

## Related

- `docs/agent/review-ops.md` — operational command reference (GraphQL/REST,
  thread enumeration, SHA checks, paused-review recovery).
- `CONTRIBUTING.md` — contributor-facing short form.
- `AGENTS.md` — agent contract (attribution, commit hygiene).
