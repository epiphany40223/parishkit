"""Disposable runtime failures expose only code locations, not private values."""

import json

from .runtime_setup_provider import failure_diagnostic


def test_runtime_fixture_diagnostic_omits_error_message_and_stack_locals():
    """A real traceback remains useful without weakening application log redaction."""
    private = "SYNTHETIC-PRIVATE-VALUE"
    try:
        raise ValueError(private)
    except ValueError as error:
        diagnostic = failure_diagnostic(error)
    assert diagnostic["exception"] == "ValueError"
    assert diagnostic["frames"][0]["file"] == "test_runtime_setup_diagnostics.py"
    assert diagnostic["frames"][0]["line"] > 0
    assert private not in json.dumps(diagnostic)
