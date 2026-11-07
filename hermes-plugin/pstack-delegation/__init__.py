"""``pstack`` delegation backend: route each ``delegate_task`` task by pstack role.

Hermes' core ``delegate_task`` has no per-task ``model`` or ``readonly``: a ``model`` key is
silently ignored and ``readonly`` refuses the task. This backend gives pstack's role panel a
real seam instead. It declares three per-task parameters:

* ``pstack_role`` - the role line (``interrogate reviewers``, ``how explorer``, ...);
* ``seat`` - the 0-based entry of a panel role's list (one task per entry);
* ``access`` - ``read-only`` or ``write``; Hermes cannot narrow a child's tools per task, so
  read-only stays advisory and is surfaced in the route label for the parent to verify.

Each task resolves through ``pstack-models.json`` (beside ``config.yaml``), then
``delegation.backends.pstack.aliases``, then upstream pstack's defaults. Enable with::

    hermes plugins enable pstack-delegation
    hermes config set delegation.backend pstack
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.delegation_provider import DelegationProvider, DelegationRouteError

from .routing import (
    ACCESS_MODES,
    BACKEND_NAME,
    ROLES,
    RoutingError,
    load_panel,
    panel_path,
    resolve,
)


def _hermes_home() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home())


_DESCRIPTION = (
    "pstack routing (delegation.backend: pstack): when a pstack skill names a role line, set `pstack_role` to "
    "it and never pass `model` - the backend picks model, provider and effort from pstack-models.json. Panel "
    "roles (interrogate reviewers, arena runners, architect runners, arena cross-judge pool) spawn one task per "
    "entry with `seat` 0..n-1. Set `access: read-only` for reviewers and explorers and also say READ-ONLY in "
    "`context`: Hermes children keep your toolsets, so check `git status` after they return. Each result's "
    "`route` names the model the child actually ran on; cite it instead of the configured slug."
)


class PstackDelegationProvider(DelegationProvider):
    @property
    def name(self) -> str:
        return BACKEND_NAME

    @property
    def display_name(self) -> str:
        return "pstack roles"

    @property
    def summary(self) -> str:
        return "Route each subagent by pstack role through pstack-models.json (model, provider, effort or profile)."

    def task_parameters(self, config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "pstack_role": {
                "type": "string",
                "enum": list(ROLES),
                "description": "pstack role line this child fills; the backend resolves its model. Omit for "
                               "tasks outside pstack's roles (they take the default route).",
            },
            "seat": {
                "type": "integer",
                "minimum": 0,
                "description": "0-based entry of a panel role's list (one task per entry). Omit for single roles.",
            },
            "access": {
                "type": "string",
                "enum": list(ACCESS_MODES),
                "description": "Intended access. read-only is advisory on Hermes: also state it in context and "
                               "verify the worktree afterwards.",
            },
        }

    def description(self, config: Mapping[str, Any]) -> str:
        return _DESCRIPTION

    def route_task(self, task: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        settings = self.settings(config)
        try:
            role = task.get("pstack_role")
            panel = load_panel(panel_path(settings, _hermes_home())) if role is not None else {}
            result = resolve(
                role=role, seat=task.get("seat"), access=task.get("access"),
                panel=panel, settings=settings, config=config,
            )
        except RoutingError as exc:
            raise DelegationRouteError(str(exc)) from exc
        return {**result.route, "label": result.label}


def register(ctx) -> None:
    ctx.register_delegation_provider(PstackDelegationProvider())
