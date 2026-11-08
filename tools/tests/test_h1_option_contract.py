"""The H1 delegation contract routes spawns by the model_map `option`, never by pstack keys.

Hermes refuses a task carrying a key the active delegation backend does not declare. Under
`delegation.backend: model_map` that backend declares only `option`, so a shipped skill that still
says `pstack_role`, `seat` or `access` gets every spawn refused. These tests run the converter's
real maps over their own upstream anchors and read the result, the same way test_panel_location.py
does for T10, so they assert on generated text rather than on converter source.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import convert as cv  # noqa: E402

CONVERT = Path(cv.__file__).resolve()
CONTRACT = CONVERT.parent / "assets" / "hermes-delegation.md"
PSTACK_KEYS = ("pstack_role", "`seat", "seat: n", "access: read-only", "`access`")


def _map(name: str) -> list[tuple[str, str]]:
    if hasattr(cv, name):
        return [tuple(pair) for pair in getattr(cv, name)]
    tree = ast.parse(CONVERT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return [tuple(pair) for pair in ast.literal_eval(node.value)]
    raise AssertionError(f"{name} not found in convert.py")


def _converted(name: str) -> str:
    text = "\n\n".join(old for old, _ in _map(name))
    for old, new in _map(name):
        text = text.replace(old, new)
    return text


def test_h1_output_routes_by_option_and_names_no_pstack_key():
    text = _converted("H1_MAP")
    assert "`option`" in text
    for key in PSTACK_KEYS:
        assert key not in text, key


def test_every_map_that_speaks_about_routing_drops_the_pstack_keys():
    for name in ("T8_MAP", "T10_MAP", "HELP_MAP"):
        text = _converted(name)
        for key in PSTACK_KEYS + ("pstack-models.json", "delegation.backend: pstack"):
            assert key not in text, (name, key)


def test_panel_spawns_take_one_panel_option_per_seat():
    text = _converted("H1_MAP")
    assert "`panel-`" in text
    for skill_word in ("runner n", "adversary n", "judge"):
        line = next(line for line in text.split("\n") if skill_word in line and "panel-" in line)
        assert "option" in line, skill_word


def test_refused_option_falls_back_to_the_default_route_and_says_so():
    text = _converted("H1_MAP")
    assert "without `option`" in text
    assert "default route" in text


def test_h1_forbidden_tokens_cover_the_retired_keys():
    for token in ("pstack_role", "pstack-models.json", "access: read-only"):
        assert token in cv.H1_FORBIDDEN, token


def test_contract_asset_documents_model_map_and_no_pstack_keys():
    text = CONTRACT.read_text(encoding="utf-8")
    assert "delegation.backend: model_map" in text
    assert "delegation.backends.model_map.options" in text
    for key in PSTACK_KEYS + ("pstack-models.json",):
        assert key not in text, key
