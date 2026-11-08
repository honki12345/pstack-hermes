# Models: how pstack roles pick a model on Hermes

pstack skills name a **role line** for every spawn (`how explorer`,
`interrogate reviewers`, `feature, refactoring`, ...). On Hermes a role becomes
a model through the **model routes** of the official `advanced-delegation`
plugin (`delegation.backend: model_map`). Core `delegate_task` drops a per-task
`model` without an error and refuses `readonly`, so without that backend every
spawn runs on the parent model.

## One route table for the whole profile

The routes live in `config.yaml` under `delegation.backends.model_map`. Each
route has a name, a one-line description, and a provider, model and reasoning
effort. `delegate_task` gains one per-task key, `option`, whose values are the
route names; the model reads only the names and descriptions. pstack's skills
pick a route by role through the table in
`skills/poteto-mode/references/hermes-delegation.md`, and every other delegation
in the profile picks from the same table.

| Route | pstack roles |
|---|---|
| `implement` | feature, refactoring · bug-fix · perf-issue · hillclimb · swarm workers |
| `explore` | how explorer · why investigators |
| `research` | none (general web and document research) |
| `quick` | reflect tooling |
| `deep` | judgment and prose · hardest tasks · how explainer · why synthesizer · reflect judgment, divergent, synthesizer |
| `general` | none (the default for tasks that pick no route) |
| `panel-*` | how critics · arena runners · arena cross-judge pool · architect runners · interrogate reviewers |

Panel roles run one task per `panel-` route, in the order the `option` list
shows them, so the number of `panel-` routes is the seat count. All five panel
roles share those seats.

Each result's `route` records the route name (`label`) and the provider, model
and reasoning effort the child actually ran on.

## Build the table

`setup-pstack` writes it. To build it by hand, or to migrate a profile that used
the retired pstack backend, give `tools/model_map_options.py` a role panel in
the `pstack-models.json` shape:

```sh
PY=~/.hermes/hermes-agent/venv/bin/python   # has PyYAML, needed for --config
BLOCK=$($PY tools/model_map_options.py --panel ~/.hermes/pstack-models.json --config ~/.hermes/config.yaml)
hermes config set delegation.backends.model_map "$BLOCK"
hermes config set delegation.backend model_map
```

`--config` supplies the main provider and model (`model.provider`,
`model.default`) and the default effort (`delegation.reasoning_effort`), and
keeps routes the profile added itself. Without PyYAML, pass `--provider`,
`--parent-model` and `--default-effort` instead and run it with `python3 -I`.
The command prints the block and writes nothing.

| Panel entry | Route |
|---|---|
| `inherit-parent` / `auto` | The main model at the default effort. |
| `<model>@<effort>` | That model at that reasoning effort, on the main provider. |
| `<model>` | That model on the main provider. |
| `{"model", "provider", "reasoning_effort", "service_tier", "fallback_providers"}` | An explicit route. |
| `profile:<name>` | Refused: a model route cannot run as a profile. |

The builder refuses a panel in which roles that share a route name different
models, or in which the five panel roles name different seat lists, and says
which roles differ. A role with no line takes its route's model from the roles
that have one. `research` copies `explore`, and `general` is the main model at
the default effort, which is the route a task with no role took under the
pstack backend.

With the panel this fork was migrated from, the table is:

| Route | Provider | Model | Effort |
|---|---|---|---|
| `implement`, `explore`, `research` | main | `claude-sonnet-5-5[1m]` | high |
| `quick` | main | `claude-sonnet-5-5[1m]` | medium |
| `deep` | main | `claude-opus-5-5[1m]` | xhigh |
| `general` (default) | main | `claude-opus-5-5[1m]` | high |
| `panel-opus` | main | `claude-opus-5-5[1m]` | xhigh |
| `panel-gpt` | `openai-codex` | `gpt-6-astra-900k` | xhigh |
| `panel-fable` | main | `claude-fable-5-1[1m]` | high |

`hermes delegation config --show` prints the active table.

Verified live in a throwaway profile (Hermes v0.23.0, `advanced-delegation`
1.1.0, this table): one `delegate_task` call with `panel-opus`, `panel-gpt`,
`panel-fable` and a task without `option` ran on `claude-opus-5-5[1m]` (xhigh),
`gpt-6-astra-900k` (xhigh), `claude-fable-5-1[1m]` (high) and `general`
(`claude-opus-5-5[1m]`, high). `option: panel-nonexistent` was refused before
any child started. `/pstack:interrogate` spawned its three adversaries on the
three `panel-` routes, and a `/pstack:poteto-mode` investigation sent its
investigators to `explore`.

## The shipped template

`config/models.json` is generated from `tools/assets/model-panel.json`. It is a
role-panel template, and no skill reads it at run time. Every role is
`inherit-parent`, and each panel role has three seats.

## The retired pstack backend

`hermes-plugin/pstack-delegation` (`delegation.backend: pstack`, task keys
`pstack_role`, `seat` and `access`, panel file `pstack-models.json`) is
deprecated. The shipped skills no longer send its keys, and under `model_map`
Hermes refuses a task that carries them. It stays in the repository until the
profiles that used it have moved, so `hermes config set delegation.backend
pstack` still works as a rollback. Under it the skills send no `option`, and
every spawn runs on the parent route.

## Fan-out limits

A one-shot run (`hermes -z`) and a cron tick may spawn at most
`delegation.oneshot_max_children` children in total over the whole run. The
default is 2, and 0 means unlimited. One `interrogate reviewers` round already
spends 3, so size the budget for every round the run may do (for example 12),
or the call that crosses it is refused before any of its children start.
Interactive sessions are not charged. They are bounded per call by
`delegation.max_concurrent_children` (default 10).

## Related

- `tools/slug_drift.py` checks configured `vendor/model` slugs against the
  OpenRouter catalog by hand. With `--prose`, it also scans skill text for stale
  slug mentions.
