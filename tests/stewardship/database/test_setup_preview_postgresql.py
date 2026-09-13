"""Exact first-campaign preview binds the original live owner and staged evidence."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import pytest
from django.core import signing
from django.test import Client

from parishkit.stewardship.accounts.branding_staging import stage_branding
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_credentials import stage_credential
from parishkit.stewardship.accounts.setup_drafts import save_section, save_sections
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_preview import (
    PREVIEW_SALT,
    prepare_preview,
    verify_preview,
)
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.storage import StaleRecordError

from ..test_integration_candidates import account
from ..test_setup_forms import VALUES
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_branding_setup_postgresql import graphics
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import publish
from .test_setup_preparation_postgresql import with_schedules
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_setup_views_postgresql import setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def complete_draft(service, monkeypatch, tmp_path):
    """Prepare all public inputs and sealed credentials through actual owner APIs."""
    request, status, _, _ = with_schedules(service, monkeypatch)
    publish("google_workspace", material=b"g" * 32)
    media = tmp_path / "media"
    media.mkdir(mode=0o700)
    with web_login():
        status = save_sections(
            request,
            service,
            status.attempt_id,
            updates=VALUES,
            expected_version=status.version,
        )
        status, _ = stage_credential(
            request,
            service,
            status.attempt_id,
            target="google_workspace",
            candidate=account(),
            expected_version=status.version,
        )
        bundle = stage_branding(
            request,
            service,
            media,
            graphics(),
            base_digest=service.store.active().digest,
            setup_attempt_id=status.attempt_id,
        )
        status = save_section(
            request,
            service,
            status.attempt_id,
            step="branding",
            values={"bundle_id": str(bundle)},
            expected_version=status.version,
        )
    return request, status, media


def test_exact_preview_is_passive_and_stale_on_any_edit(
    setup_service, monkeypatch, tmp_path
):
    """Receipt signatures pin current values, not authority or stale session state."""
    request, status, _ = complete_draft(setup_service, monkeypatch, tmp_path)
    with web_login():
        activity = PortalSession.objects.get(
            pk=request.portal_session.pk
        ).last_activity_at
        preview = prepare_preview(request, setup_service)
        token = signing.dumps(preview.binding(), salt=PREVIEW_SALT)
        assert (
            verify_preview(request, setup_service, token).binding() == preview.binding()
        )
        assert (
            PortalSession.objects.get(pk=request.portal_session.pk).last_activity_at
            == activity
        )
        assert (
            preview.compiled.candidate.predecessor_digest
            == setup_service.store.active().digest
        )
        assert "synthetic-private" not in str(preview.compiled.candidate.document())
        save_section(
            request,
            setup_service,
            status.attempt_id,
            step="access",
            values=VALUES["access"] | {"staff_addresses": ["new@example.org"]},
            expected_version=status.version,
        )
        with pytest.raises(StaleRecordError):
            verify_preview(request, setup_service, token)
        with pytest.raises(signing.BadSignature):
            verify_preview(request, setup_service, token + "tampered")
        with pytest.raises(ValueError):
            verify_preview(request, setup_service, "x" * 4097)
    assert SetupAttempt.objects.get().state == "collecting"
    assert not Campaign.objects.exists() and not setup_service.configured()


def test_another_login_and_cancelled_draft_cannot_reuse_preview(
    setup_service, monkeypatch, tmp_path
):
    """Original-login binding survives signature replay and explicit cancellation."""
    request, status, _ = complete_draft(setup_service, monkeypatch, tmp_path)
    with web_login():
        token = signing.dumps(
            prepare_preview(request, setup_service).binding(), salt=PREVIEW_SALT
        )
        with pytest.raises(LookupError):
            verify_preview(login(setup_service), setup_service, token)
        cancel_setup(request, setup_service, status.attempt_id)
        with pytest.raises(LookupError):
            verify_preview(request, setup_service, token)


def test_preview_http_is_private_inert_and_contains_all_named_slots(
    setup_http, monkeypatch, tmp_path, settings
):
    """Actual restricted web responses disclose public intent, never private bytes."""
    request, _, media = complete_draft(setup_http, monkeypatch, tmp_path)
    settings.STEWARDSHIP_MEDIA_ROOT = media
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    with web_login():
        response = browser.get("/admin/setup/preview")
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "no-store"
        for expected in (
            b"First-campaign setup preview",
            b"Sample Parish",
            b"Thank You page",
            b"Initial invitation",
            b"no private credential values",
            b"Check readiness and finish setup",
        ):
            assert expected in response.content
        assert b"synthetic-private" not in response.content
        assert (
            b"example.invalid" not in response.content
        )  # No sample link requested by this template.
        assert browser.post("/admin/setup/preview", {}).status_code in {403, 405}
        assert browser.get("/admin/setup/preview?mode=production").status_code == 400
    assert not setup_http.configured()
