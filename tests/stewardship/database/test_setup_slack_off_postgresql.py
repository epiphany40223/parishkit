"""Turning Slack off during setup discards its staged token and test (#1499).

A save that leaves the Slack step off continues as if Slack had never been
on: migration 0111 lets the web scrub the attempt's staged Slack token, for
its owner, only while the saved Slack step is off, and the queued Slack test
is cancelled. The Turn off Slack notifications button on the Slack
connection step, Test Slack and the failed-test notice posts that step's
own form.
"""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from contextlib import nullcontext
from functools import partial
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError
from django.db.models import F
from django.test import Client
from django.utils import timezone

from parishkit.stewardship.accounts.setup_credentials import stage_credential
from parishkit.stewardship.accounts.setup_drafts import save_section, view_draft
from parishkit.stewardship.accounts.setup_notification_models import SetupSlackDelivery
from parishkit.stewardship.accounts.setup_preview_views import _readiness
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential
from parishkit.stewardship.accounts.setup_wizard import observed, wizard_for
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_setup_notifications_postgresql import ready
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
web_login = partial(task_login, ServiceRole.WEB, exact=True, reconnect=True)
OFF = {"enabled": False, "channel_id": ""}
SLACK_STEPS = {"slack_credential", "slack_test"}


def turn_off(request, service, status):
    """Save the Slack step off, as the button and an unticked box both do."""
    with web_login():
        return save_section(
            request,
            service,
            status.attempt_id,
            step="slack",
            values=OFF,
            expected_version=status.version,
        )


def keys(request, service):
    """The step keys the stepper shows for this sign-in's draft."""
    with web_login():
        return {step.key for step in wizard_for(view_draft(request, service)).steps}


def test_turning_slack_off_discards_the_token_and_the_queued_test(
    setup_service, monkeypatch, tmp_path
):
    """One save scrubs the staged token, cancels the queued test and bumps the
    draft version once; the Slack steps leave the stepper and Review."""
    request, status, _, _, identifier = ready(setup_service, monkeypatch, tmp_path)
    assert SetupSlackDelivery.objects.get(pk=identifier).state == "queued"
    assert keys(request, setup_service) >= SLACK_STEPS
    after = turn_off(request, setup_service, status)
    assert after.version == status.version + 1
    credential = SetupSealedCredential.objects.get(target="slack")
    assert credential.scrubbed_at is not None and credential.ciphertext is None
    assert credential.settings == {}
    assert SetupSlackDelivery.objects.get(pk=identifier).state == "cancelled"
    # The scrub is audited for the Administrator, the cancel as a system
    # outcome, as the stale-test sweep records it.
    scrubbed = AuditEvent.objects.get(
        event_type="setup_credential_scrubbed", subject_id=credential.pk
    )
    assert scrubbed.actor_id == request.portal_session.principal_id
    assert AuditEvent.objects.filter(
        event_type="setup_slack_cancelled", actor_id=None
    ).exists()
    with web_login():
        draft = view_draft(request, setup_service)
        credentials, tests = observed(draft)
    assert "slack" not in credentials and "slack_test" not in tests
    assert not SLACK_STEPS & keys(request, setup_service)
    # Review lists no Slack test, so Finish setup waits for none.
    assert [item["label"] for item in _readiness(draft, tests)] == ["Test email"]


def test_turning_slack_on_again_needs_a_new_token_and_test(
    setup_service, monkeypatch, tmp_path
):
    """The discarded token is never offered again: the same channel needs a
    new token, staged as a new row beside the scrubbed tombstone."""
    request, status, _, _, _ = ready(setup_service, monkeypatch, tmp_path)
    status = turn_off(request, setup_service, status)
    with web_login():
        status = save_section(
            request,
            setup_service,
            status.attempt_id,
            step="slack",
            values={"enabled": True, "channel_id": "CFIXTURE"},
            expected_version=status.version,
        )
        wizard = wizard_for(view_draft(request, setup_service))
        assert wizard.step("slack_credential").state != "done"
        assert wizard.step("slack_test").state != "done"
        status, receipt = stage_credential(
            request,
            setup_service,
            status.attempt_id,
            target="slack",
            candidate=b"synthetic-private-slack",
            expected_version=status.version,
        )
        wizard = wizard_for(view_draft(request, setup_service))
    assert not receipt.scrubbed
    rows = SetupSealedCredential.objects.filter(target="slack")
    assert rows.count() == 2
    assert rows.filter(scrubbed_at=None).get().pk == receipt.identifier
    assert wizard.step("slack_credential").state == "done"
    assert wizard.step("slack_test").state != "done"


def test_saving_slack_off_without_a_staged_token_changes_nothing_else(
    setup_service, monkeypatch, tmp_path
):
    """Slack turned off before any token or test is an ordinary save."""
    request, status, _, _, _ = ready(
        setup_service, monkeypatch, tmp_path, enqueue=False
    )
    status = turn_off(request, setup_service, status)
    status = turn_off(request, setup_service, status)
    assert SetupSealedCredential.objects.filter(target="slack").count() == 1
    assert not SetupSlackDelivery.objects.exists()


