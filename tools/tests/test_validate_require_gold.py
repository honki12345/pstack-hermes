"""--require-gold: an unavailable loader must never look like a pass.

CI runs `validate.py --require-gold`; without it, a runner with no hermes
install degrades to the static mirror and exits 0, which is how the GOLD layer
went unenforced for the entire history of this repo.

The state split matters too. Two distinct failures were previously collapsed:
"the environment is broken" (a harness problem) and "the loader refused the
package" (an input problem). Reporting either as the other sends the reader to
the wrong place.
"""

import validate


def code(**overrides):
    base = {
        "static_failed": False,
        "loader_state": "ran",
        "loader_ok": True,
        "require_gold": False,
    }
    base.update(overrides)
    return validate.resolve_exit_code(**base)


def state(gold_status, load_status):
    return validate.loader_state({"status": gold_status}, {"status": load_status})


# --- state classification -------------------------------------------------


def test_both_checks_returning_structured_output_means_ran():
    assert state("ok", "ok") == "ran"
    assert state("ok", "nonzero-exit") == "ran"
    assert state("nonzero-exit", "nonzero-exit") == "ran"


def test_loader_rejecting_the_package_still_counts_as_ran():
    # The loader imported and then raised on the input: the harness is fine.
    assert state("ok", "call-failed") == "ran"
    assert state("call-failed", "call-failed") == "ran"


def test_import_or_subprocess_failure_means_broken():
    assert state("import-failed", "ok") == "broken"
    assert state("ok", "no-json-stdout") == "broken"
    assert state("error", "error") == "broken"


def test_partial_run_is_broken_never_skipped():
    # One check ran, the other did not (environment vanished mid-run, or one
    # probe errored). Half a verification is not a skip.
    assert state("ok", "unavailable") == "broken"
    assert state("unavailable", "ok") == "broken"
    assert state("ok", "skipped") == "broken"


def test_no_environment_at_all_means_unavailable():
    assert state("unavailable", "unavailable") == "unavailable"
    assert state("unavailable", "skipped") == "unavailable"


# --- traceback classification ---------------------------------------------


def test_failure_before_the_import_marker_is_a_broken_environment():
    # ModuleNotFoundError is the obvious case; the marker is what actually
    # decides, so a SyntaxError during import lands here too instead of being
    # mistaken for a rejection.
    stderr = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 3, in <module>\n'
        "ModuleNotFoundError: No module named 'hermes_cli'"
    )
    assert validate._loader_failure_status(stderr) == "import-failed"

    syntax = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 4, in <module>\n'
        "SyntaxError: invalid syntax"
    )
    assert validate._loader_failure_status(syntax) == "import-failed"


def test_failure_after_the_import_marker_is_a_rejection():
    stderr = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 7, in <module>\n'
        "hermes_cli.agent_plugins.AgentPluginError: invalid server declaration\n"
        f"{validate.LOADER_IMPORT_MARKER}\n"
    )
    assert validate._loader_failure_status(stderr) == "call-failed"


def test_unparseable_output_without_traceback_is_unknown():
    assert validate._loader_failure_status("") == "no-json-stdout"
    assert validate._loader_failure_status("some noise") == "no-json-stdout"


# --- exit codes -----------------------------------------------------------


def test_clean_run_is_zero():
    assert code() == 0


def test_static_failure_is_one_even_when_the_loader_passed():
    assert code(static_failed=True) == 1


def test_loader_ran_and_rejected_is_two_with_or_without_the_flag():
    assert code(loader_ok=False) == 2
    assert code(loader_ok=False, require_gold=True) == 2


def test_absent_loader_skips_by_default_and_fails_when_required():
    assert code(loader_state="unavailable") == 0
    assert code(loader_state="unavailable", require_gold=True) == 3


def test_broken_loader_never_skips_silently():
    assert code(loader_state="broken") == 2
    assert code(loader_state="broken", require_gold=True) == 3


def test_static_failure_outranks_a_missing_loader():
    assert code(static_failed=True, loader_state="unavailable", require_gold=True) == 1
    assert code(static_failed=True, loader_state="broken", require_gold=True) == 1