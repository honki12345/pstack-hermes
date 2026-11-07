"""Structural guard for the ci.yml concurrency finding of the PR #67 review.

ci.yml concurrency: non-PR runs must not share a concurrency group with any
other run. cancel-in-progress protects only started runs; by default a newer
pending run cancels an older pending one in the same group, so a branch push
could be cancelled before it started and skip verify-determinism.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_ci_concurrency_keeps_non_pr_runs_whole():
    data = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    concurrency = data["concurrency"]
    group = concurrency["group"]
    assert "github.run_id" in group, (
        "non-PR runs must take a per-run concurrency group"
    )
    assert "github.event_name == 'pull_request'" in group, (
        "the shared group must be limited to pull_request runs"
    )
    assert "github.ref" in group, "PR runs must keep sharing the per-ref group"
    cancel = concurrency["cancel-in-progress"].strip()
    assert cancel == "${{ github.event_name == 'pull_request' }}", (
        f"cancel-in-progress must stay PR-only, got {cancel!r}"
    )
