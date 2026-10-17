"""Explanatory sign-in hints never mention Google in LOCAL (#649).

LOCAL has no Google sign-in, so the setup wizard and settings pages must not
tell a developer to confirm it's them with Google there. Each page keeps its
Production sentence unchanged in every other profile. Since #1547 no page
asks for a recent sign-in, so none draws a step-up hint at all.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Value
from uuid import UUID

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.stewardship.accounts import family_maintenance_views
from parishkit.stewardship.accounts.integration_forms import (
    InlineCredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.accounts.leader_import import LEADER_SORTING
from parishkit.stewardship.accounts.leader_import_views import PREFIX as LEADER_PREFIX
from parishkit.stewardship.accounts.setup_credential_views import SetupCredentialForm
from parishkit.stewardship.accounts.setup_forms import SetupAccessForm
from parishkit.stewardship.accounts.user_reads import USER_SORTING
from parishkit.stewardship.accounts.user_views import PREFIX
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.web.tables import paginate

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
CONTEXT = {
    "server_now": NOW,
    "deadline": NOW + timedelta(hours=1),
    "absolute_deadline": NOW + timedelta(hours=4),
    "csrf_token": "a" * 64,
}
DRAFT = Value(status=Value(version=1, attempt_id=UUID(int=649), state="open"))


def pages():
    """Page name -> (template, context, Production sentences, LOCAL sentences).

    A function, not a constant: the real forms (whose field help is part of
    the page) are built at test time, after Django is configured.
    """
    return {
        "setup-access": (
            "stewardship/setup-step.html",
            {
                "draft": DRAFT,
                "step": "access",
                "step_label": "Who may sign in",
                "form": SetupAccessForm(),
            },
            (
                "Who besides you may sign in to this administration site, and"
                " with which role. Everyone signs in with a Google account. Each"
                " address you list, under any of the three roles, becomes one user"
                " with the highest role it is listed under; no one else is let in."
                " You can name each user and change their role later on the Users"
                " page; these lists take effect only when setup finishes.",
            ),
            (
                "Who besides you may sign in to this administration site, and"
                " with which role. Each address you list, under any of the three"
                " roles, becomes one user with the highest role it is listed under;"
                " no one else is let in. You can name each user and change their"
                " role later on the Users page; these lists take effect only when"
                " setup finishes.",
            ),
        ),
        "users": (
            "stewardship/users.html",
            {
                "table": paginate([], {}, prefix=PREFIX, sorting=USER_SORTING),
                "base_digest": "0" * 64,
                "refresh_url": "/admin/users/",
                "new_url": "/admin/users/new/",
                "delete_url": "/admin/users/deletion/",
                # The ParishSoft Ministry leaders table below it (#952 slice 6).
                "leaders": paginate(
                    [], {}, prefix=LEADER_PREFIX, sorting=LEADER_SORTING
                ),
                "leaders_unreachable": 0,
                "import_url": "/admin/users/leaders/import/",
            },
            ("Everyone who may sign in to the Admin portal with Google.",),
            ("Everyone who may sign in to the Admin portal.",),
        ),
    }


NAMES = sorted(pages())
# Google sign-in phrases that are wrong in LOCAL, matched without case.
# Provider text such as "Google Workspace", "Gmail" or "Google Drive" stays:
# it is true in every profile and contains none of these.
SIGN_IN_PHRASES = ("with google", "google sign-in", "google account")
# A page whose later text is true in every profile is checked only before
# that marker. None needs one since the Users page (#952) dropped its
# hosted-domain rules.
CHECKED_BEFORE = {}


def flatten(html):
    """Collapse whitespace so sentences split across template lines match."""
    return " ".join(html.split())


def render(name, local):
    """Render one page in LOCAL (with its context flag) or in another profile."""
    template, values, _, _ = pages()[name]
    page = CONTEXT | values
    if local:
        page["local_environment"] = True
    return flatten(render_to_string(template, page))


def assert_no_google_sign_in(text):
    """No Google sign-in phrase, in any letter case."""
    lowered = text.lower()
    for phrase in SIGN_IN_PHRASES:
        assert phrase not in lowered


@pytest.mark.parametrize("name", NAMES)
def test_production_wording_is_unchanged(name):
    """Outside LOCAL each page keeps its Google sentences."""
    _, _, production, local = pages()[name]
    page = render(name, local=False)
    for sentence in production:
        assert sentence in page
    for sentence in local:
        assert sentence not in page


@pytest.mark.parametrize("local", [False, True])
def test_staff_address_help_is_neutral_everywhere(local):
    """The access step's Staff-address help names email addresses, not Google."""
    page = render("setup-access", local)
    assert "Email addresses of the people who get Staff access, one per line," in page
    assert "Individual Google account addresses" not in page


