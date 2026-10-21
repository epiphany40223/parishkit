"""Installed private files, actual consumer ACKs and later exact YAML selection."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from parishkit.stewardship.accounts.configuration_errors import (
    ConfigurationReadinessUnavailable,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_installation import (
    CredentialInstaller,
    acknowledge_loaded_credential,
)
from parishkit.stewardship.accounts.integration_credentials import selection_key
from parishkit.stewardship.accounts.key_files import (
    file_fingerprint,
    read_private,
    write_private,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest

from . import campaign_builders
from .auth_builders import signed_in, stale_sign_in
from .campaign_builders import change
from .test_configuration_service_postgresql import (  # noqa: F401
    as_config_installer,
    config_role,
)
from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401
from .test_integration_views_postgresql import (  # noqa: F401
    handoff,
    hidden,
    post,
    save_key,
)

pytestmark = pytest.mark.django_db(transaction=True)
PRIOR = b"synthetic-working-parishsoft-key"
CANDIDATE = b"synthetic-next-parishsoft-key"
SETTINGS = "/admin/system/integrations/parishsoft/"


@pytest.fixture
def replacement(request, monkeypatch, tmp_path, google):
    """Start from genuine known key bytes, not a forged installed SHA256 receipt."""
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
    browser, _ = signed_in()
    result = save_key(browser, CANDIDATE.decode())
    assert result.status_code == 302, result.content
    row = SecretReplacementRequest.objects.get()
    directory = tmp_path / "private-parishsoft"
    directory.mkdir(mode=0o700)
    write_private(directory / "credential", PRIOR)
    installer = CredentialInstaller(
        CredentialFiles(directory / "credential", private),
        validate=lambda value: value == CANDIDATE,
    )
    with identity("pk_stewardship_credential_parishsoft"):
        assert installer.run_once().state == "awaiting_ack"
    assert read_private(installer.files.path) == CANDIDATE
    return SimpleNamespace(
        service=service,
        browser=browser,
        installer=installer,
        row=row,
        selection=ConfigurationChangeRequest.objects.get(
            request_key=selection_key(row.pk)
        ),
    )


def review(browser):
    """Finish switching's Review, in the settings page's dialog (#1568)."""
    return post(browser, SETTINGS, {"action": "finish"})


def confirm_switch(browser, preview, row, **fields):
    """The dialog's Apply of Finish switching: its preview and key change."""
    return post(
        browser,
        SETTINGS,
        {"action": "confirm", "preview": preview, "request_id": str(row.pk)} | fields,
    )


def complete(value):
    """Install the key, then let its automatic switch fail as in #307 M1.

    Only the actual required worker's attestation of its loaded key bytes
    finishes the replacement. Another settings change is then applied first,
    so the save's own selection request fails as ``stale_base``: the key is
    installed but not selected, and these tests exercise **Finish switching**
    as the recovery.
    """
    with identity("pk_stewardship_worker"):
        acknowledge_loaded_credential(
            request_id=value.row.pk, consumer="worker", loaded_value=CANDIDATE
        )
    with identity("pk_stewardship_credential_parishsoft"):
        assert value.installer.run_once().state == "applied"
    value.row.refresh_from_db()
    rename(value.service.store, "Parish renamed first")
    with as_config_installer():
        result = install_request(
            value.service.store,
            request_id=value.selection.pk,
            correlation_id=uuid4(),
        )
    assert result.state == "failed" and result.failure_code == "stale_base"


def rename(store, name):
    """Apply an unrelated settings change, as another Administrator might."""
    base = store.active()
    record = base.document()["sections"]["parish"][0]
    change(
        store,
        base,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": record["id"],
                "values": {"name": name},
            }
        ],
    )


def test_replacement_audit_is_independent_of_query_plan(request, sql_plan_mode):
    """Runtime attribution metadata is readable under either PostgreSQL plan mode."""
    value = request.getfixturevalue("replacement")
    complete(value)
    with identity("pk_stewardship_credential_parishsoft"):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, validation_schema FROM stewardship_configuration_version"
            )
            assert cursor.fetchall()
        with (
            pytest.raises(DatabaseError) as error,
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT canonical_document FROM stewardship_configuration_version"
            )
        assert error.value.__cause__.sqlstate == "42501"


