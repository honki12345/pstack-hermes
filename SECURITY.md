# Security Policy

## Supported versions

The `main` branch (and its `v*` tags) is the only supported line; `develop` is
the integration branch. This repository, `honki12345/pstack-hermes`, is an
independently maintained fork — see [NOTICE.md](NOTICE.md). The package is content-only
(markdown skills + config); the tooling (`tools/`) is Python 3.11.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** on this repository
(Security tab → Report a vulnerability). Reports stay private until a fix is
ready.

## This is a port — read this before reporting anywhere

`pstack-hermes` is an **unofficial adaptation** of Lauren Tan's Cursor plugin
for the Hermes Agent platform. The skills loaded from this package are **not**
upstream text: they are upstream text plus this project's adaptation passes
(T1–T12) — Cursor-only mechanics reworded to hermes mechanisms, two upstream
containers excluded for install-scanner compliance, and one hermes-native
replacement skill added.

Anything caused by those adaptations — or by how this package is configured,
installed, or enabled — is **this project's responsibility, not upstream's**.
Report it here.

### Adaptations that are by design (not bugs, not upstream problems)

| Observation | Reality |
|---|---|
| `make-bot-ui` and `automations/benny` are missing | Excluded deliberately: their installer / copy-instruction patterns hard-block the community-source install scanner. The make-bot-ui slot is filled by the hermes-native `hermesbot` skill. |
| Skills say `delegate_task`, `clarify`, `session_search`, `hermes cron`, or `goal.md` where Cursor docs say Task / AskQuestion / transcript files / `/loop` / `/goal` | Port adaptation passes (T6–T12). The Cursor originals do not exist on hermes. |
| `config/models.json` panel and `inherit-parent` defaults | Port-local configuration written by `setup-pstack`. A bad or unresolvable model slug here is a local configuration problem. |
| The package no longer matches upstream pstack file-for-file | Expected. See the differences contract in the package README and the machine-generated fix register in `.build-provenance.txt`. |
| Plan-checker marker, ban list, validator, anchor-audit behavior | This repository's own tooling (`tools/`) — report here. |

### Where to report what

| Problem | Report to |
|---|---|
| This package's content, tooling, adaptations, configuration, install steps, or docs | **This repository** — Issues, or private vulnerability reporting for security matters |
| A hermes platform bug in how Hermes handles valid plugin input (scanner, loader, manifest parsing, scheduler, gateway, CLI) — including defects that surface only while a plugin is installed | Upstream: <https://github.com/NousResearch/hermes-agent> |
| A bug in the original Cursor plugin that also reproduces **without** this port | Upstream: <https://github.com/cursor/plugins/tree/main/pstack> (MIT © Lauren Tan) |

Do **not** file this port's misadjustments, misconfigurations, or
misinterpretations upstream. The test is ownership, not visibility: ask
whether the fault lies in this package (content, tooling, adaptation,
configuration) or in Hermes' own handling of valid plugin input. A defect in
Hermes' scanner, loader, or manifest handling can surface only while a plugin
is installed — disabling the plugin hides it without making it a port
problem — so file those upstream. If you cannot tell which side owns it, open
an issue here first with the platform, the exact command, and
`hermes plugins doctor <package> --ci` output — triage starts here, and we
will escalate genuine upstream defects with a minimal reproduction.

## Scope notes

- The shipped package (generated surfaces at the repo root) performs no network calls, no telemetry,
  and no code execution beyond the scripts documented in each skill.
- The install-scanner contract (community-source safety verdict) is enforced
  in CI; the banned-construct list lives in `tools/bans.py` — the single
  source of truth shared by `validate.py` and the CI scanner gate.