@pytest.mark.parametrize("name", NAMES)
def test_local_hints_never_mention_google_sign_in(name):
    """In LOCAL each page shows its neutral sentences and no Google sign-in."""
    _, _, production, local = pages()[name]
    page = render(name, local=True)
    for sentence in local:
        assert sentence in page
    for sentence in production:
        assert sentence not in page
    marker = CHECKED_BEFORE.get(name)
    if marker:
        assert marker in page
        page = page.split(marker, 1)[0]
    assert_no_google_sign_in(page)


def test_the_view_renders_local_wording_through_the_request(monkeypatch, settings):
    """The real view's render picks up LOCAL from the context processor.

    A template-only test would still pass if a view switched to
    ``render_to_string`` without the request, losing the LOCAL flag; this
    calls the Family portal availability view through a request instead.
    """
    views = family_maintenance_views
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = DeploymentProfile.LOCAL.value
    monkeypatch.setattr(views, "runtime", lambda: None)
    monkeypatch.setattr(views, "principal", lambda request, service: None)
    monkeypatch.setattr(
        views.family_maintenance,
        "current_state",
        lambda cached: Value(closed=False, actor_id=None, message=""),
    )
    monkeypatch.setattr(
        views,
        "SystemConfiguration",
        Value(
            objects=Value(values_list=lambda *fields: Value(first=lambda: (None, None)))
        ),
    )
    request = RequestFactory().get("/admin/mail/family-portal/")
    request.portal_session = None
    response = views.family_portal(request)
    page = flatten(response.content.decode())
    assert response.status_code == 200
    assert "This is the local laptop environment" in page
    assert_no_google_sign_in(page)


# The pages that drew a "needs a sign-in from the last five minutes" (or
# hour) hint until #1547, with what each needs to render.
FORMER_STEP_UP_PAGES = {
    "setup": ("stewardship/setup.html", {"draft": None}),
    "setup-credential": (
        "stewardship/setup-credential.html",
        {
            "draft": DRAFT,
            "target": "parishsoft",
            "label": "ParishSoft",
            "form": SetupCredentialForm("parishsoft"),
        },
    ),
    "integration-settings": (
        "stewardship/integration-settings.html",
        {
            "target": "slack",
            "label": "Slack",
            "configured": True,
            "form": IntegrationForm("slack"),
            "credential": InlineCredentialForm("slack"),
            "status_url": "/admin/integrations/slack/status",
        },
    ),
    "credential-selection": (
        "stewardship/credential-selection.html",
        {
            "label": "Slack",
            "receipt": Value(pk=UUID(int=6490), resulting_fingerprint="abc123"),
            "preview": "p",
        },
    ),
    "family-portal-maintenance": (
        "stewardship/family-portal-maintenance.html",
        {"state": Value(closed=False), "message_limit": 500},
    ),
}
# Phrases of the retired step-up hint, matched without case.
STEP_UP_PHRASES = ("for security,", "sign-in from the last", "confirm it's you")


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("name", sorted(FORMER_STEP_UP_PAGES))
def test_no_page_asks_for_a_recent_sign_in(name, local):
    """A sign-in hours old draws no step-up hint, and the page's rules stay."""
    template, values = FORMER_STEP_UP_PAGES[name]
    page = CONTEXT | values | {"local_environment": local}
    request = RequestFactory().get("/admin/")
    request.portal_session = Value(authenticated_at=NOW - timedelta(hours=3))
    html = flatten(render_to_string(template, page, request=request)).lower()
    for phrase in STEP_UP_PHRASES:
        assert phrase not in html
    if name == "setup-credential":
        assert "the slack token needs slack turned on first." in html
    if name == "family-portal-maintenance":
        assert "every change is recorded in the audit log." in html
