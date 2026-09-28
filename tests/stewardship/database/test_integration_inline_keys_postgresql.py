"""A key saved on its settings page installs and switches with no server step."""

import re
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType
from uuid import uuid4

import pytest
from django.utils import timezone

from parishkit.stewardship.accounts import integration_credentials as credentials
from parishkit.stewardship.accounts.configuration_errors import (
    ConfigurationReadinessUnavailable,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_installation import CredentialInstaller
from parishkit.stewardship.accounts.integration_credentials import (
    CredentialSummary,
    selection_key,
    summary,
)
from parishkit.stewardship.accounts.key_files import (
    file_fingerprint,
    read_private,
    write_private,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.credential_runtime import acknowledge_rotations
from parishkit.stewardship.deployment import ServiceRole, load_deployment

from . import campaign_builders
from .auth_builders import signed_in
from .test_configuration_service_postgresql import (  # noqa: F401
    as_config_installer,
    config_role,
)
from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401
from .test_integration_views_postgresql import (  # noqa: F401
    URL,
    handoff,
    hidden,
    post,
)

pytestmark = pytest.mark.django_db(transaction=True)
PRIOR = b"synthetic-working-parishsoft-key"
CANDIDATE = b"synthetic-next-parishsoft-key"


@pytest.fixture
def working(request, monkeypatch, tmp_path, google):
    """A signed-in Admin, the applied PRIOR key and a worker reading its folder."""
    original = campaign_builders.configuration_document

    def document():
        """Only fixture initialization supplies the existing working fingerprint."""
        result = original()
        result["sections"]["integrations"][0]["values"]["credential_fingerprint"] = (
            file_fingerprint(PRIOR)
        )
        return result

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    service = request.getfixturevalue("auth_service")
    private = request.getfixturevalue("handoff")
    request.getfixturevalue("config_role")
    deployment = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    path = deployment.paths["credentials"] / "parishsoft" / "credential"
    path.parent.mkdir(parents=True, mode=0o700)
    write_private(path, PRIOR)
    worker = replace(
        deployment,
        service_role=ServiceRole.WORKER,
        secrets=MappingProxyType({"parishsoft": path}),
    )
    # Receipt publication reads Linux /proc process identity; its own tests
    # cover it. Here the acknowledgement's SQL effects are what matter.
    published = []
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.publish_single_process_receipts",
        lambda configuration, receipts: published.append(dict(receipts)),
    )
    browser, _ = signed_in()
    return {
        "service": service,
        "browser": browser,
        "installer": CredentialInstaller(
            CredentialFiles(path, private),
            validate=lambda value: value == CANDIDATE,
        ),
        "worker": worker,
        "published": published,
    }


def save(value, candidate=CANDIDATE, **settings):
    """Submit the page's own form with a pasted key, as a browser would."""
    browser, store = value["browser"], value["service"].store
    page = browser.get(URL)
    fields = {
        "action": "preview",
        "base_digest": store.active().digest,
        "organization_id": "12345",
        "intent": hidden(page, "intent"),
        "candidate": candidate.decode(),
    } | settings
    with identity("pk_stewardship_web"):
        return post(browser, URL, fields)


def install(value, request_id):
    """Run the configuration installer once for the key's selection request."""
    with as_config_installer():
        return install_request(
            value["service"].store, request_id=request_id, correlation_id=uuid4()
        )


def test_saved_key_installs_acknowledges_and_is_selected(working):
    """One save leads to the new key in use with no operator command."""
    response = save(working)
    assert response.status_code == 302 and response["Location"] == URL
    row = SecretReplacementRequest.objects.get()
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    assert b"Checking and installing" in working["browser"].get(URL).content
    # The selection waits, retryable, until the replacement finishes.
    with pytest.raises(ConfigurationReadinessUnavailable):
        install(working, selection.pk)
    with identity("pk_stewardship_credential_parishsoft"):
        assert working["installer"].run_once().state == "awaiting_ack"
    with identity("pk_stewardship_worker"):
        assert acknowledge_rotations(working["worker"], {}) == [row.pk]
        # A later pass has nothing left to acknowledge.
        assert acknowledge_rotations(working["worker"], {}) == []
    assert working["published"] == [{"parishsoft": file_fingerprint(CANDIDATE)}]
    with identity("pk_stewardship_credential_parishsoft"):
        assert working["installer"].run_once().state == "applied"
    assert install(working, selection.pk).state == "applied"
    record = (
        working["service"]
        .store.active()
        .document()["sections"]["integrations"][0]["values"]
    )
    assert record["credential_fingerprint"] == file_fingerprint(CANDIDATE)
    page = working["browser"].get(URL).content
    assert b"Key updated." in page and CANDIDATE not in page
    # An identical browser retry returns without staging anything new.
    assert SecretReplacementRequest.objects.count() == 1


def test_key_and_changed_organization_are_checked_and_saved_together(working):
    """The new organization ID travels with the key it was checked against."""
    assert save(working, organization_id="54321").status_code == 302
    row = SecretReplacementRequest.objects.get()
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    assert selection.patch[0]["values"]["settings"]["organization_id"] == "54321"
    with identity("pk_stewardship_credential_parishsoft"):
        working["installer"].run_once()
    with identity("pk_stewardship_worker"):
        acknowledge_rotations(working["worker"], {})
    with identity("pk_stewardship_credential_parishsoft"):
        working["installer"].run_once()
    assert install(working, selection.pk).state == "applied"
    settings = (
        working["service"]
        .store.active()
        .document()["sections"]["integrations"][0]["values"]["settings"]
    )
    assert settings["organization_id"] == "54321"


def test_rejected_key_keeps_the_old_one_and_says_so(working):
    """A provider refusal fails the selection and shows one plain sentence."""
    assert save(working, candidate=b"synthetic-wrong-key").status_code == 302
    row = SecretReplacementRequest.objects.get()
    with identity("pk_stewardship_credential_parishsoft"):
        working["installer"].run_once()
        working["installer"].run_once()
    row.refresh_from_db()
    assert row.state == "failed"
    assert read_private(working["worker"].secrets["parishsoft"]) == PRIOR
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    result = install(working, selection.pk)
    assert result.state == "failed" and result.failure_code == "invalid_candidate"
    page = working["browser"].get(URL).content
    assert b"ParishSoft did not accept the new API key" in page
    assert b"The previous key is still in use." in page


def test_worker_acknowledges_only_bytes_it_reads_from_its_mount(working, tmp_path):
    """A consumer whose folder still holds the old key does not acknowledge."""
    save(working)
    with identity("pk_stewardship_credential_parishsoft"):
        assert working["installer"].run_once().state == "awaiting_ack"
    other = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path / "other")})
    path = other.paths["credentials"] / "parishsoft" / "credential"
    path.parent.mkdir(parents=True, mode=0o700)
    write_private(path, PRIOR)
    stale = replace(working["worker"], secrets=MappingProxyType({"parishsoft": path}))
    with identity("pk_stewardship_worker"):
        assert acknowledge_rotations(stale, {}) == []
    assert working["published"] == []


