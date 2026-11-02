#!/usr/bin/env python3
"""Fail when a step passes a `with:` key the action does not declare.

The runner treats an unrecognized input as a warning, not an error: it logs
"Unexpected input(s) 'X'" and silently ignores the value. That is how
`.github/workflows/labeler.yml` shipped `syncLabels: true` against an action
whose input id is `sync-labels` - label sync stayed off while the step comment
promised it, and no gate noticed. Verified against the other gates by
reintroducing the exact defect: `actionlint` exits 0 (it cannot read remote
action metadata) and the whole pytest suite passes unchanged. This is the gate
for that class: a silent misconfiguration that only the live runner log showed.

Resolution rules:
- local actions (`./...`) are read from disk;
- `docker://` references have no declared inputs and are skipped;
- everything else is fetched at the pinned ref, trying `action.yml` then
  `action.yaml`.

Transport:
- default `auto` fetches from raw.githubusercontent.com, falling back to
  `gh api` (api.github.com) when the direct fetch fails and `gh` is available;
- `urllib` and `gh` force one transport.

Proxy handling: the default opener uses **environment** proxies only. Python's
implicit opener falls back to the Windows WinINET registry, where a stale
`ProxyServer=localhost:...` produces `SSL: UNEXPECTED_EOF_WHILE_READING` for
hosts that curl/git/gh reach fine. `--system-proxy` opts back into the registry
for machines that genuinely rely on Internet Options.

A fetch or parse failure for a needed action is a tool error (exit 2), never a
pass - the check cannot certify inputs it could not read. Exit codes: 0 clean,
1 findings, 2 tool error.
"""

from __future__ import annotations

import argparse
import base64
import functools
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW_BASE = "https://raw.githubusercontent.com/"
RAW_URL = RAW_BASE + "{repo}/{ref}/{name}"
FETCH_TIMEOUT_SECONDS = 20
FETCH_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (0.5, 1.0, 2.0)


class FetchError(OSError):
    """A remote fetch failed after retries, with a proxy hint when relevant."""


def build_opener(system_proxy: bool = False) -> urllib.request.OpenerDirector:
    """Opener that honours env proxies only unless `system_proxy` is set."""
    proxies = (
        urllib.request.getproxies()
        if system_proxy
        else urllib.request.getproxies_environment()
    )
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


@functools.lru_cache(maxsize=1)
def default_opener() -> urllib.request.OpenerDirector:
    return build_opener()


def _proxy_hint(system_proxy: bool = False) -> str:
    """Explain a registry-proxy failure only when it is the likely cause."""
    if urllib.request.getproxies_environment():
        return ""  # an explicit env proxy is in use; not the registry trap
    system = urllib.request.getproxies()
    if not system:
        return ""
    if system_proxy:
        return (
            f" [system proxy {system} was used (--system-proxy); if it cannot "
            "reach the host, unset it or set NO_PROXY=<host>]"
        )
    return (
        f" [system proxy {system} is configured; this tool ignores the Windows "
        "registry proxy by default - retry with --system-proxy or set "
        "NO_PROXY=<host>]"
    )


def fetch_url(
    url: str,
    opener: urllib.request.OpenerDirector | None = None,
    attempts: int = FETCH_ATTEMPTS,
    sleep=time.sleep,
    system_proxy: bool = False,
) -> str | None:
    """Return the body, or None when the resource does not exist (404).

    Retries connection/TLS/5xx failures with backoff. A 4xx other than 404 is
    not retried. Exhausted retries raise FetchError (an OSError), never None.
    """
    opener = opener or default_opener()
    attempts = max(1, attempts)
    last: OSError | None = None
    for attempt in range(1, attempts + 1):
        try:
            with opener.open(url, timeout=FETCH_TIMEOUT_SECONDS) as resp:
                # Untrusted input, but parsed as YAML data only - never executed.
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code < 500:
                raise
            last = exc
        except OSError as exc:
            last = exc
        if attempt < attempts:
            sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
    assert last is not None  # the loop either returned, raised, or set `last`
    raise FetchError(f"{last}{_proxy_hint(system_proxy)}") from last


