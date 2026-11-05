"""The shared banned-construct scanner: one source of truth for validate + CI gate."""

import bans


def test_security_bans_match_any_decodable_extension(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "evil.ts").write_text("fetch('http://127.0.0.1:4173');", encoding="utf-8")

    violations = bans.find_violations(tmp_path)

    assert any("evil.ts" in v and "127.0.0.1:4173" in v for v in violations)


def test_vocab_bans_apply_only_to_the_hermes_facing_surface(tmp_path):
    # Only .build-provenance.txt is exempt from vocab bans.
    (tmp_path / ".build-provenance.txt").write_text("fixed subagent_type", encoding="utf-8")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "s.md").write_text("uses subagent_type", encoding="utf-8")

    flagged = [v for v in bans.find_violations(tmp_path) if "subagent_type" in v]

    assert len(flagged) == 1
    assert "skills/s.md" in flagged[0]


def test_clean_tree_has_no_violations(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "ok.md").write_text("hello hermes", encoding="utf-8")

    assert bans.find_violations(tmp_path) == []


def test_undecodable_binary_files_are_skipped(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe" + b"tailscale.com/install.sh")

    assert bans.find_violations(tmp_path) == []


def test_root_scratch_dump_with_banned_token_is_not_scanned(tmp_path):
    # Issue #128: an ignored scratch dump at repo root containing a banned
    # token must not be scanned as package content.
    (tmp_path / "ev.json").write_text("subagent_type here", encoding="utf-8")
    (tmp_path / "dump.json").write_text("tailscale.com/install.sh", encoding="utf-8")

    assert bans.find_violations(tmp_path) == []


def test_package_file_with_banned_token_still_flagged(tmp_path):
    # Guard against silently narrowing the gate: a real package file with a
    # banned token must still be flagged.
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "evil.md").write_text("uses subagent_type", encoding="utf-8")

    violations = bans.find_violations(tmp_path)

    assert any("evil.md" in v and "subagent_type" in v for v in violations)


def test_generated_node_modules_are_skipped(tmp_path):
    vendor = tmp_path / "skills" / "poteto-mode" / "scripts" / "node_modules" / "bad"
    vendor.mkdir(parents=True)
    (vendor / "README.md").write_text("tailscale.com/install.sh\r\n", encoding="utf-8")
    skill = tmp_path / "skills" / "ok" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("hello hermes", encoding="utf-8")

    scanned = [rel for rel, _text in bans.iter_text_files(tmp_path)]

    assert scanned == ["skills/ok/SKILL.md"]
    assert bans.find_violations(tmp_path) == []
