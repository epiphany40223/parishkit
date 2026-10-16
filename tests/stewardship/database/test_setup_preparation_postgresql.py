"""First-campaign draft sections, as the setup wizard saved them before #142.

Setup collects no campaign since #142, and its service code refuses the
first-campaign steps. A draft saved by the older release may still hold
them, though, and an attempt confirmed before the upgrade still finishes
with its campaign (``test_setup_preview_postgresql.before_142``). The
helpers here stage those sections straight into the draft, as that release
stored them; the SQL draft guards, which still constrain those steps, check
every write. The tests check those guards.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.setup_campaign import _source_result
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.campaigns.work_locks import work_transaction

from ..campaign_factory import campaign, schedule
from ..content_factory import content
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_campaign_postgresql import completed
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def stage(request, attempt_id, updates):
    """Write first-campaign ``updates`` into the draft as the pre-#142 wizard did.

    Each step is created or replaced, and the attempt's version moves on as
    a save would, so later ordinary saves see a current version. Call inside
    ``web_login()``. Returns the attempt's id and new version.
    """
    context = {"actor_id": request.portal_session.principal_id}
    with transaction.atomic(), work_transaction():
        attempt = SetupAttempt.objects.select_for_update().get(pk=attempt_id)
        # The campaign goes first: SQL checks each child against it.
        for step in sorted(updates, key=lambda item: (item != "campaign", item)):
            row = SetupDraftSection.objects.filter(attempt=attempt, step=step).first()
            if row is None:
                SetupDraftSection.objects.create(
                    attempt=attempt, step=step, values=updates[step], **context
                )
            else:
                SetupDraftSection.objects.filter(pk=row.pk).update(
                    values=updates[step], version=F("version") + 1, **context
                )
        SetupAttempt.objects.filter(pk=attempt.pk).update(
            version=F("version") + 1, **context
        )
        attempt.refresh_from_db()
    return SimpleNamespace(attempt_id=attempt.pk, version=attempt.version)


def first_campaign(service, monkeypatch, **overrides):
    """The loaded draft with a first campaign on its staged load and timezone."""
    request, attempt = completed(service, monkeypatch)
    timezone = SetupDraftSection.objects.get(attempt=attempt, step="parish").values[
        "timezone"
    ]
    with web_login():
        status = stage(
            request,
            attempt.pk,
            {
                "campaign": {
                    "source_result": str(_source_result(attempt).pk),
                    "campaign": campaign(timezone=timezone, **overrides)["values"],
                }
            },
        )
    return request, status


def with_schedules(service, monkeypatch):
    """The first campaign with its Initial email and one Initial schedule."""
    request, status = first_campaign(service, monkeypatch)
    template = content(str(status.attempt_id), kind="email", slot="initial")
    row = schedule(
        str(status.attempt_id),
        template_version=template["id"],
        subject=template["values"]["subject"],
    )
    with web_login():
        status = stage(
            request,
            status.attempt_id,
            {"email_initial": template, "schedules": {"records": [row]}},
        )
    return request, status, template, row


def test_first_campaign_steps_are_refused_by_the_service(setup_service, monkeypatch):
    """Setup saves no first-campaign step since #142; the draft keeps its old one."""
    request, status, template, _ = with_schedules(setup_service, monkeypatch)
    with web_login():
        for step, values in (
            ("campaign", SetupDraftSection.objects.get(step="campaign").values),
            ("email_initial", template),
            ("schedules", {"records": []}),
        ):
            with pytest.raises(ValueError, match="Invalid public setup values"):
                save_section(
                    request,
                    setup_service,
                    status.attempt_id,
                    step=step,
                    values=values,
                    expected_version=status.version,
                )
    assert SetupDraftSection.objects.get(step="email_initial").values == template


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


def test_sql_rejects_foreign_campaign_content(setup_service, monkeypatch):
    """The worker-independent web guard repeats attempt and content ownership."""
    request, status = first_campaign(setup_service, monkeypatch)
    with (
        web_login(),
        pytest.raises(DatabaseError, match="original campaign and slot"),
        transaction.atomic(),
        work_transaction(),
    ):
        SetupDraftSection.objects.create(
            attempt_id=status.attempt_id,
            step="page_welcome",
            values=content(str(uuid4())),
            actor_id=request.portal_session.principal_id,
        )