def fetch_via_gh(
    url: str, runner=subprocess.run, ref: str | None = None
) -> str | None:
    """Fetch a raw action file through `gh api` (authenticated api.github.com).

    A ref can contain `/`, so a raw URL cannot be split back into ref and path
    on its own. Callers that know the ref pass it; the remainder after
    `owner/repo/<ref>/` is then the file path.
    """
    if not url.startswith(RAW_BASE):
        raise ValueError(f"not a raw.githubusercontent.com URL: {url!r}")
    owner, repo_name, remainder = url[len(RAW_BASE) :].split("/", 2)
    repo = f"{owner}/{repo_name}"
    if ref is None:
        ref, name = remainder.split("/", 1)
    else:
        prefix = f"{ref}/"
        if not remainder.startswith(prefix):
            raise ValueError(f"{ref!r} is not the ref in {url!r}")
        name = remainder[len(prefix) :]
    endpoint = f"repos/{repo}/contents/{name}?ref={ref}"
    try:
        proc = runner(
            ["gh", "api", endpoint, "--jq", ".content"],
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"gh api timed out after {FETCH_TIMEOUT_SECONDS}s") from exc
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "404" in err or "Not Found" in err:
            return None
        raise OSError(f"gh api failed ({proc.returncode}): {err or 'no stderr'}")
    payload = (proc.stdout or "").strip()
    if not payload or payload == "null":
        return None
    try:
        return base64.b64decode(payload).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise OSError(f"gh api returned undecodable content: {exc}") from exc


def make_fetch(
    transport: str = "auto",
    system_proxy: bool = False,
    attempts: int = FETCH_ATTEMPTS,
    urllib_fetch=None,
    gh_fetch=None,
):
    """Build the fetch callable for a transport (`auto` / `urllib` / `gh`)."""
    if transport not in {"auto", "urllib", "gh"}:
        raise ValueError(f"unknown transport: {transport!r}")
    if transport == "gh":
        if gh_fetch is not None:
            return lambda url, ref=None: gh_fetch(url)
        return fetch_via_gh
    opener = build_opener(system_proxy)
    direct = urllib_fetch or (
        lambda url: fetch_url(
            url, opener=opener, attempts=attempts, system_proxy=system_proxy
        )
    )
    if transport == "urllib":
        return direct
    fallback = gh_fetch or fetch_via_gh

    def auto(url: str, ref: str | None = None) -> str | None:
        try:
            return direct(url)
        except OSError as exc:
            if gh_fetch is None and shutil.which("gh") is None:
                raise
            try:
                if gh_fetch is not None:
                    return gh_fetch(url)
                return fallback(url, ref=ref)
            except OSError as gh_exc:
                raise FetchError(f"{exc}; gh fallback failed: {gh_exc}") from exc

    return auto


def _steps_from(doc: object) -> list[dict]:
    """All step mappings in a workflow file or a composite action file."""
    if not isinstance(doc, dict):
        return []
    runs = doc.get("runs")
    if isinstance(runs, dict) and runs.get("using") == "composite":
        return list(runs.get("steps") or [])
    steps: list[dict] = []
    for job in (doc.get("jobs") or {}).values():
        if isinstance(job, dict):
            for step in job.get("steps") or []:
                if isinstance(step, dict):
                    steps.append(step)
    return steps


def iter_usages(root: Path):
    """Yield (rel_path, step_index, uses, with_dict) for every action step."""
    targets = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    targets += sorted((root / ".github" / "actions").glob("*/action.y*ml"))
    for path in targets:
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            yield path.relative_to(root).as_posix(), -1, None, exc
            continue
        rel = path.relative_to(root).as_posix()
        for i, step in enumerate(_steps_from(doc)):
            uses = step.get("uses")
            if isinstance(uses, str):
                yield rel, i, uses, dict(step.get("with") or {})


