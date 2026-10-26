"""Queue and answer setup's quick parish data check in tests (#2201).

Check parish data queues a check of the staged ParishSoft key under the web
login, and the isolated ParishSoft installer answers it. These helpers run
the web's real ``start_check`` and the installer's real ``run_pending``
under their exact grants, with a canned helper answer (no ParishSoft
request).
"""

from unittest.mock import patch

from parishkit.stewardship.accounts.setup_checks import run_pending, start_check
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.deployment import DeploymentProfile

from ..test_setup_forms import VALUES

# What the helper answers for the synthetic parish unless a test says otherwise.
CHECKED = {
    "outcome": "ok",
    "name": "St. Example Parish",
    "families": 812,
    "members": 2034,
}


def queue_check(request, service, attempt_id):
    """Check parish data, as the page's button does, under the web login."""
    from .test_runtime_auth_grants_postgresql import web_login

    version = SetupAttempt.objects.get(pk=attempt_id).version
    with web_login():
        return start_check(request, service, attempt_id, expected_version=version)


def answer_check(private=None, answer=None):
    """Answer the queued check as the ParishSoft installer would.

    ``private`` is the ParishSoft target's private handoff (by default the
    tests' fixed key), which opens the staged key; ``answer`` is the
    helper's answer (default ``CHECKED``). Returns whether a check was
    answered.
    """
    from .test_handoff_discovery_postgresql import key
    from .test_setup_exchange_postgresql import target_login

    private = key("parishsoft") if private is None else private
    with (
        patch(
            "parishkit.stewardship.provider_checks.check_source",
            return_value=CHECKED if answer is None else answer,
        ),
        target_login("parishsoft", reconnect=True),
    ):
        return run_pending(private, check=lambda: None, profile=DeploymentProfile.LOCAL)


def checked(request, service, attempt_id, private=None, answer=None):
    """Queue a check of the current key and answer it; returns the attempt."""
    queue_check(request, service, attempt_id)
    assert answer_check(private, answer)
    return SetupAttempt.objects.get(pk=attempt_id)


def checked_setup(service):
    """A collecting attempt with its Parish profile, ParishSoft key and check.

    The key's organization is chosen from its lookup (#1554) and its parish
    data checked (#2201), with no setup load: what Review and the final
    confirmation need since #2201. Returns ``(request, attempt)``.
    """
    from parishkit.stewardship.source.models import SourceMutationLease
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    from .test_runtime_auth_grants_postgresql import web_login
    from .test_setup_credentials_postgresql import staged

    # The source pointer and lease the final load uses, as the setup load's
    # tests make them (a fresh install has both rows).
    SourceCurrent.objects.get_or_create(singleton=True)
    SourceMutationLease.objects.get_or_create(singleton=True)
    request, attempt, _, private = staged(service)
    with web_login():
        save_section(
            request,
            service,
            attempt.attempt_id,
            step="parish",
            values=VALUES["parish"],
            expected_version=attempt.version,
        )
    return request, checked(request, service, attempt.attempt_id, private)
