# Package layout

Which paths are **generated** (converter output) vs **hand-written** (source):

| Path | Kind | Notes |
|---|---|---|
| `plugin.json` | generated | Root agent-plugin manifest (JSON). Rebuilt by `tools/convert.py`. Never hand-edit. |
| `skills/` | generated | All `SKILL.md` and references. Rebuilt by converter. |
| `config/models.json` | generated | Model panel from `tools/assets/model-panel.json`. |
| `assets/` | copied | Verbatim from upstream Cursor manifest asset refs. |
| `README.md` | generated | Built from the README template inside `tools/convert.py`. Never hand-edit. |
| `LICENSE` | generated | Upstream MIT plus the adaptation line, written by `tools/convert.py`. Never hand-edit. |
| `.build-provenance.txt` | generated | Rebuild stamp. |
| `tools/`, `tools/assets/` | hand-written | Converter, validator, bans, assets source. Edit here, then rebuild. |
| `docs/`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `NOTICE.md`, `CHANGELOG.md` | hand-written | Docs and contract. |
| `hermes-plugin/` | hand-written | Hermes plugins, not part of the generated package. `pstack-delegation` is the deprecated role-routed delegation backend (`delegation.backend: pstack`); the skills route through the official `advanced-delegation` `model_map` backend instead ([MODELS.md](../MODELS.md)). `poteto-delegation-gate` blocks working tools on a poteto-mode turn until the todolist records the delegation decision. |

Rule: change `tools/` / `tools/assets/`, rebuild with `tools/convert.py`, validate. Never hand-edit generated surfaces.
