# Models: how pstack roles pick a model on Hermes

pstack skills name a **role line** for every spawn (`how explorer`,
`interrogate reviewers`, `feature, refactoring`, ...). On Hermes a role becomes
a model only through the **pstack delegation backend**
([`hermes-plugin/pstack-delegation`](../hermes-plugin/pstack-delegation/README.md)).
Core `delegate_task` drops a per-task `model` without an error and refuses
`readonly`, so without the backend every spawn runs on the parent model.

## Where a role's model comes from

With `delegation.backend: pstack`, each task's `pstack_role` (plus `seat` for
panel roles) resolves in this order:

1. **Your panel**: `pstack-models.json` beside the hermes `config.yaml`, per
   profile. `setup-pstack` writes it. Plugin updates leave it alone.
2. **Aliases**: `delegation.backends.pstack.aliases` in `config.yaml` map any
   entry string, such as an upstream slug, to a real route.
3. **Upstream defaults**: upstream pstack's Cursor slugs (`grok-4.7-xhigh-fast`,
   `claude-opus-5-5-xhigh`). An unmapped default runs on the parent route and
   the label says so. To refuse it instead, set
   `delegation.backends.pstack.unmapped_defaults: refuse`.

Each result's `route.label` records what the child actually ran on.

## Entry grammar

| Entry | Route |
|---|---|
| `inherit-parent` / `auto` | The parent route. |
| `<model>@<effort>` | That model at that reasoning effort, on the default route's provider. |
| `<model>` | That model on the default route's provider. |
| `profile:<name>` | Run the child as that Hermes profile, with its own model, credentials, and skills. |
| `{"model", "provider", "reasoning_effort", "service_tier", "fallback_providers"}` | An explicit route. Credentials never go in the panel. |

Panel roles take a list, one subagent per entry: `how critics`, `arena runners`,
`arena cross-judge pool`, `architect runners`, and `interrogate reviewers`.
Check a panel with
`python3 hermes-plugin/pstack-delegation/routing.py --check <path>`.

## The shipped template

`config/models.json` is generated from `tools/assets/model-panel.json`. It is a
template, and no skill reads it at run time. Every role is `inherit-parent`, and
each panel role has three seats. Copy it as the starting point for your
`pstack-models.json`, or run `setup-pstack`.

## Fan-out limits

A one-shot run (`hermes -z`) and a cron tick allow at most
`delegation.oneshot_max_children` children. The default is 2. A three-seat panel
such as `interrogate reviewers` needs that value raised to 3 or more in the
profile that runs it, or the whole call is refused before any child starts.
Interactive sessions use `delegation.max_concurrent_children`.

Verified live: one `delegate_task` call asked for seats 0, 1, and 2 of
`["claude-opus-5-5", "profile:reviewer", "inherit-parent"]`. The children ran on
`claude-opus-5-5`, `gpt-6-astra-900k`, and `claude-sonnet-5-5[1m]`.

## Related

- `tools/slug_drift.py` checks configured `vendor/model` slugs against the
  OpenRouter catalog by hand. With `--prose`, it also scans skill text for stale
  slug mentions.
