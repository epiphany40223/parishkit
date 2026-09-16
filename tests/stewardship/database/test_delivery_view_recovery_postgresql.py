"""Operational failures, admission changes and replay remain recoverable HTML."""

# ruff: noqa: F811 -- pytest injects the imported fixture by name.

from uuid import uuid4

import pytest
from django.db import DatabaseError
from django.db.models import F

from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import delivery_views
from parishkit.stewardship.jobs.delivery_resolution_models import DeliveryResolution
from parishkit.stewardship.jobs.recipient_models import RecipientRefusalResolution
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_outbox_boundaries_postgresql import control
from .test_recipient_suppressions_postgresql import refused, remember

pytestmark = pytest.mark.django_db(transaction=True)


def post(browser, path, values):
    """Use the browser's real CSRF secret under restricted SQL session identity."""
    with task_login(ServiceRole.WEB, exact=True):
        return browser.post(
            path, values, HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value
        )


def test_missing_delivery_refusal_and_task_are_not_outages(family_mail, google):
    """Authenticated stale UUID links get 404, never a retryable service outage."""
    browser, _ = signed_in()
    missing = uuid4()
    for path in (
        f"/admin/deliveries/{missing}",
        f"/admin/deliveries/refusals/{missing}",
    ):
        assert browser.get(path).status_code == 404
    common = dict(command_id=str(uuid4()), note="Private evidence")
    assert (
        post(
            browser,
            f"/admin/deliveries/{missing}/resolve",
            common | dict(expected_version="1", action="note"),
        ).status_code
        == 404
    )
    source = SourceCurrent.objects.get()
    assert (
        post(
            browser,
            f"/admin/deliveries/refusals/{missing}/clear",
            common
            | dict(
                source_snapshot_id=str(source.snapshot_id),
                source_generation=str(source.generation),
                verified="yes",
            ),
        ).status_code
        == 404
    )
    assert (
        post(
            browser,
            f"/admin/background/tasks/{missing}/retry-family-preparation",
            dict(command_id=str(uuid4())),
        ).status_code
        == 404
    )


def test_refusal_clearance_hides_dirty_source_and_returns_conflict(family_mail, google):
    """A concurrent reconciliation change is a reloadable conflict, not an outage."""
    harness = activate_response_service(family_mail)
    refusal = remember(refused(harness))
    browser, _ = signed_in()
    source = SourceCurrent.objects.get()
    CampaignCredentialState.objects.update(
        population_dirty=True, version=F("version") + 1
    )
    path = f"/admin/deliveries/refusals/{refusal.pk}"
    page = browser.get(path)
    assert page.status_code == 200 and b'name="verified"' not in page.content
    response = post(
        browser,
        path + "/clear",
        dict(
            command_id=str(uuid4()),
            source_snapshot_id=str(source.snapshot_id),
            source_generation=str(source.generation),
            verified="yes",
            note="Private evidence",
        ),
    )
    assert response.status_code == 409
    assert b"Private evidence" not in response.content
    assert not RecipientRefusalResolution.objects.exists()


def test_pause_hides_resend_without_hiding_evidence_acceptance(family_mail, google):
    """UI commands follow the same fresh-send admission as the authoritative SQL."""
    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(harness)
        control(harness.campaign, "pause")
        browser, _ = signed_in()
        response = browser.get(f"/admin/deliveries/{message.pk}")
        assert response.status_code == 200
        assert b'name="action" value="resend"' not in response.content
        assert b'name="action" value="accept"' in response.content


def test_resend_replay_does_not_require_keys_again(family_mail, google, monkeypatch):
    """A retained receipt survives a later key-loader outage without new effects."""
    monkeypatch.setattr(
        delivery_views,
        "_retry_inputs",
        lambda: dict(
            general=family_mail.rings.general,
            public=family_mail.rings.public,
            public_origin="http://localhost:8000",
        ),
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        browser, _ = signed_in()
        path = f"/admin/deliveries/{message.pk}/resolve"
        values = dict(
            command_id=str(uuid4()),
            expected_version=str(message.version),
            action="resend",
            note="Confirmed",
            duplicate_acknowledged="yes",
        )
        assert post(browser, path, values).status_code == 302

        def unavailable():
            """Fail if the replay needlessly asks for encryption configuration."""
            raise AssertionError("Replay must not load keys")

        monkeypatch.setattr(delivery_views, "_retry_inputs", unavailable)
        assert post(browser, path, values).status_code == 302
        assert DeliveryResolution.objects.count() == 1


def test_paginated_empty_evidence_does_not_claim_no_evidence_exists(
    family_mail, google
):
    """A later shared history page explains its scope and links back to page one."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        browser, _ = signed_in()
        response = browser.get(f"/admin/deliveries/{message.pk}?page=2&size=1")
        assert response.status_code == 200
        assert b"No Admin evidence on this page." in response.content
        assert b"Previous page" in response.content
        assert b"page=1" in response.content


def test_database_outage_has_fixed_private_recovery(auth_service, google, monkeypatch):
    """A genuine unavailable dependency stays 503 without a second chrome query."""
    browser, _ = signed_in()

    def unavailable(*args, **kwargs):
        """Simulate a private database exception at the view's read boundary."""
        raise DatabaseError("private-database-marker")

    monkeypatch.setattr(delivery_views, "listing", unavailable)
    response = browser.get("/admin/deliveries")
    assert response.status_code == 503 and response["Retry-After"] == "5"
    assert b"private-database-marker" not in response.content
