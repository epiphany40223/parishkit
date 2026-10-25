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

    def exchange(payload, *, seconds, check, helper, on_timeout=None):
        """Restart the finite fixture for each independently validated full load."""
        nonlocal responses
        check()
        request = json.loads(payload)
        if request["url"].rstrip("/").endswith("organizations/search"):
            responses = iter(pages)
        result = next(responses)
        check()
        return b"200\n" + json.dumps(result).encode()

    def validate(
        target,
        settings,
        value,
        *,
        seconds,
        check,
        profile,
        base_url=None,
        redirect=None,
    ):
        """The installed target owner still admits its exact candidate and context."""
        from parishkit.stewardship.deployment import DeploymentProfile

        check()
        assert target in {"parishsoft", "google_workspace"}
        assert value and seconds > 0
        assert isinstance(profile, DeploymentProfile)
        return True

    def lookup(value, *, check, profile, base_url=None):
        """The key reaches one organization that holds Families (#1554).

        The real lookup runs an isolated helper that talks to ParishSoft;
        the finite fixture answers in its place, as it does for the load.
        """
        check()
        assert value and profile
        return {
            "outcome": "ok",
            "organizations": [{"id": 1, "name": "Example Parish", "families": True}],
            "unchecked": 0,
            "elapsed": 0.0,
        }

    def source_check(value, *, organization, check, profile, base_url=None):
        """ParishSoft answers the quick parish data check (#2201).

        The real check runs an isolated helper that talks to ParishSoft; the
        finite fixture answers in its place, as it does for the lookup.
        """
        check()
        assert value and profile and organization == 1
        return {"outcome": "ok", "name": "Example Parish", "families": 2, "members": 3}

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
    provider_checks.lookup_organizations = lookup
    provider_checks.check_source = source_check
    readiness_delivery_process.submit_sample = send
    from parishkit.stewardship.cli import main as cli

    cli(sys.argv[1:])


if __name__ == "__main__":
    main()
