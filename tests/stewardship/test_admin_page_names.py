"""Every Admin page has one name (#520, navigation rule 4).

The menu entry, the breadcrumb, the page heading and the browser title use
the registry label, and every "Return to" link names its target the same way.
The placement table in the admin-portal spec is the source of the names, so
the registry is checked against it too. NAV-4 covers Campaign setup and Mail
and Family portal; NAV-5a and NAV-5b add the other groups to ``TEMPLATES``.
"""

import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import admin_navigation as navigation

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/parishkit/stewardship"
TEMPLATE_DIR = PACKAGE / "accounts/templates/stewardship"
SPEC = ROOT / "docs/specs/stewardship/admin-portal/spec.md"

# The menu groups whose page names are settled so far.
GROUPS = {"campaign", "mail"}

# Each page's template. Object-named pages (an email or page being edited)
# take their heading from the object and are left out; so are pages whose
# heading the view supplies.
TEMPLATES = {
    "campaign_settings": "campaign-settings.html",
    "campaign_clone": "clone-settings.html",
    "content_catalog": "content-catalog.html",
    "content_history": "content-history.html",
    "content_history_revision": "content-history.html",
    "campaign_mail": "campaign-mail.html",
    "campaign_mail_families": "campaign-mail-families.html",
    "schedule_settings": "schedule-settings.html",
    "share_settings": "share-settings.html",
    "artwork_settings": "artwork-settings.html",
    "artwork_upload": "artwork-settings.html",
    "artwork_preview": "artwork-preview.html",
    "artwork_remove": "artwork-remove.html",
    "talent_settings": "talent-settings.html",
    "campaign_ministries": "campaign-ministries.html",
    "go_live": "go-live-readiness.html",
    "go_live_families": "go-live-families.html",
    "go_live_cleanup": "go-live-cleanup.html",
    "go_live_links": "go-live-links.html",
    "production_confirmation": "production-confirmation.html",
    "production_progress": "production-progress.html",
    "production_withdrawal": "production-withdrawal.html",
    "delivery_control": "delivery-control.html",
    "family_email_progress": "family-email-progress.html",
    "family_email_sends": "family-email-sends.html",
    "deliveries": "deliveries.html",
    "delivery": "delivery.html",
    "delivery_refusals": "delivery-refusals.html",
    "delivery_refusal": "delivery-refusal.html",
    "held_emails": "held-emails.html",
    "family_portal": "family-portal-maintenance.html",
    "presence": "presence.html",
}
OBJECT_NAMED = {"content_edit", "content_revision"}

# The review steps of these pages' change flows render their own templates;
# their "Return to" links are checked with the pages'.
REVIEWS = (
    "campaign-preview.html",
    "campaign-ministries-preview.html",
    "clone-preview.html",
    "content-preview.html",
    "content-settings.html",
    "schedule-preview.html",
    "share-preview.html",
    "talent-preview.html",
)

TRANSLATED = re.compile(r'{% translate "([^"]+)" %}')


def _source(template):
    """The template's text."""
    return (TEMPLATE_DIR / template).read_text(encoding="utf-8")


def _names(fragment):
    """The translated strings in a template fragment."""
    return set(TRANSLATED.findall(fragment))


def _title_and_heading(template):
    """The translated names in the browser title block and the ``<h1>``."""
    source = _source(template)
    title = re.search(r"{% block title %}(.*?){% endblock %}", source, re.S)
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", source, re.S)
    assert title and heading, template
    return _names(title.group(1)), _names(heading.group(1))


def _spec_names():
    """``{url name: page name}`` from the spec's placement table.

    Object-named rows (an italic name) are skipped: they have no fixed name.
    """
    names = {}
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| `(\w+)` \| ([^|]+?) \|", line)
        if match and not match.group(2).startswith("_"):
            names[match.group(1)] = match.group(2)
    return names


def _group_pages():
    """Registered pages in the groups whose names are settled."""
    return sorted(
        name for name, page in navigation.PAGES.items() if page.section in GROUPS
    )


def test_every_group_page_is_checked_or_object_named():
    """A new page in a settled group must join the names check."""
    assert set(_group_pages()) == set(TEMPLATES) | OBJECT_NAMED


@pytest.mark.parametrize("name", sorted(set(_group_pages()) - OBJECT_NAMED))
def test_registry_label_matches_the_spec(name):
    """The menu and trail use the name the placement table gives the page."""
    assert str(navigation.PAGES[name].label) == _spec_names()[name]


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_title_and_heading_use_the_page_name(name):
    """The browser title and the heading both name the page by its label.

    A template shared by two pages (Content history and one Earlier
    version) may hold both pages' names, and no other.
    """
    template = TEMPLATES[name]
    shared = {
        str(navigation.PAGES[other].label)
        for other, path in TEMPLATES.items()
        if path == template
    }
    title, heading = _title_and_heading(template)
    label = str(navigation.PAGES[name].label)
    assert label in title and label in heading
    assert title <= shared and heading <= shared