@pytest.mark.parametrize("target", ["slack", "google_workspace"])
def test_the_guard_refuses_a_collecting_scrub_unless_slack_is_off(
    setup_service, monkeypatch, tmp_path, target
):
    """Only the Slack token, and only once the saved Slack step is off, may
    be scrubbed while setup is collecting (0111's guard)."""
    request, status, _, _, _ = ready(
        setup_service, monkeypatch, tmp_path, enqueue=False
    )

    def scrub():
        """Scrub the staged credential directly, as the owner, from the web."""
        with web_login(), work_transaction():
            SetupSealedCredential.objects.filter(target=target).update(
                ciphertext=None,
                settings={},
                scrubbed_at=timezone.now(),
                actor_id=request.portal_session.principal_id,
                version=F("version") + 1,
            )

    with pytest.raises(DatabaseError):
        scrub()
    turn_off(request, setup_service, status)
    if target == "slack":
        assert SetupSealedCredential.objects.get(target="slack").scrubbed_at
    else:
        with pytest.raises(DatabaseError):
            scrub()
        assert SetupSealedCredential.objects.get(target=target).scrubbed_at is None


@pytest.mark.parametrize("who", ["no actor", "another actor", "not the web"])
def test_the_guard_refuses_a_slack_off_scrub_by_anyone_but_the_owner_on_the_web(
    setup_service, monkeypatch, tmp_path, who
):
    """With Slack saved off, the collecting scrub still needs the attempt's
    owner as actor and the web's role (0111's guard)."""
    request, status, _, _, _ = ready(
        setup_service, monkeypatch, tmp_path, enqueue=False
    )
    # Save Slack off without the save's own discard, so the token is still
    # staged when the direct scrubs below try it.
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_credentials.discard_slack",
        lambda attempt, *, actor_id: None,
    )
    turn_off(request, setup_service, status)
    owner = request.portal_session.principal_id
    actor = {"no actor": None, "another actor": uuid4()}.get(who, owner)
    login = web_login if who != "not the web" else nullcontext

    def scrub(actor_id):
        """Scrub the staged Slack token directly, as ``actor_id``."""
        with login(), work_transaction():
            SetupSealedCredential.objects.filter(target="slack").update(
                ciphertext=None,
                settings={},
                scrubbed_at=timezone.now(),
                actor_id=actor_id,
                version=F("version") + 1,
            )

    with pytest.raises(DatabaseError, match="Only a setup with Slack turned off"):
        scrub(actor)
    assert SetupSealedCredential.objects.get(target="slack").scrubbed_at is None
    # The owner on the web may, so the refusal above is the condition's own.
    login = web_login
    scrub(owner)
    assert SetupSealedCredential.objects.get(target="slack").scrubbed_at


def browser_for(service, monkeypatch, tmp_path, *, enqueue=False):
    """The original sign-in's browser on a complete draft with Slack staged."""
    request, status, _, _, identifier = ready(
        service, monkeypatch, tmp_path, enqueue=enqueue
    )
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    return browser, request, SimpleNamespace(status=status, identifier=identifier)


@pytest.mark.parametrize(
    "path", ["/admin/setup/credentials/slack", "/admin/setup/slack-test"]
)
def test_each_slack_page_offers_turning_slack_off_through_the_slack_step(
    setup_http, monkeypatch, tmp_path, path
):
    """The Slack connection and Test Slack pages post the Slack step's own
    form with Slack off, above their own form."""
    browser, _, _ = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        response = browser.get(path)
    assert response.status_code == 200
    html = response.content.decode()
    form = html.split("data-setup-slack-off>", 1)[1].split("</form>", 1)[0]
    assert '<form method="post" action="/admin/setup/slack"' in html
    assert '<input type="hidden" name="channel_id" value="">' in form
    assert 'name="version"' in form and "Turn off Slack notifications" in form
    assert "Slack is optional." in form
    assert html.index("data-setup-slack-off") < html.rindex("<form")


def test_the_button_saves_slack_off_and_opens_the_next_step_not_done(
    setup_http, monkeypatch, tmp_path
):
    """CSRF and the exact draft version are required; the save opens Test
    email (the version bump re-requires it), and Test Slack is gone: its
    address opens Test email too, and a Slack test post is refused."""
    browser, request, ready_state = browser_for(
        setup_http, monkeypatch, tmp_path, enqueue=True
    )
    version = ready_state.status.version
    fields = {"version": str(version), "channel_id": ""}
    with web_login():
        # The page sets the CSRF cookie; a post without its token is refused.
        assert browser.get("/admin/setup/slack-test").status_code == 200
        assert browser.post("/admin/setup/slack", fields).status_code == 403
        stale = post(browser, "/admin/setup/slack", fields | {"version": "1"})
        assert stale.status_code == 409
        assert not SetupSealedCredential.objects.filter(
            target="slack", scrubbed_at__isnull=False
        ).exists()
        response = post(browser, "/admin/setup/slack", fields)
        assert response.status_code == 302
        assert response["Location"] == "/admin/setup/mail-test"
        # Back or an old tab on Test Slack opens the same next step, and a
        # Slack test can no longer be sent (#1505).
        back = browser.get("/admin/setup/slack-test")
        assert back.status_code == 302
        assert back["Location"] == "/admin/setup/mail-test"
        assert browser.head("/admin/setup/slack-test").status_code == 302
        assert post(browser, "/admin/setup/slack-test", {}).status_code == 400
        page = browser.get("/admin/setup/mail-test")
    assert page.status_code == 200
    assert "Test Slack" not in page.content.decode()
    assert SetupSealedCredential.objects.get(target="slack").scrubbed_at is not None
    row = SetupSlackDelivery.objects.get(pk=ready_state.identifier)
    assert row.state == "cancelled"
    assert request.portal_session.principal_id
