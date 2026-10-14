"""Original-login share/schedule forms and atomic cross-section reconciliation."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.setup_drafts import save_section, save_sections
from parishkit.stewardship.accounts.setup_models import SetupDraftSection
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction

from ..campaign_factory import schedule
from ..content_factory import content
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_content_postgresql import first_campaign
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_setup_views_postgresql import (  # noqa: F401
    admin_session,
    post,
    reviewed,
    setup_http,
)

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
        with pytest.raises(ValueError, match="Initial invitation schedule"):
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
            "campaign": original["campaign"] | {"start_date": "2054-10-05"}
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
        corrected = row | {"values": row["values"] | {"date": "2054-10-06"}}
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
        updated["values"]["date"] = "2054-09-01"
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