@pytest.mark.parametrize("template", sorted(set(TEMPLATES.values()) | set(REVIEWS)))
def test_return_links_name_a_page(template):
    """Every "Return to X" names X by a page's label; no "Back to" wording."""
    labels = {str(page.label) for page in navigation.PAGES.values()}
    source = _source(template)
    assert "Back to " not in source
    for target in re.findall(r'"Return to ([^"]+)"', source):
        assert target in labels, (template, target)


# Names the pages had before NAV-4. Each is a page name nobody should see
# again: a new link or message that uses one would bring back a second name
# for a renamed page. Matched case-sensitively, as a page name is written.
RETIRED = (
    "Delivery controls",
    "Mail schedules",
    "Family email sends",
    "Withdraw from Production",
    "Campaign content and templates",
    "Campaign email test",
    "Families with Testing submissions",
    "How Families will share",
    "Production activation progress",
)

# Legitimate uses, as (path relative to the package, retired name), each
# with the reason it is not a page name.
ALLOWED = {
    # "Mail schedules keep sending the same kind of email": the schedules
    # themselves, not the page.
    ("accounts/templates/stewardship/setup-content.html", "Mail schedules"),
    # "Distinct Families with Testing submissions": a readiness count and
    # the Testing submissions table's caption, not the page's name.
    (
        "accounts/templates/stewardship/go-live-readiness.html",
        "Families with Testing submissions",
    ),
    (
        "accounts/templates/stewardship/go-live-families.html",
        "Families with Testing submissions",
    ),
}

COMMENT = re.compile(r"{% comment %}.*?{% endcomment %}|{#.*?#}", re.S)
GETTEXT = {"_", "gettext", "gettext_lazy", "ngettext", "pgettext"}


def _call_name(node):
    """The called function's bare name, or None."""
    function = node.func
    return getattr(function, "id", None) or getattr(function, "attr", None)


def _python_texts(path):
    """User-facing strings in a module: every gettext argument.

    The command-line wrapper's help is user-facing too, so for
    ``admin_cli.py`` every string constant is checked except docstrings.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    texts = [
        argument.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) in GETTEXT
        for argument in node.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]
    if path.name == "admin_cli.py":
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node,
                (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        texts += [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]
    return texts


def _user_facing_texts():
    """``(relative path, text)`` for every template, script and gettext string."""
    for path in sorted(PACKAGE.rglob("*")):
        relative = str(path.relative_to(PACKAGE))
        if path.suffix == ".html":
            text = COMMENT.sub("", path.read_text(encoding="utf-8"))
            yield relative, text
        elif path.suffix == ".js" and "node_modules" not in path.parts:
            yield relative, path.read_text(encoding="utf-8")
        elif path.suffix == ".py":
            for text in _python_texts(path):
                yield relative, text


def test_retired_page_names_are_not_shown():
    """No template, script or user-facing message uses a retired page name."""
    found = {
        (relative, name)
        for relative, text in _user_facing_texts()
        for name in RETIRED
        if name in text
    }
    assert found <= ALLOWED, sorted(found - ALLOWED)
    # An allowance with nothing left to allow is stale.
    assert found >= ALLOWED, sorted(ALLOWED - found)


def test_cancel_go_live_sits_under_production_activation():
    """The trail runs Home › Campaign setup › Production activation › Cancel go-live."""
    campaign = uuid4()
    progress = reverse("admin:production_progress", args=[campaign])
    items = [
        navigation.MenuItem(
            "campaign", "production_progress", "Production activation", progress
        )
    ]
    match = SimpleNamespace(
        url_name="production_withdrawal",
        namespace=navigation.NAMESPACE,
        kwargs={"campaign_id": campaign},
    )
    _, trail = navigation.build(match, items)
    assert [(crumb["label"], crumb["url"]) for crumb in trail][1:] == [
        ("Campaign setup", progress),
        ("Production activation", progress),
        ("Cancel go-live", None),
    ]


def test_cancel_go_live_blocking_message_links_the_pages_it_names():
    """Work in flight names Pause and resume mail and Outgoing mail as links."""
    campaign = SimpleNamespace(
        pk=uuid4(),
        active_configuration=SimpleNamespace(
            name="Annual campaign", starts_at=datetime(2054, 10, 1, tzinfo=UTC)
        ),
    )
    inventory = dict.fromkeys(
        ("occurrences", "cancellable", "messages", "failed", "delivered"), 0
    )
    html = render_to_string(
        "stewardship/production-withdrawal.html",
        {
            "admin_chrome": None,
            "campaign": campaign,
            "available": True,
            "fresh": True,
            "preview": {
                "reason": "Fix a date",
                "inventory": inventory | {"blocking": 1},
            },
        },
    )
    controls = reverse("admin:delivery_control", args=[campaign.pk])
    outgoing = reverse("admin:deliveries")
    assert f'<a href="{controls}">Pause and resume mail</a>' in html
    assert f'<a href="{outgoing}">Outgoing mail</a>' in html
    assert "<h1>Cancel go-live</h1>" in html
    assert "Confirm cancelling go-live" not in html
