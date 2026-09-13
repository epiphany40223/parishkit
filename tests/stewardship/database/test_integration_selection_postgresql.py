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
from parishkit.stewardship.accounts.key_files import (
    file_fingerprint,
    read_private,
    write_private,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest

from . import campaign_builders
from .auth_builders import signed_in
from .campaign_builders import change
from .test_configuration_service_postgresql import (  # noqa: F401
    as_config_installer,
    config_role,
)
from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401
from .test_integration_views_postgresql import (  # noqa: F401
    REPLACE,
    handoff,
    hidden,
    post,
)

pytestmark = pytest.mark.django_db(transaction=True)
PRIOR = b"synthetic-working-parishsoft-key"
CANDIDATE = b"synthetic-next-parishsoft-key"


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
    browser, _ = signed_in()
    intent = hidden(browser.get(REPLACE), "intent")
    result = post(browser, REPLACE, {"intent": intent, "candidate": CANDIDATE.decode()})
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
        url=result["Location"] + "/select",
    )


def complete(value):
    """Finish only after the actual required worker attests its loaded key bytes."""
    with identity("pk_stewardship_worker"):
        acknowledge_loaded_credential(
            request_id=value.row.pk, consumer="worker", loaded_value=CANDIDATE
        )
    with identity("pk_stewardship_credential_parishsoft"):
        assert value.installer.run_once().state == "applied"
    value.row.refresh_from_db()


@pytest.mark.parametrize("plan_mode", ["force_custom_plan", "force_generic_plan"])
def test_replacement_audit_is_independent_of_query_plan(request, plan_mode):
    """Runtime attribution metadata is readable under either PostgreSQL plan mode."""
    with connection.cursor() as cursor:
        cursor.execute(f"SET plan_cache_mode = '{plan_mode}'")
    try:
        value = request.getfixturevalue("replacement")
        complete(value)
        with identity("pk_stewardship_credential_parishsoft"):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, validation_schema "
                    "FROM stewardship_configuration_version"
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
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET plan_cache_mode")


def test_select_acknowledged_fingerprint_via_real_web_and_config_roles(
    replacement, request
):
    """Neither installed bytes nor an Applying request alone changes selected YAML."""
    value = replacement
    assert value.browser.get(value.url).status_code == 404
    complete(value)
    old = value.service.store.active()
    with identity("pk_stewardship_web"):
        preview = hidden(value.browser.get(value.url), "preview")
        response = post(
            value.browser, value.url, {"action": "confirm", "preview": preview}
        )
        assert response.status_code == 302, response.content
    assert value.service.store.active() == old
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
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
    assert b"already selected" in value.browser.get(value.url).content
    assert (
        post(value.browser, value.url, {"action": "confirm", "preview": preview})[
            "Location"
        ]
        == response["Location"]
    )
    assert CANDIDATE not in value.browser.get(value.url).content


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
        preview = hidden(value.browser.get(value.url), "preview")
        response = post(
            value.browser, value.url, {"action": "confirm", "preview": preview}
        )
    assert response.status_code == 302, response.content
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
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
        retry = post(
            value.browser, value.url, {"action": "confirm", "preview": preview}
        )
    assert retry.status_code == 302, retry.content
    assert retry["Location"] == response["Location"]


