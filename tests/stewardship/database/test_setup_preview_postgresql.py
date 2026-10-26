"""Exact setup preview binds the original live owner and staged evidence."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import sys
from types import SimpleNamespace

import pytest
from django.core import signing
from django.db import transaction
from django.test import Client

from parishkit.stewardship.accounts.branding_staging import stage_branding
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_credentials import stage_credential
from parishkit.stewardship.accounts.setup_drafts import save_section, save_sections
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.accounts.setup_preview import (
    PREVIEW_SALT,
    prepare_preview,
    verify_preview,
)
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from ..content_factory import content
from ..test_integration_candidates import account
from ..test_setup_forms import VALUES
from .setup_check_builders import checked, checked_setup
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_branding_setup_postgresql import graphics
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import publish
from .test_setup_preparation_postgresql import with_schedules
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_setup_views_postgresql import setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def complete_draft(service, monkeypatch, tmp_path, *, campaign=False):
    """Prepare all public inputs and sealed credentials through actual owner APIs.

    The draft is the checked system setup (#2201: its parish data checked,
    with no setup load), with no first-campaign sections, as the wizard saves
    since #142. ``campaign`` instead stages first-campaign, content and
    schedule sections on a staged load, as an attempt saved before #142 may
    hold, and then checks its parish data too, which its final confirmation
    needs since #2201.
    """
    if campaign:
        request, status, _, _ = with_schedules(service, monkeypatch)
        attempt = checked(request, service, status.attempt_id)
    else:
        request, attempt = checked_setup(service)
    status = SimpleNamespace(attempt_id=attempt.pk, version=attempt.version)
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


def before_142(monkeypatch):
    """Stand in for an attempt confirmed before #142, whose Review held a campaign.

    Such an attempt, frozen before the upgrade, still finishes as it was
    confirmed, so the one-campaign completion stays tested. The release
    before #142 compiled the draft's campaign, content and schedule sections
    too: ``complete_draft`` then stages them, as its wizard pages did, and
    the Review compiles them with ``one_campaign_candidate``, that release's
    compiler.
    """
    from parishkit.stewardship.accounts import setup_preview
    from parishkit.stewardship.accounts.setup_content_values import CONTENT_STEPS

    module = sys.modules[__name__]
    monkeypatch.setattr(
        setup_preview,
        "SYSTEM_SECTIONS",
        setup_preview.SYSTEM_SECTIONS | {"campaign", "schedules", *CONTENT_STEPS},
    )
    monkeypatch.setattr(setup_preview, "compile_candidate", one_campaign_candidate)

    def staged(service):
        """The loaded draft with its first campaign, content and schedules.

        Its parish data is checked too (#2201), as a confirmation needs.
        """
        request, status, _, _ = with_schedules(service, monkeypatch)
        return request, checked(request, service, status.attempt_id)

    monkeypatch.setattr(module, "checked_setup", staged)


def one_campaign_candidate(base, *, attempt_id, operation_id, sections, **kwargs):
    """The pre-#142 setup compiler: the system candidate plus the first campaign.

    Its campaign is the draft's ``campaign`` section under the attempt's
    UUID, with the draft's applicable content and its schedules. The
    candidate is built and checked as setup builds any candidate
    (``setup_candidate._compile``).
    """
    from parishkit.stewardship.accounts import setup_candidate
    from parishkit.stewardship.accounts.content_forms import (
        LEGACY_PAGE_REFERENCES,
        page_slots,
    )
    from parishkit.stewardship.accounts.setup_content_values import CONTENT_STEPS
    from parishkit.stewardship.accounts.setup_forms import FORMS, validate_values

    values = {step: validate_values(step, sections[step]) for step in FORMS}
    campaign = dict(sections["campaign"]["campaign"])
    active_pages = page_slots(campaign)
    content = [
        sections[step]
        for step in CONTENT_STEPS
        if sections.get(step, {}).get("values")
        and (step.startswith("email_") or step[5:] in active_pages)
    ]
    campaign["content_versions"] = {
        row["values"]["slot"]: row["id"]
        for row in content
        if row["values"]["kind"] == "page"
        and row["values"]["slot"] in LEGACY_PAGE_REFERENCES
    }
    public = setup_candidate._system_records(
        base,
        attempt_id,
        operation_id,
        values,
        kwargs["branding"],
        kwargs["credentials"],
    ) | {
        "campaigns": [{"id": str(attempt_id), "values": campaign}],
        "content": content,
        "schedules": sections["schedules"]["records"],
    }
    return setup_candidate._compile(
        base, public, candidate_id=kwargs["candidate_id"], operation_id=operation_id
    )


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


@pytest.mark.parametrize(
    "step", ["page_submission_confirmation", "email_critical_alert"]
)
def test_retired_step_left_in_a_draft_blocks_neither_saving_nor_finishing(
    setup_service, monkeypatch, tmp_path, step
):
    """A draft saved before a slot retired (#260, #913) still saves and previews.

    The leftover step is written straight to SQL, as the older release did;
    the current code can no longer save it. Like every first-campaign section
    (#142), it never reaches the candidate, and later saves of the system
    steps still work.
    """
    request, status, _ = complete_draft(
        setup_service, monkeypatch, tmp_path, campaign=True
    )
    kind, _, slot = step.partition("_")
    with web_login(), transaction.atomic(), work_transaction():
        SetupDraftSection.objects.create(
            attempt_id=status.attempt_id,
            step=step,
            values=content(str(status.attempt_id), kind=kind, slot=slot),
            actor_id=request.portal_session.principal_id,
        )
    with web_login():
        status = save_section(
            request,
            setup_service,
            status.attempt_id,
            step="access",
            values=VALUES["access"] | {"staff_addresses": ["new@example.org"]},
            expected_version=status.version,
        )
        preview = prepare_preview(request, setup_service)
    assert not preview.compiled.candidate.document()["sections"].get("content")
    # The leftover is inert, not rewritten.
    assert SetupDraftSection.objects.filter(step=step).exists()


def test_review_page_is_gone_and_finish_follows_the_load(
    setup_http, monkeypatch, tmp_path, settings
):
    """Setup has no Review page (#2145): its old address is not kept (no
    redirect), and Finish setup's Back is Check parish data. Finish setup
    still compiles the exact preview and shows no private bytes."""
    request, _, media = complete_draft(setup_http, monkeypatch, tmp_path)
    settings.STEWARDSHIP_MEDIA_ROOT = media
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    with web_login():
        assert browser.get("/admin/setup/preview").status_code == 404
        response = browser.get("/admin/setup/confirm")
        assert response.status_code == 200, response.content
        content = response.content.decode()
        assert (
            '<a class="setup-back" data-nav-slot="back" href="/admin/setup/source">'
            in content
        )
        assert "/admin/setup/preview" not in content
        assert "synthetic-private" not in content
    assert not setup_http.configured()
