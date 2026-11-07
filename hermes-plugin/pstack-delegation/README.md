# pstack-delegation

A Hermes delegation backend that routes each `delegate_task` subagent by
**pstack role**, so pstack's per-role models and multi-model panels actually run
on the models you configured.

## Why it exists

Hermes v0.23.0 `delegate_task` takes no per-task model. Verified by execution:

| A pstack skill asks for | What core Hermes does |
|---|---|
| `model: gpt-5.6-sol-max` on a task | Dropped without an error. The child ran as `claude-opus-5-5` (the parent). |
| `readonly: true` on a task | Refused: "does not take 'readonly' ... this task was not started". |

So `interrogate`'s three-vendor review, `arena`'s runners, and every role line in
`pstack-models.json` silently collapse to the parent model. This backend uses
Hermes' delegation provider seam (`delegation.backend`,
`agent/delegation_provider.py`) to restore per-role routing.

## What it adds to `delegate_task`

| Task key | Meaning |
|---|---|
| `pstack_role` | Role line, for example `interrogate reviewers` or `how explorer` (enum of the 18 setup-pstack roles). |
| `seat` | 0-based entry of a panel role's list. Spawn one task per entry. |
| `access` | `read-only` or `write`. Read-only is advisory: Hermes cannot narrow one child's tools, so the label says `[read-only: advisory]` and the skills tell the parent to check `git status`. |

Each task resolves in order:

1. The role's line in `pstack-models.json` beside `config.yaml`. With
   `setup-pstack`, that is `$HERMES_HOME/pstack-models.json`, which is per profile.
2. `delegation.backends.pstack.aliases`, which maps any entry string to a route.
3. Upstream pstack's default for the role, which is a Cursor slug such as
   `claude-opus-5-5-max`. An unmapped default runs on the parent route, and the
   label says so. Set `unmapped_defaults: refuse` to refuse it instead.

The route merges over the `delegation` section, so `delegation.reasoning_effort`
and the other defaults still apply. A `provider` pin drops the default endpoint's
`base_url`, `api_key`, and `model`. Every result carries
`route.label`, for example `interrogate reviewers[1] -> openai-codex/gpt-5.6-sol @xhigh`.

## Panel entries

```json
{
  "budget": "unlimited",
  "roles": {
    "feature, refactoring": "claude-opus-5-5@high",
    "judgment and prose": "claude-opus-5-5@max",
    "interrogate reviewers": [
      "claude-opus-5-5@max",
      {"provider": "openai-codex", "model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
      "profile:grok-reviewer"
    ],
    "swarm workers": "inherit-parent"
  }
}
```

| Entry | Route |
|---|---|
| `inherit-parent` / `auto` | The parent route. |
| `<model>@<effort>` | That model at that reasoning effort, on the default route's provider. |
| `<model>` | That model on the default route's provider. |
| `profile:<name>` | Run the child as that Hermes profile, with its model, credentials, and skills. |
| `{...}` | An explicit route: `model`, `provider`, `reasoning_effort`, `service_tier`, `profile`, `fallback_providers`. Credentials never go in the panel. |

Check a panel before you use it:

```sh
python3 hermes-plugin/pstack-delegation/routing.py --check ~/.hermes/pstack-models.json
```

## Install

This changes how every subagent in the profile is routed. Install it per profile,
and keep a way back.

```sh
cp -R hermes-plugin/pstack-delegation ~/.hermes/plugins/pstack-delegation   # or a symlink
hermes plugins doctor ~/.hermes/plugins/pstack-delegation --ci
hermes plugins enable pstack-delegation
hermes config set delegation.backend pstack
# optional aliases for upstream default slugs:
#   delegation:
#     backends:
#       pstack:
#         aliases:
#           claude-opus-5-5-max: {model: claude-opus-5-5, reasoning_effort: max}
#           grok-4.7-xhigh-fast: {provider: xai, model: grok-4.7-fast, reasoning_effort: xhigh}
```

The backend applies from the next session (`/new`), or immediately with
`/delegation pstack --now`. To roll back, run
`hermes config set delegation.backend default` and
`hermes plugins disable pstack-delegation`.

## Tests

```sh
uv run --frozen pytest -q tools/tests/test_pstack_delegation_routing.py      # pure routing, repo venv
cd ~/.hermes/hermes-agent && .venv/bin/python -m pytest -q -p no:cacheprovider --import-mode=importlib \
  --rootdir <fork>/hermes-plugin/pstack-delegation/tests \
  <fork>/hermes-plugin/pstack-delegation/tests/test_hermes_integration.py   # real Hermes loader + routes
```

The integration test loads the plugin through Hermes' real discovery against a
temporary `HERMES_HOME`. It asserts that the `delegate_task` schema carries the
three keys and stays byte-stable, that three panel seats build three children on
three models, and that a fourth seat is refused alone. Child runs are recorded
instead of started.
