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
                "For security, saving or removing settings here needs a"
                " Google sign-in from the last five minutes.",
            ),
            (
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
    assert pages()["family-portal-maintenance"][3][0] in page
    assert_no_google_sign_in(page)


# The pages whose "needs a sign-in from the last five minutes" hint shows only
# when the step-up would be asked for now (#1159). Family portal availability
# takes ``fresh`` from its view; the others ask the ``fresh_sign_in`` tag.
STEP_UP_HINTS = (
    "setup",
    "setup-credential",
    "integration-settings",
    "export-fresh-help",
    "credential-selection",
    "family-portal-maintenance",
)


def render_signed_in(name, local, age, monkeypatch):
    """Render one page through a request whose sign-in is ``age`` old."""
    from parishkit.stewardship.accounts import sessions

    monkeypatch.setattr(sessions, "database_now", lambda: NOW)
    template, values, _, _ = pages()[name]
    page = CONTEXT | values | {"fresh": age <= timedelta(minutes=5)}
    if local:
        page["local_environment"] = True
    request = RequestFactory().get("/admin/")
    request.portal_session = Value(authenticated_at=NOW - age)
    return flatten(render_to_string(template, page, request=request))


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("name", STEP_UP_HINTS)
def test_step_up_hint_shows_only_when_the_sign_in_is_stale(name, local, monkeypatch):
    """A fresh sign-in hides the hint; one over five minutes old shows it."""
    _, _, production, local_sentences = pages()[name]
    sentences = local_sentences if local else production
    fresh = render_signed_in(name, local, timedelta(minutes=1), monkeypatch)
    stale = render_signed_in(name, local, timedelta(minutes=6), monkeypatch)
    for sentence in sentences:
        assert sentence not in fresh
        assert sentence in stale


def test_fresh_pages_keep_their_other_rules(monkeypatch):
    """Only the step-up sentence goes: the order and audit rules stay."""
    setup = render_signed_in("setup-credential", False, timedelta(0), monkeypatch)
    assert "the Slack token needs Slack turned on first." in setup
    maintenance = render_signed_in(
        "family-portal-maintenance", False, timedelta(0), monkeypatch
    )
    assert "Every change is recorded in the audit log." in maintenance


# The integrations with a key field: ParishSoft and Slack drew a second
# "For security" paragraph for the key under the settings one (pk-local
# batch 12). The settings paragraph already says what you entered is not kept.
KEY_PAGES = {
    "parishsoft": "ParishSoft",
    "slack": "Slack",
}


def integration_page(target, label):
    """The settings page context for an integration with a key field."""
    return {
        "target": target,
        "label": label,
        "configured": True,
        "form": IntegrationForm(target),
        "credential": InlineCredentialForm(target),
        "status_url": f"/admin/integrations/{target}/status",
    }


def step_up_notices(page):
    """How many step-up notices a rendered page holds.

    Every step-up hint names the five-minute (or "few minutes") window, so
    counting that phrase counts the notices, whatever their wording.
    The top notice of a page that also disables its buttons ("You need to
    have signed in within the last five minutes") counts too.
    """
    return page.count("the last five minutes") + page.count("the last few minutes")


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("target", sorted(KEY_PAGES))
def test_integration_key_page_shows_the_step_up_notice_once(target, local, monkeypatch):
    """A stale sign-in sees one step-up notice on a key page, not two."""
    from parishkit.stewardship.accounts import sessions

    monkeypatch.setattr(sessions, "database_now", lambda: NOW)
    page = CONTEXT | integration_page(target, KEY_PAGES[target])
    page["local_environment"] = local
    request = RequestFactory().get(f"/admin/system/integrations/{target}/")
    request.portal_session = Value(authenticated_at=NOW - timedelta(minutes=6))
    html = flatten(
        render_to_string("stewardship/integration-settings.html", page, request=request)
    )
    assert step_up_notices(html) == 1
    assert "what you entered is not kept" in html


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("name", STEP_UP_HINTS)
def test_step_up_hint_shows_at_most_once(name, local, monkeypatch):
    """Every page with a step-up hint shows it at most once when stale."""
    page = render_signed_in(name, local, timedelta(minutes=6), monkeypatch)
    assert step_up_notices(page) <= 1