def test_select_acknowledged_fingerprint_via_real_web_and_config_roles(
    replacement, request
):
    """Neither installed bytes nor an Applying request alone changes selected YAML."""
    value = replacement
    # Nothing to finish while the key is still being installed.
    assert review(value.browser).status_code == 409
    complete(value)
    old = value.service.store.active()
    with identity("pk_stewardship_web"):
        page = review(value.browser)
        # The dialog's confirmation, never a page of its own (#1568).
        assert b"data-review-answer" in page.content
        assert b"Finish switching to the new key?" in page.content
        assert b"<html" not in page.content
        assert hidden(page, "request_id") == str(value.row.pk)
        preview = hidden(page, "preview")
        response = confirm_switch(value.browser, preview, value.row)
        assert response.status_code == 302, response.content
    assert value.service.store.active() == old
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    request.getfixturevalue("config_role")
    with as_config_installer():
        assert (
            install_request(
                value.service.store, request_id=row.pk, correlation_id=uuid4()
            ).state
            == "applied"
        )
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT ciphertext FROM stewardship_sealed_credential_staging"
            )
    record = value.service.store.active().document()["sections"]["integrations"][0][
        "values"
    ]
    assert record["credential_fingerprint"] == file_fingerprint(CANDIDATE)
    finished = review(value.browser)
    assert finished.status_code == 409
    assert b"No new key is waiting to be switched to." in finished.content
    # The switch's status returns to the page it was confirmed on.
    status = value.browser.get(response["Location"]).content
    assert f'<a href="{SETTINGS}">Return to ParishSoft</a>'.encode() in status
    assert (
        confirm_switch(value.browser, preview, value.row)["Location"]
        == response["Location"]
    )
    assert CANDIDATE not in page.content and CANDIDATE not in finished.content


@pytest.mark.parametrize("cadence_before", [False, True])
def test_selection_parser_is_bound_to_preview_not_later_cadence(
    replacement, cadence_before
):
    """Old/new selection receipts replay exactly after an unrelated scheduling edit."""
    value = replacement
    complete(value)

    def cadence(time):
        """Apply a real public settings request without changing authentication."""
        base = value.service.store.active()
        record = base.document()["sections"]["integrations"][0]
        change(
            value.service.store,
            base,
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "integrations",
                    "id": record["id"],
                    "values": {
                        "settings": record["values"]["settings"]
                        | {"nightly_time": time}
                    },
                }
            ],
        )

    if cadence_before:
        cadence("03:15")
    with identity("pk_stewardship_web"):
        preview = hidden(review(value.browser), "preview")
        response = confirm_switch(value.browser, preview, value.row)
    assert response.status_code == 302, response.content
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    assert row.request_schema == (
        "integration-credential-cadence-v8"
        if cadence_before
        else "integration-credential-patch-v6"
    )
    assert (
        install_request(
            value.service.store, request_id=row.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )
    cadence("04:15")
    with identity("pk_stewardship_web"):
        retry = confirm_switch(value.browser, preview, value.row)
    assert retry.status_code == 302, retry.content
    assert retry["Location"] == response["Location"]


@pytest.mark.parametrize("failure", ["stale_base", "settings", "hidden", "csrf"])
def test_selection_rechecks_proof_and_intent(replacement, monkeypatch, failure):
    """A correct old preview never authorizes new settings or a changed proof."""
    value = replacement
    complete(value)
    preview = hidden(review(value.browser), "preview")
    before = value.service.store.active()
    if failure in {"stale_base", "settings"}:
        section = "parish" if failure == "stale_base" else "integrations"
        record = before.document()["sections"][section][0]
        change(
            value.service.store,
            before,
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": section,
                    "id": record["id"],
                    "values": {"name": "New parish"}
                    if failure == "stale_base"
                    else {"settings": {"organization_id": "4321"}},
                }
            ],
        )
    count = ConfigurationChangeRequest.objects.count()
    data = {"action": "confirm", "preview": preview, "request_id": str(value.row.pk)}
    if failure == "hidden":
        data["credential_fingerprint"] = "b" * 64
    response = (
        value.browser.post(SETTINGS, data)
        if failure == "csrf"
        else post(value.browser, SETTINGS, data)
    )
    assert (
        response.status_code
        == {
            "stale_base": 409,
            "settings": 409,
            "hidden": 400,
            "csrf": 403,
        }[failure]
    )
    assert ConfigurationChangeRequest.objects.count() == count


