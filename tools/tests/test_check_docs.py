"""check_docs must catch the drift classes the root-layout migration produced.

Each test pins one failure mode: a broken router link, a generated path
mislabelled as hand-written (README.md), a stale `pstack/` subdirectory path,
a pin-declarer list that disagrees with check_pins, and a publish `cp` source
that no longer exists (after-install.md). The final test runs the gate against
this repository so the checked-in docs cannot drift again.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_docs  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _router_tree(tmp_path: Path, agents: str) -> Path:
    _write(tmp_path / "AGENTS.md", agents)
    _write(tmp_path / "docs" / "agent" / "README.md", "# router\n")
    return tmp_path


def test_router_links_resolve(tmp_path):
    root = _router_tree(tmp_path, "[layout](docs/agent/layout.md)\n[ext](https://example.com)\n")
    _write(tmp_path / "docs" / "agent" / "layout.md", "# layout\n")
    assert check_docs.check_router_links(root) == []


def test_broken_router_link_fails(tmp_path):
    root = _router_tree(tmp_path, "[gone](docs/agent/nope.md)\n")
    problems = check_docs.check_router_links(root)
    assert any("broken link target docs/agent/nope.md" in item for item in problems)


def test_readme_marked_hand_written_fails(tmp_path):
    _write(tmp_path / ".gitattributes", "README.md linguist-generated=true\n")
    _write(
        tmp_path / "docs" / "agent" / "layout.md",
        "| Path | Kind | Notes |\n|---|---|---|\n| `README.md` | hand-written | docs |\n",
    )
    problems = check_docs.check_layout_manifest(tmp_path)
    assert any("README.md" in item and "hand-written" in item for item in problems)


def test_generated_path_not_in_gitattributes_fails(tmp_path):
    _write(tmp_path / ".gitattributes", "README.md linguist-generated=true\n")
    _write(
        tmp_path / "docs" / "agent" / "layout.md",
        "| Path | Kind | Notes |\n|---|---|---|\n| `docs/` | generated | nope |\n",
    )
    problems = check_docs.check_layout_manifest(tmp_path)
    assert any("docs/" in item and "does not mark it generated" in item for item in problems)


def test_layout_manifest_matches(tmp_path):
    _write(
        tmp_path / ".gitattributes",
        "README.md linguist-generated=true\nskills/** linguist-generated=true\n",
    )
    _write(
        tmp_path / "docs" / "agent" / "layout.md",
        "| Path | Kind | Notes |\n|---|---|---|\n"
        "| `README.md` | generated | built |\n"
        "| `skills/` | generated | built |\n"
        "| `tools/` | hand-written | source |\n",
    )
    assert check_docs.check_layout_manifest(tmp_path) == []


def test_pin_declarers_match_check_pins(tmp_path):
    _write(
        tmp_path / "CONTRIBUTING.md",
        "- **`UPSTREAM_PIN` moves in lock-step.** It is declared in `ci.yml`,\n"
        "  `release.yml` and `publish-dist.yml`; one rule.\n",
    )
    assert check_docs.check_pin_declarers(tmp_path) == []


def test_removed_workflow_in_pin_list_fails(tmp_path):
    _write(
        tmp_path / "CONTRIBUTING.md",
        "- **`UPSTREAM_PIN` moves in lock-step.** It is declared in `ci.yml`,\n"
        "  `release.yml`, `publish-dist.yml` and `upstream-drift-watch.yml`.\n",
    )
    problems = check_docs.check_pin_declarers(tmp_path)
    assert any("upstream-drift-watch.yml" in item for item in problems)


def test_neighbouring_bullet_does_not_leak_into_pin_list(tmp_path):
    _write(
        tmp_path / "CONTRIBUTING.md",
        "- **Dependabot.** `dependabot.yml` keeps pins current.\n\n"
        "- **`UPSTREAM_PIN` moves in lock-step.** It is declared in `ci.yml`,\n"
        "  `release.yml` and `publish-dist.yml`.\n",
    )
    assert check_docs.check_pin_declarers(tmp_path) == []


def test_stale_subdirectory_path_fails(tmp_path):
    _write(tmp_path / "AGENTS.md", "cd pstack/skills/poteto-mode/scripts\n")
    problems = check_docs.check_stale_paths(tmp_path)
    assert any("pstack/skills" in item for item in problems)


def test_stale_path_in_readme_fails(tmp_path):
    _write(tmp_path / "README.md", "git show origin/main:pstack/skills/x\n")
    problems = check_docs.check_stale_paths(tmp_path)
    assert any("README.md" in item and "pstack/skills" in item for item in problems)


def test_stale_subdirectory_install_route_in_readme_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "Hermes updates unpinned `dist` root installs and `master` subdirectory\n"
        "installs.\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_stale_subdirectory_install_route_in_using_fails(tmp_path):
    _write(
        tmp_path / "docs" / "USING.md",
        "Subdirectory updates replace shipped skills; in-place edits are not durable.\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("docs/USING.md" in item for item in problems)


def test_legacy_subdirectory_install_command_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install iap/pstack-hermes/pstack --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_subdirectory_install_command_with_trailing_slash_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install iap/pstack-hermes/pstack/ --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_git_url_install_command_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install https://github.com/iap/pstack-hermes.git/pstack --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_ssh_url_install_command_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install git@github.com:iap/pstack-hermes/pstack --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_userless_scp_install_command_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install github.com:iap/pstack-hermes/pstack --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_unrelated_scp_install_command_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install example.com:iap/pstack-hermes/pstack --enable\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_legacy_markdown_link_text_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "[https://github.com/iap/pstack-hermes/pstack](https://example.com)\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_markdown_emphasis_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "See *https://github.com/iap/pstack-hermes/tree/master/pstack*.\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_hash_subdir_install_command_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install iap/pstack-hermes#pstack --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_install_option_before_identifier_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install --enable iap/pstack-hermes/pstack\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_install_option_with_value_before_identifier_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install --force --ref abc123 iap/pstack-hermes/pstack\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_other_owner_prefix_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install acme-iap/pstack-hermes/pstack --enable\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_nested_path_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install acme/iap/pstack-hermes/pstack --enable\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_nested_url_path_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install https://github.com/acme/iap/pstack-hermes/pstack --enable\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_legacy_install_other_option_before_identifier_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install --allow-removed iap/pstack-hermes/pstack\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_install_continuation_before_identifier_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install \\\n  iap/pstack-hermes/pstack --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_install_quoted_ref_separated_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install --ref \"$SHA\" iap/pstack-hermes/pstack\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_install_quoted_ref_equals_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install --ref=\"$SHA\" iap/pstack-hermes/pstack\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_install_newline_inside_inline_code_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "Run `hermes plugins install\niap/pstack-hermes/pstack --enable`.\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_schemeless_github_install_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install github.com/iap/pstack-hermes/pstack --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_root_install_without_subdir_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install iap/pstack-hermes --enable\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_legacy_tree_url_install_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install https://github.com/iap/pstack-hermes/tree/master/pstack --enable\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_tree_url_with_query_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install https://github.com/iap/pstack-hermes/tree/master/pstack?plain=1\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_legacy_tree_url_with_sentence_period_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "See https://github.com/iap/pstack-hermes/tree/master/pstack.\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_dot_named_subdirectory_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install iap/pstack-hermes/pstack.tools --enable\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_legacy_install_command_with_semicolon_fails(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install iap/pstack-hermes/pstack; echo done\n",
    )
    problems = check_docs.check_stale_install_route(tmp_path)
    assert any("subdirectory-install route" in item for item in problems)


def test_similarly_named_subdirectory_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install iap/pstack-hermes/pstack-tools --enable\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_root_git_url_install_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "hermes plugins install https://github.com/iap/pstack-hermes.git --enable\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_migration_note_is_not_flagged(tmp_path):
    _write(
        tmp_path / "README.md",
        "Installs created before the root-layout migration (the old\n"
        "source-subdirectory route) cannot update in place. Remove it,\n"
        "then run `hermes plugins install iap/pstack-hermes --enable`.\n",
    )
    assert check_docs.check_stale_install_route(tmp_path) == []


def test_upstream_repo_path_is_not_flagged(tmp_path):
    _write(
        tmp_path / "docs" / "agent" / "upstream-drift.md",
        "git -C <clone> log --oneline <pin>..HEAD --\n  pstack/` (upstream path)\n",
    )
    assert check_docs.check_stale_paths(tmp_path) == []


def test_pr_template_bare_pstack_path_fails(tmp_path):
    _write(tmp_path / ".github" / "PULL_REQUEST_TEMPLATE.md", "if `pstack/` changed\n")
    problems = check_docs.check_stale_paths(tmp_path)
    assert any("PULL_REQUEST_TEMPLATE" in item for item in problems)


def test_publish_source_missing_fails(tmp_path):
    _write(
        tmp_path / ".github" / "workflows" / "publish-dist.yml",
        'run: |\n  cp -a plugin.json after-install.md "$dist/"\n',
    )
    problems = check_docs.check_publish_sources(tmp_path)
    assert any("after-install.md" in item for item in problems)


def test_publish_sources_exist(tmp_path):
    _write(tmp_path / "plugin.json", "{}\n")
    _write(tmp_path / "README.md", "# x\n")
    _write(
        tmp_path / ".github" / "workflows" / "publish-dist.yml",
        'run: |\n  cp -a plugin.json README.md "$dist/"\n',
    )
    assert check_docs.check_publish_sources(tmp_path) == []


def test_repository_docs_are_clean():
    code, lines = check_docs.check(Path(check_docs.ROOT))
    assert code == 0, "\n".join(lines)


def test_stale_root_generated_file_path_fails(tmp_path):
    _write(tmp_path / "AGENTS.md", "copy pstack/plugin.json into the package\n")
    problems = check_docs.check_stale_paths(tmp_path)
    assert any("pstack/plugin.json" in item for item in problems)


def test_bare_pstack_prose_is_not_flagged(tmp_path):
    _write(
        tmp_path / "docs" / "USING.md",
        "The generated package lives at the repository root (not a `pstack/`\n"
        "subdirectory).\n",
    )
    assert check_docs.check_stale_paths(tmp_path) == []


def test_inline_conditional_publish_source_missing_fails(tmp_path):
    _write(
        tmp_path / ".github" / "workflows" / "publish-dist.yml",
        'run: |\n  if [ -d assets ]; then cp -a assets "$dist/"; fi\n',
    )
    problems = check_docs.check_publish_sources(tmp_path)
    assert any("assets" in item for item in problems)


def test_inline_conditional_publish_source_exists(tmp_path):
    _write(tmp_path / "assets" / "keep.txt", "x\n")
    _write(
        tmp_path / ".github" / "workflows" / "publish-dist.yml",
        'run: |\n  if [ -d assets ]; then cp -a assets "$dist/"; fi\n',
    )
    assert check_docs.check_publish_sources(tmp_path) == []


def test_bare_package_path_in_agent_contract_fails(tmp_path):
    _write(tmp_path / "AGENTS.md", "The package lives at pstack/ in this repo.\n")
    problems = check_docs.check_stale_paths(tmp_path)
    assert any("bare pstack/" in item for item in problems)


def test_generated_marker_missing_from_layout_fails(tmp_path):
    _write(tmp_path / ".gitattributes", "LICENSE linguist-generated=true\n")
    _write(
        tmp_path / "docs" / "agent" / "layout.md",
        "| Path | Kind | Notes |\n|---|---|---|\n| `README.md` | generated | x |\n",
    )
    problems = check_docs.check_layout_manifest(tmp_path)
    assert any("LICENSE" in item and "does not list it" in item for item in problems)


def test_generated_marker_listed_in_layout_passes(tmp_path):
    _write(tmp_path / ".gitattributes", "LICENSE linguist-generated=true\n")
    _write(
        tmp_path / "docs" / "agent" / "layout.md",
        "| Path | Kind | Notes |\n|---|---|---|\n| `LICENSE` | generated | x |\n",
    )
    assert check_docs.check_layout_manifest(tmp_path) == []
