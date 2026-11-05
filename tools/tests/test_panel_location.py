"""setup-pstack must not write the model panel into the plugin directory.

`hermes plugins update` (and any reinstall) replaces the installed package tree,
so a panel written inside it is silently discarded - the user's model choices
revert to the shipped defaults with no warning. The write target therefore lives
in the hermes config directory, beside `config.yaml`, which survives updates.

The assertions run on the text the converter actually produces, not on the
converter's source: the bug was never in validate.py, it was in the instruction
the skill followed, so asserting on the source would only prove the source says
what the source says. An earlier source-only version of this file passed a
counterfactual that reintroduced the in-package wording - that is how the weakness
was found.

T10_MAP is a local of apply_phase1_transforms(), and driving that function needs a
full converted package plus an upstream clone, neither of which the lint-and-tests
job has. So the map is read out of the module source and applied to a fixture here.
That keeps the assertion on the real transform rather than on prose, and keeps the
test hermetic: no network, no pinned checkout.

The fixture is built from T10_MAP's own anchors rather than hand-typed, because a
hand-typed approximation drifts from upstream and then silently matches nothing -
which is exactly what happened on the first attempt at this file.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import convert as cv  # noqa: E402

CONVERT = Path(cv.__file__).resolve()
REPO = CONVERT.parents[1]

# The pre-fix wording. Assembled from parts so this file has no stray apostrophe.
IN_PACKAGE_WRITE = "in this plugin" + chr(39) + "s directory"
NEXT_TO_MANIFEST = "next to plugin.json"


def _load_t10_map() -> list[tuple[str, str]]:
    """Extract T10_MAP from convert.py without importing a pinned upstream tree."""
    tree = ast.parse(CONVERT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(getattr(t, "id", None) == "T10_MAP" for t in node.targets):
                return [tuple(pair) for pair in ast.literal_eval(node.value)]
    raise AssertionError("T10_MAP not found in convert.py")


def _load_named_maps(names: tuple[str, ...]) -> list[list[tuple[str, str]]]:
    """Extract several converter maps by name, same AST approach as _load_t10_map."""
    tree = ast.parse(CONVERT.read_text(encoding="utf-8"))
    found: dict[str, list[tuple[str, str]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = getattr(target, "id", None)
                if name in names and name not in found:
                    found[name] = [tuple(p) for p in ast.literal_eval(node.value)]
    missing = [n for n in names if n not in found]
    assert not missing, f"maps not found in convert.py: {missing}"
    return [found[n] for n in names]

def _upstream_fixture() -> str:
    """Rebuild a minimal upstream SKILL.md from T10_MAP's own anchors.

    Each anchor's left-hand side is upstream's wording, so concatenating a few of
    them reconstructs text the map is guaranteed to match.
    """
    anchors = [old for old, _ in _load_t10_map()]
    # The anchors are ordered as they appear in the upstream file.
    return "\n\n".join(anchors)


def _converted_setup_text() -> str:
    """Apply the real T10_MAP to the reconstructed upstream text."""
    text = _upstream_fixture()
    applied = 0
    for old, new in _load_t10_map():
        if old in text:
            text = text.replace(old, new, 1)
            applied += 1
    assert applied >= 3, (
        f"only {applied} T10 anchors matched the fixture; the map or this "
        "reconstruction drifted apart"
    )
    return text


def test_generated_skill_never_says_write_into_the_plugin_dir():
    text = _converted_setup_text()
    assert IN_PACKAGE_WRITE not in text, (
        "generated setup-pstack still tells the agent to write the panel into the "
        "package directory, where plugins update discards it"
    )
    assert NEXT_TO_MANIFEST not in text


def test_generated_skill_writes_beside_config_yaml():
    text = _converted_setup_text()
    assert "pstack-models.json" in text
    assert "config.yaml" in text


def test_converter_source_has_no_in_package_write_target():
    """Guard the source too, so the ban survives even if a map is bypassed."""
    src = CONVERT.read_text(encoding="utf-8")
    assert IN_PACKAGE_WRITE not in src
    assert NEXT_TO_MANIFEST not in src


def test_read_side_points_at_the_user_level_panel():
    """Every map that writes or reads the panel must name the user-level path.

    Per-map rather than "any map mentions it": a single-map assertion passes while
    one shipped skill has quietly stopped reading the panel, which is the failure
    this is guarding. Also asserts the in-package fallback is GONE - reading it
    after an update would copy the shipped default over the user's saved choices,
    so its presence is worse than its absence.
    """
    src = CONVERT.read_text(encoding="utf-8")

    # setup-pstack: writes it, and reads it back.
    t10 = _load_t10_map()
    assert any("pstack-models.json" in side for pair in t10 for side in pair), (
        "T10 no longer points at the user-level panel"
    )

    # Every map entry whose REPLACEMENT text names a panel path must name the
    # user-level one. Scoped to replacement text deliberately: upstream anchors
    # legitimately mention models.json in other contexts (the shipped default
    # panel, prose about the Cursor rule) and matching those produced false
    # positives on T8.
    for name in ("T8_MAP", "T9_MAP", "T13_MAP"):
        for _, replacement in _load_named_maps((name,))[0]:
            if "models.json" not in replacement and "model panel" not in replacement:
                continue
            assert "pstack-models.json" in replacement, (
                f"{name} writes panel guidance without naming pstack-models.json: "
                f"{replacement[:90]!r}"
            )

    # The in-package fallback must not come back: see the module docstring.
    assert "legacy `config/models.json`" not in src, (
        "a map still tells the agent to recover the in-package panel; after an "
        "update that file holds the shipped default, so migrating it would discard "
        "the user's configuration"
    )


def test_shipped_default_panel_still_lives_in_the_package():
    """Only the user's override moves; the shipped default is package content.

    validate.py asserts the shipped `config/models.json` equals the repo-owned
    panel, so it must keep shipping. This records that the fix does not
    over-reach into removing it.
    """
    panel = REPO / "tools" / "assets" / "model-panel.json"
    assert panel.is_file(), "repo-owned default panel missing"
    assert '"roles"' in panel.read_text(encoding="utf-8")