def test_an_old_sign_in_confirms_the_selection(replacement):
    """A sign-in older than five minutes confirms the reviewed key (#1547)."""
    value = replacement
    complete(value)
    preview = hidden(review(value.browser), "preview")
    count = ConfigurationChangeRequest.objects.count()
    stale_sign_in()
    response = confirm_switch(value.browser, preview, value.row)
    assert response.status_code == 302, response.content
    assert ConfigurationChangeRequest.objects.count() == count + 1


def test_installer_refuses_fingerprint_without_matching_ack(replacement):
    """Reject an arbitrary changed reference before selecting any authority file."""
    value = replacement
    complete(value)
    old = value.service.store.active()
    record = old.document()["sections"]["integrations"][0]
    from parishkit.stewardship.accounts.configuration_requests import record_request
    from parishkit.stewardship.accounts.request_patch import CREDENTIAL_REQUEST_SCHEMA

    request = record_request(
        base_digest=old.digest,
        actor_id=uuid4(),
        request_key=uuid4(),
        correlation_id=uuid4(),
        request_schema=CREDENTIAL_REQUEST_SCHEMA,
        patch=[
            {
                "operation": "update",
                "section": "integrations",
                "id": record["id"],
                "values": {"credential_fingerprint": "f" * 64},
            }
        ],
    )
    receipt = install_request(
        value.service.store, request_id=request.request_id, correlation_id=uuid4()
    )
    assert receipt.state == "failed" and receipt.failure_code == "invalid_candidate"
    assert value.service.store.active() == old