def test_stale_sign_in_gets_step_up_and_stages_nothing(working, monkeypatch):
    """A key is never read or sealed without a recent Google sign-in."""
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.sessions.database_now",
        lambda: timezone.now() + timedelta(minutes=6),
    )
    response = save(working)
    assert response.status_code == 403
    assert CANDIDATE not in response.content
    assert not SecretReplacementRequest.objects.exists()
    assert not ConfigurationChangeRequest.objects.exists()


def test_blank_key_keeps_the_settings_preview(working):
    """Whitespace in the key field is not a key; settings still preview first."""
    response = save(working, candidate=b"   ", organization_id="54321")
    assert response.status_code == 200
    assert b'name="preview"' in response.content
    assert not SecretReplacementRequest.objects.exists()


def test_stale_key_save_page_says_nothing_was_done(working, monkeypatch):
    """A browser save refused for a stale sign-in explains itself plainly.

    The key is never kept for after the step-up (secrets are not stored), so
    the page says nothing was done and the key must be entered again.
    """
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.sessions.database_now",
        lambda: timezone.now() + timedelta(minutes=6),
    )
    browser, store = working["browser"], working["service"].store
    page = browser.get(URL)
    assert not page.context["fresh"]
    fields = {
        "action": "preview",
        "base_digest": store.active().digest,
        "organization_id": "12345",
        "intent": hidden(page, "intent"),
        "candidate": CANDIDATE.decode(),
        "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
    }
    with identity("pk_stewardship_web"):
        response = browser.post(
            URL,
            fields,
            HTTP_ACCEPT="text/html,*/*;q=0.8",
            HTTP_SEC_FETCH_MODE="navigate",
        )
    assert response.status_code == 403
    assert b"Confirm with Google" in response.content
    assert b"Nothing was done or sent." in response.content
    assert b"enter it again after you return" in response.content
    assert CANDIDATE not in response.content
    assert not SecretReplacementRequest.objects.exists()