@pytest.mark.parametrize(
    "failure", ["stale_base", "settings", "freshness", "hidden", "csrf"]
)
def test_selection_rechecks_proof_intent_and_fresh_auth(
    replacement, monkeypatch, failure
):
    """A correct old preview never authorizes new settings or stale authentication."""
    value = replacement
    complete(value)
    preview = hidden(value.browser.get(value.url), "preview")
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
    elif failure == "freshness":
        monkeypatch.setattr(
            "parishkit.stewardship.accounts.sessions.database_now",
            lambda: timezone.now() + timedelta(minutes=6),
        )
    count = ConfigurationChangeRequest.objects.count()
    data = {"action": "confirm", "preview": preview}
    if failure == "hidden":
        data["credential_fingerprint"] = "b" * 64
    response = (
        value.browser.post(value.url, data)
        if failure == "csrf"
        else post(value.browser, value.url, data)
    )
    assert (
        response.status_code
        == {
            "stale_base": 409,
            "settings": 409,
            "freshness": 403,
            "hidden": 400,
            "csrf": 403,
        }[failure]
    )
    assert ConfigurationChangeRequest.objects.count() == count


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
    preview = hidden(value.browser.get(value.url), "preview")
    response = post(value.browser, value.url, {"action": "confirm", "preview": preview})
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    intent = hidden(value.browser.get(REPLACE), "intent")
    assert (
        post(
            value.browser, REPLACE, {"intent": intent, "candidate": "third-candidate"}
        ).status_code
        == 409
    )
    assert SecretReplacementRequest.objects.count() == 1
    assert read_private(value.installer.files.path) == CANDIDATE
    assert (
        install_request(
            value.service.store, request_id=row.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )
    assert not row.checkpoints.filter(state="failed").exists()
    assert value.service.store.active() != old
    intent = hidden(value.browser.get(REPLACE), "intent")
    assert (
        post(
            value.browser, REPLACE, {"intent": intent, "candidate": "third-candidate"}
        ).status_code
        == 302
    )
    newest = SecretReplacementRequest.objects.exclude(pk=value.row.pk).get()
    assert newest.expected_fingerprint == file_fingerprint(CANDIDATE)
    from parishkit.stewardship.accounts.secret_requests import cancel_secret_request

    cancel_secret_request(
        request_id=newest.pk, actor_id=newest.requested_by_id, correlation_id=uuid4()
    )
    with identity("pk_stewardship_credential_parishsoft"):
        assert value.installer.run_once().state == "cancelled"
    assert read_private(value.installer.files.path) == CANDIDATE


def test_selection_migration_roundtrip_and_populated_refusal(replacement):
    """Retained requests cannot lose the parser discriminator required for replay."""
    from django.db.migrations.executor import MigrationExecutor

    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    previous = [("stewardship_accounts", "0056_configuration_credential_receipts")]
    try:
        MigrationExecutor(connection).migrate(previous)
        MigrationExecutor(connection).migrate(leaves)
        # Downgrading drops the later setup table, including its runtime ACL.
        # As in a real upgrade, reapply this fixture's consumer grant after DDL;
        # migration ownership does not implicitly authorize the target installer.
        with connection.cursor() as cursor:
            cursor.execute(
                "GRANT SELECT ON stewardship_setup_credential_install "
                "TO pk_stewardship_credential_parishsoft"
            )
        complete(replacement)
        preview = hidden(replacement.browser.get(replacement.url), "preview")
        assert (
            post(
                replacement.browser,
                replacement.url,
                {"action": "confirm", "preview": preview},
            ).status_code
            == 302
        )
        with pytest.raises(DatabaseError, match="selection history prevents downgrade"):
            MigrationExecutor(connection).migrate(previous)
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_pending_replacement_holds_selection_and_latest_receipt_is_required(
    replacement,
):
    """An in-flight replacement is not mistaken for current acknowledged evidence."""
    value = replacement
    complete(value)
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
    assert value.browser.get(value.url).status_code == 503


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
    preview = hidden(value.browser.get(value.url), "preview")
    response = post(value.browser, value.url, {"action": "confirm", "preview": preview})
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
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
        assert value.browser.get(value.url).status_code == 200
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
    preview = hidden(value.browser.get(value.url), "preview")
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
    page = browser.get(value.url)
    assert page.status_code == 200
    own_preview = hidden(page, "preview")
    assert (
        post(browser, value.url, {"action": "confirm", "preview": preview}).status_code
        == 403
    )
    response = post(browser, value.url, {"action": "confirm", "preview": own_preview})
    assert response.status_code == 302, response.content
    assert (
        b"Review installed credential selection"
        in browser.get("/admin/configuration/integrations/parishsoft").content
    )
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
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
