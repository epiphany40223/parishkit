"""Real Admin HTTP flows over immutable YAML, sealed intake and restricted SQL roles."""

import json
import re
from datetime import timedelta
from html import unescape
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.accounts.handoff_discovery import publish_handoff
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.provider_models import ProviderValidationContext
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.secret_models import (
    SealedCredentialStaging,
    SecretReplacementRequest,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import runtime_grants

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
INDEX = "/admin/configuration/integrations"
URL = INDEX + "/parishsoft"
SECRET = "SYNTHETIC-PRIVATE-CANDIDATE"


def post(browser, url, values):
    """Every HTTP mutation goes through real Django CSRF enforcement."""
    return browser.post(
        url, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def hidden(response, name):
    """Use the signed token actually emitted in HTML, not a manufactured intent."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(rf'name="{name}" value="([^"]+)"', response.content.decode()).group(1)
    )


def save_key(browser, candidate, page=None, **fields):
    """Paste a key into the settings page's own form, as a browser would.

    ``page`` is the rendered settings page whose signed intent and base the
    save uses; by default a fresh one. Extra ``fields`` override the form.
    """
    page = page if page is not None else browser.get(URL)
    return post(
        browser,
        URL,
        {
            "action": "preview",
            "base_digest": hidden(page, "base_digest"),
            "organization_id": "12345",
            "intent": hidden(page, "intent"),
            "candidate": candidate,
        }
        | fields,
    )


def status_url(row):
    """The key change's details page."""
    return f"/admin/configuration/credentials/{row.pk}"


def edit(store, **values):
    """Known synthetic tenant profile starts with organization 12345."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "organization_id": "54321",
    } | values


@pytest.fixture
def handoff(auth_service, request):
    """Advertise encryption only after bootstrap, using the actual installer role."""
    request.getfixturevalue("isolated_roles")
    private = PrivateHandoff("parishsoft", Key("handoff", "active", b"h" * 32))
    with connection.cursor() as cursor:
        cursor.execute(
            "GRANT SELECT, INSERT ON stewardship_public_credential_handoff "
            "TO pk_stewardship_credential_parishsoft"
        )
        tables, columns = runtime_grants(ServiceRole.WEB)
        for table, permissions in tables.items():
            cursor.execute(
                f'GRANT {", ".join(sorted(permissions))} ON "{table}" '
                "TO pk_stewardship_web"
            )
        for table, permissions in columns.items():
            for permission, names in permissions.items():
                selected = ", ".join(f'"{name}"' for name in sorted(names))
                cursor.execute(
                    f'GRANT {permission} ({selected}) ON "{table}" '
                    "TO pk_stewardship_web"
                )
    with identity("pk_stewardship_credential_parishsoft"):
        publish_handoff(private)
    return private


def test_settings_preview_install_and_exact_retry(auth_service, google):
    """The web editor queues settings; the existing installer applies YAML and SQL."""
    browser, _ = signed_in()
    assert browser.get(INDEX).status_code == browser.get(URL).status_code == 200
    # The ParishSoft page reports when data was last fully reloaded.
    assert b"Last full ParishSoft refresh:" in browser.get(URL).content
    old = auth_service.store.active()
    preview = hidden(post(browser, URL, edit(auth_service.store)), "preview")
    response = post(browser, URL, {"action": "confirm", "preview": preview})
    assert response.status_code == 302
    assert auth_service.store.active() == old
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    assert (
        install_request(
            auth_service.store, request_id=row.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )
    record = auth_service.store.active().document()["sections"]["integrations"][0][
        "values"
    ]
    assert record["settings"] == {
        "organization_id": "54321",
        "full_refresh": "daily",
        "nightly_time": "02:00",
    }
    assert record["credential_fingerprint"] == "a" * 64
    assert (
        post(browser, URL, {"action": "confirm", "preview": preview})["Location"]
        == response["Location"]
    )


def test_nightly_only_edit_uses_parish_time_and_real_scheduler_receipt(
    auth_service, google
):
    """Admin HTTP intent reaches canonical YAML, projections and scheduler slots."""
    from zoneinfo import ZoneInfo

    from parishkit.stewardship.accounts.configuration_models import (
        AppliedConfigurationVersion,
    )
    from parishkit.stewardship.jobs.scheduler import scheduler_session
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
    from parishkit.stewardship.source.production import produce_refreshes
    from parishkit.stewardship.source.refresh_models import SourceRefreshTick

    SourceCurrent.objects.get_or_create(singleton=True)
    SourceMutationLease.objects.get_or_create(singleton=True)
    browser, _ = signed_in()
    preview = hidden(
        post(
            browser,
            URL,
            edit(auth_service.store, organization_id="12345", nightly_time="03:15"),
        ),
        "preview",
    )
    response = post(browser, URL, {"action": "confirm", "preview": preview})
    assert response.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    assert request.request_schema == "source-cadence-patch-v8"
    assert (
        install_request(
            auth_service.store, request_id=request.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )
    configuration = AppliedConfigurationVersion.objects.get(
        pk=request.candidate_version_id
    )
    assert configuration.validation_schema == "source-cadence-v8"
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 2
    tick = SourceRefreshTick.objects.get(command__cause="nightly")
    assert tick.configuration_id == configuration.pk
    assert tick.nightly_time == "03:15"
    local = tick.due_at.astimezone(ZoneInfo(tick.timezone))
    assert (local.hour, local.minute) == (3, 15)
    assert browser.get(URL).status_code == 200


@pytest.mark.parametrize("direct_insert", [False, True])
def test_cadence_upgrade_does_not_bypass_manual_grant_provenance(
    auth_service, direct_insert
):
    """A structurally valid new rule still must identify this precise request."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts.configuration_requests import record_request
    from parishkit.stewardship.accounts.request_patch import build_candidate

    base = auth_service.store.active()
    integration = base.document()["sections"]["integrations"][0]
    rule = address("new-admin@example.org")
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": integration["id"],
            "values": {
                "settings": integration["values"]["settings"]
                | {"nightly_time": "03:15"}
            },
        },
        {"operation": "add", "section": "login_rules", **rule},
    ]
    if direct_insert:
        # A valid digest/patch cannot hide wrong provenance from the installer,
        # even if a producer bypasses the ordinary intake helper entirely.
        intent = build_candidate(
            base,
            patch,
            candidate_id=uuid4(),
            request_schema="source-cadence-patch-v8",
        )
        request = ConfigurationChangeRequest.objects.create(
            actor_id=uuid4(),
            request_key=uuid4(),
            request_schema="source-cadence-patch-v8",
            base_id=base.version_id,
            patch=intent.patch(),
            payload_fingerprint=intent.payload_fingerprint,
            candidate_version_id=intent.candidate.version_id,
            candidate_digest=intent.candidate.digest,
        )
        result = install_request(
            auth_service.store, request_id=request.pk, correlation_id=uuid4()
        )
        assert result.state == "failed" and result.failure_code == "invalid_candidate"
        assert auth_service.store.active() == base
    else:
        with pytest.raises(ConfigError):
            record_request(
                base_digest=base.digest,
                patch=patch,
                actor_id=uuid4(),
                request_key=uuid4(),
                correlation_id=uuid4(),
            )
        assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_id": "0"},
        {"organization_id": "12345"},
        {"candidate": SECRET},
        {"credential_fingerprint": "b" * 64},
        {"base_digest": "malformed"},
        {"organization_id": ["123", "456"]},
    ],
)
def test_invalid_and_hidden_settings_do_not_queue(auth_service, google, changes):
    """Private credentials and unknown fields are never part of public YAML input."""
    browser, _ = signed_in()
    response = post(browser, URL, edit(auth_service.store, **changes))
    assert response.status_code == 400
    assert SECRET.encode() not in response.content
    assert not ConfigurationChangeRequest.objects.exists()


