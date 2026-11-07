# Model mapping (for now)

pstack picks a model per role. Three layers, highest precedence first:

1. **Your panel** — `pstack-models.json` beside the hermes `config.yaml`
   (written by `setup-pstack`; survives plugin updates).
2. **The shipped panel** — `config/models.json` inside the package. What a role
   uses until you configure it.
3. **The method text** — skills name Cursor-family defaults
   (`grok-4.7-xhigh-fast`, `claude-opus-5-5-max`, `gpt-5.6-sol-max`); this port
   substitutes its own hermes-side defaults for those names, as mapped below.

## Upstream name → hermes-side default

| Upstream name in the method text | Roles it defaults | Hermes-side default, for now |
|---|---|---|
| `grok-4.7-xhigh-fast` | `feature, refactoring`, `bug-fix`, `perf-issue`, `hillclimb` | `z-ai/glm-5.2` |
| `grok-4.7-xhigh-fast` | `swarm workers`, `how explorer` | `meituan/longcat-2.0` |
| `claude-opus-5-5-max` | `judgment and prose`, `how explainer` | `meituan/longcat-2.0` |
| `claude-opus-5-5-max` | `why synthesizer`, `reflect judgment, divergent, synthesizer` | `z-ai/glm-5.2` |
| `gpt-5.6-sol-max` | `interrogate` reviewer slots | the `interrogate reviewers` list below |
| `inherit-parent` | any role set to it | unchanged — the role runs on the parent chat model |

The `vendor/model` values are ids from the provider catalog the tooling checks
against (OpenRouter); `uv run --frozen tools/slug_drift.py` re-verifies every
configured slug by hand and exits non-zero when one leaves the catalog. **This
mapping is provisional ("for now")** — it moves when upstream renames models or
the panel is refreshed, and `setup-pstack` lets you override every row.

## The shipped panel

`config/models.json` (shipped defaults at package root; 18 roles):

| Role | Default |
|---|---|
| feature, refactoring | `z-ai/glm-5.2` |
| bug-fix | `z-ai/glm-5.2` |
| perf-issue | `z-ai/glm-5.2` |
| hillclimb | `z-ai/glm-5.2` |
| judgment and prose | `meituan/longcat-2.0` |
| hardest tasks | `z-ai/glm-5.2` |
| how explorer | `meituan/longcat-2.0` |
| how explainer | `meituan/longcat-2.0` |
| how critics | `meituan/longcat-2.0`, `meta/muse-spark-1.2-contributor` |
| why investigators | `meituan/longcat-2.0` |
| why synthesizer | `z-ai/glm-5.2` |
| reflect tooling | `qwen/qwen-2.5-7b-instruct` |
| reflect judgment, divergent, synthesizer | `z-ai/glm-5.2` |
| arena runners | `meituan/longcat-2.0`, `meta/muse-spark-1.2-contributor` |
| arena cross-judge pool | `z-ai/glm-5.2`, `meituan/longcat-2.0` |
| swarm workers | `meituan/longcat-2.0` |
| architect runners | `z-ai/glm-5.2`, `meta/muse-spark-1.2-contributor` |
| interrogate reviewers | `z-ai/glm-5.2`, `meituan/longcat-2.0`, `meta/muse-spark-1.2-contributor` |

## Example `pstack-models.json`

`setup-pstack` writes one entry per role and overwrites the whole file on
re-runs, so it stays idempotent. A minimal user panel looks like:

```json
{
  "budget": "unlimited",
  "roles": {
    "feature, refactoring": "z-ai/glm-5.2",
    "bug-fix": "z-ai/glm-5.2",
    "judgment and prose": "meituan/longcat-2.0",
    "swarm workers": "meituan/longcat-2.0",
    "arena cross-judge pool": ["z-ai/glm-5.2", "meituan/longcat-2.0"]
  }
}
```

Run the `setup-pstack` skill to generate the full file (it detects the models
you actually have and applies the effort tiers); edit it directly for one-off
changes.

## Related

- `tools/slug_drift.py` — validates every configured slug against the live
  catalog; `--prose` also scans skill text for stale slug mentions.
- Panel storage: kept outside the package so `hermes plugins update` never
  discards it.
