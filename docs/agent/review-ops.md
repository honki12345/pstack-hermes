# Reviewer-bot threads

How to drive Qodo's open-PR review and CodeRabbit's **SHA-current** verdict to a
clean state before merge. The port contract outranks every bot.

## Non-negotiables

1. **Verify before changing.** A finding is a claim, not a fact. Reproduce it
   against the current head. Bots are sometimes stale, wrong about the symptom,
   or right about the symptom and wrong about the fix.
2. **Fix the defect, not the gate.** Never silence a check to go green: no new
   `# noqa`, no broadened regex, no deleted/skipped test, no loosened assertion.
3. **Never resolve threads yourself.** Reply in-thread with evidence; the
   maintainer (or the bot) resolves.
4. **CodeRabbit clean is SHA-bound.** A CodeRabbit verdict on an older commit
   is not clean; compare its commit with the current `headRefOid` before
   believing it. Qodo's open-PR review is recorded at its own commit and is not
   treated as a current-head verdict.
5. **One writer per file.** Delegated workers may read, triage, and verify in
   parallel; only the branch owner edits, rebases, and pushes.

## Bot order

- **Qodo reviews the open PR automatically.** It reviews when a PR is opened,
  reopened, or marked ready. Treat that as the Qodo review for the PR: triage its
  findings, but do not wait for a fresh Qodo verdict after a fix push and do not
  post `/agentic_review` in the automatic setup. A manual trigger is only for a
  confirmed manual-only configuration. CodeRabbit is the SHA-bound per-head gate
  below.
- **CodeRabbit is manual in this repo.** `.coderabbit.yaml` sets
  `auto_review.enabled: false`, so a push never starts it. Request
  `@coderabbitai full review` for the first pass and again after any rebase,
  force-push, or large diff; use `@coderabbitai review` only for a small fix
  push within the same round. A CodeRabbit check of `skipping` means **not
  requested**, never clean. If it does not respond, check CodeRabbit's
  documented commands rather than assuming it is clean.

  Two "no verdict" states look identical in the UI and are not:

  - `skipping` - never requested.
  - `in_progress` that never moves - **the review is paused**, not slow. After a
    run of new commits on one branch CodeRabbit posts a "Reviews paused" note and
    stops reviewing, while its check run stays live and idle. Six fix commits
    into a single round was enough to trigger it here. Send
    `@coderabbitai resume`, then `@coderabbitai review`; a paused review never
    times out, so waiting for it is not a strategy. The threshold is CodeRabbit's
    `auto_pause_after_reviewed_commits`; this repo does not set it, so the vendor
    default applies. Its own pause notice and its published schema disagree on
    the key's nesting (`reviews.auto_pause_after_reviewed_commits` vs
    `reviews.auto_review.auto_pause_after_reviewed_commits`) - check the current
    schema before setting it. Hitting the threshold is a signal that the round is
    churning: prefer fewer, larger fix pushes over one commit per finding.

For CodeRabbit, one request per head SHA. Do not re-send its review command
because a verdict is slow - that is noise, not pressure: a paused review does
not resume because it was asked twice, and a running one gains nothing from a
duplicate comment. Diagnose the state first (skipping / paused / running), clear
it if paused, then wait. Repeating the command also buries the evidence the next
reader needs under duplicate ask-and-ack comments.

Round loop:
1. Open the PR and wait for Qodo's initial automatic review.
2. Triage every Qodo finding (matrix below). Fix valid ones; reply on the rest.
   Do not wait for Qodo to re-review a later head.
3. Request the appropriate CodeRabbit review on the current head. Use
   `full review` for the first pass, a rebase, a force-push, or a large diff;
   use `review` only for a small fix push in the same round. Confirm the
   verdict's `commit.oid` equals `headRefOid`.
4. Triage CodeRabbit. Every fix push requires a new CodeRabbit request; Qodo
   does not re-run. Return to step 3.
