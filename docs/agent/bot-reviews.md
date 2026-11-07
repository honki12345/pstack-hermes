# Bot reviews (SSOT)

Single source of truth for the Qodo + CodeRabbit review gate on `iap/pstack-hermes`.
Operational command reference (GraphQL/REST, thread enumeration, SHA checks) lives in
[`docs/agent/review-ops.md`](review-ops.md); this file owns the **flow, gate, and policy**.
`CONTRIBUTING.md` § "Bot review gate (pre-merge mandate)" is the contributor-facing
short form and must stay consistent with this file.

## Bot order

1. **Qodo [bot]** reviews the open PR automatically (on open / reopen / ready-for-review).
   Do **not** trigger Qodo manually. Triage its findings first; do not wait for a fresh
   Qodo verdict after a fix push — Qodo re-reviews are a *reply trigger*, not a push
   trigger: a re-review happens when a follow-up reply addresses Qodo
   (`@qodo …` in-thread, e.g. `@qodo I pushed fixes, can you review again?`), and
   the re-review can carry **new** findings. Only a follow-up explicitly asking
   for a re-review (e.g. `@qodo fixed:` with a re-review request) opens the
   bounded wait for it as for the initial review (ledger it and proceed if it
   never arrives); dismissal replies (`@qodo false positive:` / `intentional:` /
   `rejected:`) never start a wait. At most two re-review cycles per head SHA;
   a further re-review request on that head escalates to the maintainer. Triage
   whatever it raises the same way (same resolve-all gate) **before** requesting
   CodeRabbit — the CodeRabbit request follows the Qodo triage, never precedes it.
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

Two distinct gates — do not confuse them:

- **Trigger gate (start the CodeRabbit review):** Qodo *triaged* — every Qodo finding
  is fixed or answered with evidence and dismissal triggers posted. Bot-1 resolved UI
  state is **not** a precondition (cross-repo-conflict dismissals stay open by design —
  see resolution mechanics).
- **Merge gate (ship):** ALL findings from both bots resolved — Bot 1 *and* Bot 2
  threads closed via their own triggers, CodeRabbit clean on the current head, CI
  green, local gates pass. A Qodo cross-repo-conflict dismissal is *answered* but
  stays open by Qodo's design; GitHub resolution (the maintainer's button /
  `resolveReviewThread`) closes that thread, satisfying the gate — until the
  maintainer resolves it, it stays an explicit open ledger item. If GitHub-side
  resolution is unavailable, escalate as an issue; do not merge around it.

## SHA-bound verdicts

A CodeRabbit verdict on an older commit is not clean. Compare `commit.oid` with
`headRefOid` before believing it. Qodo's open-PR review is recorded at its own
commit and is never treated as a current-head verdict.

## "No verdict" states (CodeRabbit)

- `skipping` — no verdict. If a request is posted and then skipped, first
  verify the cause from the request response and check-run lifecycle: a skip after
  a request does not by itself prove rate limiting (seat or installation problems
  also skip). Then recover once: for rate limiting, retry the appropriate review
  command after about one hour — then it is a fresh request on the same head, not
  a loop; for seat or installation problems, retry once the cause is fixed.
  Exactly one retry per head SHA; a second skip escalates to the maintainer.
- `in_progress` that never moves — **paused**, not slow. After a run of new commits
  CodeRabbit posts a "Reviews paused" note and stops. Send `@coderabbitai resume`,
  then `@coderabbitai review`. A paused review never times out. Prefer fewer, larger
  fix pushes over one commit per finding. One `resume` + `review` pair per head SHA;
  a second consecutive pause on that same head SHA escalates to the maintainer,
  not another trigger.

One request per head SHA. Do not re-send because a verdict is slow. The
single exception: a check that is *paused* (its in-progress run never moves
while CodeRabbit posts a "Reviews paused" note) is recovered by ONE
`@coderabbitai resume` + `@coderabbitai review` pair per head SHA; a second
consecutive pause on that same head SHA escalates to the maintainer instead of
another trigger.

## Round loop

1. Open PR; wait for Qodo's initial automatic review. Bounded wait only (poll with
   backoff, a few minutes to ~15–20 min): if it never arrives, ledger `Qodo: no review
   yet` and proceed — no findings means the gate is unaffected. Never wait
   indefinitely, and never trigger Qodo to force a review.
2. Triage every Qodo finding (matrix below). Fix valid ones; reply on the rest.
   Only a follow-up explicitly asking for a re-review opens the bounded wait
   (same as step 1); dismissal replies never do. Wait for a re-review that was
   asked for, triage whatever it raises before step 3. At most two re-review
   cycles per head SHA; a further re-review request on that head escalates.
