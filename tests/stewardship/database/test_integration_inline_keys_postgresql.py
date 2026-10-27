"""A key saved on its settings page installs and switches with no server step."""

import re
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from django.utils import timezone

from parishkit.stewardship.accounts import integration_credentials as credentials
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.configuration_errors import (
    ConfigurationReadinessUnavailable,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.credential_errors import (
    CredentialOrganizationMismatch,
)
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
from parishkit.stewardship.accounts.secret_models import (
    SealedCredentialStaging,
    SecretReplacementRequest,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.credential_runtime import acknowledge_rotations
from parishkit.stewardship.deployment import ServiceRole, load_deployment

from ..schedule_rows import rows, shown
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
        lambda configuration, receipts, deferred=frozenset(): published.append(
            dict(receipts)
        ),
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
    """Submit the page's own form with a pasted key, as a browser would.

    A setting given as None is left out. The page has no organization field
    (#1554), so none is posted unless a test forges one.
    """
    browser, store = value["browser"], value["service"].store
    page = browser.get(URL)
    fields = {
        "action": "preview",
        "base_digest": store.active().digest,
        "intent": hidden(page, "intent"),
        "candidate": candidate.decode(),
    } | settings
    fields = {name: field for name, field in fields.items() if field is not None}
    with identity("pk_stewardship_web"):
        return post(browser, URL, fields)


def install(value, request_id):
    """Run the configuration installer once for the key's selection request."""
    with as_config_installer():
        return install_request(
            value["service"].store, request_id=request_id, correlation_id=uuid4()
        )


def test_key_paste_after_the_approved_sessions_ended_still_stages(working):
    """An ended automation session does not block its approving browser's paste.

    The browser approved a full-scope automation session and then ended it.
    The browser's own sign-in is still live, and its request names no
    automation session, so its key replacement no longer depends on the
    sessions it approved (#1763; before that, #1547 showed a sign-in page).
    """
    from parishkit.stewardship.accounts import automation_sessions as automation

    from .automation_builders import approve_now, start
    from .test_automation_sessions_postgresql import owner

    service = working["service"]
    secret, code = start(service)
    approve_now(service, code)
    row = AutomationSession.objects.get(secret_digest=automation.secret_digest(secret))
    assert automation.revoke(row.pk, owner(service), reason="revoked_by_owner")
    response = save(working)
    assert response.status_code == 302
    assert response["Location"] == URL
    staged = SecretReplacementRequest.objects.get()
    # The request holds the ended session's sign-in instant, so this is the
    # shared-instant case 0114's check used to refuse.
    assert staged.reauthenticated_at == row.authenticated_at
    assert staged.automation_session_id is None
    assert SealedCredentialStaging.objects.count() == 1


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


@pytest.mark.parametrize("with_key", [False, True])
def test_the_page_names_the_organization_and_refuses_a_posted_one(working, with_key):
    """The organization is chosen once, in setup (#1554): the page names it
    as text above the key, never a field, and a request that posts one
    anyway (with or without a key) is refused as unexpected input, with
    nothing queued or staged.

    No load recorded a name here, so it is named by its ID alone.
    """
    page = working["browser"].get(URL)
    assert "organization_id" not in page.context["form"].fields
    assert page.context["organization"] == "ParishSoft organization 12345"
    content = page.content.decode()
    assert "ParishSoft organization: ParishSoft organization 12345" in content
    assert "It must be a key that reaches ParishSoft organization 12345;" in content
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
    assert not SecretReplacementRequest.objects.exists()
    assert not ConfigurationChangeRequest.objects.exists()
    # Without it, the key saves alone and the organization stays.
    assert save(working).status_code == 302
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(SecretReplacementRequest.objects.get().pk)
    )
    assert set(selection.patch[0]["values"]) == {"credential_fingerprint"}


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

    assert save(working).status_code == 302
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
    # Finish switching opens the page's review dialog (#1568), never a page.
    assert 'data-review-open="integration-review"' in page
    assert '<input type="hidden" name="action" value="finish">' in page
    assert "/admin/system/key-changes/" not in page
    assert summary("parishsoft", _record(working)).kind == "unselected"
    # Finish switching previews the original selection on today's settings.
    preview = hidden(post(browser, URL, {"action": "finish"}), "preview")
    with identity("pk_stewardship_web"):
        response = post(
            browser,
            URL,
            {"action": "confirm", "preview": preview, "request_id": str(row.pk)},
        )
    assert response.status_code == 302, response.content
    assert b"Switching to it now" in browser.get(URL).content
    finish = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    assert install(working, finish.pk).state == "applied"
    record = _record(working)["values"]
    assert record["credential_fingerprint"] == fingerprint
    assert record["settings"]["organization_id"] == "12345"
    page = browser.get(URL).content
    assert b"Key updated." in page and b"switching to it did not" not in page
    assert b"switching to it did not" not in browser.get("/admin/").content
    assert (
        store.active().document()["sections"]["parish"][0]["values"]["name"]
        == "Renamed first"
    )


def failed_switch(value, *, organization=None, **values):
    """Save a key, then let another change land first.

    ``values`` is the other Administrator's ParishSoft settings change (by
    default a new parish name instead). ``organization`` forges the key's
    save with another organization ID, which no page or command can post
    (#1554), for the refusals that guard every other path. Returns the key's
    request row, whose own selection has failed as ``stale_base``.
    """
    from parishkit.stewardship.accounts.integration_forms import IntegrationForm

    def forged(form, settings=IntegrationForm.public_settings):
        """The form's settings with the forged organization."""
        return settings(form) | {"organization_id": organization}

    with pytest.MonkeyPatch.context() as patched:
        if organization is not None:
            patched.setattr(IntegrationForm, "public_settings", forged)
        assert save(value).status_code == 302
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
    settings are carried over. The organization is the same (the page has
    no field for it, #1554), so the confirmation lists no other setting.
    """
    row = failed_switch(working, nightly_time="04:15")
    browser = working["browser"]
    page = post(browser, URL, {"action": "finish"})
    assert page.context["changes"] == []
    assert b"Settings saved with the new key" not in page.content
    with identity("pk_stewardship_web"):
        response = post(
            browser,
            URL,
            {
                "action": "confirm",
                "preview": hidden(page, "preview"),
                "request_id": str(row.pk),
            },
        )
    assert response.status_code == 302, response.content
    finish = UUID(response["Location"].rstrip("/").rsplit("/", 1)[-1])
    assert install(working, finish).state == "applied"
    settings = _record(working)["values"]["settings"]
    assert settings["nightly_time"] == "04:15"
    assert settings["organization_id"] == "12345"
    assert _record(working)["values"]["credential_fingerprint"] == (
        file_fingerprint(CANDIDATE)
    )


def test_finish_switching_cannot_change_a_loaded_organization(working, monkeypatch):
    """A key saved with another organization can't switch once data is loaded.

    No page or command saves one (the organization has no field, #1554), so
    the save is forged here. Its review in the dialog refuses in plain
    language, naming the organization, and records nothing (#1568), and the
    configuration installer refuses the same change from any other path.
    """
    from parishkit.stewardship.accounts import integration_selection
    from parishkit.stewardship.accounts.configuration_requests import record_request

    failed_switch(working, organization="54321")
    monkeypatch.setattr(integration_selection, "loaded_organization", lambda: 12345)
    count = ConfigurationChangeRequest.objects.count()
    browser = working["browser"]
    page = post(browser, URL, {"action": "finish"})
    assert page.status_code == 409
    assert page.json()["refusal"]["message"] == (
        "The ParishSoft organization is ParishSoft organization 12345 and "
        "can't change after setup."
    )
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
    # A rejected key names only that cause (#1495).
    assert b"does not reach the" not in page
    assert not AuditEvent.objects.filter(
        event_type="credential_key_organization_mismatch"
    ).exists()


@pytest.mark.parametrize("loaded", ["St. Example Parish (12345)", None])
def test_key_for_another_organization_says_exactly_that(working, monkeypatch, loaded):
    """A key that does not reach the organization fails with its own line (#1495).

    The real installer role records the cause as one audit event about the
    request, committed with the failure; the request itself stays failed,
    the previous key stays in use, and the page names that one cause and
    the loaded organization, "Name (ID)" (#1554).
    """
    from parishkit.stewardship.source import organization_names

    def validate(value):
        """The check's "mismatch" verdict, as provider_checks raises it."""
        raise CredentialOrganizationMismatch()

    installer = CredentialInstaller(working["installer"].files, validate=validate)
    monkeypatch.setattr(organization_names, "loaded_organization_label", lambda: loaded)
    assert save(working).status_code == 302
    row = SecretReplacementRequest.objects.get()
    with identity("pk_stewardship_credential_parishsoft"):
        installer.run_once()
        installer.run_once()
    row.refresh_from_db()
    assert row.state == "failed" and row.cleanup_reason == "failed"
    assert read_private(working["worker"].secrets["parishsoft"]) == PRIOR
    event = AuditEvent.objects.get(event_type="credential_key_organization_mismatch")
    assert event.subject_id == row.pk and event.actor_id is None
    # Committed with the move to cleanup, under the same correlation.
    assert AuditEvent.objects.filter(
        event_type="secret_request_cleanup_pending",
        subject_id=row.pk,
        correlation_id=event.correlation_id,
    ).exists()
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    assert install(working, selection.pk).state == "failed"
    page = working["browser"].get(URL).content.decode()
    expected = (
        f"This key does not reach {loaded or 'the organization chosen in setup'}, "
        "whose data is loaded. Paste a key that reaches it."
    )
    assert expected in page
    assert "ParishSoft did not accept" not in page
    assert summary("parishsoft", _record(working)).message == expected


def test_later_rejected_key_does_not_inherit_an_earlier_mismatch(working):
    """A mismatch on one request never colours a later request's failure (#1495)."""

    def mismatch(value):
        """The check's "mismatch" verdict, as provider_checks raises it."""
        raise CredentialOrganizationMismatch()

    def refuse(files_installer):
        """Run a request through the installer until it has failed."""
        with identity("pk_stewardship_credential_parishsoft"):
            files_installer.run_once()
            files_installer.run_once()

    assert save(working).status_code == 302
    first = SecretReplacementRequest.objects.get()
    refuse(CredentialInstaller(working["installer"].files, validate=mismatch))
    assert AuditEvent.objects.filter(
        event_type="credential_key_organization_mismatch", subject_id=first.pk
    ).exists()
    # A later attempt with a key ParishSoft rejects shows the general line.
    assert save(working, candidate=b"synthetic-wrong-key").status_code == 302
    later = SecretReplacementRequest.objects.exclude(pk=first.pk).get()
    refuse(working["installer"])
    later.refresh_from_db()
    assert later.state == "failed"
    page = working["browser"].get(URL).content
    assert b"ParishSoft did not accept the new API key" in page
    assert b"does not reach the" not in page


def test_key_change_in_progress_is_announced_on_admin_pages(working):
    """While a key is checked, Admin pages say other changes wait (#456 M4).

    Every later settings change waits behind the key's selection request,
    and one saved meanwhile fails if the key is accepted, so the banner says
    so before an Administrator saves anything. It goes once the change ends.
    """
    assert save(working, candidate=b"synthetic-wrong-key").status_code == 302
    home = working["browser"].get("/admin/").content.decode()
    assert "data-key-changes" in home and "Other settings changes wait" in home
    assert f'<a href="{URL}">ParishSoft</a>' in home
    with identity("pk_stewardship_credential_parishsoft"):
        working["installer"].run_once()
        working["installer"].run_once()
    assert SecretReplacementRequest.objects.get().state == "failed"
    assert "data-key-changes" not in working["browser"].get("/admin/").content.decode()


def test_key_change_banner_stays_until_an_accepted_key_is_installed(working):
    """An accepted key keeps the banner through installation, then it clears.

    The banner follows the key change itself: it shows while the key is
    checked, installed and acknowledged, and goes once the installer has
    applied it, whether or not the selection has run yet.
    """

    def banner():
        """Whether the Admin home page shows the key-change banner."""
        return "data-key-changes" in working["browser"].get("/admin/").content.decode()

    assert save(working).status_code == 302
    row = SecretReplacementRequest.objects.get()
    assert banner()
    with identity("pk_stewardship_credential_parishsoft"):
        assert working["installer"].run_once().state == "awaiting_ack"
    assert banner()
    with identity("pk_stewardship_worker"):
        acknowledge_rotations(working["worker"], {})
    with identity("pk_stewardship_credential_parishsoft"):
        assert working["installer"].run_once().state == "applied"
    assert not banner()
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    assert install(working, selection.pk).state == "applied"
    assert not banner()


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


def test_an_old_sign_in_stages_the_key(working):
    """A sign-in older than five minutes saves and stages the key (#1547).

    The page carries no sign-in notice or warning that the key is not kept.
    """
    stale_sign_in()
    page = working["browser"].get(URL)
    assert b"enter it again when you return" not in page.content
    assert b"the last five minutes" not in page.content
    response = save(working)
    assert response.status_code == 302 and response["Location"] == URL
    assert CANDIDATE not in response.content
    assert SecretReplacementRequest.objects.count() == 1


def test_blank_key_keeps_the_settings_preview(working):
    """Whitespace in the key field is not a key: the Save is a settings
    review, which here changes nothing (ParishSoft's organization has no
    field, #1554), so it is refused as no change and nothing is staged."""
    response = save(working, candidate=b"   ")
    assert response.status_code == 400
    assert response.json()["refusal"]["message"] == "No settings have changed."
    assert not SecretReplacementRequest.objects.exists()


def test_key_with_a_first_schedule_change_says_to_save_them_apart(working):
    """A key plus a refresh schedule change is refused (#632).

    The refusal names the reason and the order to save in, rather than the
    generic "Check your entries" page, and nothing is staged. The page is
    redrawn with the refusal at the key field, which is empty again (the key
    is never shown back), and the schedule edits kept (#563).
    """
    hourly = {"kind": "full", "every": 60, "from": "00:00", "to": "23:00"}
    response = save(working, **rows([hourly]))
    assert response.status_code == 400
    html = response.content.decode()
    (field,) = re.findall(r'<input[^>]*name="candidate"[^>]*>', html)
    assert 'aria-invalid="true"' in field and "value=" not in field
    (error,) = re.findall(
        r'<ul class="errorlist" id="id_candidate_error">(.*?)</ul>', html
    )
    assert "Nothing was saved: a new key and a refresh schedule change" in error
    assert "save the schedule change first" in error
    # The summary, focused on load, links down to the key field.
    assert re.search(
        r'data-error-summary.*?<a href="#id_candidate">Nothing was saved', html, re.S
    )
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
    # Its one follow-up redraws the status line and editor in place (#1568).
    assert (
        b"<a hidden data-live-follow data-in-place data-in-place-quiet "
        b'data-in-place-only="integration-editor" href="'
        + URL.encode()
        + b'#integration-status">'
    ) in finished
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
    assert 'action="/admin/parish/parishsoft-refresh/"' in page
    assert re.search(r'name="request_key" value="[0-9a-f-]{36}"', page)
    assert "Run a full refresh now" in page


def _record(value):
    """The applied ParishSoft integration record."""
    document = value["service"].store.active().document()
    return document["sections"]["integrations"][0]


def test_a_key_pasted_with_the_schedule_untouched_is_saved(working, monkeypatch):
    """A key pasted on the page as drawn is saved (#1492).

    The browser posts the schedule rows back untouched, and the page has no
    organization field (#1554), so the save is neither a key plus a schedule
    change (``key_with_schedule_change``) nor an organization change: the
    key's selection changes the key alone.
    """
    page = shown(working["browser"].get(URL).content.decode())
    assert int(page["rules-TOTAL_FORMS"]) > 0
    response = save(working, **page)
    assert response.status_code == 302, response.content
    row = SecretReplacementRequest.objects.get()
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(row.pk)
    )
    assert [set(item["values"]) for item in selection.patch] == [
        {"credential_fingerprint"}
    ]