5. Merge only when CodeRabbit is clean on the current head, every Qodo finding
   is fixed or answered, every thread is resolved, CI is green, and the local
   gates pass. Confirm the CodeRabbit check is not `skipping`.

Wait for Qodo's initial review and CodeRabbit's current-head verdict - poll with
backoff. Do not assume clean because no comment arrived within an arbitrary
minute, and do not treat a pre-push "clean" comment as current.

## Triage matrix

| Situation | Action | Evidence in the reply |
|---|---|---|
| Valid, in scope | Minimal fix + a test that fails before and passes after | commit SHA, `file:line`, test name, gate output |
| Valid, out of scope | Do not widen the PR; open an issue and link it | issue link + why it is out of scope |
| False positive | No code change; correct the bot | counter-evidence: code path, test, or contract quote |
| Already fixed | No code change | commit SHA + the current diff hunk |
| Outdated hunk | Re-check against head; reply on current code | head SHA + current `file:line` |
| Duplicate | Point at the canonical thread | thread/comment link |
| Blocked / product decision | Stop and ask the maintainer | the conflict, the options, the contract clause |

## Finding vs recommendation

The finding names a symptom; the "Recommended Fix" is **advisory**. Reproduce
the symptom first, then choose the smallest correct fix. Reject a recommendation
that conflicts with the contract (hand-editing generated output, reintroducing
Cursor vocabulary, adding a lint ignore) even when the symptom is real. If the
symptom is real but the recommendation is wrong, fix it differently and say so
in the reply.

## Tooling

Prefer **GraphQL**: it is the only source for thread IDs, resolution state,
outdated hunks, and the reviewed SHA. Use **REST** only as a fallback.

Examples are POSIX shell. On PowerShell, `--paginate` cannot be combined with
`--input`; run these in Git Bash, or drop `--paginate` and pass the cursor
explicitly. Non-paginated calls can use `--input <file>` (UTF-8, no BOM).

Set the PR under review once; every command below uses these:

```sh
OWNER=$(gh repo view --json owner -q .owner.login)
REPO=$(gh repo view --json name -q .name)
PR=$(gh pr view --json number -q .number)   # run on the PR branch
```

List every review thread, paginated (`gh api` follows `$endCursor`):

```sh
gh api graphql --paginate --slurp -f query='
query($owner:String!,$repo:String!,$pr:Int!,$endCursor:String){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$pr){
      headRefOid
      reviewThreads(first:100,after:$endCursor){
        pageInfo{hasNextPage endCursor}
        nodes{
          id isResolved isOutdated path line subjectType
          comments(first:1){nodes{
            databaseId author{login} commit{oid} originalCommit{oid} body
          }}
        }
      }
    }
  }
}' -F owner="$OWNER" -F repo="$REPO" -F pr="$PR"
```

`--slurp` returns one object per page; inspect all of them. Without
`--paginate`, pass the previous page's `endCursor` as `-F endCursor=...` and
stop when `hasNextPage` is false.

Bot verdicts carry their reviewed commit too:

```sh
gh api graphql -f query='
query($owner:String!,$repo:String!,$pr:Int!){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$pr){
      headRefOid
      latestReviews(first:100){nodes{author{login} state commit{oid} submittedAt}}
    }
  }
}' -F owner="$OWNER" -F repo="$REPO" -F pr="$PR"
```

`latestReviews` returns the latest review per author, so a current bot verdict
cannot be pushed out of the window by later reviews from someone else.

**SHA check.** A CodeRabbit thread or verdict is current only when its
`commit.oid` equals `headRefOid`. A different `commit.oid` (or an
`originalCommit.oid` behind the head) is stale and is not clean. Qodo's open-PR
review is recorded at its own commit and is not a current-head verdict.
Re-request CodeRabbit on the new head; never post a Qodo trigger in the
automatic setup.

Reply in the same thread:

```sh
gh api graphql -f query='
mutation($tid:ID!,$body:String!){
  addPullRequestReviewThreadReply(
    input:{pullRequestReviewThreadId:$tid,body:$body}
  ){comment{id url}}
}' -f tid=PRRT_... -f body='...'
```

REST fallback (list and reply; **no resolution state**):

```sh
gh api "repos/$OWNER/$REPO/pulls/$PR/comments" --paginate \
  --jq '.[] | {id,path,line,in_reply_to_id,commit_id,user:.user.login}'

gh api --method POST "repos/$OWNER/$REPO/pulls/$PR/comments" \
  -f body='...' -F in_reply_to=<numeric comment id>
```

The numeric id is `comments.nodes[].databaseId` from GraphQL (or `id` in REST).
Resolution is GraphQL-only. If GraphQL is unavailable, report "cannot certify
clean" instead of merging.

Checks and merge state:

```sh
gh pr view "$PR" --json headRefOid,mergeStateStatus,statusCheckRollup
gh pr checks "$PR"
```

## Reply format

Reply in the bot's thread, not as a standalone post. Start with the head SHA
you verified against, then:

- **What** — `file:line` + a short quote of the finding.
- **Why** — the reason tied to the contract, diff, or outdated hunk.
- **Evidence** — commit SHA, test name, gate output, or counterexample.
- **Verification** — commands run; state honest `not run` + cause if blocked.

Avoid generic phrases such as "already discussed" with nothing behind them. If
a reply cannot be posted, report that limitation instead of claiming it landed.

Tone is part of the format, not decoration:

- **Open with `<sha>: <verdict>`.** The head SHA you verified against, then the
  verdict - `accepted` / `rejected` / `already fixed` / `out of scope` - then the
  mechanism and the evidence. SHA first is not a courtesy to the reviewer; it is
  what keeps a verdict SHA-bound per the round loop. No preamble before either.
- **No thanks.** Never "thank you", "thanks", "great catch", "good find", or a
  thank-you mention of the bot. A finding is a claim to adjudicate, not a gift,
  and a reply that opens by thanking spends its most-read clause on nothing the
  reviewer can act on.
- **No emoji, no encouragement, no hedging.** Terse, technical, declarative.
  The reader is deciding whether to merge.
- **Address the claim, not the author.** "The classifier counted
  `import-failed` as available" beats "you are right that this is subtle".

```
Wrong   <sha> - thank you, this was a real contract mismatch.
Right   <sha>: accepted. main counted import-failed as available, so a loader
        that never ran was reported as exit 2. Fixed by a three-way
        loader_state; 14 tests cover the matrix.
```

## Round ledger

Keep **one** PR comment updated per round: round number, head SHA, bot, open
findings, action (fix / reply / dismiss), status. It prevents re-triaging the
same root cause across rounds and gives the maintainer a merge checklist.

## Loop budget and escalation

- Three rounds per bot with no new information → stop and ask the maintainer.
- Contradictory demands (bot A wants X, bot B wants not-X) → the contract
  decides; escalate rather than oscillate.
- A bot re-raising a resolved root cause on a new SHA → reply with the prior
  SHA and fix; do not re-fix.
- Never merge to escape the loop.

## Common false pressures on this repo

| Bot nudge | Contract response |
|---|---|
| Hand-edit generated `skills/` / `config/` / package README to "fix" prose | Rebuild from converter / assets instead |
| Reintroduce Cursor tool names for "clarity" | Banned — use hermes vocabulary |
| Clean up / modernize `agents/` or re-add excluded `benny` / `make-bot-ui` | Leave as-is (scanner / dual-load contract) |
| Implement GitLab stack provider because docs mention `glab` | Halted |
| Treat missing Graphite as a hard CI/product failure | GitHub fallback is intentional |
| Mutate installed stock skills via learning / `skill_manage` | Forbidden as durable source of truth |
| Broaden a check or add `# noqa` to silence a finding | Fix the input, not the gate |
| Bump an action/dependency or reformat the file "while here" | Separate PR — keep the diff scoped |
