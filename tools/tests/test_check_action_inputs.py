"""check_action_inputs must catch the exact defect that motivated it.

`.github/workflows/labeler.yml` passed `syncLabels` where the action declares
`sync-labels`. The runner logs "Unexpected input(s)" and silently ignores the
value - label sync stayed off while the step comment promised it. The defect
passed actionlint and the entire test suite (both re-verified by reintroducing
it), so these tests pin the counterfactual: the check fails on the undeclared
key, and passes once the key is corrected.
"""

import base64
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_action_inputs  # noqa: E402


def _workflow(tmp_path: Path, uses: str, with_: dict | None = None) -> Path:
    step: dict = {"uses": uses}
    if with_ is not None:
        step["with"] = with_
    doc = {
        "name": "t",
        "on": ["push"],
        "jobs": {"j": {"runs-on": "ubuntu-latest", "steps": [step]}},
    }
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / "t.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tmp_path


def _local_action_with_inputs(tmp_path: Path, inputs: list[str]) -> Path:
    return _write_action(
        tmp_path,
        "demo",
        {k: {"description": k, "required": False} for k in inputs},
        [{"shell": "bash", "run": "true"}],
    )


def _write_action(tmp_path: Path, name: str, inputs: dict, steps: list[dict]) -> Path:
    d = tmp_path / ".github" / "actions" / name
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "name": name,
        "description": name,
        "inputs": inputs,
        "runs": {"using": "composite", "steps": steps},
    }
    (d / "action.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tmp_path


def test_declared_input_passes(tmp_path):
    _local_action_with_inputs(tmp_path, ["sync-labels", "configuration-path"])
    _workflow(tmp_path, "./.github/actions/demo", {"sync-labels": True})
    code, _ = check_action_inputs.check(tmp_path, fetch=lambda url, **_: None)
    assert code == 0


def test_undeclared_input_is_caught_the_labeler_case(tmp_path):
    """The counterfactual: `syncLabels` is not `sync-labels`."""
    _local_action_with_inputs(tmp_path, ["sync-labels"])
    _workflow(tmp_path, "./.github/actions/demo", {"syncLabels": True})
    code, report = check_action_inputs.check(tmp_path, fetch=lambda url, **_: None)
    assert code == 1
    assert any("FAIL" in line and "syncLabels" in line for line in report)


def test_remote_action_unknown_input_is_caught(tmp_path):
    _workflow(tmp_path, "actions/labeler@" + "a" * 40, {"syncLabels": True})
    body = yaml.safe_dump({"inputs": {"sync-labels": {"description": "x"}}})
    url = check_action_inputs.RAW_URL.format(repo="actions/labeler", ref="a" * 40,
                                             name="action.yml")
    code, report = check_action_inputs.check(
        tmp_path, fetch=lambda u, **_: body if u == url else None
    )
    assert code == 1
    assert any("syncLabels" in line for line in report)


def test_action_yaml_fallback(tmp_path):
    _workflow(tmp_path, "actions/labeler@" + "b" * 40, {"repo-token": "x"})
    body = yaml.safe_dump({"inputs": {"repo-token": {"description": "x"}}})
    yml = check_action_inputs.RAW_URL.format(repo="actions/labeler", ref="b" * 40,
                                             name="action.yml")
    yaml_ = check_action_inputs.RAW_URL.format(repo="actions/labeler", ref="b" * 40,
                                               name="action.yaml")
    code, _ = check_action_inputs.check(
        tmp_path, fetch=lambda u, **_: None if u == yml else (body if u == yaml_ else None)
    )
    assert code == 0


def test_unresolvable_action_is_a_tool_error_not_a_pass(tmp_path):
    _workflow(tmp_path, "actions/labeler@" + "c" * 40, {"x": "1"})
    code, report = check_action_inputs.check(tmp_path, fetch=lambda url, **_: None)
    assert code == 2  # cannot certify inputs it could not read
    assert any("cannot resolve" in line for line in report)


def test_docker_reference_is_skipped(tmp_path):
    _workflow(tmp_path, "docker://alpine:3.20", {"entrypoint": "/bin/sh"})
    code, report = check_action_inputs.check(tmp_path, fetch=lambda url, **_: None)
    assert code == 0
    assert any("skip" in line for line in report)


def test_step_without_with_is_fine(tmp_path):
    _write_action(tmp_path, "demo", {}, [{"shell": "bash", "run": "true"}])
    _workflow(tmp_path, "./.github/actions/demo")
    code, _ = check_action_inputs.check(tmp_path, fetch=lambda url, **_: None)
    assert code == 0


def test_composite_action_steps_are_scanned_too(tmp_path):
    """The check covers .github/actions/*/action.yml, not only workflows."""
    _local_action_with_inputs(tmp_path, ["good-input"])
    _write_action(
        tmp_path,
        "caller",
        {},
        [{"uses": "./.github/actions/demo", "with": {"badInput": "1"}}],
    )
    code, report = check_action_inputs.check(tmp_path, fetch=lambda url, **_: None)
    assert code == 1
    assert any("badInput" in line for line in report)


def test_broken_yaml_is_a_tool_error(tmp_path):
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "broken.yml").write_text("jobs: [unclosed\n", encoding="utf-8")
    code, report = check_action_inputs.check(tmp_path, fetch=lambda url, **_: None)
    assert code == 2
    assert any("unparseable" in line or "cannot resolve" in line for line in report)


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


