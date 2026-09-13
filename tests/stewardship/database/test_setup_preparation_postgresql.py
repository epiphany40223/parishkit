"""Original-login share/schedule forms and atomic cross-section reconciliation."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.setup_drafts import save_section, save_sections
from parishkit.stewardship.accounts.setup_models import SetupDraftSection
from parishkit.stewardship.accounts.share_forms import default_share_options
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction

from ..campaign_factory import financial, schedule
from ..content_factory import content
from ..test_share_forms import data_for
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_content_postgresql import first_campaign
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def with_schedules(service, monkeypatch):
    """Save real original-owner content before its dependent logical schedule."""
    request, status = first_campaign(service, monkeypatch)
    template = content(str(status.attempt_id), kind="email", slot="initial")
    with web_login():
        status = save_section(
            request,
            service,
            status.attempt_id,
            step="email_initial",
            values=template,
            expected_version=status.version,
        )
        row = schedule(
            str(status.attempt_id),
            template_version=template["id"],
            subject=template["values"]["subject"],
        )
        status = save_section(
            request,
            service,
            status.attempt_id,
            step="schedules",
            values={"records": [row]},
            expected_version=status.version,
        )
    return request, status, template, row


def test_content_replacement_reconciles_and_clear_requires_explicit_removal(
    setup_service, monkeypatch
):
    """A named template and every draft consumer change together or not at all."""
    request, status, _, row = with_schedules(setup_service, monkeypatch)
    replacement = content(
        str(status.attempt_id), kind="email", slot="initial", subject="Updated subject"
    )
    with web_login():
        status = save_section(
            request,
            setup_service,
            status.attempt_id,
            step="email_initial",
            values=replacement,
            expected_version=status.version,
        )
        saved = SetupDraftSection.objects.get(step="schedules").values["records"][0]
        assert saved["id"] == row["id"]
        assert saved["values"]["subject"] == "Updated subject"
        assert saved["values"]["template_version"] == replacement["id"]
        with pytest.raises(ValueError, match="before clearing"):
            save_section(
                request,
                setup_service,
                status.attempt_id,
                step="email_initial",
                values={"id": None, "values": None},
                expected_version=status.version,
            )
        assert SetupDraftSection.objects.get(step="email_initial").values == replacement
        save_sections(
            request,
            setup_service,
            status.attempt_id,
            updates={
                "schedules": {"records": []},
                "email_initial": {"id": None, "values": None},
            },
            expected_version=status.version,
        )
        assert SetupDraftSection.objects.get(step="schedules").values == {"records": []}
    assert not Campaign.objects.exists()


def test_dates_and_schedules_save_atomically(setup_service, monkeypatch):
    """An ordinary date edit cannot strand mail; one combined edit resolves it."""
    request, status, _, row = with_schedules(setup_service, monkeypatch)
    with web_login():
        original = SetupDraftSection.objects.get(step="campaign").values
        changed = original | {
            "campaign": original["campaign"] | {"start_date": "2026-10-05"}
        }
        with pytest.raises(ConfigError):
            save_section(
                request,
                setup_service,
                status.attempt_id,
                step="campaign",
                values=changed,
                expected_version=status.version,
            )
        assert SetupDraftSection.objects.get(step="campaign").values == original
        corrected = row | {"values": row["values"] | {"date": "2026-10-06"}}
        save_sections(
            request,
            setup_service,
            status.attempt_id,
            updates={"campaign": changed, "schedules": {"records": [corrected]}},
            expected_version=status.version,
        )
        assert SetupDraftSection.objects.get(step="campaign").values == changed
        assert SetupDraftSection.objects.get(step="schedules").values == {
            "records": [corrected]
        }


@pytest.mark.parametrize("invalid", ["owner", "template", "duplicate", "time"])
def test_bad_schedule_collection_keeps_previous_draft(
    setup_service, monkeypatch, invalid
):
    """Canonical individual values alone do not authorize a whole schedule set."""
    request, status, _, row = with_schedules(setup_service, monkeypatch)
    updated = row | {"values": dict(row["values"])}
    if invalid == "owner":
        updated["values"]["campaign_id"] = str(uuid4())
    elif invalid == "template":
        updated["values"]["template_version"] = str(uuid4())
    elif invalid == "time":
        updated["values"]["date"] = "2026-09-01"
    records = [row, updated] if invalid == "duplicate" else [updated]
    with web_login():
        with pytest.raises((ConfigError, ValueError)):
            save_section(
                request,
                setup_service,
                status.attempt_id,
                step="schedules",
                values={"records": records},
                expected_version=status.version,
            )
        assert SetupDraftSection.objects.get(step="schedules").values == {
            "records": [row]
        }


def test_sql_schedule_owner_guard(setup_service, monkeypatch):
    """A direct web UPDATE cannot substitute another campaign for the owner."""
    request, status, _, row = with_schedules(setup_service, monkeypatch)
    row["values"]["campaign_id"] = str(uuid4())
    with (
        web_login(),
        pytest.raises(DatabaseError, match="original campaign"),
        transaction.atomic(),
        work_transaction(),
    ):
        saved = SetupDraftSection.objects.get(step="schedules")
        SetupDraftSection.objects.filter(pk=saved.pk).update(
            values={"records": [row]},
            version=saved.version + 1,
            actor_id=request.portal_session.principal_id,
        )


def test_share_http_preserves_ids_and_has_closed_versioned_fields(
    setup_http, monkeypatch
):
    """Real initial share forms reorder saved IDs and reject invisible extras."""
    previous = default_share_options()
    request, status = first_campaign(
        setup_http,
        monkeypatch,
        modules=["financial"],
        share_options=previous,
        financial=financial(fund_duids=[9], comparison_fund_duids=[9]),
    )
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    url = "/admin/setup/shares"
    data = data_for(previous)
    data.pop("action")
    data.pop("base_digest")
    data.update(
        version=str(status.version), **{"options-0-ORDER": "2", "options-1-ORDER": "1"}
    )
    with web_login():
        response = browser.get(url)
        assert response.status_code == 200, response.content
        assert browser.post(url, data).status_code == 403
        for changed in (
            {"options-99-label": "Unexpected"},
            {"options-TOTAL_FORMS": "999999"},
            {"options-0-label": "{{ family_code }}"},
            {"options-0-id": str(uuid4())},
        ):
            assert post(browser, url, data | changed).status_code == 400
        saved = post(browser, url, data)
        assert saved.status_code == 302, saved.content
        options = SetupDraftSection.objects.get(step="campaign").values["campaign"][
            "share_options"
        ]
        assert [option["id"] for option in options[:2]] == [
            previous[1]["id"],
            previous[0]["id"],
        ]
        assert post(browser, url, data).status_code == 409
    assert not setup_http.configured()


def test_schedule_http_uses_local_window_and_saved_subject(setup_http, monkeypatch):
    """The window and formset share one version and reject forged subject values."""
    from ..test_schedule_forms import data_for as schedule_data

    request, status, _, row = with_schedules(setup_http, monkeypatch)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    url = "/admin/setup/schedules"
    data = schedule_data([row])
    data.pop("action")
    data.update(
        {
            "version": str(status.version),
            "window-start_date": "2026-10-01",
            "window-end_date": "2026-10-31",
        }
    )
    with web_login():
        response = browser.get(url)
        assert response.status_code == 200, response.content
        assert b"America/New_York" in response.content
        assert browser.post(url, data).status_code == 403
        assert (
            post(browser, url, data | {"schedules-0-subject": "Unowned"}).status_code
            == 400
        )
        assert (
            post(browser, url, data | {"schedules-0-date": "2026-09-01"}).status_code
            == 400
        )
        duplicate = data | {
            "schedules-1-kind": "initial",
            "schedules-1-date": "2026-10-02",
            "schedules-1-time": "09:00:00",
            "schedules-1-template_version": row["values"]["template_version"],
        }
        rejected = post(browser, url, duplicate)
        assert rejected.status_code == 400 and b"Nothing was saved" in rejected.content
        saved = post(browser, url, data)
        assert saved.status_code == 302, saved.content
        assert post(browser, url, data).status_code == 409
        assert SetupDraftSection.objects.get(step="schedules").values == {
            "records": [row]
        }
    assert not setup_http.configured()
