"""Every Admin page has one name (#520, navigation rule 4).

The menu entry, the breadcrumb, the page heading and the browser title use
the registry label, and every "Return to" link names its target the same way.
The placement table in the admin-portal spec is the source of the names, so
the registry is checked against it too. NAV-4 covered Campaign setup and Mail
and Family portal, and NAV-5a Home, Parish data, Users and access, System and
the setup wizard; NAV-5b adds Responses and reports.
"""

import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template import Context, Template
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.accounts import setup_wizard

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/parishkit/stewardship"
TEMPLATE_DIR = PACKAGE / "accounts/templates/stewardship"
SPEC = ROOT / "docs/specs/stewardship/admin-portal/spec.md"

# The menu groups whose page names are settled so far.
GROUPS = {"campaign", "mail", "reports", "parish", "users", "system"}
# Pages outside every menu group whose names are settled too.
UNGROUPED = {"index", "configuration_request"}

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
    "family_portal": "family-portal-maintenance.html",
    "presence": "presence.html",
    "parish_settings": "parish-settings.html",
    "branding_settings": "branding-settings.html",
    "branding_preview": "branding-preview.html",
    "ministries": "ministries.html",
    "hosted_files": "hosted-files.html",
    "hosted_file_delete": "hosted-file-delete.html",
    "hosted_file_rename": "hosted-file-rename.html",
    "source_refresh": "source-refresh.html",
    "users": "users.html",
    "user_rules": "user-rule-preview.html",
    "assignments": "assignment-preview.html",
    "chair_confirmations": "chair-confirmation-preview.html",
    "chair_reviews": "chair-review-preview.html",
    "automation_access": "automation-access.html",
    "automation_approval": "automation-approval.html",
    "system_health": "system-health.html",
    "integrations": "integrations.html",
    "credential_status": "credential-status.html",
    "select_credential": "credential-selection.html",
    "background": "background.html",
    "background_task_page": "background-task.html",
    "logs": "logs.html",
    "participation": "participation.html",
    "financial_report": "financial-report.html",
    "talents_report": "talents-report.html",
    "response_dashboard": "response-dashboard.html",
    "information_queue": "information.html",
    "information_item": "information.html",
    "report_exact": "report-exact.html",
    "weekly_digest_manual": "weekly-manual.html",
    "weekly_digest_snapshot": "weekly-digest.html",
    "weekly_digest_item": "weekly-digest.html",
    "ministry_report": "ministry-report.html",
    "ministry_joiners": "ministry-report.html",
    "ministry_leavers": "ministry-report.html",
    "ministry_followup": "ministry-followup.html",
    "ministry_followup_item": "ministry-followup.html",
    "family_directory": "directory.html",
    "family_codes": "codes.html",
    "family_timeline": "family-timeline.html",
    "index": "home.html",
    "configuration_request": "configuration-request.html",
}
# Each integration's page is named after the integration, each response
# list after its list and each export after its report.
OBJECT_NAMED = {
    "content_edit",
    "content_revision",
    "integration_settings",
    "response_list",
    "report_export",
}
# Pages whose heading the view supplies. The two report roots render the
# "no campaign" page named after the report opened (checked below), and a
# daily report takes the title saved with the emailed report.
VIEW_NAMED = {"reports", "ministry_reports", "daily_digest_snapshot"}
# A sign-in rule change's status answers JSON only (the spec: not a page).
NOT_PAGES = {"rule_request"}
# Pages whose new name waits for a later slice, with that slice. Portal users
# becomes Sign-in rules when NAV-15 splits it; renaming the combined page now
# would mislabel its Ministry assignment and Chairperson tables.
PENDING = {"users": "NAV-15"}

# Each setup wizard step's template, by stepper key: its heading is the
# stepper's label. The data-entry and connection steps share templates whose
# heading is the view's ``step_label``, taken from the same stepper entry.
SETUP = {
    "branding": "setup-branding.html",
    "campaign": "setup-campaign.html",
    "content": "setup-content.html",
    "shares": "setup-shares.html",
    "schedules": "setup-schedules.html",
    "source": "setup-source.html",
    "preview": "setup-preview.html",
    "mail_test": "setup-mail.html",
    "slack_test": "setup-notification.html",
    "finish": "setup-confirmation.html",
}
SETUP_SHARED = ("setup-step.html", "setup-credential.html")

# The review steps of these pages' change flows render their own templates;
# their "Return to" links are checked with the pages'.
REVIEWS = (
    "campaign-preview.html",
    "campaign-ministries-preview.html",
    "integration-preview.html",
    "ministry-preview.html",
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
    """Registered pages in the groups whose names are settled, and Home's."""
    return sorted(
        name
        for name, page in navigation.PAGES.items()
        if page.section in GROUPS or name in UNGROUPED
    )


def test_every_group_page_is_checked_or_object_named():
    """A new page in a settled group must join the names check."""
    assert set(_group_pages()) == (
        set(TEMPLATES) | OBJECT_NAMED | NOT_PAGES | VIEW_NAMED
    )


@pytest.mark.parametrize(
    "name", sorted(set(_group_pages()) - OBJECT_NAMED - NOT_PAGES - set(PENDING))
)
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


# Names the pages had before NAV-4, NAV-5a and NAV-5b. Each is a page name
# nobody should see again: a new link or message that uses one would bring
# back a second name for a renamed page. Matched case-sensitively, as a
# page name is written.
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
    # Retired after NAV-5b review: the setup content editor's old wording.
    "content list",
    # Retired by NAV-5b.
    "Campaign reports",
    "Financial stewardship detail",
    "Additional-information staff queue",
    "Ministry report pages",
    "Participation and campaign statistics",
    "Additional information and follow-up",
    "Queued participation export",
    "Weekly information report",
    "Request a manual information report",
    "Manual information report",
    "Return to the administration portal",
    "Return to this campaign",
    # Retired by NAV-5a.
    "Ministry activity",
    "Chair suggestions",
    "Chair reviews",
    "Review Chairperson confirmation",
    "Review Chairperson assignment decision",
    "Review Ministry assignment change",
    "Back to ",
    "Campaign administration",
    "Configuration change status",
    "Background task details",
    "Review login rule change",
    "Logo preview",
    "First-campaign setup preview",
    "Setup email test",
    "Setup Slack test",
    "Finish initial setup",
    "Setup credential",
)

