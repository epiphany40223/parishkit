"""Answer and choose a setup ParishSoft organization lookup in tests (#1554).

A staged ParishSoft key has no organization until its lookup is answered by
the isolated ParishSoft installer and the Administrator chooses one. These
helpers run the installer's real ``run_pending`` under its exact grants with
a canned helper answer (no ParishSoft request), and the web's real
``choose_organization`` under the web login.
"""

from unittest.mock import patch

from parishkit.stewardship.accounts.setup_lookups import (
    choose_organization,
    current_lookup,
    run_pending,
)
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.deployment import DeploymentProfile

# The one organization a test key reaches unless a test says otherwise.
ORGANIZATIONS = [{"id": 1, "name": "St. Example Parish", "families": True}]


def found(organizations=None, *, unchecked=0):
    """A helper answer listing ``organizations`` (default ``ORGANIZATIONS``)."""
    return {
        "outcome": "ok",
        "organizations": list(
            ORGANIZATIONS if organizations is None else organizations
        ),
        "unchecked": unchecked,
        "elapsed": 120.0 if unchecked else None,
    }


def answer_lookup(private=None, answer=None):
    """Answer the queued lookup as the ParishSoft installer would.

    ``private`` is the ParishSoft target's private handoff (the tests'
    ``publish``; by default its fixed test key), which opens the staged
    key; ``answer`` is the helper's answer (default ``found()``).
    """
    from .test_handoff_discovery_postgresql import key
    from .test_setup_exchange_postgresql import target_login

    private = key("parishsoft") if private is None else private

    with (
        patch(
            "parishkit.stewardship.provider_checks.lookup_organizations",
            return_value=found() if answer is None else answer,
        ),
        target_login("parishsoft", reconnect=True),
    ):
        return run_pending(private, check=lambda: None, profile=DeploymentProfile.LOCAL)


def choose(request, service, attempt_id, organization_id=1):
    """Choose ``organization_id`` from the open lookup, as the web page does."""
    from .test_runtime_auth_grants_postgresql import web_login

    lookup = current_lookup(attempt_id)
    version = SetupAttempt.objects.get(pk=attempt_id).version
    with web_login():
        return choose_organization(
            request,
            service,
            attempt_id,
            lookup_id=lookup.identifier,
            organization_id=organization_id,
            expected_version=version,
        )


def answered_and_chosen(request, service, attempt_id, private=None, organization_id=1):
    """Answer the lookup with ``organization_id`` alone and choose it."""
    answer_lookup(
        private,
        found(
            [{"id": organization_id, "name": "St. Example Parish", "families": True}]
        ),
    )
    return choose(request, service, attempt_id, organization_id)
