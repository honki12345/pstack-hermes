"""Anchor accounting: transform maps must apply, and dead anchors must fail loudly."""

import convert


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_apply_map_replaces_all_occurrences_and_counts_hits(tmp_path):
    f1 = _write(tmp_path, "a.md", "keep OLD-A plus OLD-A twice")
    f2 = _write(tmp_path, "b.md", "unrelated content")
    st = convert.Stats()

    changed = convert.apply_map([f1, f2], [("OLD-A", "NEW-A")], map_name="TTEST", st=st)

    assert changed == 1
    assert f1.read_text(encoding="utf-8") == "keep NEW-A plus NEW-A twice"
    assert f2.read_text(encoding="utf-8") == "unrelated content"
    assert st.anchor_hits == {("TTEST", 0): 2}


def test_apply_map_preserves_pair_order_within_a_file(tmp_path):
    f = _write(tmp_path, "a.md", "one AAA two")
    st = convert.Stats()

    convert.apply_map([f], [("AAA", "BBB"), ("one BBB", "one CCC")], map_name="T", st=st)

    assert f.read_text(encoding="utf-8") == "one CCC two"


def test_apply_map_skips_files_without_matches(tmp_path):
    f = _write(tmp_path, "a.md", "nothing to see")
    st = convert.Stats()

    changed = convert.apply_map([f], [("ABSENT", "x")], map_name="T", st=st)

    assert changed == 0
    assert st.anchor_hits == {}


def test_audit_names_every_dead_anchor():
    st = convert.Stats()
    st.anchor_hits[("T", 0)] = 1
    maps = {"T": [("present", "x"), ("dead-one", "y"), ("dead-two", "z")]}

    try:
        convert.audit_anchor_hits(maps, st)
    except convert.ConvertError as exc:
        msg = str(exc)
        assert "dead-one" in msg and "dead-two" in msg
        assert "present" not in msg
    else:
        raise AssertionError("expected ConvertError naming the dead anchors")


def test_audit_passes_when_every_anchor_hit():
    st = convert.Stats()
    st.anchor_hits[("A", 0)] = 3
    st.anchor_hits[("B", 0)] = 1
    st.anchor_hits[("B", 1)] = 1

    convert.audit_anchor_hits(
        {"A": [("x", "y")], "B": [("p", "q"), ("r", "s")]}, st
    )


def test_worktree_awk_pairs_keep_full_paths():
    """F11: `worktree <path>` lines are read whole, so paths with spaces survive.
    (F10/F12, the GNU/BSD stat+date helpers, retired at c742dd6: upstream ships them.)"""
    assert convert.WORKTREE_AWK_PAIRS
    for old, new in convert.WORKTREE_AWK_PAIRS:
        assert "print $2" in old
        assert "substr($0, 10)" in new and "print $2" not in new


def test_principle_slug_follows_upstream_naming_rule():
    assert convert.principle_slug("Test Behavior, Not Implementation") == (
        "principle-test-behavior-not-implementation"
    )
    assert convert.principle_slug("Redesign from First Principles") == (
        "principle-redesign-from-first-principles"
    )


def _index_tree(tmp_path, entries, leaves):
    _write(
        tmp_path,
        "poteto-mode/SKILL.md",
        "---\nname: poteto-mode\n---\n\n## Principles\n\n"
        + "".join(f"{e}\n" for e in entries)
        + "\n## Autonomy\n",
    )
    for leaf in leaves:
        _write(tmp_path, f"{leaf}/SKILL.md", f"---\nname: {leaf}\n---\n")
    return tmp_path


def test_principle_index_drift_accepts_titles_and_explicit_slugs(tmp_path):
    skills = _index_tree(
        tmp_path,
        [
            "- **Prove It Works.** After a task.",
            "- **Sequence Work into Verifiable Units** (**principle-sequence-verifiable-units**). Steps.",
        ],
        ["principle-prove-it-works", "principle-sequence-verifiable-units"],
    )
    assert convert.principle_index_drift(skills) == []


def test_principle_index_drift_names_missing_and_orphaned_entries(tmp_path):
    skills = _index_tree(
        tmp_path,
        ["- **Prove It Works.** After a task.", "- **Gone Principle.** Removed upstream."],
        ["principle-prove-it-works", "principle-explain-the-number"],
    )
    drift = convert.principle_index_drift(skills)
    assert "index entry without a shipped leaf: principle-gone-principle" in drift
    assert "leaf missing from the index: principle-explain-the-number" in drift
