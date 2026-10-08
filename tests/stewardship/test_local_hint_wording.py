"""Explanatory sign-in hints never mention Google in LOCAL (#649).

LOCAL has no Google sign-in, so the setup wizard and settings pages must not
tell a developer to confirm it's them with Google there. Each page keeps its
Production sentence unchanged in every other profile. The step-up prompts
themselves are covered by #619's tests; these are the hints around forms.
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
from parishkit.stewardship.accounts.setup_credential_views import SetupCredentialForm
from parishkit.stewardship.accounts.setup_forms import SetupAccessForm
from parishkit.stewardship.accounts.user_rows import ROLE_LABELS
from parishkit.stewardship.accounts.user_rules import ROLE_ORDER
from parishkit.stewardship.accounts.user_views import TABLES, user_tables
from parishkit.stewardship.deployment import DeploymentProfile

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
        "setup": (
            "stewardship/setup.html",
            {"draft": None},
            (
                "Saving a credential needs a Google sign-in from the last five"
                " minutes. If yours is older, the page asks you to confirm it's"
                " you with Google; your setup is kept.",
            ),
            (
                "Saving a credential needs a sign-in from the last five minutes."
                " If yours is older, the page asks you to confirm your sign-in;"
                " your setup is kept.",
            ),
        ),
        "setup-credential": (
            "stewardship/setup-credential.html",
            {
                "draft": DRAFT,
                "target": "parishsoft",
                "label": "ParishSoft",
                "form": SetupCredentialForm("parishsoft"),
            },
            (
                "For security, saving a credential needs a Google sign-in from"
                " the last five minutes; if yours is older, you will be asked to"
                " confirm it's you with Google, which keeps this setup.",
            ),
            (
                "For security, saving a credential needs a sign-in from the last"
                " five minutes; if yours is older, you will be asked to confirm"
                " your sign-in, which keeps this setup.",
            ),
        ),
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
                " with which role. Everyone signs in with a Google account. These"
                " rules decide which accounts are let in. You can change them"
                " later, and they take effect only when setup finishes.",
            ),
            (
                "Who besides you may sign in to this administration site, and"
                " with which role. These rules decide which accounts are let in."
                " You can change them later, and they take effect only when"
                " setup finishes.",
            ),
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
            (
                "For security, saving a new key needs a Google sign-in from the"
                " last five minutes. If yours is older when you choose Save, you"
                " are asked to confirm it's you with Google first,",
                "For security, saving or removing settings here needs a"
                " Google sign-in from the last five minutes.",
            ),
            (
                "For security, saving a new key needs a sign-in from the last five"
                " minutes. If yours is older when you choose Save, you are asked"
                " to confirm your sign-in first,",
                "For security, saving or removing settings here needs a"
                " sign-in from the last five minutes.",
            ),
        ),
        # The export panels' hint (#547), shared by the directory and
        # financial report pages.
        "export-fresh-help": (
            "stewardship/export-fresh-help.html",
            {},
            (
                "For security, queuing this export needs a Google sign-in from"
                " the last five minutes. If yours is older, you are asked to"
                " confirm it's you with Google first and nothing is queued:",
            ),
            (
                "For security, queuing this export needs a sign-in from the last"
                " five minutes. If yours is older, you are asked to confirm your"
                " sign-in first and nothing is queued:",
            ),
        ),
        "credential-selection": (
            "stewardship/credential-selection.html",
            {
                "label": "Slack",
                "receipt": Value(pk=UUID(int=6490), resulting_fingerprint="abc123"),
                "preview": "p",
            },
            (
                "Confirming needs a Google sign-in from the last few minutes; if"
                " yours is older, you are asked to confirm it's you with Google"
                " first.",
            ),
            (
                "Confirming needs a sign-in from the last few minutes; if yours is"
                " older, you are asked to confirm your sign-in first.",
            ),
        ),
        "family-portal-maintenance": (
            "stewardship/family-portal-maintenance.html",
            {"state": Value(closed=False), "message_limit": 500},
            (
                "Changing this requires you to confirm your sign-in with Google,"
                " and every change is recorded in the audit log.",
            ),
            (
                "Changing this requires you to confirm your sign-in, and every"
                " change is recorded in the audit log.",
            ),
        ),
        "users": (
            "stewardship/users.html",
            {
                **user_tables({}, {name: [] for name in TABLES}),
                "base_digest": "0" * 64,
                "roles": [(role, ROLE_LABELS[role]) for role in ROLE_ORDER],
            },
            (
                "Who may sign in to this portal with Google, and why.",
                "A Google sign-in attempt that policy then refused is not counted.",
            ),
            (
                "Who may sign in to this portal, and why.",
                "A sign-in attempt that policy then refused is not counted.",
            ),
        ),
    }


NAMES = sorted(pages())
# Google sign-in phrases that are wrong in LOCAL, matched without case.
# Provider text such as "Google Workspace", "Gmail" or "Google Drive" stays:
# it is true in every profile and contains none of these.
SIGN_IN_PHRASES = ("with google", "google sign-in", "google account")
# The Users page goes on to explain how domain rules match a Google account's
# hosted-domain claim. That is true in every profile (it is why a LOCAL
# sign-in only matches exact addresses), so only the part before the
# domain-rules section is checked there.
CHECKED_BEFORE = {"users": 'id="domain-rules"'}


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
    assert "Individual email addresses that get Staff access, one per line," in page
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
    assert pages()["family-portal-maintenance"][3][0] in page
    assert_no_google_sign_in(page)
