"""Synthetic external responses inside actual disposable runtime service owners."""

import json
import sys
from pathlib import Path
from traceback import walk_tb


def failure_diagnostic(error):
    """Synthetic harness diagnostics retain code locations, never values or locals."""
    return {
        "event": "SYNTHETIC_RUNTIME_FAILURE",
        "exception": type(error).__name__,
        "frames": [
            {
                "file": Path(frame.f_code.co_filename).name,
                "function": frame.f_code.co_name,
                "line": line,
            }
            for frame, line in walk_tb(error.__traceback__)
        ],
    }


def main():
    """Replace only network boundaries; keep CLI, grants, journals and leases real."""
    from parishkit import parishsoft_transport
    from parishkit.stewardship import (
        observability,
        provider_checks,
        readiness_delivery_process,
    )
    from parishkit.stewardship.readiness_delivery import DeliveryOutcome

    original_failure = observability.emit_failure

    def report_failure(error, **kwargs):
        """Expose safe fixture-only trace locations for intermittent worker failures."""
        print(json.dumps(failure_diagnostic(error)), file=sys.stderr, flush=True)
        return original_failure(error, **kwargs)

    observability.emit_failure = report_failure

    pages = json.loads(sys.argv.pop(1))
    responses = iter(())

    def exchange(payload, *, seconds, check):
        """Restart the finite fixture for each independently validated full load."""
        nonlocal responses
        check()
        request = json.loads(payload)
        if request["url"].rstrip("/").endswith("organizations/search"):
            responses = iter(pages)
        result = next(responses)
        check()
        return b"200\n" + json.dumps(result).encode()

    def validate(target, settings, value, *, seconds, check):
        """The installed target owner still admits its exact candidate and context."""
        check()
        assert target in {"parishsoft", "google_workspace"}
        assert value and seconds > 0
        return True

    def send(value, settings, mail, *, seconds, check):
        """Provider acceptance is synthetic; persisted submission ownership is not."""
        check()
        assert value and seconds > 0
        assert mail.recipient == settings["recipient"] == "testing@example.org"
        assert "TEST" in mail.message()["Subject"]
        check()
        return DeliveryOutcome.ACCEPTED

    parishsoft_transport._exchange = exchange
    provider_checks.check_candidate = validate
    readiness_delivery_process.submit_sample = send
    from parishkit.stewardship.cli import main as cli

    cli(sys.argv[1:])


if __name__ == "__main__":
    main()