# Legitimate uses, as (path relative to the package, retired name), each
# with the reason it is not a page name.
ALLOWED = {
    # "Mail schedules keep sending the same kind of email": the schedules
    # themselves, not the page.
    ("accounts/templates/stewardship/setup-content.html", "Mail schedules"),
    # "Ministry activity" filters Ministry requests by whether a Ministry is
    # active; it names the Ministry's state, not the Ministries page.
    ("accounts/templates/stewardship/ministry-report.html", "Ministry activity"),
    # "Back to edit" steps back inside the Family form, outside the Admin
    # portal and its page names.
    ("accounts/static/stewardship/family-v1.js", "Back to "),
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


# Retired names matched exactly as written, because their lowercase form is
# ordinary prose or part of a current name: "Dates and mail schedules",
# "delivery controls" in general wording, "ministry activity" as a state,
# and "back to " in sentences.
CASE_SENSITIVE = {
    "Mail schedules",
    "Delivery controls",
    "Ministry activity",
    "Back to ",
}


def _mentions(text, name):
    """Whether ``text`` uses a retired ``name``: exactly, or in any case."""
    if name in CASE_SENSITIVE:
        return name in text
    return name.lower() in text.lower()


def test_retired_page_names_are_not_shown():
    """No template, script or user-facing message uses a retired page name."""
    found = {
        (relative, name)
        for relative, text in _user_facing_texts()
        for name in RETIRED
        if _mentions(text, name)
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
    controls = reverse("admin:delivery_control")
    outgoing = reverse("admin:deliveries")
    assert f'<a href="{controls}">Pause and resume mail</a>' in html
    assert f'<a href="{outgoing}">Outgoing mail</a>' in html
    assert "<h1>Cancel go-live</h1>" in html
    assert "Confirm cancelling go-live" not in html


@pytest.mark.parametrize("key", sorted(SETUP))
def test_setup_step_heading_is_its_stepper_label(key):
    """A wizard heading uses the stepper's label; its title adds "Initial setup"."""
    title, heading = _title_and_heading(SETUP[key])
    label = str(setup_wizard.BY_KEY[key].label)
    assert heading == {label}
    assert title == {label, "Initial setup"}


@pytest.mark.parametrize("template", SETUP_SHARED)
def test_shared_setup_templates_take_the_stepper_label(template):
    """Shared step templates name the step only through ``step_label``."""
    source = _source(template)
    assert "<h1>{{ step_label }}</h1>" in source
    assert '{% block title %}{{ step_label }} — {% translate "Initial setup" %}' in (
        source
    )


def test_every_setup_step_is_checked():
    """A new stepper entry must join the setup names check."""
    shared = {
        page.key
        for page in setup_wizard.PAGES
        if page.route in {"admin:setup_step", "admin:setup_credential"}
    }
    assert set(setup_wizard.BY_KEY) == set(SETUP) | shared


def test_pending_names_match_the_spec_later():
    """A pending page keeps its old name and the spec still names its new one."""
    for name in PENDING:
        assert str(navigation.PAGES[name].label) != _spec_names()[name]


@pytest.mark.parametrize(
    ("view", "name"),
    [
        ("parishkit.stewardship.reports.workspace_views", "reports"),
        ("parishkit.stewardship.reports.ministry_views", "ministry_reports"),
    ],
)
def test_empty_report_page_is_named_after_its_report(view, name):
    """The "no campaign" page takes the name of the report root that shows it."""
    source = Path(__import__(view, fromlist=["_"]).__file__).read_text(encoding="utf-8")
    assert f'{{"page_name": PAGES["{name}"].label}}' in source
    html = render_to_string(
        "stewardship/report-empty.html",
        {"admin_chrome": None, "page_name": navigation.PAGES[name].label},
    )
    label = str(navigation.PAGES[name].label)
    assert f"<h1>{label}</h1>" in html and f"<title>{label}" in html


def _title(template, context):
    """Render only a template's browser-title block with ``context``."""
    source = _source(template)
    block = re.search(r"{% block title %}(.*?){% endblock %}", source, re.S)
    return Template("{% load i18n %}" + block.group(1)).render(Context(context))


@pytest.mark.parametrize(
    ("name", "context"),
    [
        ("information_queue", {}),
        ("information_item", {"item": {"pk": 1}}),
        ("ministry_followup", {}),
        ("ministry_followup_item", {"item": {"pk": 1}}),
        ("weekly_digest_snapshot", {}),
        ("weekly_digest_item", {"detail": True}),
        ("ministry_report", {"action": None}),
        ("ministry_joiners", {"action": "join"}),
        ("ministry_leavers", {"action": "leave"}),
        ("content_history", {}),
        ("content_history_revision", {"selected": {"id": "x"}}),
    ],
)
def test_shared_templates_name_each_route(name, context):
    """A template shared by a list and its item names each by the context
    its route's view gives it (``item``, ``detail``, ``action``,
    ``selected``)."""
    assert _title(TEMPLATES[name], context) == str(navigation.PAGES[name].label)
