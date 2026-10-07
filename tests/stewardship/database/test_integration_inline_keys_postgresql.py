"""A key saved on its settings page installs and switches with no server step."""

import re
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType
from uuid import UUID, uuid4

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
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.credential_runtime import acknowledge_rotations
from parishkit.stewardship.deployment import ServiceRole, load_deployment

from . import campaign_builders
from .auth_builders import signed_in, stale_sign_in
from .campaign_builders import change
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


def test_before_a_load_key_and_changed_organization_are_saved_together(working):
    """Before any ParishSoft data is loaded, the organization ID can still change.

    The new organization ID travels with the key it was checked against.
    Once data is loaded it cannot change (see the next tests).
    """
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


@pytest.mark.parametrize("with_key", [False, True])
def test_organization_cannot_change_after_the_first_load(
    working, monkeypatch, with_key
):
    """After a load, a different organization ID is refused in plain language.

    Every refresh must read the organization whose data is loaded, so saving
    another ID (with or without a new key) would stop them all. The page
    shows the ID read-only, and a posted change is refused: nothing is
    queued or staged.
    """
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.integration_views.loaded_organization",
        lambda: 12345,
    )
    page = working["browser"].get(URL)
    field = page.context["form"]["organization_id"]
    assert field.field.widget.attrs.get("readonly") is True
    assert b"so its ID can no longer change" in page.content
    if with_key:
        response = save(working, organization_id="54321")
    else:
        browser, store = working["browser"], working["service"].store
        with identity("pk_stewardship_web"):
            response = post(
                browser,
                URL,
                {
                    "action": "preview",
                    "base_digest": store.active().digest,
                    "organization_id": "54321",
                },
            )
    assert response.status_code == 400
    assert b"can&#x27;t change after ParishSoft data has been loaded" in (
        response.content
    )
    assert b"Keep 12345 here." in response.content
    assert not SecretReplacementRequest.objects.exists()
    assert not ConfigurationChangeRequest.objects.exists()
    # The same organization still saves a key and other settings.
    assert save(working).status_code == 302
    assert SecretReplacementRequest.objects.count() == 1


def installed(value):
    """Check, install and acknowledge the saved key; return its request row."""
    with identity("pk_stewardship_credential_parishsoft"):
        assert value["installer"].run_once().state == "awaiting_ack"
    with identity("pk_stewardship_worker"):
        acknowledge_rotations(value["worker"], {})
    with identity("pk_stewardship_credential_parishsoft"):
        assert value["installer"].run_once().state == "applied"
    return SecretReplacementRequest.objects.get()