class _Opener:
    """Fake opener: fail `failures` times, then return `body`."""

    def __init__(self, failures: int, body: bytes = b"ok"):
        self.failures = failures
        self.body = body
        self.calls = 0

    def open(self, url, timeout=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise urllib.error.URLError("SSL: UNEXPECTED_EOF_WHILE_READING")
        return _Response(self.body)


def _raw(repo: str = "actions/labeler", ref: str = "a" * 40, name: str = "action.yml") -> str:
    return check_action_inputs.RAW_URL.format(repo=repo, ref=ref, name=name)


def test_opener_ignores_windows_registry_proxy(monkeypatch):
    calls = {"env": 0, "system": 0}

    def env_proxies():
        calls["env"] += 1
        return {}

    def system_proxies():
        calls["system"] += 1
        return {"https": "http://localhost:1080"}

    monkeypatch.setattr(check_action_inputs.urllib.request, "getproxies_environment", env_proxies)
    monkeypatch.setattr(check_action_inputs.urllib.request, "getproxies", system_proxies)

    check_action_inputs.build_opener()
    assert calls == {"env": 1, "system": 0}

    check_action_inputs.build_opener(system_proxy=True)
    assert calls["system"] == 1


def test_fetch_retries_transient_tls_error_then_succeeds():
    opener = _Opener(failures=2, body=b"inputs: {}\n")
    body = check_action_inputs.fetch_url(
        _raw(), opener=opener, attempts=3, sleep=lambda seconds: None
    )
    assert body == "inputs: {}\n"
    assert opener.calls == 3


def test_fetch_does_not_retry_404():
    class NotFound:
        calls = 0

        def open(self, url, timeout=None):
            self.calls += 1
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    opener = NotFound()
    assert check_action_inputs.fetch_url(_raw(), opener=opener, attempts=3) is None
    assert opener.calls == 1


def test_fetch_exhausts_attempts_as_tool_error():
    opener = _Opener(failures=99)
    with pytest.raises(check_action_inputs.FetchError):
        check_action_inputs.fetch_url(
            _raw(), opener=opener, attempts=2, sleep=lambda seconds: None
        )
    assert opener.calls == 2


def test_auto_transport_falls_back_to_gh():
    def urllib_fetch(url):
        raise urllib.error.URLError("SSL: UNEXPECTED_EOF_WHILE_READING")

    fetch = check_action_inputs.make_fetch(
        "auto", urllib_fetch=urllib_fetch, gh_fetch=lambda url: "from-gh"
    )
    assert fetch(_raw()) == "from-gh"


def test_auto_transport_without_gh_reraises(monkeypatch):
    monkeypatch.setattr(check_action_inputs.shutil, "which", lambda name: None)

    def urllib_fetch(url):
        raise urllib.error.URLError("boom")

    fetch = check_action_inputs.make_fetch("auto", urllib_fetch=urllib_fetch, gh_fetch=None)
    with pytest.raises(urllib.error.URLError):
        fetch(_raw())


def test_fetch_via_gh_decodes_base64():
    body = "inputs:\n  sync-labels:\n    description: x\n"
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    seen: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = encoded
        stderr = ""

    def runner(args, **kwargs):
        seen.append(args)
        return Proc()

    assert check_action_inputs.fetch_via_gh(_raw(), runner=runner) == body
    assert seen == [
        [
            "gh",
            "api",
            f"repos/actions/labeler/contents/action.yml?ref={'a' * 40}",
            "--jq",
            ".content",
        ]
    ]


def test_fetch_via_gh_404_returns_none():
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "gh: Not Found (HTTP 404)"

    assert check_action_inputs.fetch_via_gh(_raw(), runner=lambda *a, **k: Proc()) is None


def test_fetch_via_gh_failure_is_a_tool_error():
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "gh: authentication required"

    with pytest.raises(OSError, match="authentication required"):
        check_action_inputs.fetch_via_gh(_raw(), runner=lambda *a, **k: Proc())



def test_fetch_via_gh_handles_refs_with_slashes():
    body = "inputs: {}\n"
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    seen: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = encoded
        stderr = ""

    def runner(args, **kwargs):
        seen.append(args)
        return Proc()

    url = _raw(ref="feature/windows")
    assert (
        check_action_inputs.fetch_via_gh(url, runner=runner, ref="feature/windows")
        == body
    )
    assert seen[0][2] == (
        "repos/actions/labeler/contents/action.yml?ref=feature/windows"
    )


def test_remote_action_with_slash_ref_and_subdirectory(tmp_path):
    _workflow(tmp_path, "actions/labeler/sub@feature/windows", {"good-input": "1"})
    body = yaml.safe_dump({"inputs": {"good-input": {"description": "x"}}})
    url = check_action_inputs.RAW_URL.format(
        repo="actions/labeler", ref="feature/windows", name="sub/action.yml"
    )

    def fetch(u, ref=None):
        return body if (u, ref) == (url, "feature/windows") else None

    code, report = check_action_inputs.check(tmp_path, fetch=fetch)
    assert code == 0, report