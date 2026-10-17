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
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_branding_setup_postgresql import graphics
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_campaign_postgresql import completed
from .test_setup_credentials_postgresql import publish
from .test_setup_preparation_postgresql import with_schedules
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_setup_views_postgresql import (
    admin_session,
    reviewed,
    setup_http,  # noqa: F401
)

pytestmark = pytest.mark.django_db(transaction=True)


def complete_draft(service, monkeypatch, tmp_path, *, campaign=False):
    """Prepare all public inputs and sealed credentials through actual owner APIs.

    The draft is the loaded system setup, with no first-campaign sections, as
    the wizard saves since #142. ``campaign`` also stages first-campaign,
    content and schedule sections, as an attempt saved before #142 may hold.
    """
    if campaign:
        request, status, _, _ = with_schedules(service, monkeypatch)
    else:
        request, attempt = completed(service, monkeypatch)
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

    def staged(service, monkeypatch):
        """The loaded draft with its first campaign, content and schedules."""
        request, status, _, _ = with_schedules(service, monkeypatch)
        return request, SimpleNamespace(pk=status.attempt_id, version=status.version)

    monkeypatch.setattr(module, "completed", staged)


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


def test_preview_http_is_private_inert_and_shows_the_system_settings(
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
            b"<h1>Review</h1>",
            b"Sample Parish",
            b"keys themselves are never displayed",
        ):
            assert expected in response.content
        # Review follows the tests (#1476). Opened by its address before the
        # email test was accepted, its Readiness names the test with a link
        # to it, it offers no Continue (Finish setup waits), and it records
        # no review, so it is asked for again once the test is accepted.
        content = response.content.decode().split("<h1>", 1)[1]
        assert "Check readiness and finish setup" not in content
        assert 'href="/admin/setup/slack-test"' not in content
        assert 'href="/admin/setup/confirm"' not in content
        assert "Test email: not accepted for these settings yet." in content
        assert '<a href="/admin/setup/mail-test">Go to “Test email”</a>' in content
        assert "setup-continue" not in content
        assert b"synthetic-private" not in response.content
        assert "setup_reviewed" not in admin_session(browser)
        # Setup collects no campaign (#142): no page or email samples.
        assert b"Named pages and email samples" not in response.content
        assert browser.post("/admin/setup/preview", {}).status_code in {403, 405}
        assert browser.get("/admin/setup/preview?mode=production").status_code == 400
    assert not setup_http.configured()


def test_review_after_an_accepted_test_shows_it_and_continues_to_finish(
    setup_http, monkeypatch, tmp_path, settings
):
    """With the email test of this draft version accepted, Review says so,
    links no step from its body, continues to Finish setup and records the
    review for this exact draft version only (#1476)."""
    from .test_setup_confirmation_postgresql import prepared

    request, _, _ = prepared(setup_http, monkeypatch, tmp_path)
    settings.STEWARDSHIP_MEDIA_ROOT = tmp_path / "media"
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    with web_login():
        response = browser.get("/admin/setup/preview")
        assert response.status_code == 200, response.content
        content = response.content.decode().split("<h1>", 1)[1]
        assert "Test email: accepted for these settings." in content
        assert "Test Slack" not in content  # Slack is off
        body = content.split('<div class="setup-actions">', 1)[0]
        for path in ("/admin/setup/mail-test", "/admin/setup/confirm"):
            assert f'href="{path}"' not in body, path
        assert '<a class="setup-continue" href="/admin/setup/confirm">' in content
        assert '<a class="setup-back" href="/admin/setup/mail-test">' in content
        attempt = SetupAttempt.objects.get()
        assert reviewed(browser) == {
            "attempt": str(attempt.pk),
            "preview": attempt.version,
        }
