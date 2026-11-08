---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget. Detects your available models and writes the per-route model table to `delegation.backends.model_map` in the hermes `config.yaml`, outside the plugin tree, so it survives plugin updates. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write pstack's routes to `delegation.backends.model_map` in the hermes `config.yaml` of the active profile (`~/.hermes/config.yaml`, or `$HERMES_HOME/config.yaml` for a named profile) - **outside** the plugin directory. pstack replaces the installed package on every `plugins update`, so a table written inside the plugin tree is silently discarded, while `config.yaml` survives reinstalls. The routes belong to the `model_map` delegation backend of the `advanced-delegation` plugin (`delegation.backend: model_map`), and every other delegation in the profile picks from the same table (`../poteto-mode/references/hermes-delegation.md`).

## Steps

### 1. Detect available models

Enumerate the model slugs available in this session (the configured providers' catalog); that is the dependable source. If you cannot detect any, ask the user via `clarify` to paste the slugs they have access to. Never write a real slug you have not confirmed is available. `inherit-parent` is always valid even though it is not a detected slug (hermes has no Cursor-style `auto` selector; the parent chat model IS the inherit-parent semantic).

### 2. Load current state

The default role-to-model mapping is the shape shown in step 5 below. If `config.yaml` already has `delegation.backends.model_map.options` (`hermes delegation config --show` prints them), treat each pstack route's model and reasoning effort as the current choice for every role on that route (the role table is in `../poteto-mode/references/hermes-delegation.md`). The routes record no budget label, so ask for one. Otherwise start from these defaults. Do NOT try to recover an older in-package `config/models.json`: a plugin update replaces that file with the shipped default before this skill runs, so anything found there is the default rather than the user's saved choices, and copying it forward would silently discard their configuration. If they expected a configuration to still be there, tell them plainly that an update overwrote it and ask them to re-run setup-pstack. A line whose role is not in step 5 is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer clarify over free text. Offer these four options with these exact labels, and name the current budget when the rule records one. With no rule, say that `large` matches the skill defaults.

- `unlimited — max reasoning`
- `large — xhigh reasoning`
- `medium — high reasoning`
- `small — medium reasoning`

**(b) Apply it.** Build the working table from the skill defaults, and on a re-run keep any role you changed by family, list, or alias (`inherit-parent`). On a fresh configuration every role starts as `inherit-parent` (the parent chat model), so setting concrete slugs is what lets the budget's effort tiers apply. `unlimited`, `large`, `medium`, and `small` set the effort token of every real slug, panel entries included, to `max`, `xhigh`, `high`, or `medium`. The effort token is the last token, or the one before a trailing `fast`, on the ladder `max` > `xhigh` > `high` > `medium` > `low`. If the result is not a detected slug, use the same family's detected slug with the highest effort at or below the target, else mark the role as needing a choice. `inherit-parent` does not change. So `unlimited` turns `claude-opus-5-5-xhigh` into `claude-opus-5-5-max`. Grok slugs top out at `xhigh`, so under `unlimited` the fallback puts Grok at `xhigh` and keeps `grok-4.7-xhigh-fast` as it is. `large` keeps both defaults. `small` turns them into `claude-opus-5-5-medium` and `grok-4.7-medium-fast`.

**(c) Show the roles and confirm.** Show every role with its model, marking any real slug not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles, offering the detected models plus `inherit-parent` (this role runs on the parent chat model) as the options. Prefer clarify over free text. For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one subagent runs per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena runs one judge. It tries only values from a family other than the parent's, in list order, so order it by preference. An `inherit-parent` value never judges. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real slug written must be in the detected set; `inherit-parent` always passes. If a chosen real slug is not available, stop and ask again.

### 5. Write the routes

Group the roles by route with the role table in `../poteto-mode/references/hermes-delegation.md`. Every role on one route shares its model, so when the chosen models differ inside a route, ask the user which one the route takes. Write each choice as a Hermes model id plus its effort, `<model>@<effort>`, or as `{"provider": ..., "model": ..., "reasoning_effort": ...}` for a provider other than the main one. Save the choices to a scratch file in this shape, then build the block and write it in one command. The builder keeps routes the profile added itself and replaces pstack's own, so re-runs stay idempotent:

```json
{
  "budget": "large",
  "roles": {
    "feature, refactoring": "inherit-parent",
    "bug-fix": "inherit-parent",
    "perf-issue": "inherit-parent",
    "hillclimb": "inherit-parent",
    "judgment and prose": "inherit-parent",
    "hardest tasks": "inherit-parent",
    "how explorer": "inherit-parent",
    "how explainer": "inherit-parent",
    "how critics": ["inherit-parent"],
    "why investigators": "inherit-parent",
    "why synthesizer": "inherit-parent",
    "reflect tooling": "inherit-parent",
    "reflect judgment, divergent, synthesizer": "inherit-parent",
    "arena runners": ["inherit-parent"],
    "arena cross-judge pool": ["inherit-parent"],
    "swarm workers": "inherit-parent",
    "architect runners": ["inherit-parent"],
    "interrogate reviewers": ["inherit-parent"]
  }
}
```

```sh
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
BLOCK=$("$HOME/.hermes/hermes-agent/venv/bin/python" "$HOME_DIR/plugins/pstack/tools/model_map_options.py" --panel <scratch file> --config "$HOME_DIR/config.yaml")
hermes config set delegation.backends.model_map "$BLOCK"
hermes config set delegation.backend model_map
```

The builder fails with the reason when roles on one route name different models; ask the user and run it again. Panel roles (how critics, arena runners, arena cross-judge pool, architect runners, interrogate reviewers) take an ARRAY, and all five share one list: each entry becomes one `panel-` route, so the list length sets the seat count. `swarm workers` is the default for every worker unless a race assigns another model per arm. The routes need the `advanced-delegation` plugin. If `hermes plugins list` does not show it enabled, ask before running `hermes plugins install advanced-delegation`.

### 6. Confirm

Tell the user the rule was written and that it applies to new sessions. Re-running this skill updates it.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, load the create-verification-skill skill via skill_view (it ships in this plugin) and follow it. On no, move on without pushing.

### 8. Offer a formal verification skill (optional)

Check whether the project already has a formal verification skill (a project skill that runs a model checker, a proof, or another verifier against the code). If not, offer once: "want a formal verification skill, so agents can prove a key property of your code holds in every case in scope, not only the cases tests reach? I can generate one with /create-formal-verification-skill." On yes, load the create-formal-verification-skill skill via skill_view (it ships in this plugin) and follow it. On no, move on without pushing.
