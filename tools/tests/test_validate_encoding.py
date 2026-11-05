"""Encoding checks ignore generated dependency installs inside the package tree."""

import validate


def test_encoding_check_ignores_root_scratch_files(tmp_path):
    # Issue #128: ignored scratch dumps at repo root must not be scanned.
    (tmp_path / "ev.json").write_bytes(b"third-party dump\r\n")
    (tmp_path / "dump.json").write_bytes(b"subagent_type here\r\n")
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: demo\ndescription: demo\n---\n")

    rep = validate.Report()
    validate.check_encoding(tmp_path, rep)

    assert not rep.failed
    assert any("no CRLF" in message for message in rep.passed)


def test_encoding_check_still_fails_on_package_file_crlf(tmp_path):
    # Guard against silently narrowing the gate: a real package file with
    # CRLF must still be flagged.
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: demo\ndescription: demo\n---\r\n")

    rep = validate.Report()
    validate.check_encoding(tmp_path, rep)

    assert rep.failed
    assert any("CRLF" in m for m in rep.failed)


def test_encoding_check_skips_generated_node_modules(tmp_path):
    vendor = tmp_path / "skills" / "poteto-mode" / "scripts" / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    # write_bytes, not write_text: text mode translates "\n" to os.linesep on Windows,
    # so the LF-only fixture below would be written as CRLF there and the assertion
    # would fail for a platform reason rather than a product reason. (The vendored
    # readme is deliberately CRLF -- it is the file that must be skipped.)
    (vendor / "README.md").write_bytes(b"third-party readme\r\n")
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: demo\ndescription: demo\n---\n")

    rep = validate.Report()
    validate.check_encoding(tmp_path, rep)

    assert not rep.failed
    assert any("no CRLF" in message and "1 text files" in message for message in rep.passed)
