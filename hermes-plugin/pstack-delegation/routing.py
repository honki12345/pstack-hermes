"""Pure role -> route resolution for the ``pstack`` delegation backend.

No Hermes imports: this module is unit-tested by the repository's own tooling
suite (``tools/tests/test_pstack_delegation_routing.py``) and wrapped into a
``DelegationProvider`` by ``__init__.py``.

Inputs
------
* the pstack model panel, ``pstack-models.json`` beside ``config.yaml``
  (written by the ``setup-pstack`` skill): ``{"budget": ..., "roles": {role: value}}``
  where ``value`` is one entry or, for panel roles, a list of entries;
* the backend settings block ``delegation.backends.pstack`` (``aliases``,
  ``unmapped_defaults``, ``panel_path``);
* the default route (the core ``delegation`` section).

Entry grammar
-------------
``inherit-parent`` / ``auto``   the parent route
``profile:<name>``              run the child as that Hermes profile
``<model>@<effort>``            model plus reasoning effort
``<model>``                     model only (the default route's provider applies)
``{"model": ..., "provider": ..., "reasoning_effort": ..., ...}``  an explicit route
Any string is first looked up in ``aliases``; an alias maps to one entry.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND_NAME = "pstack"
PANEL_FILENAME = "pstack-models.json"

#: Role labels exactly as the skills and ``setup-pstack`` spell them.
ROLES: tuple[str, ...] = (
    "feature, refactoring",
    "bug-fix",
    "perf-issue",
    "hillclimb",
    "judgment and prose",
    "hardest tasks",
    "how explorer",
    "how explainer",
    "how critics",
    "why investigators",
    "why synthesizer",
    "reflect tooling",
    "reflect judgment, divergent, synthesizer",
    "arena runners",
    "arena cross-judge pool",
    "swarm workers",
    "architect runners",
    "interrogate reviewers",
)

PANEL_ROLES = frozenset({
    "how critics", "arena runners", "arena cross-judge pool", "architect runners", "interrogate reviewers",
})

ACCESS_MODES = ("read-only", "write")

_OPUS = "claude-opus-5-5-max"
_SOL = "gpt-5.6-sol-max"
_GROK = "grok-4.7-xhigh-fast"
_TRIO = (_OPUS, _SOL, _GROK)

#: Upstream pstack's per-role defaults (cursor/plugins pstack ``setup-pstack``),
#: used when the user's panel has no line for a role. These are Cursor slugs:
#: they route only through ``aliases``; unmapped ones follow ``unmapped_defaults``.
UPSTREAM_DEFAULTS: dict[str, str | tuple[str, ...]] = {
    "feature, refactoring": _GROK,
    "bug-fix": _GROK,
    "perf-issue": _GROK,
    "hillclimb": _GROK,
    "judgment and prose": _OPUS,
    "hardest tasks": _OPUS,
    "how explorer": _GROK,
    "how explainer": _OPUS,
    "how critics": (_OPUS,),
    "why investigators": _GROK,
    "why synthesizer": _OPUS,
    "reflect tooling": _SOL,
    "reflect judgment, divergent, synthesizer": _OPUS,
    "arena runners": _TRIO,
    "arena cross-judge pool": _TRIO,
    "swarm workers": _GROK,
    "architect runners": _TRIO,
    "interrogate reviewers": _TRIO,
}

PARENT_ALIASES = frozenset({"inherit-parent", "auto"})

#: Route keys an explicit entry may carry (a subset of Hermes' ROUTE_KEYS; the
#: credential keys stay in config.yaml, never in the panel).
ENTRY_KEYS = frozenset({"model", "provider", "reasoning_effort", "service_tier", "profile", "fallback_providers"})

#: Keys of the core ``delegation`` section that form the default route.
DEFAULT_ROUTE_KEYS = frozenset({
    "model", "provider", "base_url", "api_key", "api_mode", "request_overrides", "reasoning_effort",
    "fallback_providers",
})

EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})


class RoutingError(ValueError):
    """A task the backend refuses; the message is shown to the model, so it names the fix."""


@dataclass(frozen=True)
class Resolution:
    route: dict[str, Any]
    label: str
    source: str  # "panel", "upstream-default", "parent"


def panel_path(settings: Mapping[str, Any], hermes_home: Path) -> Path:
    override = settings.get("panel_path")
    if isinstance(override, str) and override.strip():
        return Path(override).expanduser()
    return hermes_home / PANEL_FILENAME


def load_panel(path: Path) -> dict[str, Any]:
    """The ``roles`` mapping of the panel file, ``{}`` when the file is absent."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RoutingError(f"Cannot read the pstack model panel {path}: {exc}.") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RoutingError(
            f"The pstack model panel {path} is not valid JSON ({exc.msg} at line {exc.lineno}); "
            "fix it or re-run the setup-pstack skill.") from exc
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict):
        raise RoutingError(f"The pstack model panel {path} has no \"roles\" object; re-run the setup-pstack skill.")
    return roles


