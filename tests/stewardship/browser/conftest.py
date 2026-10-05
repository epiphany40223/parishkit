"""Serve actual shared templates/assets locally; never contact a real provider."""

import os
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from PIL import Image

from parishkit.stewardship.accounts import setup_help
from parishkit.stewardship.accounts import setup_progress_views as progress_views
from parishkit.stewardship.accounts.backup_key import KeyStatus
from parishkit.stewardship.accounts.branding_views import LogoForm
from parishkit.stewardship.accounts.campaign_forms import CampaignForm
from parishkit.stewardship.accounts.campaign_mail_views import CampaignMailForm
from parishkit.stewardship.accounts.code_reports import CODE_SORTING
from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.accounts.integration_credentials import (
    CredentialSummary,
)
from parishkit.stewardship.accounts.integration_forms import (
    InlineCredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.accounts.ministry_views import CATALOG_SORTING
from parishkit.stewardship.accounts.parish_views import ParishForm
from parishkit.stewardship.accounts.presence import PRESENCE_SORTING
from parishkit.stewardship.accounts.schedule_changes import describe as _describe
from parishkit.stewardship.accounts.schedule_forms import Schedules, ScheduleWindow
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
from parishkit.stewardship.accounts.setup_wizard import build as setup_wizard
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.jobs.views import EVENT_SORTING, TASK_SORTING
from parishkit.stewardship.reports.chart_assets import VENDORED
from parishkit.stewardship.source.refresh_status import FullRefreshStatus
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.security import CSP
from parishkit.stewardship.web.tables import paginate, window_table

from ..campaign_factory import campaign, financial, schedule
from ..content_factory import content
from ..test_setup_final_steps import wizard as final_wizard
from .automation_components import POSTS as AUTOMATION_POSTS
from .automation_components import components as automation_components
from .chart_components import components as chart_components
from .delivery_components import components as delivery_components
from .digest_components import components as digest_components
from .directory_components import components as directory_components
from .family_timeline_components import components as family_timeline_components
from .financial_components import components as financial_components
from .followup_components import POSTS as FOLLOWUP_POSTS
from .followup_components import components as followup_components
from .go_live_components import components as go_live_components
from .hosted_file_components import IMAGE_TOKEN
from .hosted_file_components import components as hosted_file_components
from .in_place_components import FORM as IN_PLACE_FORM
from .in_place_components import POSTS as IN_PLACE_POSTS
from .in_place_components import SLOW as IN_PLACE_SLOW
from .in_place_components import components as in_place_components
from .information_components import POSTS as INFORMATION_POSTS
from .information_components import components as information_components
from .log_components import components as log_components
from .menu_components import components as menu_components
from .ministry_components import components as ministry_components
from .pause_components import components as pause_components
from .report_components import components as report_components
from .response_dashboard_components import components as dashboard_components
from .response_list_components import components as response_list_components
from .security_components import components as security_components
from .send_history_components import components as send_history_components
from .send_progress_components import components as send_progress_components
from .talent_components import components as talent_components
from .user_components import components as user_components
from .weekly_components import components as weekly_components

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
# One running task shared by the background page's work summary and table.
BACKGROUND_TASK = {
    "id": str(uuid4()),
    "type": "source_refresh",
    "name": "ParishSoft data refresh",
    "state": "running",
    "heartbeat_at": NOW.isoformat(),
    "created_at": NOW.isoformat(),
    "progress": {
        "phase": "fetching",
        "current": 1000,
        "total": 3000,
        "display": Percentage(1000, 3000),
    },
}
# One history row of the background-task page, and its shared-table model.
HISTORY_EVENT = {
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


def _history(events):
    """The sorted, counted history table task_page builds for these events."""
    return window_table(
        PageWindow(1, 20),
        events,
        False,
        total=(len(events), False),
        sorting=EVENT_SORTING,
        sort=EVENT_SORTING.default,
    )


def finishing_context(**status):
    """The "Finishing setup" page context, built by the real step mapping."""
    from parishkit.stewardship.accounts.setup_finishing import finishing

    now = datetime.now(UTC)
    status = {
        "attempt": SimpleNamespace(state="frozen", attempt_id=uuid4()),
        "failure_code": "",
        "confirmed_at": now - timedelta(minutes=4),
        "authenticated_at": now - timedelta(minutes=1),
        "server_now": now,
        "targets": ["parishsoft", "google_workspace"],
        "source": None,
    } | status
    return (
        status
        | finishing(status)
        | {"next": "/admin/setup/cancel", "status_url": "/setup-status.json"}
    )


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


def no_script_context(browser_engine, **options):
    """A JavaScript-off browser context that loads Admin pages past the gate.

    The Admin portal requires JavaScript (#565): with script off, an Admin page
    shows only the "needs JavaScript" panel. Some older no-script tests still
    check the native form and link markup that the scripts submit too, such as
    POST bodies that keep identifying values out of URLs. Until each one is
    retired or rewritten when its page changes (#565), it uses this context,
    which removes the gate's class from every page the browser loads. The gate
    itself is tested with a plain context in test_admin_javascript_gate.py.
    """
    context = browser_engine.new_context(java_script_enabled=False, **options)

    def ungate(route):
        """Serve each HTML page without the class that hides it.

        Only text/html documents are rewritten; every other response,
        including a redirect or a download, passes through unchanged.
        """
        if route.request.resource_type != "document":
            route.fallback()
            return
        response = route.fetch(max_redirects=0)
        if not response.headers.get("content-type", "").startswith("text/html"):
            route.fulfill(response=response)
            return
        body = response.text().replace(' class="js-required"', "", 1)
        route.fulfill(response=response, body=body)

    context.route("**/*", ungate)
    return context


def load_collections(done=0, *, finished=None, expected=None):
    """Decoded download collections with the first ``done`` of them finished."""
    return [
        {
            "key": key,
            "count": 1234 if index < done else 0,
            "done": index < done,
            "finished": finished if key == "ministry_roster" else None,
            "expected": expected if key == "ministry_roster" else None,
        }
        for index, key in enumerate(progress_views.COLLECTIONS)
    ]


def progress_page(progress, wizard):
    """The source-load progress page context, built by the view's own helpers."""
    status_key = progress_views.summary(progress)
    return {
        "progress": progress,
        "wizard": wizard,
        "summaries": progress_views.SUMMARIES,
        "status_key": status_key,
        "phases": progress_views.phases(progress, status_key),
        "phase_status": progress_views.PHASE_STATUS,
        "collections": progress_views.collections(progress),
        "collection_text": progress_views.COLLECTION_TEXT,
    }


def invalid_schedules(owner, emails):
    """A validated formset whose only row reports a weekday on an invitation."""
    schedules = Schedules(
        {
            "schedules-TOTAL_FORMS": "1",
            "schedules-INITIAL_FORMS": "0",
            "schedules-0-kind": "initial",
            "schedules-0-date": "2054-10-01",
            "schedules-0-time": "09:00:00",
            "schedules-0-weekday": "0",
            "schedules-0-template_version": emails[0]["id"],
        },
        prefix="schedules",
        previous=[],
        templates=emails,
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert not schedules.is_valid()
    return schedules


@pytest.fixture(scope="module")
def component_origin():
    """An exact response allowlist avoids exposing source files through the server."""
    mail_campaign = campaign()
    mail = schedule(mail_campaign["id"])
    financial_campaign = campaign(modules=["financial"], financial=financial())
    # One saved email of every schedulable type, so each mail type's row can
    # offer (only) its own emails.
    mail_emails = [
        content(mail_campaign["id"], kind="email", slot=kind, subject=f"{kind} mail")
        for kind in ("initial", "reminder", "daily_digest", "weekly_digest")
    ]
    context = {
        "server_now": NOW,
        "deadline": NOW + timedelta(hours=1),
        "absolute_deadline": NOW + timedelta(hours=4),
        "csrf_token": "a" * 64,
    }
    admin = {
        "admin": True,
        "parish_name": "Sample Parish",
        "home_url": "/home",
        "home_current": False,
        "sections": [
            {
                "key": "parish",
                "label": "Parish data",
                "current": True,
                "items": [
                    {
                        "url": "/parish-settings",
                        "label": "Parish settings",
                        "current": None,
                    },
                    {
                        "url": "/ministries",
                        "label": "Ministry activity",
                        "current": "page",
                    },
                ],
            },
        ],
        "breadcrumbs": [
            {"label": "Home", "url": "/home"},
            {"label": "Parish data", "url": "/parish-settings"},
            {"label": "Ministry activity", "url": None},
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

    def ministries_table(parameters, search=""):
        """The two-row Ministry catalog table under the given sort/page choice,
        narrowed to the names containing ``search`` (a filter, #484)."""
        rows = [ministry, ministry | {"duid": 12346, "name": "Lectors"}]
        return paginate(
            [row for row in rows if search in row["name"]],
            parameters,
            carry=(("q", search), ("state", "all")),
            sorting=CATALOG_SORTING,
        )

    def background_table(window, has_next):
        """One page of the background-task table, 51 tasks in all."""
        return window_table(
            window,
            [BACKGROUND_TASK],
            has_next,
            total=(51, False),
            sorting=TASK_SORTING,
            sort=TASK_SORTING.default,
        )

    background = {
        "work": {
            "counts": {"active": 1, "queued": 0, "retry_wait": 0, "abandoned": 0},
            "tasks": [BACKGROUND_TASK],
        },
        "states": ("nonterminal", "all", "succeeded", "failed"),
        "selected_state": "nonterminal",
    }
    branding_asset = {"pk": uuid4(), "label": "large", "width": 1024, "height": 512}
    setup_draft = {
        "status": {"attempt_id": uuid4(), "state": "collecting", "version": 2},
        "sections": {},
        "idle_at": NOW + timedelta(minutes=30),
        "absolute_at": NOW + timedelta(hours=12),
        "watchdog_at": None,
    }
    # A stepper with done, current, open and blocked steps for axe/layout checks.
    wizard = setup_wizard(
        SimpleNamespace(
            status=SimpleNamespace(state="collecting", attempt_id=uuid4(), version=2),
            sections={
                "mail": {
                    "delegated_email": "stewardship@example.org",
                    "sender": "stewardship@example.org",
                    "reply_to": "office@example.org",
                }
            },
            source_task_id=None,
        ),
        "testing",
    )
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
                "summary": CredentialSummary(
                    "failed",
                    NOW,
                    "ParishSoft did not accept the new API key.",
                    uuid4(),
                ),
                "credential": InlineCredentialForm(
                    "parishsoft", initial={"intent": "synthetic-intent"}
                ),
                "full_refresh": FullRefreshStatus(NOW, NOW, True),
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
            "/credential-selection",
            "credential-selection",
            {
                "label": "ParishSoft",
                "before": "a" * 64,
                "receipt": {"pk": uuid4(), "resulting_fingerprint": "b" * 64},
                "preview": "synthetic-selection-intent",
                "selected": False,
                "changes": [
                    {"label": "Slack channel ID", "before": "C0123", "after": "C0456"}
                ],
            },
        ),
        (
            "/backup-key",
            "backup-key",
            {
                "label": "Backup encryption key",
                "current": KeyStatus("installed", "0123456789abcdef", backup_at=NOW),
                "fresh": False,
                "key_error": "This is not a backup public key.",
            },
        ),
        (
            "/backup-key-proof",
            "backup-key",
            {
                "label": "Backup encryption key",
                "current": KeyStatus("configured", "0123456789abcdef"),
                "fresh": True,
                "new_fingerprint": "fedcba9876543210",
                # A real challenge line is one unbroken 87-character word.
                "challenge": "PKBKP1:" + "A" * 80,
                "intent": "synthetic-intent",
                "code_error": "That code does not match.",
            },
        ),
        (
            "/credential-status",
            "credential-status",
            {
                "receipt": {"request_id": uuid4(), "state": "awaiting_ack"},
                "pending": True,
                # A key's status sits under its integration (#196).
                "admin_chrome": admin
                | {
                    "breadcrumbs": [
                        {"label": "Home", "url": "/home"},
                        {"label": "System", "url": "/integrations"},
                        {"label": "Integrations", "url": "/integrations"},
                        {"label": "ParishSoft", "url": "/integration-settings"},
                        {"label": "Key replacement status", "url": None},
                    ],
                    "back": {"label": "ParishSoft", "url": "/integration-settings"},
                },
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
                        "after": "2055-10-01 through 2055-10-31",
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
                "label": "Confirmation email",
                "visual": "<p>Hello Sample Family</p>",
                "placeholders": ["family_name", "parish_name"],
                # An email keeps the plain-text panel (#259) for component checks.
                "form": ContentForm(
                    kind="email",
                    slot="confirmation",
                    initial={
                        "base_digest": "a" * 64,
                        "subject": "Received",
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
                # A web-only page: no plain-text panel (#259).
                "form": SetupContentForm(
                    kind="page",
                    slot="welcome",
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
            "/setup-mail-test-step",
            "setup-mail",
            {
                "draft": setup_draft,
                "wizard": final_wizard("mail_test"),
                "tested": False,
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
            "/setup-mail-test-done",
            "setup-mail",
            {
                "draft": setup_draft,
                "wizard": final_wizard("mail_test", tests=frozenset({"mail_test"})),
                "tested": True,
                "testing_recipient": "testing@example.org",
                "pending": False,
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
            finishing_context(
                checkpoint="yaml_activated",
                prepared=True,
                credentials=[
                    {
                        "target": "parishsoft",
                        "request_id": uuid4(),
                        "request__state": "awaiting_ack",
                        "request__cleanup_reason": "",
                        "consumers": 2,
                        "acknowledged": 2,
                    }
                ],
                source={
                    "id": uuid4(),
                    "state": "running",
                    "phase": "loading",
                    "progress": Percentage(1234, 5678),
                },
            ),
        ),
        (
            "/setup-installation",
            "setup-cancel",
            finishing_context(checkpoint="validating", prepared=False, credentials=[]),
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
            # Saved emails of every mail type: the blank row shows only the
            # fields and emails of the mail type chosen in it.
            "/setup-schedules-mail",
            "setup-schedules",
            {
                "draft": setup_draft,
                "campaign_name": "Sample campaign",
                "window": SetupScheduleWindow(
                    prefix="window", previous=mail_campaign["values"]
                ),
                "schedules": Schedules(
                    prefix="schedules",
                    previous=[
                        schedule(
                            mail_campaign["id"],
                            template_version=mail_emails[0]["id"],
                            subject="initial mail",
                        )
                    ],
                    templates=mail_emails,
                    campaign_id=mail_campaign["id"],
                    campaign=mail_campaign["values"],
                ),
            },
        ),
        (
            # The owner's mistake posted without the page script: a new
            # initial invitation with a weekday. The error stays visible.
            "/setup-schedules-error",
            "setup-schedules",
            {
                "draft": setup_draft,
                "campaign_name": "Sample campaign",
                "window": SetupScheduleWindow(
                    prefix="window", previous=mail_campaign["values"]
                ),
                "schedules": invalid_schedules(mail_campaign, mail_emails),
            },
        ),
        (
            # A financial campaign: its window shows the overlap confirmation
            # only while the dates overlap the fixed financial period.
            "/setup-schedules-financial",
            "setup-schedules",
            {
                "draft": setup_draft,
                "campaign_name": "Sample campaign",
                "window": SetupScheduleWindow(
                    prefix="window", previous=financial_campaign["values"]
                ),
                "schedules": Schedules(
                    prefix="schedules",
                    previous=[],
                    templates=[],
                    campaign_id=financial_campaign["id"],
                    campaign=financial_campaign["values"],
                ),
                "templates_url": "/admin/setup/content",
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
            # A fuller sidebar than the shared fixture's single section, so the
            # group headings, dividers and indented link groups are checked
            # together.
            "/admin-navigation",
            "home",
            {
                "configuration": {"mode": "testing"},
                "admin_chrome": admin
                | {
                    "sections": [
                        {
                            "key": "campaign",
                            "label": "Campaign setup",
                            "current": False,
                            "items": [
                                {
                                    "url": "/campaign-settings",
                                    "label": "Campaign settings",
                                    "current": None,
                                },
                                {
                                    "url": "/schedules",
                                    "label": "Mail schedules",
                                    "current": None,
                                },
                            ],
                        },
                        *admin["sections"],
                        {
                            "key": "system",
                            "label": "System",
                            "current": False,
                            "items": [
                                {
                                    "url": "/background",
                                    "label": "Background work",
                                    "current": None,
                                },
                            ],
                        },
                    ]
                },
            },
        ),
        (
            "/codes",
            "codes",
            {
                "table": window_table(
                    PageWindow(2, 50),
                    [{"duid": 1234567890123456789, "code": "ABCDEFGH"}],
                    True,
                    total=(120, False),
                    sorting=CODE_SORTING,
                    sort="-duid",
                ),
            },
        ),
        (
            "/setup",
            "setup",
            {
                "draft": setup_draft,
                "wizard": wizard,
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
                    # A real parish has hundreds of Ministries, some long-named.
                    ministries=[
                        ("1", "Music ministry"),
                        (
                            "2",
                            "Parish Pastoral Council and Finance Council Joint "
                            "Subcommittee on Buildings, Grounds and Parking",
                        ),
                        *(
                            (str(number), f"Ministry {number}")
                            for number in range(3, 214)
                        ),
                    ],
                    funds=[("9", "Offertory")],
                ),
            },
        ),
        (
            "/setup-source-progress",
            "setup-source-progress",
            progress_page(
                {
                    "server_now": NOW.isoformat(),
                    "task_id": uuid4(),
                    "task_state": "running",
                    "setup_state": "loading",
                    "phase": "fetching",
                    "current": 0,
                    "total": 0,
                    "active": True,
                    "collections": load_collections(),
                    "idle_at": (NOW + timedelta(minutes=30)).isoformat(),
                    "watchdog_at": (NOW + timedelta(hours=2)).isoformat(),
                    "absolute_at": (NOW + timedelta(hours=12)).isoformat(),
                },
                wizard,
            ),
        ),
        (
            "/source-refresh",
            "source-refresh",
            {
                "refreshed_at": NOW,
                "pending": {"running": False, "waiting": False},
                "request_key": uuid4(),
            },
        ),
        (
            "/source-refresh-running",
            "source-refresh",
            {
                "refreshed_at": None,
                "pending": {"running": True, "waiting": False},
                "request_key": uuid4(),
            },
        ),
        ("/availability", "availability", {"setup": True, "admin": True}),
        ("/family-maintenance", "family-maintenance", {"message": "Back by 3 PM."}),
        ("/denied", "denied", {"retry_path": "/admin/login"}),
        ("/denied-code", "denied", {"retry_path": "/", "kind": "code"}),
        ("/denied-link", "denied", {"retry_path": "/", "kind": "link"}),
        (
            "/denied-unavailable",
            "denied",
            {"retry_path": "/", "kind": "unavailable"},
        ),
    ):
        responses[path] = (
            "text/html",
            render_to_string(f"stewardship/{template}.html", {**context, **extra}),
        )
    # The wizard's Parish step after a refused Next (#592): a website with a
    # query, a rule only the server checks, marked at its field.
    responses["/setup-parish-invalid"] = (
        "text/html",
        render_to_string(
            "stewardship/setup-step.html",
            context
            | {
                "draft": setup_draft,
                "wizard": wizard,
                "form": FORMS["parish"](
                    data={
                        "name": "Sample Parish",
                        "website": "https://example.org/?campaign=1",
                        "timezone": "America/New_York",
                        "phone": "+12125551234",
                    }
                ),
                "step": "parish",
                "step_label": STEPS["parish"],
            },
        ),
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
                    "wizard": wizard,
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
                "history": _history([]),
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
                "history": _history([]),
                "export_cleanup_retry_key": str(uuid4()),
            },
        ),
        (
            "/background-task",
            "background-task",
            {
                # What task_page adds for a refresh run (jobs/task_wording.py).
                "is_refresh": True,
                "refresh_label": "Full refresh",
                "phase_text": "Downloading from ParishSoft",
                "retry_text": "An earlier attempt stopped unexpectedly (for "
                "example, the server restarted), so this work started again "
                "automatically.",
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
                "work": {"events": [HISTORY_EVENT]},
                "history": _history([HISTORY_EVENT]),
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
                },
                "table": window_table(
                    PageWindow(1, 50),
                    [
                        {
                            "name": "Sample Family",
                            "duid": 12345,
                            "started_at": NOW,
                            "last_activity_at": NOW,
                            "presence_at": NOW,
                            "section": "welcome",
                        }
                    ],
                    False,
                    total=(1, False),
                    sorting=PRESENCE_SORTING,
                    sort="-heartbeat",
                ),
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
                # The current Testing draft: New campaign is retired, so the
                # page always edits an existing campaign (rule 10).
                "campaign": {
                    "pk": uuid4(),
                    "state": "draft",
                    "active_configuration": {"name": "Sample campaign"},
                },
                "editable": True,
                # The Admin view adds the shared field help the same way.
                "form": setup_help.apply(
                    CampaignForm(
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
                    setup_help.ADMIN_CAMPAIGN,
                    replace=True,
                ),
            },
        ),
        (
            "/campaign-preview",
            "campaign-preview",
            {
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
        (
            "/ministries",
            "ministries",
            {"table": ministries_table({}), "query": "", "state": "all"},
        ),
        (
            "/ministry-preview",
            "ministry-preview",
            {
                "changing": [ministry],
                "unchanged": [],
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
        # The same page after a refused save (#592): Django marks the blank
        # name (a rule the browser can check too) and the plain-http giving
        # address (a rule only the server checks) at their fields.
        (
            "/parish-settings-invalid",
            "parish-settings",
            {
                "configuration": {
                    "mode": "testing",
                    "testing_recipient": "testing@example.org",
                },
                "form": ParishForm(
                    data={
                        "name": "",
                        "website": "https://example.org",
                        "timezone": "America/New_York",
                        "phone": "+12125551234",
                        "online_giving_url": "http://giving.example.org",
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
                # The last step of a change confirmed on Parish settings.
                "admin_chrome": admin
                | {
                    "breadcrumbs": [
                        {"label": "Home", "url": "/home"},
                        {"label": "Parish data", "url": "/parish-settings"},
                        {"label": "Parish settings", "url": "/parish-settings"},
                        {"label": "Configuration change", "url": None},
                    ],
                    "flow_steps": [
                        {"label": "Make changes", "state": "done"},
                        {"label": "Review", "state": "done"},
                        {"label": "Apply", "state": "current"},
                    ],
                    "back": {"label": "Parish settings", "url": "/parish-settings"},
                },
            },
        ),
        (
            "/go-live-steps",
            "production-progress",
            {
                "campaign": {
                    "pk": uuid4(),
                    "state": "scheduled",
                    "active_configuration": {"name": "Sample campaign"},
                },
                "receipt": {"created_at": NOW},
                "demand": None,
                # The last of the five go-live steps, the longest indicator.
                "admin_chrome": admin
                | {
                    "breadcrumbs": [
                        {"label": "Home", "url": "/home"},
                        {"label": "Campaign setup", "url": "/campaign-settings"},
                        {"label": "Production activation", "url": None},
                    ],
                    "flow_steps": [
                        {"label": "Check readiness", "state": "done"},
                        {"label": "Testing cleanup", "state": "done"},
                        {"label": "Family links", "state": "done"},
                        {"label": "Confirm Production", "state": "done"},
                        {"label": "Activation", "state": "current"},
                    ],
                },
            },
        ),
        (
            "/background",
            "background",
            background | {"table": background_table(PageWindow(1, 50), True)},
        ),
        # The pages the shared tables' own controls lead to, at the exact
        # query strings those controls carry (#478): the Ministry table
        # sorted by name the other way, at 25 rows per page (the navigator's
        # own form, then each direction of the Ministry heading), and page 2
        # of the background tasks.
        *(
            (
                "/ministries?" + query,
                "ministries",
                {"table": ministries_table(chosen), "query": "", "state": "all"},
            )
            for query, chosen in (
                (ministries_table({}).heading_query("name"), {"sort": "-name"}),
                (ministries_table({"size": "25"}).query(1), {"size": "25"}),
                (
                    ministries_table({"size": "25"}).heading_query("name"),
                    {"size": "25", "sort": "-name"},
                ),
                (
                    ministries_table({"size": "25", "sort": "-name"}).heading_query(
                        "name"
                    ),
                    {"size": "25", "sort": "name"},
                ),
            )
        ),
        # The Ministry filter form's own GET query (#484): a search that keeps
        # one Ministry and one that matches none.
        *(
            (
                f"/ministries?q={search}&state=all&size=50&sort=name",
                "ministries",
                {
                    "table": ministries_table({}, search),
                    "query": search,
                    "state": "all",
                },
            )
            for search in ("Lectors", "Nothing")
        ),
        (
            "/background?" + background_table(PageWindow(1, 50), True).next_query,
            "background",
            background | {"table": background_table(PageWindow(2, 50), False)},
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
    responses.update(log_components(context, admin))
    responses.update(directory_components(context, admin))
    responses.update(ministry_components(context, admin))
    responses.update(followup_components(context, admin))
    responses.update(financial_components(context, admin))
    responses.update(weekly_components(context, admin))
    responses.update(chart_components(context, admin))
    responses.update(dashboard_components(context, admin))
    responses.update(response_list_components(context, admin))
    responses.update(family_timeline_components(context, admin))
    responses.update(user_components(context, admin))
    responses.update(security_components(context, admin))
    responses.update(go_live_components(context, admin))
    responses.update(pause_components(context, admin))
    responses.update(send_progress_components(context, admin))
    responses.update(send_history_components(context, admin))
    responses.update(hosted_file_components(context, admin))
    responses.update(talent_components(context, admin))
    responses.update(in_place_components(context, admin))
    responses.update(menu_components(context, admin))
    responses.update(automation_components(context, admin))
    # The in-place form page's POST answers (#519, #562): refusals answer
    # with the page the view would render (400 with the summary in the
    # region, 200 with it outside, or a 400 denial page without the region),
    # and "plain" answers 200 with a page that lacks the form's region,
    # without a redirect.
    posts = (
        IN_PLACE_POSTS
        | AUTOMATION_POSTS
        | FOLLOWUP_POSTS
        | INFORMATION_POSTS
        | {
            f"{IN_PLACE_FORM}/refuse": (400, None, responses["/in-place-refused"][1]),
            f"{IN_PLACE_FORM}/invalid": (200, None, responses["/in-place-invalid"][1]),
            f"{IN_PLACE_FORM}/denied": (400, None, responses["/in-place-denied"][1]),
            f"{IN_PLACE_FORM}/plain": (200, None, responses["/in-place-plain"][1]),
        }
    )
    for filename, kind in (
        ("ui-v1.css", "text/css"),
        ("ui-v1.js", "application/javascript"),
        ("admin-gate-v1.js", "application/javascript"),
        ("admin-menu-v1.js", "application/javascript"),
        ("admin-menu-state-v1.js", "application/javascript"),
        ("date-format-v1.js", "application/javascript"),
        ("family-v1.js", "application/javascript"),
        ("family-support-v1.js", "application/javascript"),
        ("family-login-v1.js", "application/javascript"),
        ("digest-v1.js", "application/javascript"),
        ("report-v1.js", "application/javascript"),
        ("information-export-v1.js", "application/javascript"),
        ("users-v1.js", "application/javascript"),
        ("phone-v1.js", "application/javascript"),
        ("live-status-v1.js", "application/javascript"),
        ("session-v1.js", "application/javascript"),
        ("local-sign-in-v1.js", "application/javascript"),
        ("session-v1.css", "text/css"),
        ("digest-v1.css", "text/css"),
        ("setup-v1.css", "text/css"),
        ("select-arrow-v1.svg", "image/svg+xml"),
        ("chart-v1.js", "application/javascript"),
        ("chart-v1.css", "text/css"),
    ):
        asset = f"stewardship/{filename}"
        located = finders.find(asset)
        assert located is not None, f"Required component asset is missing: {asset}"
        responses[f"/static/{asset}"] = (kind, Path(located).read_text())
    # The chart engine's vendored bundles (#477), served as the page loads them.
    for asset in VENDORED:
        responses[f"/static/{asset.static_name}"] = (
            "application/javascript",
            asset.path.read_text(),
        )

    logo = BytesIO()
    Image.new("RGB", (1024, 512), "blue").save(logo, format="PNG")
    for prefix in ("/branding/", "/admin/configuration/branding/assets/"):
        responses[f"{prefix}{branding_asset['pk']}.png"] = (
            "image/png",
            logo.getvalue(),
        )
    # A hosted image (#346) served from the same origin, like /files/<token>.
    responses[f"/files/{IMAGE_TOKEN}"] = ("image/png", logo.getvalue())
    # Every fixture page with an "About this page" panel (#227), so a browser
    # test can check the panel's placement on all of them, including fixtures
    # added later.
    responses["/about-page-index"] = (
        "text/plain",
        "\n".join(
            sorted(
                path
                for path, (kind, body) in responses.items()
                if kind == "text/html"
                and isinstance(body, str)
                and "data-about-page=" in body
            )
        ),
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
            """Fixtures never issue external redirects, even to synthetic identities.

            One path answers as an expired session does, with a redirect to
            the sign-in page, so the in-place table tests (#478) can see a
            real redirect, which Playwright cannot fulfil from a route on
            every engine. The in-place form page's paths (#519) answer as
            ``in_place_components.POSTS`` lists, and the two follow-up Save
            forms' update paths as ``followup_components.POSTS`` and
            ``information_components.POSTS`` list.
            """
            # Read the body before answering: closing the connection with
            # an unread request body can reset it before the answer arrives.
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/redirect-to-login":
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            if self.path in posts:
                status, location, body = posts[self.path]
                if self.path in IN_PLACE_SLOW:
                    time.sleep(1.5)
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", CSP)
                self.end_headers()
                self.wfile.write(body.encode())
                return
            self.send_response(405)
            self.end_headers()

    class Server(ThreadingHTTPServer):
        """A listen backlog deep enough for a page's parallel asset requests.

        Each HTTP/1.0 response closes its connection, so a page opens one
        connection per asset at once. With the default backlog of 5 the
        kernel reset the overflow now and then (net::ERR_CONNECTION_RESET), a
        script such as information-export-v1.js never ran, and tests that
        depend on it failed intermittently.
        """

        request_queue_size = 128

    server = Server(("127.0.0.1", 0), Handler)
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