def test_pending_key_status_is_passive_and_cannot_outlive_the_idle_limit(
    working, monkeypatch
):
    """The settings page follows installation through a passive fragment.

    Reloading the settings page itself would count as activity, so an open
    page would keep an idle login alive forever. The fragment it polls never
    renews idle time, stays pending until installation finishes, then points
    the page back at its settings once; a login left polling past the idle
    window is refused like any other expired login.
    """
    from parishkit.stewardship.accounts import sessions
    from parishkit.stewardship.accounts.models import PortalSession

    browser = working["browser"]
    assert save(working).status_code == 302
    page = browser.get(URL).content
    assert b'data-live-url="' + URL.encode() + b'/status"' in page
    assert b"data-reload-while-pending" not in page
    session = PortalSession.objects.get(revoked_at__isnull=True)
    activity = session.last_activity_at
    for _ in range(3):
        status = browser.get(URL + "/status")
        assert status.status_code == 200
        assert status["Cache-Control"] == "no-store"
        assert b"data-live-pending" in status.content
        assert b"<form" not in status.content
    session.refresh_from_db()
    assert session.last_activity_at == activity
    with identity("pk_stewardship_credential_parishsoft"):
        working["installer"].run_once()
    with identity("pk_stewardship_worker"):
        acknowledge_rotations(working["worker"], {})
    with identity("pk_stewardship_credential_parishsoft"):
        working["installer"].run_once()
    row = SecretReplacementRequest.objects.get()
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    assert install(working, selection.pk).state == "applied"
    finished = browser.get(URL + "/status").content
    assert b"data-live-pending" not in finished
    assert b'data-live-follow href="' + URL.encode() + b'"' in finished
    # Polling never moved the idle deadline, so once the clock passes it the
    # same poll is refused: an open page cannot keep an idle login alive.
    session.refresh_from_db()
    deadline = session.expires_at
    monkeypatch.setattr(
        sessions, "database_now", lambda: deadline + timedelta(seconds=1)
    )
    assert browser.get(URL + "/status").status_code != 200


def rejected(value):
    """Save a key the provider refuses; return its failed request row."""
    assert save(value, candidate=b"synthetic-wrong-key").status_code == 302
    with identity("pk_stewardship_credential_parishsoft"):
        value["installer"].run_once()
        value["installer"].run_once()
    row = SecretReplacementRequest.objects.get()
    install(
        value,
        ConfigurationChangeRequest.objects.get(request_key=selection_key(row.pk)).pk,
    )
    return row


def test_dismissing_a_finished_key_change_hides_it_for_everyone(working):
    """Dismiss is one CSRF-checked POST recorded in the audit log."""
    row = rejected(working)
    browser = working["browser"]
    page = browser.get(URL).content.decode()
    assert f'name="request_id" value="{row.pk}"' in page
    # Without the CSRF token nothing is recorded.
    refused = browser.post(URL + "/dismiss", {"request_id": str(row.pk)})
    assert refused.status_code == 403
    # A stale or foreign request identity dismisses nothing.
    with identity("pk_stewardship_web"):
        response = post(browser, URL + "/dismiss", {"request_id": str(uuid4())})
    assert response.status_code == 302 and response["Location"] == URL
    assert b"ParishSoft did not accept" in browser.get(URL).content
    with identity("pk_stewardship_web"):
        response = post(browser, URL + "/dismiss", {"request_id": str(row.pk)})
    assert response.status_code == 302 and response["Location"] == URL
    event = AuditEvent.objects.get(event_type="credential_result_dismissed")
    assert event.subject_id == row.pk
    # The dismissal is about the request, not the Administrator who made it.
    assert summary("parishsoft", _record(working)) is None
    assert b"ParishSoft did not accept" not in browser.get(URL).content


def test_finished_key_change_expires_after_an_hour(working, monkeypatch):
    """Without a dismissal the line drops off an hour after the change."""
    row = rejected(working)
    assert summary("parishsoft", _record(working)).kind == "failed"
    later = row.updated_at + timedelta(minutes=61)
    monkeypatch.setattr(credentials.timezone, "now", lambda: later)
    assert summary("parishsoft", _record(working)) is None


def test_a_key_that_still_needs_action_cannot_be_dismissed(working, monkeypatch):
    """An installed key that is not yet in use keeps its line and its link."""
    row = rejected(working)
    unselected = CredentialSummary("unselected", timezone.now(), "", row.pk)
    monkeypatch.setattr(credentials, "summary", lambda target, record: unselected)
    credentials.dismiss(
        "parishsoft", _record(working), row.pk, actor_id=None, parish_id=None
    )
    assert not AuditEvent.objects.filter(
        event_type="credential_result_dismissed"
    ).exists()


def test_parishsoft_page_offers_a_one_click_full_refresh(working):
    """The settings page posts to the manual refresh with a fresh request key."""
    page = working["browser"].get(URL).content.decode()
    assert 'action="/admin/source/refresh"' in page
    assert re.search(r'name="request_key" value="[0-9a-f-]{36}"', page)
    assert "Run a full refresh now" in page


def _record(value):
    """The applied ParishSoft integration record."""
    document = value["service"].store.active().document()
    return document["sections"]["integrations"][0]