def default_route(config: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in config.items() if k in DEFAULT_ROUTE_KEYS and v not in (None, "")}


def _pick_seat(role: str, value: Any, seat: int | None, origin: str) -> tuple[Any, int]:
    if isinstance(value, (list, tuple)):
        if not value:
            raise RoutingError(f"The {origin} line for {role!r} is an empty list; give it at least one entry.")
        index = 0 if seat is None else seat
        if index >= len(value):
            raise RoutingError(
                f"seat {index} is out of range for {role!r}: its {origin} line has {len(value)} "
                f"entr{'y' if len(value) == 1 else 'ies'} (seats 0..{len(value) - 1}). Spawn one task per entry.")
        return value[index], index
    if seat not in (None, 0):
        raise RoutingError(f"{role!r} has a single entry; omit seat or pass 0.")
    return value, 0


def _entry_route(entry: Any, aliases: Mapping[str, Any], depth: int = 0) -> tuple[dict[str, Any] | None, str]:
    """``(route, description)``; route None means the parent route. Raises on a malformed entry."""
    if depth > 4:
        raise RoutingError("pstack alias chain is too deep (more than 4 hops); check delegation.backends.pstack.aliases.")
    if isinstance(entry, Mapping):
        unknown = sorted(set(entry) - ENTRY_KEYS)
        if unknown:
            raise RoutingError(f"pstack panel entry has unsupported keys {unknown}; allowed: {sorted(ENTRY_KEYS)}.")
        route = {k: v for k, v in entry.items() if v not in (None, "")}
        effort = route.get("reasoning_effort")
        if effort is not None and str(effort) not in EFFORTS:
            raise RoutingError(f"pstack panel entry has unknown reasoning_effort {effort!r}.")
        desc = route.get("profile") and f"profile {route['profile']}" or "/".join(
            str(route[k]) for k in ("provider", "model") if k in route) or "parent"
        if route.get("reasoning_effort"):
            desc += f" @{route['reasoning_effort']}"
        return (route or None), desc
    if not isinstance(entry, str) or not entry.strip():
        raise RoutingError(f"pstack panel entry {entry!r} must be a non-empty string or an object.")
    text = entry.strip()
    if text in aliases:
        return _entry_route(aliases[text], aliases, depth + 1)
    if text in PARENT_ALIASES:
        return None, "parent"
    if text.startswith("profile:"):
        name = text[len("profile:"):].strip()
        if not name:
            raise RoutingError("pstack panel entry 'profile:' needs a profile name.")
        return {"profile": name}, f"profile {name}"
    model, sep, effort = text.rpartition("@")
    if sep and model and effort in EFFORTS:
        return {"model": model, "reasoning_effort": effort}, f"{model} @{effort}"
    return {"model": text}, text


