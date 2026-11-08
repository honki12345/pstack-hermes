#!/usr/bin/env python3
"""Turn a pstack role panel into the `delegation.backends.model_map` block of config.yaml.

The official `advanced-delegation` plugin's `model_map` backend gives every `delegate_task` task
one `option`: a named route from `delegation.backends.model_map.options`. pstack's skills pick
those names through the role table in `skills/poteto-mode/references/hermes-delegation.md`; this
module is that table's owner (a test keeps the contract page equal to `ROUTES` and `ROLE_ROUTES`).

Input is a role panel in the `pstack-models.json` shape that `setup-pstack` and the retiring
`pstack` backend use (`{"roles": {role: entry | [entry, ...]}}`). Output, on stdout, is one JSON
object (also valid YAML) for `hermes config set delegation.backends.model_map '<it>'`:

    {"options": [{"name", "description", "provider", "model", "reasoning_effort"}, ...],
     "default": "general"}

Nothing is written. Stdlib only, except `--config`, which needs PyYAML (Hermes' own interpreter
has it: `~/.hermes/hermes-agent/venv/bin/python`).

    python3 -I tools/model_map_options.py --panel ~/.hermes/pstack-models.json \\
        --provider <main provider> --parent-model '<main model>'
    ~/.hermes/hermes-agent/venv/bin/python tools/model_map_options.py \\
        --panel ~/.hermes/pstack-models.json --config ~/.hermes/config.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PANEL = "panel"
PANEL_PREFIX = "panel-"
DEFAULT_ROUTE = "general"
PARENT_ALIASES = frozenset({"inherit-parent", "auto"})
EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
ENTRY_KEYS = frozenset({"model", "provider", "reasoning_effort", "service_tier", "fallback_providers"})
OPTION_KEYS = ("name", "description", "provider", "model", "reasoning_effort", "service_tier",
               "fallback_providers")
PANEL_SKILLS = "Arena, architect, interrogate and how critics run one task per panel-* seat."


class GenerateError(ValueError):
    """A panel this generator cannot turn into model_map routes; the message names the fix."""


@dataclass(frozen=True)
class Route:
    name: str
    description: str
    #: Where the route's model comes from: "roles" (the roles mapped to it), "explore" (a copy of
    #: that route), or "parent" (the main model at the default effort).
    source: str = "roles"


ROUTES: tuple[Route, ...] = (
    Route("implement", "Write or change code: features, refactors, bug and perf fixes, hillclimb "
                       "loops, swarm work items."),
    Route("explore", "Read-only code investigation: trace call paths, gather evidence, map how or "
                     "why something works."),
    Route("research", "Web and document research with cited sources.", source="explore"),
    Route("quick", "Small mechanical tasks: one-line checks, lookups, tooling probes."),
    Route("deep", "Hardest problems and judgment: design calls, synthesis, explanations, audits, "
                  "careful prose."),
    Route(DEFAULT_ROUTE, "Anything that fits no other route. Default for tasks that pick none.",
          source="parent"),
)

#: Every pstack role (``hermes-plugin/pstack-delegation/routing.py`` ``ROLES``) -> its route.
ROLE_ROUTES: dict[str, str] = {
    "feature, refactoring": "implement",
    "bug-fix": "implement",
    "perf-issue": "implement",
    "hillclimb": "implement",
    "swarm workers": "implement",
    "how explorer": "explore",
    "why investigators": "explore",
    "reflect tooling": "quick",
    "judgment and prose": "deep",
    "hardest tasks": "deep",
    "how explainer": "deep",
    "why synthesizer": "deep",
    "reflect judgment, divergent, synthesizer": "deep",
    "how critics": PANEL,
    "arena runners": PANEL,
    "arena cross-judge pool": PANEL,
    "architect runners": PANEL,
    "interrogate reviewers": PANEL,
}


def _parent_route(provider: str, parent_model: str | None, effort: str | None, why: str) -> dict:
    if not parent_model:
        raise GenerateError(f"{why} runs on the main model; pass --parent-model (or --config).")
    route = {"provider": provider, "model": parent_model}
    if effort:
        route["reasoning_effort"] = effort
    return route


def _entry_route(entry: Any, role: str, provider: str, parent_model: str | None,
                 effort: str | None) -> dict:
    """One panel entry as a model_map route (provider, model, effort, ...)."""
    if isinstance(entry, Mapping):
        unknown = sorted(set(entry) - ENTRY_KEYS - {"profile"})
        if unknown:
            raise GenerateError(f"{role}: panel entry has unsupported keys {unknown}.")
        if entry.get("profile"):
            raise GenerateError(f"{role}: a profile entry cannot be a model route; model_map routes "
                                "a model, profile_routes runs a profile. Pick a model for this role.")
        if not isinstance(entry.get("model"), str) or not entry["model"].strip():
            raise GenerateError(f"{role}: panel entry needs a model.")
        route = {"provider": entry.get("provider") or provider, "model": entry["model"].strip()}
        for key in ("reasoning_effort", "service_tier", "fallback_providers"):
            if entry.get(key) not in (None, ""):
                route[key] = entry[key]
        if route.get("reasoning_effort") not in (None, *EFFORTS):
            raise GenerateError(f"{role}: unknown reasoning_effort {route['reasoning_effort']!r}.")
        return route
    if not isinstance(entry, str) or not entry.strip():
        raise GenerateError(f"{role}: panel entry {entry!r} must be a non-empty string or an object.")
    text = entry.strip()
    if text in PARENT_ALIASES:
        return _parent_route(provider, parent_model, effort, f"{role} ({text})")
    if text.startswith("profile:"):
        raise GenerateError(f"{role}: {text!r} cannot be a model route; model_map routes a model, "
                            "profile_routes runs a profile. Pick a model for this role.")
    model, sep, level = text.rpartition("@")
    if sep and model and level in EFFORTS:
        return {"provider": provider, "model": model, "reasoning_effort": level}
    return {"provider": provider, "model": text}


def _family(model: str) -> str:
    """`claude-opus-5-5[1m]` -> `opus`, `gpt-6-astra-900k` -> `gpt`, `grok-4.7` -> `grok`."""
    tail = model.lower().split("[", 1)[0].rsplit("/", 1)[-1]
    parts = tail.split("-")
    if parts[0] == "claude" and len(parts) > 1 and parts[1].isalpha():
        return parts[1]
    for word in ("gpt", "grok", "gemini", "qwen", "glm", "kimi", "deepseek"):
        if tail.startswith(word):
            return word
    return ""


def _vendor(model: str) -> str:
    tail = model.lower().rsplit("/", 1)[-1]
    return "claude" if tail.startswith("claude") else (_family(model) or "other")


def _panel_options(seats: list[dict]) -> list[dict]:
    options, used = [], set()
    for n, route in enumerate(seats, start=1):
        word = _family(route["model"]) or f"seat{n}"
        name, k = f"{PANEL_PREFIX}{word}", 2
        while name in used:
            name, k = f"{PANEL_PREFIX}{word}-{k}", k + 1
        used.add(name)
        vendor = _vendor(route["model"])
        kind = f"{vendor} family" + (f", {word}" if word not in (vendor, f"seat{n}") else "")
        description = f"Panel seat {n} of {len(seats)} ({kind}). {PANEL_SKILLS}"
        options.append({"name": name, "description": description, **route})
    return options


def _ordered(option: dict) -> dict:
    return {key: option[key] for key in OPTION_KEYS if key in option}


def build(panel: Mapping[str, Any], *, provider: str, parent_model: str | None,
          default_effort: str | None = "high", warn=None) -> dict:
    """The model_map block for a role panel (``{role: entry | [entries]}``)."""
    if not provider:
        raise GenerateError("pass --provider (the provider of entries that name none) or --config.")
    if default_effort not in (None, *EFFORTS):
        raise GenerateError(f"unknown default effort {default_effort!r}.")
    unknown = sorted(role for role in panel if role not in ROLE_ROUTES)
    if unknown:
        raise GenerateError(f"unknown role(s) {unknown}; pstack roles: {', '.join(ROLE_ROUTES)}.")

    def route_of(role: str) -> list[dict]:
        value = panel[role]
        entries = value if isinstance(value, list) else [value]
        if not entries:
            raise GenerateError(f"{role}: empty list; give it at least one entry.")
        return [_entry_route(e, role, provider, parent_model, default_effort) for e in entries]

    resolved = {role: route_of(role) for role in ROLE_ROUTES if role in panel}
    by_route: dict[str, dict] = {}
    missing: list[str] = []
    for route in ROUTES:
        if route.source != "roles":
            continue
        roles = [role for role, name in ROLE_ROUTES.items() if name == route.name]
        given = [(role, resolved[role]) for role in roles if role in resolved]
        missing += [role for role in roles if role not in resolved]
        if any(len(r) != 1 for _, r in given):
            raise GenerateError(f"route {route.name}: a single-entry role has a list "
                                f"({[role for role, r in given if len(r) != 1]}).")
        distinct = {json.dumps(r[0], sort_keys=True) for _, r in given}
        if len(distinct) > 1:
            listing = "; ".join(f"{role}: {json.dumps(r[0], sort_keys=True)}" for role, r in given)
            raise GenerateError(f"route {route.name} is shared by roles that name different models "
                                f"({listing}). Give them one model, or route them yourself.")
        by_route[route.name] = given[0][1][0] if given else _parent_route(
            provider, parent_model, default_effort, f"route {route.name} (no role has a line)")

    panel_roles = [role for role, name in ROLE_ROUTES.items() if name == PANEL]
    given_panels = [(role, resolved[role]) for role in panel_roles if role in resolved]
    missing += [role for role in panel_roles if role not in resolved]
    distinct = {json.dumps(r, sort_keys=True) for _, r in given_panels}
    if len(distinct) > 1:
        raise GenerateError("panel roles must share one seat list under model_map; these differ: "
                            + "; ".join(f"{role}: {len(r)} seat(s)" for role, r in given_panels))
    seats = given_panels[0][1] if given_panels else [
        _parent_route(provider, parent_model, default_effort, "the panel (no panel role has a line)")]

    options = []
    for route in ROUTES:
        if route.source == "parent":
            if not parent_model:
                continue
            target = _parent_route(provider, parent_model, default_effort, route.name)
        else:
            target = by_route[route.source if route.source != "roles" else route.name]
        options.append(_ordered({"name": route.name, "description": route.description, **target}))
    options += [_ordered(o) for o in _panel_options(seats)]
    if missing and warn:
        warn(f"roles with no panel line take their route's model: {', '.join(missing)}")
    block: dict[str, Any] = {"options": options}
    if any(o["name"] == DEFAULT_ROUTE for o in options):
        block["default"] = DEFAULT_ROUTE
    return block


def load_panel(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GenerateError(f"cannot read the panel {path}: {exc}") from exc
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict):
        raise GenerateError(f"{path} has no \"roles\" object.")
    return roles


def load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise GenerateError("--config needs PyYAML; run this with Hermes' interpreter "
                            "(~/.hermes/hermes-agent/venv/bin/python).") from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise GenerateError(f"cannot read {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _keep_user_routes(block: dict, existing: Sequence[Any], existing_default: Any) -> dict:
    """Append the config's own routes that this generator does not own; keep a default that still
    names a route."""
    owned = {o["name"] for o in block["options"]}
    kept = [dict(o) for o in existing if isinstance(o, Mapping) and isinstance(o.get("name"), str)
            and o["name"] not in owned and not o["name"].startswith(PANEL_PREFIX)]
    options = block["options"] + kept
    out: dict[str, Any] = {"options": options}
    names = {o["name"] for o in options}
    if isinstance(existing_default, str) and existing_default.strip() in names:
        out["default"] = existing_default.strip()
    elif "default" in block:
        out["default"] = block["default"]
    return out


def _section(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def build_from_files(*, panel: Path, config: Path | None = None, provider: str | None = None,
                     parent_model: str | None = None, default_effort: str | None = None,
                     warn=None) -> dict:
    cfg = load_config(config) if config else {}
    model_cfg = _section(cfg, "model")
    delegation = _section(cfg, "delegation")
    current = _section(_section(delegation, "backends"), "model_map")
    block = build(
        load_panel(panel),
        provider=provider or model_cfg.get("provider") or "",
        parent_model=parent_model or model_cfg.get("default") or model_cfg.get("model"),
        default_effort=default_effort or delegation.get("reasoning_effort") or "high",
        warn=warn,
    )
    if config:
        existing = current.get("options")
        block = _keep_user_routes(block, existing if isinstance(existing, list) else [],
                                  current.get("default"))
    return block


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--panel", type=Path, required=True, help="pstack-models.json (role panel)")
    parser.add_argument("--config", type=Path,
                        help="config.yaml: supplies the main provider, model and delegation effort, "
                             "and keeps its own non-pstack routes (needs PyYAML)")
    parser.add_argument("--provider", help="provider for panel entries that name none")
    parser.add_argument("--parent-model", help="the main model (inherit-parent entries, `general`)")
    parser.add_argument("--default-effort", choices=sorted(EFFORTS),
                        help="effort for main-model routes (default: delegation.reasoning_effort or high)")
    args = parser.parse_args(argv)
    try:
        block = build_from_files(
            panel=args.panel, config=args.config, provider=args.provider,
            parent_model=args.parent_model, default_effort=args.default_effort,
            warn=lambda text: print(f"model_map_options: note: {text}", file=sys.stderr),
        )
    except GenerateError as exc:
        print(f"model_map_options: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(block, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