def test_failed_switch_is_an_error_and_finish_switching_recovers(
    working, monkeypatch, caplog
):
    """An installed key whose automatic switch failed is an error with a fix.

    Another settings change applied first makes the save's own selection
    fail for good (``stale_base``). The key file is already in place, so
    the refresh refuses the mismatch: the page must say so plainly, and
    Finish switching must still work, carrying the settings the key was
    checked against (#307 M1).
    """
    from parishkit.stewardship.accounts.integration_selection import switching

    assert save(working, organization_id="54321").status_code == 302
    row = installed(working)
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    store = working["service"].store
    base = store.active()
    parish = base.document()["sections"]["parish"][0]
    change(
        store,
        base,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Renamed first"},
            }
        ],
    )
    result = install(working, selection.pk)
    assert result.state == "failed" and result.failure_code == "stale_base"
    # Meanwhile the installed key is not the selected one: consumers (the
    # refresh compares exactly this) refuse it, and mail holds (see
    # test_family_mail_worker_postgresql) because a key change is switching.
    fingerprint = file_fingerprint(CANDIDATE)
    assert _record(working)["values"]["credential_fingerprint"] != fingerprint
    from parishkit.stewardship.accounts import integration_selection

    monkeypatch.setattr(integration_selection, "SWITCH_ALERT_AFTER", timedelta(0))
    monkeypatch.setattr(integration_selection, "_alerted", {})
    assert switching("parishsoft", fingerprint)
    assert switching("parishsoft", fingerprint)
    assert not switching("parishsoft", "0" * 64)
    # A consumer holding for it logs the unfinished switch as an ERROR, at
    # most hourly, so it is noticed without opening this page (#338 review).
    alerts = [
        record
        for record in caplog.records
        if getattr(record, "extra", {}).get("failure_kind")
        == "credential_switch_unfinished"
    ]
    assert len(alerts) == 1 and alerts[0].levelname == "ERROR"
    browser = working["browser"]
    # The Admin home page says so too, with a way to the fix.
    home = browser.get("/admin/").content.decode()
    assert "a new key is installed, but switching to it did not finish" in home
    assert "ParishSoft refreshes are stopped" in home
    page = browser.get(URL).content.decode()
    assert "switching to it did not finish" in page
    assert "ParishSoft refreshes are stopped" in page
    assert "notice-error" in page and 'role="alert"' in page
    select = f"/admin/system/key-changes/{row.pk}/selection/"
    assert f'class="button" href="{select}"' in page
    assert summary("parishsoft", _record(working)).kind == "unselected"
    # Finish switching previews the original selection on today's settings.
    preview = hidden(browser.get(select), "preview")
    with identity("pk_stewardship_web"):
        response = post(browser, select, {"action": "confirm", "preview": preview})
    assert response.status_code == 302, response.content
    assert b"Switching to it now" in browser.get(URL).content
    finish = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    assert install(working, finish.pk).state == "applied"
    record = _record(working)["values"]
    assert record["credential_fingerprint"] == fingerprint
    assert record["settings"]["organization_id"] == "54321"
    page = browser.get(URL).content
    assert b"Key updated." in page and b"switching to it did not" not in page
    assert b"switching to it did not" not in browser.get("/admin/").content
    assert (
        store.active().document()["sections"]["parish"][0]["values"]["name"]
        == "Renamed first"
    )


def failed_switch(value, **values):
    """Save a key with a new organization, then let another change land first.

    ``values`` is the other Administrator's ParishSoft settings change (by
    default a new parish name instead). Returns the key's request row, whose
    own selection has failed as ``stale_base``.
    """
    assert save(value, organization_id="54321").status_code == 302
    row = installed(value)
    store = value["service"].store
    base = store.active()
    if values:
        record = base.document()["sections"]["integrations"][0]
        patch = {
            "section": "integrations",
            "id": record["id"],
            "values": {"settings": record["values"]["settings"] | values},
        }
    else:
        parish = base.document()["sections"]["parish"][0]
        patch = {
            "section": "parish",
            "id": parish["id"],
            "values": {"name": "Renamed first"},
        }
    change(store, base, uuid4(), [{"operation": "update", **patch}])
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    result = install(value, selection.pk)
    assert result.state == "failed" and result.failure_code == "stale_base"
    return row


def test_finish_switching_keeps_another_admins_newer_schedule(working):
    """Finish switching carries only the key's own settings over (#338 review).

    Another Administrator's schedule change landed first. Replaying the
    whole saved settings block would silently undo it; only the key-scope
    organization ID is carried over, and the confirm page lists it.
    """
    row = failed_switch(working, nightly_time="04:15")
    browser = working["browser"]
    select = f"/admin/system/key-changes/{row.pk}/selection/"
    page = browser.get(select)
    changes = page.context["changes"]
    assert [(c["before"], c["after"]) for c in changes] == [("12345", "54321")]
    assert b"Settings saved with the new key" in page.content
    with identity("pk_stewardship_web"):
        response = post(
            browser, select, {"action": "confirm", "preview": hidden(page, "preview")}
        )
    assert response.status_code == 302, response.content
    finish = UUID(response["Location"].rsplit("/", 1)[-1])
    assert install(working, finish).state == "applied"
    settings = _record(working)["values"]["settings"]
    assert settings["nightly_time"] == "04:15"
    assert settings["organization_id"] == "54321"
    assert _record(working)["values"]["credential_fingerprint"] == (
        file_fingerprint(CANDIDATE)
    )


