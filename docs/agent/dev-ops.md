# Development ops

How to change this repo without collateral damage, and when to parallelize.
Layout paths: [layout.md](layout.md). Review loop: [review-ops.md](review-ops.md).

## Scope discipline

- **Work the issue/PR scope.** Before editing, list the files you expect to
  touch. If the list keeps growing, stop and re-scope.
- **No drive-by changes.** No renames, reformatting, comment cleanup,
  dependency bumps, or unrelated refactors in a focused PR — they make review
  and revert harder.
- **Generated tree is expected output.** A converter change legitimately
  rewrites the package; that is in scope and the PR says `rebuilt from pin
  <sha>` (see [layout.md](layout.md)). Do not hand-edit generated files.
- **Out-of-scope finding → issue or separate PR.** Link it; do not silently
  widen the diff.
- **Never touch** `agents/`, attribution, or the excluded `benny` /
  `make-bot-ui` slots.
- **Two fixes in one file for different reasons:** land them separately so each
  can be reverted on its own.

## Parallel work

Prefer parallel `delegate_task` workers for independent, read-mostly work:

- triaging multiple review findings, exploring call sites, running tests/lint,
  reading logs, cross-checking docs.

Keep **one writer per file**:

- The coordinator owns the branch: edits, rebase, push, thread replies.
- Partition edits by file or area; integrate serially; run the full gate once
  after integration.
- Delegated workers return commands and outputs, never "it works". They do not
  push, resolve threads, or edit the same file.

Do **not** parallelize:

- rebase / force-push / branch switching;
- package rebuilds (`tools/convert.py --out` writes the tree);
- `CHANGELOG.md` and shared config (`.github/`, `tools/check_*.py` lists);
- anything that touches the same file.

Stop spawning when the remaining work is one file or one command.

## Verify

```sh
uv run --frozen tools/validate.py
uv run --frozen pytest -q
uv run --frozen ruff check tools
uv run --frozen python tools/check_docs.py
```

Docs-only changes need no rebuild; package-shaped changes rebuild first (see
[AGENTS.md](../../AGENTS.md)). Run the gate after integration, not per subagent.

## Review handoff

Push, then drive [review-ops.md](review-ops.md) to clean. Do not merge while a
bot finding or a CI failure is open on the head SHA.
