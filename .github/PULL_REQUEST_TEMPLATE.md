<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:628a9302ae5d9b5e92cca910990b71e4b30616a906a046e6b4a0bd328e7e23d3 -->

<!--
Keep this short: a template nobody finishes is worse than none.
Every checkbox below is something CI or tools/pr_checklist.py can actually
verify, EXCEPT the "Bot review (gate)" section, which is human-only
attestation (Qodo/CodeRabbit state cannot be verified by CI).
Boxes that cannot be verified were deleted, not softened.
Instructions live in HTML comments and never render in the PR form.
Full gate list mirrors the CONTRIBUTING "Pull requests" section.
Attribution is standing policy in CONTRIBUTING.md - no per-PR checkbox.
The ```yaml provenance``` block is machine-read by tools/pr_checklist.py.
-->
# Pull request

## What changed
<!-- one or two lines: the change, not a narrative -->

## Tracking

<!-- Link the issue, parent/sub-issue and milestone. Converter changes require
     an adaptation issue with scope and reviewer checks. -->

## Affected surface
- [ ] tooling (`tools/`, CI, docs)
- [ ] generated package (`plugin.json`, `skills/`, `config/`, `assets/`, `README.md`, `LICENSE`) — **requires a rebuild from the current pin**

## Provenance — required if a generated surface changed
<!-- tools/pr_checklist.py compares source_commit below to the tree's
     .build-provenance.txt. Copy the exact values from your rebuilt package.
     converter is optional; source_commit is the one that must be right. -->
```yaml
source_commit: <40-char SHA from .build-provenance.txt>
converter: convert.py (sha256[:16]=<first 16 of your convert.py hash>)
```

## Verification
<!-- Paste the command output. A checked box with no output below it is flagged
     by tools/pr_checklist.py. Put each command's output under its own box. -->
- [ ] `uv run --frozen ruff check tools`
      ```
      <output>
      ```
- [ ] `uv run --frozen pytest -q`
      ```
      <output>
      ```
- [ ] package rebuilt from the pin and `git status` shows only the intended generated diff
      ```
      <git status --porcelain output, or "n/a - no package change">
      ```
- [ ] poteto-mode scripts green, if touched (`bun run test` + `bun run typecheck`)
      ```
      <output, or "n/a">
      ```

## Notes for reviewers
<!-- Anything a reviewer should weigh: trade-offs, skipped boxes and why,
     screenshots, follow-ups. Optional. -->

## Bot review (gate)
<!-- Qodo runs automatically on open/reopen/ready. CodeRabbit is manual.
     See docs/agent/bot-reviews.md and CONTRIBUTING.md "Bot review gate". -->
- [ ] Qodo [bot] findings triaged (fixed or answered with evidence) on this head
- [ ] CodeRabbit requested (`@coderabbitai full review` / `review`) and clean on this head (`commit.oid == headRefOid`)
- [ ] No `skipping` / `pending` CodeRabbit check
- [ ] Round ledger comment updated (round, head SHA, bot, open findings, action, status)
<!-- Before merge, every review thread must be resolved. Agents must not resolve
     threads themselves (maintainer or bot resolves), except outdated threads
     (lines moved) after confirming on the thread that the finding no longer
     applies to the current head (commit.oid == headRefOid). -->
<!-- Cross-check: exactly one in-thread reply with evidence per finding; no
     double (standalone + in-thread) replies; no untagged replies left open. -->

> [!TIP]
> **Draft-first content:** Prefer writing draft messages/content before publishing to the public git provider. Keep it technical, short, essential — only on-scope context relations. Cross-check and verify for no false positive/negative. If this is an open/draft PR, add a clear note for reviewers here so they can read the context clearly.


<!-- /coderabbit-ai-generated:v1 -->