def test_replacement_seals_once_and_real_web_cannot_read_ciphertext(
    auth_service, google, handoff
):
    """The target's private key opens the exact request; HTML/audit contain no value."""
    browser, _ = signed_in()
    with identity("pk_stewardship_web"):
        page = browser.get(URL)
        response = save_key(browser, SECRET, page)
        assert response.status_code == 302, response.content
        row = SecretReplacementRequest.objects.get()
        progress = browser.get(status_url(row))
        assert progress.status_code == 200
        assert progress["Cache-Control"] == "no-store"
        assert SECRET.encode() not in progress.content
        # An identical browser retry reuses the original request.
        assert save_key(browser, SECRET, page).status_code == 302
        changed = save_key(browser, "DIFFERENT-PRIVATE", page)
        assert changed.status_code == 400
        assert b"DIFFERENT-PRIVATE" not in changed.content
    row = SecretReplacementRequest.objects.get()
    staged = SealedCredentialStaging.objects.get()
    assert row.state == "staged" and row.required_consumers == ["worker"]
    assert timedelta(minutes=59) < row.expires_at - row.created_at <= timedelta(hours=1)
    assert handoff.open(row.pk, staged.ciphertext) == SECRET.encode()
    context = ProviderValidationContext.objects.get()
    assert context.settings == {"organization_id": 12345}
    assert (
        context.actor_id
        == row.requested_by_id
        == PortalSession.objects.get().principal_id
    )
    assert SECRET not in json.dumps(list(AuditEvent.objects.values()), default=str)
    assert (
        auth_service.store.active().document()["sections"]["integrations"][0]["values"][
            "credential_fingerprint"
        ]
        == "a" * 64
    )