def test_finish_switching_cannot_change_a_loaded_organization(working, monkeypatch):
    """A key saved with a new organization before the first load can't switch after.

    The confirm page refuses in plain language and records nothing, and the
    configuration installer refuses the same change from any other path.
    """
    from parishkit.stewardship.accounts import integration_selection
    from parishkit.stewardship.accounts.configuration_requests import record_request

    row = failed_switch(working)
    monkeypatch.setattr(integration_selection, "loaded_organization", lambda: 12345)
    count = ConfigurationChangeRequest.objects.count()
    browser = working["browser"]
    page = browser.get(f"/admin/system/key-changes/{row.pk}/selection/")
    assert page.status_code == 409
    assert b"organization ID can't change after ParishSoft data" in page.content
    assert b'name="preview"' not in page.content
    assert ConfigurationChangeRequest.objects.count() == count
    # Any other path to the same change is refused by the installer.
    store = working["service"].store
    base = store.active()
    record = base.document()["sections"]["integrations"][0]
    request = record_request(
        base_digest=base.digest,
        actor_id=uuid4(),
        request_key=uuid4(),
        correlation_id=uuid4(),
        patch=[
            {
                "operation": "update",
                "section": "integrations",
                "id": record["id"],
                "values": {
                    "settings": record["values"]["settings"]
                    | {"organization_id": "54321"}
                },
            }
        ],
    )
    result = install(working, request.request_id)
    assert result.state == "failed" and result.failure_code == "invalid_candidate"
    assert _record(working)["values"]["settings"]["organization_id"] == "12345"


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


def test_stale_sign_in_gets_step_up_and_stages_nothing(working):
    """A key is never read or sealed without a recent Google sign-in."""
    stale_sign_in()
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


def test_key_with_a_first_schedule_change_says_to_save_them_apart(working):
    """Older settings with no stored schedule refuse a key plus a schedule change.

    The refusal names the reason and the order to save in, rather than the
    generic "Check your entries" page, and nothing is staged.
    """
    response = save(working, full_refresh="hourly")
    assert response.status_code == 400
    refusal = response.json()["refusal"]
    assert refusal["message"].startswith("Nothing was saved: a new key and a")
    assert "save the refresh schedule change first" in refusal["fix"]
    assert CANDIDATE not in response.content
    assert not SecretReplacementRequest.objects.exists()


def test_stale_key_save_page_says_nothing_was_done(working):
    """A browser save refused for a stale sign-in explains itself plainly.

    The key is never kept for after the step-up (secrets are not stored), so
    the page says nothing was done and the key must be entered again.
    """
    browser, store = working["browser"], working["service"].store
    # The page warns before Save, even when rendered fresh (a sign-in can age
    # while the page is open), that a step-up discards the pasted key.
    hint = b"the pasted key is not kept: paste it again when you come back"
    assert hint in browser.get(URL).content
    stale_sign_in()
    page = browser.get(URL)
    assert hint in page.content
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

    browser = working["browser"]
    assert save(working).status_code == 302
    page = browser.get(URL).content
    assert b'data-live-url="' + URL.encode() + b'status/"' in page
    assert b"data-reload-while-pending" not in page
    session = PortalSession.objects.get(revoked_at__isnull=True)
    activity = session.last_activity_at
    for _ in range(3):
        status = browser.get(URL + "status/")
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
    finished = browser.get(URL + "status/").content
    assert b"data-live-pending" not in finished
    assert b'data-live-follow href="' + URL.encode() + b'"' in finished
    # Polling never moved the idle deadline, so once the clock passes it the
    # same poll is refused: an open page cannot keep an idle login alive.
    session.refresh_from_db()
    deadline = session.expires_at
    monkeypatch.setattr(
        sessions, "database_now", lambda: deadline + timedelta(seconds=1)
    )
    assert browser.get(URL + "status/").status_code != 200


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
    refused = browser.post(URL + "dismissal/", {"request_id": str(row.pk)})
    assert refused.status_code == 403
    # A stale or foreign request identity dismisses nothing.
    with identity("pk_stewardship_web"):
        response = post(browser, URL + "dismissal/", {"request_id": str(uuid4())})
    assert response.status_code == 302 and response["Location"] == URL
    assert b"ParishSoft did not accept" in browser.get(URL).content
    with identity("pk_stewardship_web"):
        response = post(browser, URL + "dismissal/", {"request_id": str(row.pk)})
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