3. Request CodeRabbit on the current head (`full review` first pass / rebase /
   force-push / large diff; `review` for small fix push). Confirm `commit.oid == headRefOid`.
4. Triage CodeRabbit. Every fix push requires a new CodeRabbit request. Qodo does
   not re-run automatically (only on `@qodo`-addressed replies). Return to step 2
   to triage any new Qodo findings before step 3. Do not repeat a bounded wait
   already completed for a re-review.
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

## Thread resolution mechanics (why a reply alone is not enough)

Bots do **not** auto-resolve on a plain evidence reply. Each bot has its own
trigger; missing it leaves the thread open, which blocks the merge gate.

| Bot | To dismiss a finding | Trigger | Auto-resolves thread? |
|---|---|---|---|
| Qodo | Address it explicitly in plain language: `@qodo <reason>` (e.g. `@qodo false positive: ...`, `@qodo this is intentional: ...`) | Plain language at the start of the reply | Yes — Qodo classifies the dismissal (chat labels observed 2026-11-05 from Qodo's chat doc: False positive / Rejected / Deferred / Intentional; portal may show Fix later / Not specified), strikethroughs the finding, and, for ordinary findings, resolves + collapses the thread. Exception: when a cross-repo conflict is dismissed because its relationship is wrong or outdated, Qodo keeps the thread open so the relationship link remains visible. If no label is accepted, rephrase in plain language; escalate as open issue if still unresolved |
| CodeRabbit | Reply with evidence, then post `@coderabbitai resolve` as a **whole comment** (no other text) — as a reply in the thread it resolves only that thread; as a top-level PR comment it resolves **all** CodeRabbit threads on the PR | `@coderabbitai resolve` (whole comment) | Yes — per-thread when posted as a reply; all threads at once when posted top-level. `@coderabbitai approve` resolves all + submits approval only when automatic approvals are enabled (otherwise resolves threads without approving). Both must be whole comments |
| GitHub (outdated) | When a push moves the lines, GitHub marks the thread `outdated` | "Resolve conversation" button / `resolvePullRequestReviewThread` GraphQL | Manual (maintainer or bot) |

Rules:
- An untagged reply (no `@qodo` / no `@coderabbitai resolve`) is treated as
  reviewer discussion: the bot does **not** act and the thread stays open. This is
  the #1 reason "replying in the bot chat didn't work".
- For CodeRabbit, `@coderabbitai resolve` must be the **entire comment** (no other
  text). Posted as a reply in a CodeRabbit review thread it resolves **only that
  thread**; posted as a top-level PR comment it resolves **all** CodeRabbit
  threads on the PR at once. The "whole comment" rule is the trigger — mixing it
  with evidence text makes CodeRabbit treat the whole thing as chat and skip the
  resolve. Use it only after every CodeRabbit finding is fixed or answered.
- For Qodo, address it in **every** follow-up message in the thread, including
  re-reviews (`@qodo I pushed fixes, can you review again?`). A re-review can
  raise new findings — triage them the same way, in the same resolve-all gate.
- Outdated threads (lines changed by a later commit) no longer describe current
  code. A commit mismatch identifies staleness but is not a prerequisite for
  confirming whether the finding applies — compare the finding against the code at
  `headRefOid`. Resolve them only after confirming on the thread that the finding
  does **not** apply to the current head; then use the "Resolve conversation"
  button / `resolveReviewThread`. Do not resolve a substantive finding this way,
  and do not resolve merely because the lines moved.

## Cross-check before merge (anti-double-reply)

Before merging, verify each finding has **exactly one** in-thread reply with
evidence. Common failure modes:

1. **Double reply** — evidence posted both in-thread and as a standalone PR
   comment. Keep evidence in the thread only; delete/retract the standalone one.
2. **Untagged reply** — reply posted without `@qodo`/`@coderabbitai resolve`;
   bot never acted; thread still open. Fix by re-posting the trigger correctly.
3. **Stale verdict** — bot's reviewed commit != `headRefOid`; re-request on
   the current head.
4. **Outdated but unresolved** — thread marked outdated; resolve via the button
   (above) so it stops blocking the gate.

If a bot still does not mark a thread resolved after the correct trigger is used
(verified against the docs above), treat it as an open research item: capture the
PR/thread URL, the exact trigger used, and the bot's response (or silence), and
file an issue for deep research rather than merging around it.

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
| Clean up / modernize attribution or re-add excluded `benny` / `make-bot-ui` | Leave as-is (scanner / publisher contract) |
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
