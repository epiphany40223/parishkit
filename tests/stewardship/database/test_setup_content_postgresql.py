"""Private initial content editing uses real source evidence and restricted SQL."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from parishkit.stewardship.accounts.setup_campaign import campaign_catalog
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_models import SetupDraftSection
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction

from ..campaign_factory import campaign
from ..content_factory import content
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_campaign_postgresql import completed
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def first_campaign(service, monkeypatch, **overrides):
    """Use the original load result and web-authorized campaign staging."""
    request, attempt = completed(service, monkeypatch)
    with web_login():
        catalog = campaign_catalog(request, service, attempt.pk)
        saved = save_section(
            request,
            service,
            attempt.pk,
            step="campaign",
            values={
                "source_result": str(catalog.result_id),
                "campaign": campaign(timezone=catalog.timezone, **overrides)["values"],
            },
            expected_version=attempt.version,
        )
    return request, saved


def test_content_is_private_temporary_versioned_and_scrubbed(
    setup_service, monkeypatch
):
    """A large canonical page fits staging and never creates an active campaign."""
    request, attempt = first_campaign(setup_service, monkeypatch)
    record = content(
        str(attempt.attempt_id), html="<p>" + "x" * 70000 + "</p>", text="x" * 70000
    )
    with web_login():
        saved = save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step="page_welcome",
            values=record,
            expected_version=attempt.version,
        )
        assert SetupDraftSection.objects.get(step="page_welcome").values == record
        assert saved.version == attempt.version + 1
        with pytest.raises(LookupError):
            save_section(
                login(setup_service),
                setup_service,
                attempt.attempt_id,
                step="page_welcome",
                values=record,
                expected_version=saved.version,
            )
        cancel_setup(request, setup_service, attempt.attempt_id)
        assert SetupDraftSection.objects.get(step="page_welcome").values == {}
    assert not Campaign.objects.exists() and not setup_service.configured()


@pytest.mark.parametrize("invalid", ["campaign", "disabled"])
def test_content_rechecks_campaign_and_enabled_slot(
    setup_service, monkeypatch, invalid
):
    """Hidden controls and foreign IDs cannot create unrelated wizard content."""
    request, attempt = first_campaign(setup_service, monkeypatch)
    slot = "financial" if invalid == "disabled" else "welcome"
    record = content(
        str(uuid4()) if invalid == "campaign" else str(attempt.attempt_id), slot=slot
    )
    with web_login(), pytest.raises((ValueError, LookupError)):
        save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step=f"page_{slot}",
            values=record,
            expected_version=attempt.version,
        )
    assert not SetupDraftSection.objects.filter(step=f"page_{slot}").exists()


def test_sql_rejects_foreign_campaign_even_without_service(setup_service, monkeypatch):
    """The worker-independent web guard repeats attempt and content ownership."""
    request, attempt = first_campaign(setup_service, monkeypatch)
    with (
        web_login(),
        pytest.raises(DatabaseError, match="original campaign and slot"),
        transaction.atomic(),
        work_transaction(),
    ):
        SetupDraftSection.objects.create(
            attempt_id=attempt.attempt_id,
            step="page_welcome",
            values=content(str(uuid4())),
            actor_id=request.portal_session.principal_id,
        )


def test_content_http_csrf_preview_and_clear(setup_http, monkeypatch):
    """The actual editor sanitizes invalid input and renders only fictional data."""
    request, attempt = first_campaign(setup_http, monkeypatch)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    url = "/admin/setup/content/page/welcome"
    with web_login():
        assert browser.get("/admin/setup/content").status_code == 200
        response = browser.get(url)
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "no-store"
        data = {
            "version": str(attempt.version),
            "html": "<p>Hello {{ family_name }}</p>",
            "generate_text": "on",
        }
        assert browser.post(url, data).status_code == 403
        invalid = post(
            browser, url, data | {"html": '<p onclick="unsafe()">{{ invalid }}</p>'}
        )
        assert invalid.status_code == 400
        assert b'<p onclick="unsafe()">' not in invalid.content
        assert post(browser, url, data | {"unrecognized": "value"}).status_code == 400
        saved = post(browser, url, data)
        assert saved.status_code == 302, saved.content
        assert post(browser, url, data).status_code == 409
        preview = browser.get(url)
        assert b"Hello Sample Family" in preview.content
        assert b"Save temporary content" in preview.content
        assert (
            post(
                browser, url, {"version": str(attempt.version + 1), "clear": "on"}
            ).status_code
            == 302
        )
        assert SetupDraftSection.objects.get(step="page_welcome").values == {
            "id": None,
            "values": None,
        }
        assert browser.get("/admin/setup/content/page/financial").status_code == 404
    assert not setup_http.configured()
