"""check_dependabot: validate .github/dependabot.yml against documented rules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_dependabot  # noqa: E402


def _tree(tmp_path, dependabot_text, action_files=None):
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "dependabot.yml").write_text(dependabot_text, encoding="utf-8")
    if action_files:
        for name, content in action_files.items():
            d = tmp_path / ".github" / "actions" / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "action.yml").write_text(content, encoding="utf-8")
    return tmp_path


VALID = """version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
    groups:
      actions-minor-patch:
        patterns: ["*"]
        update-types: ["minor", "patch"]
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
      semver-major-days: 30
      semver-patch-days: 3
    groups:
      uv-minor-patch:
        patterns: ["*"]
        update-types: ["minor", "patch"]
"""


def test_valid_config_passes(tmp_path):
    code, _ = check_dependabot.check(_tree(tmp_path, VALID))
    assert code == 0


def test_missing_default_days_fails(tmp_path):
    bad = VALID.replace("      default-days: 7\n", "")
    code, report = check_dependabot.check(_tree(tmp_path, bad))
    assert code == 1
    assert any("default-days" in line for line in report)


def test_semver_days_on_github_actions_fails(tmp_path):
    bad = """version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
      semver-major-days: 30
    labels: ["tooling"]
"""
    code, report = check_dependabot.check(_tree(tmp_path, bad))
    assert code == 1
    assert any("semver-major-days" in line and "not supported" in line for line in report)


def test_group_missing_update_types_fails(tmp_path):
    bad = VALID.replace("        update-types: [\"minor\", \"patch\"]\n", "")
    code, report = check_dependabot.check(_tree(tmp_path, bad))
    assert code == 1
    assert any("update-types" in line for line in report)


def test_composite_action_with_uses_needs_scoped_entry(tmp_path):
    action = "name: x\nruns:\n  using: composite\n  steps:\n    - uses: foo/bar@abc\n"
    code, report = check_dependabot.check(
        _tree(tmp_path, VALID, action_files={"myact": action})
    )
    assert code == 1
    assert any("myact" in line and "directory-scoped" in line for line in report)


def test_composite_action_scoped_entry_passes(tmp_path):
    action = "name: x\nruns:\n  using: composite\n  steps:\n    - uses: foo/bar@abc\n"
    cfg = VALID + """  - package-ecosystem: "github-actions"
    directory: "/.github/actions/myact"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
"""
    code, _ = check_dependabot.check(
        _tree(tmp_path, cfg, action_files={"myact": action})
    )
    assert code == 0


def test_action_without_uses_does_not_require_entry(tmp_path):
    action = "name: x\nruns:\n  using: composite\n  steps:\n    - run: echo hi\n"
    code, _ = check_dependabot.check(
        _tree(tmp_path, VALID, action_files={"myact": action})
    )
    assert code == 0