def declared_inputs(root: Path, uses: str, fetch) -> set[str] | None:
    """Declared input ids for `uses`, or None when it cannot be resolved.

    Raises ValueError for a malformed local reference (caller reports it).
    """
    if uses.startswith("./"):
        action_dir = root / uses[2:]
        for name in ("action.yml", "action.yaml"):
            candidate = action_dir / name
            if candidate.is_file():
                doc = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                return set((doc.get("inputs") or {}).keys())
        raise ValueError(f"no action.yml next to {uses}")
    if uses.startswith("docker://"):
        return None  # Docker actions declare no inputs; nothing to check.
    if "@" not in uses:
        raise ValueError(f"uses without a ref: {uses!r}")
    action, _, ref = uses.partition("@")
    parts = action.split("/")
    if len(parts) < 2:
        raise ValueError(f"not an owner/repo reference: {uses!r}")
    repo = "/".join(parts[:2])
    subdir = "/".join(parts[2:])
    prefix = f"{subdir}/" if subdir else ""
    for name in ("action.yml", "action.yaml"):
        body = fetch(
            RAW_URL.format(repo=repo, ref=ref, name=f"{prefix}{name}"), ref=ref
        )
        if body is not None:
            doc = yaml.safe_load(body) or {}
            return set((doc.get("inputs") or {}).keys())
    raise ValueError(f"no action.yml or action.yaml at {repo}@{ref}")


def check(
    root: Path = ROOT,
    fetch=None,
    *,
    transport: str = "auto",
    system_proxy: bool = False,
    attempts: int = FETCH_ATTEMPTS,
) -> tuple[int, list[str]]:
    """Run the check; return (exit_code, report lines)."""
    if fetch is None:
        fetch = make_fetch(
            transport=transport, system_proxy=system_proxy, attempts=attempts
        )
    report: list[str] = []
    failures = 0
    tool_errors = 0
    resolved: dict[tuple[str, str], set[str] | None] = {}

    for rel, index, uses, payload in iter_usages(root):
        if index < 0:
            report.append(f"error: {rel}: unparseable YAML: {payload}")
            tool_errors += 1
            continue
        if uses is None:
            continue
        cache_key = uses
        if cache_key not in resolved:
            try:
                resolved[cache_key] = declared_inputs(root, uses, fetch)
            except (ValueError, urllib.error.URLError, OSError) as exc:
                report.append(f"error: {rel} step[{index}]: cannot resolve {uses}: {exc}")
                resolved[cache_key] = False  # sentinel: unresolved
                tool_errors += 1
        declared = resolved[cache_key]
        if declared is False:
            continue
        if declared is None:
            report.append(f"skip: {rel} step[{index}]: {uses} (no inputs concept)")
            continue
        unknown = sorted(k for k in payload if k not in declared)
        if unknown:
            failures += 1
            report.append(
                f"FAIL: {rel} step[{index}]: {uses} does not declare "
                f"{', '.join(repr(k) for k in unknown)} "
                f"(declared: {', '.join(sorted(declared)) or 'none'})"
            )

    if failures:
        return 1, report
    if tool_errors:
        return 2, report
    return 0, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_action_inputs.py", description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root")
    ap.add_argument(
        "--transport",
        choices=("auto", "urllib", "gh"),
        default="auto",
        help="auto: raw.githubusercontent.com then gh api fallback (default)",
    )
    ap.add_argument(
        "--system-proxy",
        action="store_true",
        help="use the Windows WinINET registry proxy (ignored by default)",
    )
    ap.add_argument(
        "--attempts",
        type=int,
        default=FETCH_ATTEMPTS,
        help=f"total fetch attempts per file (default {FETCH_ATTEMPTS})",
    )
    args = ap.parse_args(argv)
    code, report = check(
        args.root,
        transport=args.transport,
        system_proxy=args.system_proxy,
        attempts=args.attempts,
    )
    stream = sys.stderr if code else sys.stdout
    for line in report:
        print(line, file=stream)
    if code == 0:
        print("action inputs: every with: key is declared by its action")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
