"""Serve actual shared templates/assets locally; never contact a real provider."""

import os
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from PIL import Image

from parishkit.stewardship.accounts.branding_views import LogoForm
from parishkit.stewardship.accounts.campaign_forms import CampaignForm
from parishkit.stewardship.accounts.campaign_mail_views import CampaignMailForm
from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.accounts.integration_forms import (
    CredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.accounts.parish_views import ParishForm
from parishkit.stewardship.accounts.schedule_forms import Schedules, ScheduleWindow
from parishkit.stewardship.accounts.schedule_views import _describe
from parishkit.stewardship.accounts.setup_branding_views import SetupLogoForm
from parishkit.stewardship.accounts.setup_campaign_views import SetupCampaignForm
from parishkit.stewardship.accounts.setup_confirmation_views import (
    SetupConfirmationForm,
)
from parishkit.stewardship.accounts.setup_content_views import SetupContentForm
from parishkit.stewardship.accounts.setup_credential_views import SetupCredentialForm
from parishkit.stewardship.accounts.setup_forms import FORMS, STEPS
from parishkit.stewardship.accounts.setup_mail_views import SetupMailForm
from parishkit.stewardship.accounts.setup_notification_views import (
    SetupNotificationForm,
)
from parishkit.stewardship.accounts.setup_schedule_views import SetupScheduleWindow
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.web.security import CSP

from ..campaign_factory import campaign, schedule
from .delivery_components import components as delivery_components
from .digest_components import components as digest_components
from .directory_components import components as directory_components
from .go_live_components import components as go_live_components
from .information_components import components as information_components
from .ministry_components import components as ministry_components
from .pause_components import components as pause_components
from .report_components import components as report_components
from .weekly_components import components as weekly_components

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def browser_opt_in():
    """Skip before any browser, HTTP-server or npm-asset fixture is evaluated."""
    if os.environ.get("PARISHKIT_RUN_BROWSER_TESTS") != "1":
        pytest.skip("Browser component tests require PARISHKIT_RUN_BROWSER_TESTS=1.")


@pytest.fixture(scope="session")
def browser_processes():
    """Cache expensive startup, preserving fresh WebKit drivers and browsers."""
    from playwright.sync_api import sync_playwright

    from .process_pool import BrowserProcesses

    processes = BrowserProcesses(sync_playwright)
    try:
        yield processes
    finally:
        processes.close()


@pytest.fixture
def browser_engine(request):
    """Only opt-in tests request the session pool; baseline needs no Playwright."""
    processes = request.getfixturevalue("browser_processes")
    with processes.acquire(request.param) as browser:
        yield browser


@pytest.fixture
def page(browser_engine):
    """Each scenario has isolated browser state and an explicit non-parish zone."""
    context = browser_engine.new_context(
        timezone_id="America/Los_Angeles", reduced_motion="reduce"
    )
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture(scope="module")
def component_origin():
    """An exact response allowlist avoids exposing source files through the server."""
    mail_campaign = campaign()
    mail = schedule(mail_campaign["id"])
    context = {
        "server_now": NOW,
        "deadline": NOW + timedelta(hours=1),
        "absolute_deadline": NOW + timedelta(hours=4),
        "csrf_token": "a" * 64,
    }
    admin = {
        "admin": True,
        "parish_name": "Sample Parish",
        "navigation": [
            {"url": "/home", "label": "Home"},
            {"url": "/parish-settings", "label": "Parish settings"},
            {"url": "/ministries", "label": "Ministry activity"},
        ],
        "testing": True,
        "testing_recipient": "testing@example.org",
        "background": {"total": 1, "running": 1},
        "server_now": NOW,
        "idle_deadline": NOW + timedelta(hours=1),
        "absolute_deadline": NOW + timedelta(hours=12),
    }
    ministry = {
        "duid": 12345,
        "name": "Community outreach",
        "active": True,
        "included": True,
    }
    branding_asset = {"pk": uuid4(), "label": "large", "width": 1024, "height": 512}
    setup_draft = {
        "status": {"attempt_id": uuid4(), "state": "collecting", "version": 2},
        "sections": {},
        "idle_at": NOW + timedelta(minutes=30),
        "absolute_at": NOW + timedelta(hours=12),
        "watchdog_at": None,
    }
    responses = {
        # Admin chrome immediately polls this endpoint, including on report pages.
        # Failure-specific tests can still replace it with an explicit route.
        "/admin/presence?format=count": ("application/json", '{"count":0}'),
        "/login": ("text/html", render_to_string("stewardship/login.html", context)),
        "/family-login": (
            "text/html",
            render_to_string("stewardship/family-login.html", context),
        ),
        "/family": ("text/html", render_to_string("stewardship/family.html", context)),
        "/family-testing": (
            "text/html",
            render_to_string("stewardship/family.html", context | {"testing": True}),
        ),
        "/errors": (
            "text/html",
            render_to_string(
                "stewardship/family-login.html",
                {
                    **context,
                    "errors": [
                        {"field_id": "family-code", "message": "Check the Family code."}
                    ],
                },
            ),
        ),
    }
    for path, template, extra in (
        (
            "/branding-settings",
            "branding-settings",
            {
                "form": LogoForm(initial={"base_digest": "a" * 64}),
                "assets": [branding_asset],
            },
        ),
        (
            "/branding-preview",
            "branding-preview",
            {"assets": [branding_asset], "preview": "synthetic-preview"},
        ),
        (
            "/integrations",
            "integrations",
            {
                "integrations": [
                    {"target": "parishsoft", "label": "ParishSoft", "configured": True}
                ]
            },
        ),
        (
            "/integration-settings",
            "integration-settings",
            {
                "target": "parishsoft",
                "label": "ParishSoft",
                "fingerprint": "a" * 64,
                "latest": {"state": "staged", "updated_at": NOW},
                "replacement_allowed": True,
                "form": IntegrationForm(
                    "parishsoft",
                    initial={"organization_id": 12345, "base_digest": "a" * 64},
                ),
            },
        ),
        (
            "/integration-preview",
            "integration-preview",
            {
                "target": "parishsoft",
                "label": "ParishSoft",
                "preview": "synthetic-preview",
                "changes": [
                    {"label": "Organization ID", "before": "12345", "after": "54321"}
                ],
            },
        ),
        (
            "/credential-replace",
            "credential-replace",
            {
                "target": "parishsoft",
                "label": "ParishSoft",
                "form": CredentialForm(initial={"intent": "synthetic-intent"}),
            },
        ),
        (
            "/credential-selection",
            "credential-selection",
            {
                "label": "ParishSoft",
                "before": "a" * 64,
                "receipt": {"pk": uuid4(), "resulting_fingerprint": "b" * 64},
                "preview": "synthetic-selection-intent",
                "selected": False,
            },
        ),
        (
            "/credential-status",
            "credential-status",
            {
                "receipt": {"request_id": uuid4(), "state": "awaiting_ack"},
                "pending": True,
            },
        ),
        (
            "/clone-settings",
            "clone-settings",
            {
                "source": {
                    "pk": mail_campaign["id"],
                    "active_configuration": mail_campaign["values"],
                },
                "form": CampaignForm(
                    initial={
                        "timezone": "America/New_York",
                        "census": True,
                        "base_digest": "a" * 64,
                    }
                ),
                "schedules": Schedules(
                    previous=[mail],
                    templates=[],
                    campaign_id=mail_campaign["id"],
                    campaign=mail_campaign["values"],
                    prefix="schedules",
                ),
                "clone_seed": "synthetic-seed",
            },
        ),
        (
            "/clone-preview",
            "clone-preview",
            {
                "source": {
                    "pk": mail_campaign["id"],
                    "active_configuration": mail_campaign["values"],
                },
                "changes": [
                    {
                        "label": "Campaign dates",
                        "after": "2027-10-01 through 2027-10-31",
                    }
                ],
                "schedules": [_describe(mail["values"], mail_campaign["values"])],
                "preview": "synthetic-preview",
                "content_previews": [
                    {
                        "label": "Welcome",
                        "rendered": {
                            "subject": None,
                            "html": "<p>Welcome, Sample Family.</p>",
                            "text": "Welcome, Sample Family.",
                        },
                    }
                ],
            },
        ),
        (
            "/schedule-settings",
            "schedule-settings",
            {
                "campaign": {
                    "pk": mail_campaign["id"],
                    "active_configuration": mail_campaign["values"],
                },
                "window": ScheduleWindow(
                    previous=mail_campaign["values"], editable=True, prefix="window"
                ),
                "schedules": Schedules(
                    previous=[mail],
                    templates=[],
                    campaign_id=mail_campaign["id"],
                    campaign=mail_campaign["values"],
                    prefix="schedules",
                ),
                "base_digest": "a" * 64,
                "editable": True,
            },
        ),
        (
            "/schedule-preview",
            "schedule-preview",
            {
                "campaign": {
                    "pk": mail_campaign["id"],
                    "active_configuration": mail_campaign["values"],
                },
                "changes": [
                    {
                        "label": "Reminder",
                        "operation": "remove",
                        "before": _describe(mail["values"], mail_campaign["values"]),
                        "after": None,
                        "impact": {
                            "delivered": 1234,
                            "blocking": 0,
                            "occurrences": 3456,
                            "cancellable": 2222,
                            "outboxes": 0,
                            "failed": 0,
                        },
                    }
                ],
                "window_changes": {},
                "blocking": 0,
                "preview": "synthetic-preview",
            },
        ),
        (
            "/content-settings",
            "content-settings",
            {
                "campaign": {
                    "pk": uuid4(),
                    "active_configuration": {"name": "Sample campaign"},
                },
                "label": "Family welcome",
                "visual": "<p>Hello Sample Family</p>",
                "placeholders": ["family_name", "parish_name"],
                "form": ContentForm(
                    kind="page",
                    initial={
                        "base_digest": "a" * 64,
                        "html": "<p>Hello Sample Family</p>",
                        "text": "Hello Sample Family",
                        "generate_text": True,
                    },
                ),
            },
        ),
        (
            "/setup-content-edit",
            "setup-content-edit",
            {
                "draft": setup_draft,
                "label": "Family welcome",
                "visual": "<p>Hello Sample Family</p>",
                "placeholders": ["family_name", "parish_name"],
                "form": SetupContentForm(
                    kind="page",
                    initial={
                        "html": "<p>Hello Sample Family</p>",
                        "text": "Hello Sample Family",
                        "generate_text": True,
                    },
                ),
                "sample": {
                    "html": "<p>Hello Sample Family</p>",
                    "text": "Hello Sample Family",
                },
            },
        ),
        (
            "/setup-shares",
            "setup-shares",
            {
                "draft": setup_draft,
                "campaign_name": "Sample campaign",
                "formset": ShareOptions(
                    prefix="options", previous=default_share_options()
                ),
            },
        ),
        (
            "/setup-slack-test",
            "setup-notification",
            {
                "draft": setup_draft,
                "channel_id": "CFIXTURE",
                "pending": True,
                "unknown": False,
                "form": SetupNotificationForm(
                    initial={
                        "preview_token": "synthetic-preview",
                        "request_key": uuid4(),
                    }
                ),
                "items": [
                    {
                        "id": "synthetic-delivery",
                        "state": "queued",
                        "label": "Awaiting Slack installer",
                        "created_at": NOW.isoformat(),
                        "current": True,
                    }
                ],
            },
        ),
        (
            "/setup-mail-test",
            "setup-mail",
            {
                "draft": setup_draft,
                "testing_recipient": "testing@example.org",
                "pending": True,
                "unknown": False,
                "form": SetupMailForm(
                    initial={
                        "preview_token": "synthetic-preview",
                        "request_key": uuid4(),
                        "slot": "initial",
                    }
                ),
                "items": [
                    {
                        "id": "synthetic-delivery",
                        "state": "queued",
                        "label": "Awaiting mail worker",
                        "created_at": NOW.isoformat(),
                        "current": True,
                    }
                ],
            },
        ),
        (
            "/campaign-mail",
            "campaign-mail",
            {
                "campaign": {
                    "pk": mail_campaign["id"],
                    "active_configuration": mail_campaign["values"],
                },
                "form": CampaignMailForm(
                    initial={"preview_token": "synthetic-preview"}
                ),
                "sample": {
                    "subject": "[TEST] Sample invitation",
                    "html": "<p>Hello Sample Family.</p>",
                    "text": "Hello Sample Family.",
                },
                "testing_recipient": "testing@example.org",
                "pending": False,
                "unknown": False,
                "items": [
                    {
                        "id": uuid4(),
                        "label": "Provider accepted the test",
                        "created_at": NOW,
                        "current": True,
                    }
                ],
            },
        ),
        (
            "/campaign-mail-unknown",
            "campaign-mail",
            {
                "campaign": {
                    "pk": mail_campaign["id"],
                    "active_configuration": mail_campaign["values"],
                },
                "form": CampaignMailForm(
                    initial={"preview_token": "synthetic-preview"}
                ),
                "sample": {
                    "subject": "[TEST] Sample invitation",
                    "html": "<p>Hello Sample Family.</p>",
                    "text": "Hello Sample Family.",
                },
                "testing_recipient": "testing@example.org",
                "pending": False,
                "unknown": True,
                "items": [
                    {
                        "id": uuid4(),
                        "label": "Delivery uncertain",
                        "created_at": NOW,
                        "current": False,
                    }
                ],
            },
        ),
        (
            "/campaign-mail-pending",
            "campaign-mail",
            {
                "campaign": {
                    "pk": mail_campaign["id"],
                    "active_configuration": mail_campaign["values"],
                },
                "form": CampaignMailForm(
                    initial={"preview_token": "synthetic-preview"}
                ),
                "sample": {
                    "subject": "[TEST] Sample invitation",
                    "html": "<p>Hello Sample Family.</p>",
                    "text": "Hello Sample Family.",
                },
                "testing_recipient": "testing@example.org",
                "pending": True,
                "unknown": False,
                "items": [
                    {
                        "id": uuid4(),
                        "label": "Awaiting mail worker",
                        "created_at": NOW,
                        "current": True,
                    }
                ],
            },
        ),
        (
            "/setup-confirmation",
            "setup-confirmation",
            {
                "draft": setup_draft,
                "form": SetupConfirmationForm(
                    initial={"preview_token": "synthetic-preview"}
                ),
                "candidate_digest": "a" * 64,
            },
        ),
        (
            "/setup-confirmation-unready",
            "setup-confirmation",
            {
                "draft": setup_draft,
                "form": SetupConfirmationForm(
                    initial={"preview_token": "synthetic-preview"}
                ),
                "candidate_digest": "a" * 64,
                "readiness_problem": "The reviewed email test must be accepted.",
            },
        ),
        (
            "/setup-finalization",
            "setup-cancel",
            {
                "attempt": {"state": "frozen", "attempt_id": uuid4()},
                "checkpoint": "yaml_activated",
                "prepared": True,
                "credentials": [
                    {"target": "parishsoft", "request__state": "awaiting_ack"}
                ],
                "source": {
                    "id": uuid4(),
                    "state": "running",
                    "phase": "loading",
                    "progress": Percentage(1234, 5678),
                },
            },
        ),
        (
            "/setup-installation",
            "setup-cancel",
            {
                "attempt": {"state": "frozen", "attempt_id": uuid4()},
                "checkpoint": "validating",
                "prepared": False,
                "credentials": [],
            },
        ),
        (
            "/setup-preview",
            "setup-preview",
            {
                "draft": setup_draft,
                "parish": {
                    "name": "Sample Parish",
                    "website": "https://example.invalid/",
                    "phone": "+12025550123",
                },
                "campaign": mail_campaign["values"],
                "testing_recipient": "testing@example.org",
                "candidate_digest": "a" * 64,
                "document": '{"sections": {"campaigns": []}}',
                "samples": [
                    {
                        "label": "Family welcome",
                        "sample": {
                            "html": "<p>Hello Sample Family</p>",
                            "text": "Hello Sample Family",
                        },
                    }
                ],
            },
        ),
        (
            "/setup-schedules",
            "setup-schedules",
            {
                "draft": setup_draft,
                "campaign_name": "Sample campaign",
                "window": SetupScheduleWindow(
                    prefix="window", previous=mail_campaign["values"]
                ),
                "schedules": Schedules(
                    prefix="schedules",
                    previous=[mail],
                    templates=[],
                    campaign_id=mail_campaign["id"],
                    campaign=mail_campaign["values"],
                ),
            },
        ),
        (
            "/content-history",
            "content-history",
            {
                "campaign": {
                    "pk": uuid4(),
                    "state": "archived",
                    "active_configuration": {"name": "Prior campaign"},
                },
                "version": {"pk": uuid4()},
                "entries": [
                    {"id": uuid4(), "label": "Family welcome", "subject": None}
                ],
                "selected": True,
                "sample": {
                    "html": "<p>Hello Sample Family</p>",
                    "text": "Hello Sample Family",
                    "subject": None,
                },
            },
        ),
        (
            "/content-preview",
            "content-preview",
            {
                "campaign": {"pk": uuid4()},
                "label": "Family welcome",
                "before": None,
                "after": {
                    "html": "<p>Hello Sample Family</p>",
                    "text": "Hello Sample Family",
                    "subject": None,
                },
                "preview": "synthetic-preview",
                "affected": [],
            },
        ),
        (
            "/home",
            "home",
            {"configuration": {"mode": "testing"}, "admin_chrome": admin},
        ),
        (
            "/codes",
            "codes",
            {
                "table_caption": "Active Families",
                "table_headings": ["Family DUID", "Code"],
                "table_rows": [["1234567890123456789", "ABCDEFGH"]],
                "page": 2,
                "size": 50,
                "has_next": True,
                "previous_page": 1,
                "next_page": 3,
            },
        ),
        (
            "/setup",
            "setup",
            {
                "draft": setup_draft,
                "steps": [
                    {"key": key, "label": label, "saved": False}
                    for key, label in STEPS.items()
                ],
            },
        ),
        (
            "/setup-branding",
            "setup-branding",
            {"draft": setup_draft, "form": SetupLogoForm(), "assets": []},
        ),
        (
            "/setup-credential",
            "setup-credential",
            {
                "draft": setup_draft,
                "form": SetupCredentialForm("parishsoft"),
                "label": "ParishSoft",
                "saved": True,
            },
        ),
        (
            "/setup-campaign",
            "setup-campaign",
            {
                "draft": setup_draft,
                "form": SetupCampaignForm(
                    initial={"timezone": "America/New_York"},
                    ministries=[("1", "Music ministry")],
                    funds=[("9", "Offertory")],
                ),
            },
        ),
        (
            "/setup-source-progress",
            "setup-source-progress",
            {
                "progress": {
                    "server_now": NOW.isoformat(),
                    "task_id": uuid4(),
                    "task_state": "running",
                    "setup_state": "loading",
                    "phase": "fetching",
                    "current": 0,
                    "total": 0,
                    "active": True,
                    "idle_at": (NOW + timedelta(minutes=30)).isoformat(),
                    "watchdog_at": (NOW + timedelta(hours=2)).isoformat(),
                    "absolute_at": (NOW + timedelta(hours=12)).isoformat(),
                }
            },
        ),
        ("/availability", "availability", {"setup": True, "admin": True}),
        ("/denied", "denied", {"retry_path": "/admin/login"}),
    ):
        responses[path] = (
            "text/html",
            render_to_string(f"stewardship/{template}.html", {**context, **extra}),
        )
    for step, form_type in FORMS.items():
        if step == "branding":
            continue
        responses["/setup-" + step] = (
            "text/html",
            render_to_string(
                "stewardship/setup-step.html",
                context
                | {
                    "draft": setup_draft,
                    "form": form_type(),
                    "step": step,
                    "step_label": STEPS[step],
                },
            ),
        )
    for path, template, extra in (
        (
            "/export-cleanup-conflict",
            "export-cleanup-error",
            {"task_id": uuid4(), "conflict": True},
        ),
        (
            "/export-cleanup-invalid",
            "export-cleanup-error",
            {"task_id": uuid4(), "conflict": False},
        ),
        (
            "/export-cleanup-stale",
            "background-task",
            {
                "task": {
                    "id": uuid4(),
                    "type": "report_export_cleanup",
                    "state": "failed",
                    "created_at": NOW.isoformat(),
                    "attempt": 5,
                    "retry_sequence": 0,
                    "progress": {"phase": "queued", "current": 0, "total": 0},
                },
                "work": {"events": [], "latest_run_id": str(uuid4())},
            },
        ),
        (
            "/export-cleanup-task",
            "background-task",
            {
                "task": {
                    "id": uuid4(),
                    "type": "report_export_cleanup",
                    "state": "failed",
                    "created_at": NOW.isoformat(),
                    "attempt": 5,
                    "retry_sequence": 0,
                    "progress": {"phase": "queued", "current": 0, "total": 0},
                },
                "work": {"events": []},
                "export_cleanup_retry_key": str(uuid4()),
            },
        ),
        (
            "/background-task",
            "background-task",
            {
                "task": {
                    "id": uuid4(),
                    "type": "source_refresh",
                    "state": "running",
                    "active": True,
                    "created_at": NOW.isoformat(),
                    "attempt": 1,
                    "retry_sequence": 0,
                    "progress": {
                        "phase": "fetching",
                        "current": 1000,
                        "total": 4000,
                        "display": Percentage(1000, 4000),
                    },
                },
                "work": {
                    "events": [
                        {
                            "version": 2,
                            "at": NOW.isoformat(),
                            "action": "progress",
                            "state": "running",
                            "progress": {
                                "phase": "fetching",
                                "current": 1000,
                                "total": 4000,
                                "display": Percentage(1000, 4000),
                            },
                        }
                    ]
                },
            },
        ),
        (
            "/presence",
            "presence",
            {
                "presence": {
                    "count": 1,
                    "as_of": NOW,
                    "sessions": [
                        {
                            "name": "Sample Family",
                            "duid": 12345,
                            "started_at": NOW,
                            "last_activity_at": NOW,
                            "presence_at": NOW,
                            "section": "welcome",
                        }
                    ],
                }
            },
        ),
        (
            "/share-settings",
            "share-settings",
            {
                "campaign": {
                    "pk": uuid4(),
                    "active_configuration": {"name": "Sample campaign"},
                },
                "base_digest": "a" * 64,
                "formset": ShareOptions(
                    prefix="options", previous=default_share_options()
                ),
            },
        ),
        (
            "/share-preview",
            "share-preview",
            {
                "campaign": {"pk": uuid4()},
                "preview": "synthetic-signed-intent",
                "before": [],
                "after": default_share_options(),
            },
        ),
        (
            "/campaign-settings",
            "campaign-settings",
            {
                "editable": True,
                "form": CampaignForm(
                    initial={
                        "name": "Sample campaign",
                        "timezone": "America/New_York",
                        "start_date": "2026-10-01",
                        "end_date": "2026-10-31",
                        "census": True,
                        "base_digest": "a" * 64,
                    },
                    ministries=[("4", "Community outreach")],
                    funds=[("9", "Offertory")],
                ),
            },
        ),
        (
            "/campaign-preview",
            "campaign-preview",
            {
                "creating": True,
                "preview": "synthetic-signed-intent",
                "changes": [
                    {
                        "label": "Campaign timezone",
                        "before": None,
                        "after": "America/New_York",
                    }
                ],
            },
        ),
        ("/ministries", "ministries", {"ministries": [ministry], "state": "all"}),
        (
            "/ministry-preview",
            "ministry-preview",
            {
                "ministry": ministry,
                "new_active": False,
                "preview": "synthetic-signed-intent",
                "seeded_count": 2,
                "manual_count": 1,
            },
        ),
        (
            "/parish-settings",
            "parish-settings",
            {
                "configuration": {
                    "mode": "testing",
                    "testing_recipient": "testing@example.org",
                },
                "form": ParishForm(
                    initial={
                        "name": "Sample Parish",
                        "website": "https://example.org",
                        "timezone": "America/New_York",
                        "phone": "+12125551234",
                        "base_digest": "a" * 64,
                    }
                ),
            },
        ),
        (
            "/parish-preview",
            "parish-preview",
            {
                "changes": [
                    {
                        "label": "Parish timezone",
                        "before": "America/New_York",
                        "after": "America/Los_Angeles",
                    }
                ],
                "timezone_changed": True,
                "preview": "synthetic-signed-intent",
            },
        ),
        (
            "/configuration-request",
            "configuration-request",
            {
                "receipt": {"state": "staged", "request_id": uuid4()},
            },
        ),
        (
            "/background",
            "background",
            {
                "work": {
                    "counts": {
                        "active": 1,
                        "queued": 0,
                        "retry_wait": 0,
                        "abandoned": 0,
                    },
                    "tasks": [
                        {
                            "id": str(uuid4()),
                            "type": "source_refresh",
                            "state": "running",
                            "heartbeat_at": NOW.isoformat(),
                            "progress": {
                                "phase": "fetching",
                                "current": 1000,
                                "total": 3000,
                                "display": Percentage(1000, 3000),
                            },
                        }
                    ],
                },
                "states": ("nonterminal", "all", "succeeded", "failed"),
                "selected_state": "nonterminal",
            },
        ),
    ):
        responses[path] = (
            "text/html",
            render_to_string(
                f"stewardship/{template}.html",
                context | {"admin_chrome": admin} | extra,
            ),
        )
    for path, template, extra in delivery_components(NOW):
        responses[path] = (
            "text/html",
            render_to_string(
                f"stewardship/{template}.html",
                context | {"admin_chrome": admin | {"delivery_unknown": 1}} | extra,
            ),
        )
    responses.update(digest_components(context, admin))
    responses.update(report_components(context, admin))
    responses.update(information_components(context, admin))
    responses.update(directory_components(context, admin))
    responses.update(ministry_components(context, admin))
    responses.update(weekly_components(context, admin))
    responses.update(go_live_components(context, admin))
    responses.update(pause_components(context, admin))
    for filename, kind in (
        ("ui-v1.css", "text/css"),
        ("ui-v1.js", "application/javascript"),
        ("family-v1.js", "application/javascript"),
        ("digest-v1.js", "application/javascript"),
        ("report-v1.js", "application/javascript"),
        ("information-export-v1.js", "application/javascript"),
        ("digest-v1.css", "text/css"),
    ):
        asset = f"stewardship/{filename}"
        located = finders.find(asset)
        assert located is not None, f"Required component asset is missing: {asset}"
        responses[f"/static/{asset}"] = (kind, Path(located).read_text())

    logo = BytesIO()
    Image.new("RGB", (1024, 512), "blue").save(logo, format="PNG")
    for prefix in ("/branding/", "/admin/configuration/branding/assets/"):
        responses[f"{prefix}{branding_asset['pk']}.png"] = (
            "image/png",
            logo.getvalue(),
        )

    class Handler(BaseHTTPRequestHandler):
        """Suppress raw request logging; unknown routes are intentionally empty."""

        def do_GET(self):
            """Serve only exact pre-rendered component fixtures with actual CSP."""
            kind, body = responses.get(self.path, ("text/plain", ""))
            self.send_response(200 if self.path in responses else 404)
            self.send_header(
                "Content-Type",
                kind if kind == "image/png" else kind + "; charset=utf-8",
            )
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else body.encode())

        def log_message(self, *args):
            """Fixture HTTP traffic must not generate private request diagnostics."""

        def do_POST(self):
            """Fixtures never issue external redirects, even to synthetic identities."""
            self.send_response(405)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def axe_source():
    """Pinned test-only accessibility scanner, not a browser-delivered dependency."""
    path = Path(__file__).parent / "node_modules/axe-core/axe.min.js"
    assert path.is_file(), "Run npm ci --prefix tests/stewardship/browser."
    return path.read_text()