def test_private_form_errors_and_csrf_never_stage_or_redisplay(
    auth_service, google, handoff
):
    """Invalid controls, CSRF and bad signatures fail before sealed persistence."""
    browser, _ = signed_in()
    page = browser.get(URL)
    token = hidden(page, "intent")
    for fields in [
        {"intent": ""},
        {"intent": "forged"},
        {"target": "slack"},
        {"intent": [token, token]},
    ]:
        response = save_key(browser, SECRET, page, **fields)
        assert response.status_code == 400
        assert SECRET.encode() not in response.content
    fields = {
        "action": "preview",
        "base_digest": hidden(page, "base_digest"),
        "organization_id": "12345",
        "candidate": SECRET,
        "intent": token,
    }
    assert browser.post(URL, fields).status_code == 403
    assert not SecretReplacementRequest.objects.exists()


def test_stale_configuration_and_authentication_deny_secret_intake(
    auth_service, google, handoff, monkeypatch
):
    """Old forms do not bind credentials to changed settings or renew authentication."""
    browser, _ = signed_in()
    page = browser.get(URL)
    version = auth_service.store.active()
    record = version.document()["sections"]["parish"][0]
    change(
        auth_service.store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": record["id"],
                "values": {"name": "Changed Parish"},
            }
        ],
    )
    assert save_key(browser, SECRET, page).status_code == 409
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.sessions.database_now",
        lambda: timezone.now() + timedelta(minutes=6),
    )
    assert save_key(browser, SECRET).status_code == 403
    assert not SecretReplacementRequest.objects.exists()


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_integrations_and_secrets_are_admin_only(auth_service, google, role):
    """Direct endpoints and submitted targets cannot promote a read-only role."""
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    for url in (
        INDEX,
        URL,
        "/admin/configuration/credentials/" + str(uuid4()),
    ):
        assert browser.get(url).status_code == 403
    response = post(
        browser,
        URL,
        {"action": "preview", "candidate": SECRET, "intent": "forged"},
    )
    assert response.status_code == 403
    assert not SecretReplacementRequest.objects.exists()


def test_missing_handoff_and_unknown_targets_fail_closed(auth_service, google):
    """Web cannot invent a key, create an unknown integration or claim readiness."""
    browser, _ = signed_in()
    page = browser.get(URL)
    assert page.status_code == 200 and page.context["credential_unavailable"]
    assert b'name="candidate"' not in page.content
    assert browser.get(INDEX + "/unknown").status_code == 404
    # The unlinked stand-alone "replace credential" page is gone: it staged a
    # key with nothing to switch to it, which stopped mail (#307 M1).
    for target in ("parishsoft", "google_workspace", "slack", "email"):
        assert browser.get(f"{INDEX}/{target}/credential").status_code == 404
    assert (
        browser.get("/admin/configuration/credentials/" + str(uuid4())).status_code
        == 404
    )


def test_credential_status_follows_live_and_never_renews_idle(
    auth_service, google, handoff
):
    """The self-updating status page is a passive read: polling it cannot keep
    an otherwise idle login alive, and it stays pending until installed."""
    browser, _ = signed_in()
    assert save_key(browser, SECRET).status_code == 302
    location = status_url(SecretReplacementRequest.objects.get())
    session = PortalSession.objects.get(revoked_at__isnull=True)
    activity = session.last_activity_at
    for _ in range(3):
        progress = browser.get(location)
        assert progress.status_code == 200
        assert b'data-live-status="credential"' in progress.content
        assert b"data-live-pending" in progress.content
        assert b"live-status-v1.js" in progress.content
    session.refresh_from_db()
    assert session.last_activity_at == activity
