"""The Production confirmation accepts a full automation session (ADM-11 PR 5).

A genuine go-live through cleanup and link preparation (the confirmation
suites' fixtures), then a confirmation row built from a real signed preview,
so every other clause of ``stewardship_production_confirmation_guard_v1``
passes and only the two clauses 0013 changed decide: the five-minute sign-in
and the sign-in after cleanup. The automation session's sign-in instant is
moved a day back, before cleanup finished, so both clauses are exercised.
The old guard refuses it; the new one refuses a read-only, revoked, expired
or another Administrator's session; and ``confirmation_commands.confirm``
through the automation caller confirms Production, the Python post-cleanup
check included.
"""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core import signing
from django.db import DatabaseError, transaction

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.admin_caller import AdminCaller
from parishkit.stewardship.accounts.confirmation_commands import SALT, confirm
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.runtime import campaign_transaction

from .automation_builders import HOST
from .test_automation_fresh_guards_postgresql import (
    age,
    expire,
    old_guard,
    shift,
    unexpire,
)
from .test_confirmation_readiness_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    prepare,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_confirmation_sql_postgresql import fresh
from .test_setup_mail_views_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)
GUARD = "stewardship_production_confirmation_guard_v1"


def approved(service, login, *, scope="full"):
    """An automation session approved from ``login``'s fresh browser session.

    Returns ``(secret, row)``. The pairing request is built as ``login
    start`` builds it; approval runs through the service, as the page does.
    """
    secret = secrets.token_urlsafe(32)
    request = automation.pairing_request(
        digest=automation.secret_digest(secret),
        host_digest=HOST,
        name="ops",
        label="confirmation",
        expect_email="admin@example.org",
        scope={"read_only": "read-only"}.get(scope, scope),
        days=30,
    )
    principal = current_principal(service.store, login.portal_session.principal_id)
    with transaction.atomic():
        row = automation.approve(
            principal, login.portal_session, request, scope=scope, days=30
        )
    return secret, row


def caller(service, secret):
    """One command's caller, admitted as the command line admits it."""
    return AdminCaller.from_automation(secret, HOST, store=service.store)


class Admitted(Exception):
    """The guard admitted the row; raised to roll the insert back."""


def refused(values, campaign_id):
    """Insert a confirmation in the ordered transaction; the SQL state refusing it.

    An admitted row is rolled back too (None), so later cases start clean.
    """
    try:
        with campaign_transaction(campaign_id, correlation_id=uuid4()):
            ProductionConfirmation.objects.create(**values)
            raise Admitted
    except Admitted:
        return None
    except DatabaseError as error:
        return error.__cause__.sqlstate


def test_the_confirmation_guard_and_command_accept_a_full_session(ready_links):
    """Both changed clauses: refused by the old guard and every narrower session."""
    preparation, arguments = prepare(ready_links)
    login, service, campaign_id = arguments[:3]
    with web_login():
        fresh(arguments)
    secret, row = approved(service, login)
    reader_secret, reader = approved(service, login, scope="read_only")
    ended_secret, ended = approved(service, login)
    # A day back: older than five minutes, and before cleanup completed.
    age(timedelta(days=1))
    shift(reader, timedelta(seconds=1))
    full = caller(service, secret)
    narrow = caller(service, reader_secret)
    revoked = caller(service, ended_secret)
    automation.revoke(
        ended.pk,
        current_principal(service.store, ended.principal_id),
        reason="revoked_by_owner",
    )
    with web_login():
        preview, _, token = fresh(arguments)
    state = preview.readiness
    bound = signing.loads(token, salt=SALT)

    def values(command):
        """The confirmation the page would insert, for ``command``'s session."""
        return dict(
            request_id=preparation.transition_id,
            preparation_id=preparation.pk,
            activation_id=uuid4(),
            generation_id=state.generation_id,
            request_key=uuid4(),
            session_id=command.portal_session.pk,
            actor_id=command.portal_session.principal_id,
            correlation_id=uuid4(),
            authenticated_at=command.portal_session.authenticated_at,
            expires_at=preview.expires_at,
            preview_at=state.observed_at,
            preview_counts=bound["counts"],
            readiness_digest=state.digest,
            impact_revision=state.impact_revision,
            target_state=state.target_state,
            expected_request_version=state.transition.version,
            expected_campaign_version=state.campaign.version,
            expected_runtime_version=state.campaign.systemconfiguration_set.get().version,
        )

    with old_guard(GUARD), web_login():
        assert refused(values(full), campaign_id) == "42501"
    with web_login():
        assert refused(values(full), campaign_id) is None
        assert refused(values(narrow), campaign_id) == "42501"
        assert refused(values(revoked), campaign_id) == "42501"
        assert refused(values(full) | {"actor_id": uuid4()}, campaign_id) == "42501"
    expire(row)
    with web_login():
        assert refused(values(full), campaign_id) == "42501"
    unexpire(row)
    # The page's confirmation through the command line's caller: the Python
    # post-cleanup check accepts it too, and Production is confirmed.
    with web_login():
        confirm(full, *arguments[1:], token=token, typed="Production")
    confirmation = ProductionConfirmation.objects.get()
    assert confirmation.session_id == full.portal_session.pk
    assert confirmation.authenticated_at == full.portal_session.authenticated_at