def test_second_replacement_requires_selection_of_the_installed_predecessor(
    replacement,
):
    """A new candidate cannot bind stale YAML as the working-file rollback value."""
    value = replacement
    complete(value)
    old = value.service.store.active()
    assert save_key(value.browser, "third-candidate").status_code == 409
    assert SecretReplacementRequest.objects.count() == 1
    preview = hidden(review(value.browser), "preview")
    response = confirm_switch(value.browser, preview, value.row)
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    assert read_private(value.installer.files.path) == CANDIDATE
    assert (
        install_request(
            value.service.store, request_id=row.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )
    assert not row.checkpoints.filter(state="failed").exists()
    assert value.service.store.active() != old
    assert save_key(value.browser, "third-candidate").status_code == 302
    newest = SecretReplacementRequest.objects.exclude(pk=value.row.pk).get()
    assert newest.expected_fingerprint == file_fingerprint(CANDIDATE)
    from parishkit.stewardship.accounts.secret_requests import cancel_secret_request

    cancel_secret_request(
        request_id=newest.pk, actor_id=newest.requested_by_id, correlation_id=uuid4()
    )
    with identity("pk_stewardship_credential_parishsoft"):
        assert value.installer.run_once().state == "cancelled"
    assert read_private(value.installer.files.path) == CANDIDATE


def test_pending_replacement_holds_selection_and_latest_receipt_is_required(
    replacement,
):
    """An in-flight replacement is not mistaken for current acknowledged evidence."""
    value = replacement
    complete(value)
    preview = hidden(review(value.browser), "preview")
    count = ConfigurationChangeRequest.objects.count()
    from parishkit.stewardship.accounts.secret_requests import stage_secret_request

    # The legacy internal protocol can also reserve a target. Even though the
    # web rejects an unselected sealed predecessor, selection must check every
    # reservation rather than infer availability from the latest applied row.
    stage_secret_request(
        request_id=uuid4(),
        target="parishsoft",
        staging_reference=uuid4(),
        actor_id=value.row.requested_by_id,
        reauthenticated_at=value.row.reauthenticated_at,
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint=file_fingerprint(CANDIDATE),
        correlation_id=uuid4(),
    )
    # Apply repeats the selection's check of every reservation under the lock.
    assert confirm_switch(value.browser, preview, value.row).status_code == 503
    assert ConfigurationChangeRequest.objects.count() == count


def test_pending_replacement_does_not_permanently_reject_selection_intent(
    replacement, monkeypatch
):
    """A transient target hold leaves the accepted configuration request retryable."""
    from parishkit.stewardship.accounts import integration_selection
    from parishkit.stewardship.accounts.request_models import (
        ConfigurationRequestCheckpoint,
    )

    value = replacement
    complete(value)
    preview = hidden(review(value.browser), "preview")
    response = confirm_switch(value.browser, preview, value.row)
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )

    def held(*args):
        """The separate race test exercises SQL; here isolate resumable installation."""
        raise ConfigurationReadinessUnavailable("Synthetic target hold.")

    with monkeypatch.context() as patch:
        patch.setattr(integration_selection, "current_receipt", held)
        with pytest.raises(ConfigurationReadinessUnavailable):
            install_request(
                value.service.store, request_id=request.pk, correlation_id=uuid4()
            )
    assert not ConfigurationRequestCheckpoint.objects.filter(
        request=request, state="failed"
    ).exists()
    assert (
        install_request(
            value.service.store, request_id=request.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )


@pytest.mark.parametrize("hook", ["build_candidate", "render"])
def test_selection_preview_does_not_hold_the_global_work_lock(
    replacement, monkeypatch, hook
):
    """CPU/template work cannot serialize independent source and task owners."""
    from parishkit.stewardship.accounts import integration_selection_views as views

    value = replacement
    complete(value)
    original = getattr(views, hook)
    observed = []

    def probe(*args, **kwargs):
        """Read the real backend's lock inventory immediately before rendering work."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND locktype='advisory' AND classid=736220 AND objid=1 "
                "AND objsubid=2 AND granted)"
            )
            assert cursor.fetchone()[0] is False
        observed.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(views, hook, probe)
    with identity("pk_stewardship_web"):
        assert review(value.browser).status_code == 200
    assert observed == [True]


def test_another_admin_can_select_with_own_preview_but_not_replay_original(
    replacement, google, request
):
    """Credential selection survives original-Admin departure without sharing intent."""
    from django.db.models import F

    from parishkit.stewardship.accounts.models import PortalUser

    from ..policy_factory import address

    value = replacement
    complete(value)
    preview = hidden(review(value.browser), "preview")
    change(
        value.service.store,
        value.service.store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("second@example.org"),
            }
        ],
    )
    google[0]["email"] = "second@example.org"
    google[0]["sub"] = "second-google-subject"
    PortalUser.objects.filter(pk=value.row.requested_by_id).update(
        disabled=True, version=F("version") + 1
    )
    browser, _ = signed_in()
    page = review(browser)
    assert page.status_code == 200
    own_preview = hidden(page, "preview")
    assert confirm_switch(browser, preview, value.row).status_code == 403
    response = confirm_switch(browser, own_preview, value.row)
    assert response.status_code == 302, response.content
    assert (
        b"Switching to it now"
        in browser.get("/admin/system/integrations/parishsoft/").content
    )
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    assert row.actor_id != value.row.requested_by_id
    request.getfixturevalue("config_role")
    with as_config_installer():
        assert (
            install_request(
                value.service.store, request_id=row.pk, correlation_id=uuid4()
            ).state
            == "applied"
        )


def test_a_switch_is_confirmed_only_on_its_own_integrations_page(replacement):
    """Finish switching's Apply names its key change, which must belong to
    the integration whose page it is posted to (#1568): the same signed
    preview posted to another integration's page is refused (404) and
    records nothing."""
    value = replacement
    complete(value)
    preview = hidden(review(value.browser), "preview")
    count = ConfigurationChangeRequest.objects.count()
    response = post(
        value.browser,
        "/admin/system/integrations/google_workspace/",
        {"action": "confirm", "preview": preview, "request_id": str(value.row.pk)},
    )
    assert response.status_code == 404
    assert ConfigurationChangeRequest.objects.count() == count
