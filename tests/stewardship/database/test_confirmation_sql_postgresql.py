"""Restricted SQL and competing connections enforce the final confirmation boundary."""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from uuid import uuid4

import pytest
from django.core import signing
from django.db import DatabaseError, connection
from django.db.models import F
from django.test import RequestFactory
from django.urls import reverse

from parishkit.stewardship.accounts.admin_session_store import SessionStore
from parishkit.stewardship.accounts.confirmation_commands import (
    SALT,
    confirm,
    verify_preview,
)
from parishkit.stewardship.accounts.installation_lock import (
    ConfigurationBusy,
    installation_lock,
)
from parishkit.stewardship.accounts.sessions import database_now, issue_admin
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.runtime import campaign_transaction
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from .test_activation_sql_races_postgresql import wait_for_work_lock
from .test_confirmation_readiness_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    prepare,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_setup_mail_views_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def fresh(arguments):
    """Use the actual session issuer, then obtain current signed preview evidence."""
    login, service = arguments[:2]
    issue_admin(
        login,
        login.portal_session.principal_id,
        store=service.store,
        authenticated_at=database_now(),
    )
    return verify_preview(*arguments)


def test_sql_cannot_borrow_session_scope_versions_or_unordered_activation(ready_links):
    """Even direct INSERT with web grants must pass the independent SQL boundary."""
    preparation, arguments = prepare(ready_links)
    login = arguments[0]
    with web_login():
        preview, _, token = fresh(arguments)
        state = preview.readiness
        bound = signing.loads(token, salt=SALT)
        values = dict(
            request_id=preparation.transition_id,
            preparation_id=preparation.pk,
            activation_id=uuid4(),
            generation_id=state.generation_id,
            request_key=uuid4(),
            session_id=login.portal_session.pk,
            actor_id=login.portal_session.principal_id,
            correlation_id=uuid4(),
            authenticated_at=login.portal_session.authenticated_at,
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
        with pytest.raises(DatabaseError) as failure:
            ProductionConfirmation.objects.create(**values)
        assert failure.value.__cause__.sqlstate == "42501"
        for patch in (
            {"actor_id": uuid4()},
            {"session_id": uuid4()},
            {"authenticated_at": values["authenticated_at"] - timedelta(minutes=6)},
            {"expected_request_version": values["expected_request_version"] + 1},
            {"expected_campaign_version": values["expected_campaign_version"] + 1},
            {"expected_runtime_version": values["expected_runtime_version"] + 1},
            {"impact_revision": state.impact_revision + 1},
            {"generation_id": uuid4()},
            {"preparation_id": uuid4()},
            {"expires_at": state.observed_at},
            {"preview_counts": bound["counts"] | {"unknown": 1}},
            {
                "target_state": "active"
                if state.target_state == "scheduled"
                else "scheduled"
            },
        ):
            with (
                pytest.raises(DatabaseError) as failure,
                campaign_transaction(state.campaign.pk, correlation_id=uuid4()),
            ):
                ProductionConfirmation.objects.create(**(values | patch))
            assert failure.value.__cause__.sqlstate in {"42501", "23514"}, patch
    assert not ProductionConfirmation.objects.exists()


def test_waiting_confirmation_rechecks_changed_impact_and_busy_http_is_retryable(
    ready_links,
):
    """Prove an actual lock wait, then reject the newly committed eligibility change."""
    preparation, arguments = prepare(ready_links)
    login, service = arguments[:2]
    with web_login():
        _, _, token = fresh(arguments)
    path = reverse("admin:go_live_confirm")
    started = Queue()

    def competing():
        """Use a separate web connection and cookie, never shared request state."""
        connection.close()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_web")
                cursor.execute("SET statement_timeout='5s'")
                cursor.execute("SELECT pg_backend_pid()")
                started.put(cursor.fetchone()[0])
            request = RequestFactory().post(path)
            request.session = SessionStore(login.session.session_key)
            try:
                confirm(
                    request, service, *arguments[2:], token=token, typed="Production"
                )
            except StaleRecordError:
                return "stale"
            return "committed"
        finally:
            connection.close()

    with web_login(), ThreadPoolExecutor(max_workers=1) as pool:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        with work_transaction():
            FamilyCampaign.objects.update(
                email_deliverable=False, version=F("version") + 1
            )
            result = pool.submit(competing)
            wait_for_work_lock(started.get(timeout=3))
        assert result.result(timeout=5) == "stale"
    assert not ProductionConfirmation.objects.exists()
    # Nonblocking installer admission is a recoverable refusal (the Confirm
    # Production dialog answers it with a 409; test_go_live_confirm), never
    # a confirmation made around another installer.
    with installation_lock(), ThreadPoolExecutor(max_workers=1) as pool:

        def submit():
            """Own the database handle solely within this competing thread."""
            connection.close()
            try:
                with web_login():
                    request = RequestFactory().post(path)
                    request.session = SessionStore(login.session.session_key)
                    confirm(
                        request,
                        service,
                        *arguments[2:],
                        token=token,
                        typed="Production",
                    )
            except ConfigurationBusy:
                return "busy"
            finally:
                connection.close()
            return "committed"

        assert pool.submit(submit).result(timeout=5) == "busy"
    assert not ProductionConfirmation.objects.exists()