def resolve(
    *,
    role: str | None,
    seat: int | None,
    access: str | None,
    panel: Mapping[str, Any],
    settings: Mapping[str, Any],
    config: Mapping[str, Any],
) -> Resolution:
    """Route one task. ``role`` None = the default route (a task outside pstack's roles)."""
    if seat is not None and (isinstance(seat, bool) or not isinstance(seat, int) or seat < 0):
        raise RoutingError(f"seat must be a non-negative integer (got {seat!r}).")
    if access is not None and access not in ACCESS_MODES:
        raise RoutingError(f"access must be one of {', '.join(ACCESS_MODES)} (got {access!r}).")
    base = default_route(config)
    suffix = " [read-only: advisory]" if access == "read-only" else ""
    if role is None:
        if seat is not None:
            raise RoutingError("seat needs pstack_role; set the role whose panel line the seat indexes.")
        return Resolution(base, "default" + suffix, "parent")
    if role not in ROLES:
        raise RoutingError(f"Unknown pstack_role {role!r}.")
    aliases = settings.get("aliases") or {}
    if not isinstance(aliases, Mapping):
        raise RoutingError("delegation.backends.pstack.aliases must be a mapping of name -> entry.")

    if role in panel:
        value, origin = panel[role], "panel"
    else:
        value, origin = UPSTREAM_DEFAULTS[role], "upstream-default"
    entry, index = _pick_seat(role, value, seat, origin)
    tag = f"{role}[{index}]" if isinstance(value, (list, tuple)) else role

    if origin == "upstream-default" and isinstance(entry, str) and entry not in aliases:
        policy = settings.get("unmapped_defaults", "parent")
        if policy == "refuse":
            raise RoutingError(
                f"{role!r} has no panel line and its upstream default {entry!r} has no alias. Add the role to "
                "pstack-models.json (setup-pstack) or map the slug under delegation.backends.pstack.aliases.")
        return Resolution(base, f"{tag} -> parent (unmapped default {entry}){suffix}", "parent")

    route, desc = _entry_route(entry, aliases)
    if route is None:
        return Resolution(base, f"{tag} -> parent{suffix}", "parent")
    if "profile" in route:
        return Resolution({"profile": route["profile"]}, f"{tag} -> {desc}{suffix}", origin)
    if "provider" in route:
        # A provider pin replaces the default route's endpoint; never pair it with another provider's key or URL.
        base = {k: v for k, v in base.items() if k not in ("base_url", "api_key", "api_mode", "model")}
    return Resolution({**base, **route}, f"{tag} -> {desc}{suffix}", origin)


def check_panel(panel: Mapping[str, Any], settings: Mapping[str, Any]) -> list[str]:
    """Every problem a panel would hit at spawn time, as ``role[seat]: message`` lines (empty = ok)."""
    problems = [f"{role!r}: unknown role (setup-pstack roles: {', '.join(ROLES)})" for role in panel if role not in ROLES]
    for role in ROLES:
        if role not in panel:
            continue
        value = panel[role]
        if role in PANEL_ROLES and not isinstance(value, list):
            problems.append(f"{role}: a panel role takes a list (one subagent per entry)")
            continue
        seats = range(len(value)) if isinstance(value, list) else [None]
        if isinstance(value, list) and not value:
            problems.append(f"{role}: empty list")
        for seat in seats:
            try:
                resolve(role=role, seat=seat, access=None, panel=panel, settings=settings, config={})
            except RoutingError as exc:
                problems.append(f"{role}[{seat or 0}]: {exc}")
    return problems


def _main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Check a pstack model panel against the pstack delegation backend.")
    parser.add_argument("--check", type=Path, required=True, help="path to pstack-models.json")
    args = parser.parse_args(argv)
    try:
        problems = check_panel(load_panel(args.check), {})
    except RoutingError as exc:
        problems = [str(exc)]
    for line in problems:
        print(line)
    print("panel ok" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
