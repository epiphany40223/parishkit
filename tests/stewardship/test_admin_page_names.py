"""Every Admin page has one name (#520, navigation rule 4).

The menu entry, the breadcrumb, the page heading and the browser title use
the registry label, and every "Return to" link names its target the same way.
The placement table in the admin-portal spec is the source of the names, so
the registry is checked against it too. NAV-4 covered Campaign setup and Mail
and Family portal, and NAV-5a Home, Parish data, Users and access, System and
the setup wizard; NAV-5b adds Responses and reports (since R39, Review work
and Reports and queries).
"""

import ast
import re
from pathlib import Path

import pytest
from django.template import Context, Template
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.accounts import setup_wizard
from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP_NAME

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/parishkit/stewardship"
TEMPLATE_DIR = PACKAGE / "accounts/templates/stewardship"
SPEC = ROOT / "docs/specs/stewardship/admin-portal/spec.md"

# The menu groups whose page names are settled so far.
GROUPS = {"campaign", "mail", "review", "reports", "parish", "users", "system"}
# Pages outside every menu group whose names are settled too.
UNGROUPED = {"index", "configuration_request", "campaign_create"}

# Each page's template. Object-named pages (an email or page being edited)
# take their heading from the object and are left out; so are pages whose
# heading the view supplies.
TEMPLATES = {
    "campaign_settings": "campaign-settings.html",
    "campaign_create": "campaign-create.html",
    "campaign_clone": "clone-settings.html",
    "content_catalog": "content-catalog.html",
    "content_history": "content-history.html",
    "content_history_revision": "content-history.html",
    "campaign_mail": "campaign-mail.html",
    "campaign_mail_families": "campaign-mail-families.html",
    "schedule_settings": "schedule-settings.html",
    "schedule_new": "schedule-entry.html",
    "schedule_edit": "schedule-entry.html",
    "schedule_calendar": "schedule-calendar.html",
    "share_settings": "share-settings.html",
    "artwork_settings": "artwork-settings.html",
    "artwork_upload": "artwork-settings.html",
    "reminder_workgroup": "reminder-workgroup.html",
    "go_live": "go-live-check.html",
    "go_live_families": "go-live-families.html",
    "go_live_prepare": "go-live-prepare.html",
    "go_live_confirm": "go-live-confirm.html",
    "production_progress": "production-progress.html",
    "delivery_control": "delivery-control.html",
    "mail_queue": "mail-queue.html",
    "family_email_sends": "family-email-sends.html",
    "deliveries": "deliveries.html",
    "delivery_refusals": "delivery-refusals.html",
    "delivery_refusal": "delivery-refusal.html",
    "family_portal": "family-portal-maintenance.html",
    "presence": "presence.html",
    "held_emails": "held-emails.html",
    "parish_settings": "parish-settings.html",
    "branding_settings": "branding-settings.html",
    "ministries": "ministries.html",
    "hosted_files": "hosted-files.html",
    "hosted_file_rename": "hosted-file-rename.html",
    "past_campaigns": "past-campaigns.html",
    "source_refresh": "source-refresh.html",
    "users": "users.html",
    "user_new": "user-entry.html",
    "user_edit": "user-entry.html",
    "signed_in_sessions": "signed-in-sessions.html",
    "automation_access": "automation-access.html",
    "automation_approval": "automation-approval.html",
    "system_health": "system-health.html",
    "source_form": "source-form.html",
    "parishsoft_fixes": "data-to-fix.html",
    "integrations": "integrations.html",
    "background": "background.html",
    "background_task_page": "background-task.html",
    "logs": "logs.html",
    "campaign_comparison": "campaign-comparison.html",
    "financial_report": "financial-report.html",
    "pledges_to_enter": "pledges-to-enter.html",
    "limitations_report": "limitations-report.html",
    "census_changes": "census-changes.html",
    "update_parishsoft": "update-parishsoft.html",
    "response_dashboard": "response-dashboard.html",
    "information_queue": "information.html",
    "information_item": "information.html",
    "weekly_digest_manual": "weekly-manual.html",
    "weekly_digest_snapshot": "weekly-digest.html",
    "weekly_digest_item": "weekly-digest.html",
    # Ministry requests is the follow-up queue's page (#1133).
    "ministry_report": "ministry-requests.html",
    "ministry_followup_item": "ministry-followup.html",
    "family_directory": "directory.html",
    "emailed_reports": "emailed-reports.html",
    "timeline": "timeline.html",
    "index": "home.html",
    "configuration_request": "configuration-request.html",
}
# Each integration's page is named after the integration and each export
# after its report.
OBJECT_NAMED = {
    "content_edit",
    # One email is named after its type and Family (#1015).
    "delivery",
    "content_revision",
    "integration_settings",
    "report_export",
}
# Pages whose heading the view supplies. The reports root renders the "no
# campaign" page named after the report opened (checked below), and a daily
# report takes the title saved with the emailed report.
VIEW_NAMED = {"reports", "daily_digest_snapshot"}
# Delete scheduled emails and Delete user answer their list's dialog with a
# change's status page, as does Delete email templates for Pages and emails'
# tables, and Delete hosted files the library's dialog with a redirect or a
# refusal (#879), never a page of their own. Import Ministry leaders answers
# Users' review dialog with the review's fragment (#750), and so does Email
# Ministry leaders (#1592).
NOT_PAGES = {
    "schedule_delete",
    "user_delete",
    "hosted_file_delete",
    "content_delete",
    "user_leader_import",
    "user_leader_emails",
}
# Pages whose new name waits for a later slice, with that slice. None is
# left: Portal users became Users with #952.
PENDING = {}

# Each setup wizard step's template, by stepper key: its heading is the
# stepper's label. The data-entry and connection steps share templates whose
# heading is the view's ``step_label``, taken from the same stepper entry.
SETUP = {
    "branding": "setup-branding.html",
    "source": "setup-source.html",
    "preview": "setup-preview.html",
    "mail_test": "setup-mail.html",
    "slack_test": "setup-notification.html",
    "finish": "setup-confirmation.html",
}
SETUP_SHARED = ("setup-step.html", "setup-credential.html")

TRANSLATED = re.compile(r'{% translate "([^"]+)" %}')


def _source(template):
    """The template's text, with the reminder exclusion name (#1061) spelled out.

    Its page names itself through a template tag reading the name's one
    source, so the checks below see the name that tag renders.
    """
    return (
        (TEMPLATE_DIR / template)
        .read_text(encoding="utf-8")
        .replace(
            "{% reminder_workgroup_name %}",
            f'{{% translate "{REMINDER_WORKGROUP_NAME}" %}}',
        )
    )


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


# "Return to" links whose target has no fixed name (navigation rule 4): the
# shared error page returns to whatever page the reader came from, as does
# the delivery error page for a refused form (#1401: the outgoing mail list,
# a message, a refused address or a background task), and the export error
# page, which reads nothing from the database, cannot name the export it
# returns to.
UNNAMED_RETURNS = {
    ("error.html", "the previous page"),
    ("delivery-error.html", "the previous page"),
    ("report-export-error.html", "the export status page"),
}
# "Return to {{ … }}" links, by template, and the variable each names its
# target with. Each variable holds a registry label: the trail's parent
# crumb (admin_chrome.back), the page a test email was opened from
# (refusals.return_link), the integration's name (integration_views.LABELS)
# and the export's report page (export_ui's report_name). A new one must
# be added here once its variable is known to hold a page's name.
VARIABLE_RETURNS = {
    ("campaign-mail.html", "return_link.label"),
    ("configuration-request.html", "admin_chrome.back.label"),
    ("integration-preview.html", "label"),
    ("report-export.html", "report_name"),
}
VARIABLE_RETURN = re.compile(
    r"{% blocktranslate(?: with (\w+)=([\w.]+))? %}Return to {{ (\w+) }}"
)


@pytest.mark.parametrize(
    "template", sorted(path.name for path in TEMPLATE_DIR.glob("*.html"))
)
def test_return_links_name_a_page(template):
    """Every "Return to X" names X by a page's label; no "Back to" wording.

    Every Admin template is checked, error pages and shared fragments too,
    not only the pages' own templates (#520).
    """
    labels = {str(page.label) for page in navigation.PAGES.values()}
    source = _source(template)
    # The Census changes worklist's "Back to To review" is a review decision,
    # not a link back to a page (see ALLOWED), as is the review pipeline's
    # Back to review on Ministry requests (#1684), Additional information
    # and Limitations (#1685).
    if template not in {"census-changes.html", *REVIEW_PIPELINE_PAGES}:
        assert "Back to " not in source
    for target in re.findall(r'"Return to ([^"]+)"', source):
        assert target in labels or (template, target) in UNNAMED_RETURNS, (
            template,
            target,
        )


def test_variable_return_links_are_known():
    """Every "Return to {{ … }}" link names its target from a variable known
    to hold a page's name; a new one must be checked and listed."""
    found = set()
    for path in sorted(TEMPLATE_DIR.glob("*.html")):
        for alias, value, used in VARIABLE_RETURN.findall(path.read_text("utf-8")):
            found.add((path.name, value if alias == used else used))
    assert found == VARIABLE_RETURNS, sorted(found ^ VARIABLE_RETURNS)


# Python-built "Return to" link labels: a view's link_label or back_label.
PYTHON_RETURN = re.compile(r'(?:link_label=|"back_label", )_\("Return to ([^"]+)"\)')


def test_python_return_links_name_a_page():
    """A "Return to" link label built in a view names a page by its label."""
    labels = {str(page.label) for page in navigation.PAGES.values()}
    found = [
        (path.name, target)
        for path in sorted(PACKAGE.rglob("*.py"))
        for target in PYTHON_RETURN.findall(path.read_text("utf-8"))
    ]
    # The guard sees the known labels (Ministry follow-up, Ministry requests;
    # setup_content_views' "Return to Pages and emails" was removed by #1269),
    # so its pattern still matches.
    assert len(found) >= 2, found
    assert all(target in labels for _path, target in found), found


# A link whose whole text is one translated string, to an Admin page.
PAGE_LINK = re.compile(
    r"<a\b[^>]*href=\"{% url '(?:admin):(\w+)'[^%]*%}[^\"]*\"[^>]*>"
    r'\s*{% translate "([^"]+)" %}\s*</a>'
)


def _former_names():
    """Names the placement table's "Current names" column says a page replaced.

    Left out: a former name that is some page's name today (Ministry
    follow-up was the item's name and is now the queue's), placeholders,
    quoted wording rather than a name, single generic words ("Message",
    "Joining") that ordinary link text may start with, and rows that are
    not pages (the shared header, a form action), whose Name column is a
    parenthesised note other than "(retired)".
    """
    current = {str(page.label).lower() for page in navigation.PAGES.values()}
    names = set()
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| `\w+` \| ([^|]+?) \|(?:[^|]*\|){2} ([^|]+?) \|", line)
        if not match:
            continue
        page_name, former = match.groups()
        if page_name.startswith("(") and page_name != "(retired)":
            continue
        for name in re.split(r";| / ", former):
            name = re.sub(r"\(.*?\)", "", name).strip()
            if (
                " " in name
                and not name.startswith("_")
                and not re.search("['\"]", name)
                and name.lower() not in current
            ):
                names.add(name)
    return names


FORMER_NAME_LINKS: set[tuple[str, str]] = set()


def test_page_links_do_not_use_a_former_name():
    """A link to an Admin page never calls it by a name the page replaced.

    Action links ("Refresh now", "Open email") may word themselves freely;
    a link whose text is a former page name ("ParishSoft refresh"), or
    starts with one before a comma ("Ministry reports, …"), is a second
    name for the page (rule 4).
    """
    former = _former_names()
    found = set()
    for path in sorted(TEMPLATE_DIR.glob("*.html")):
        for _name, text in PAGE_LINK.findall(
            COMMENT.sub("", path.read_text(encoding="utf-8"))
        ):
            for name in former:
                # The whole text, or the name before a comma or dash, so
                # "Return to Testing cleanup" is not "Return to Testing".
                if re.match(rf"{re.escape(name)}(?:$|[,:;—])", text, re.I):
                    found.add((path.name, text))
    assert found == FORMER_NAME_LINKS, sorted(found ^ FORMER_NAME_LINKS)


def test_former_names_are_read_from_the_spec():
    """The placement table parse finds the renames it should guard."""
    former = _former_names()
    assert {"ParishSoft refresh", "Ministry reports", "Delivery controls"} <= former
    # Each of a cell's " / " alternatives is a name of its own.
    assert {"Ministry export", "Family-directory mail-merge export"} <= former
    # An object-named row's former link names count; its placeholder does not.
    assert {"ParishSoft settings", "Off-site backup settings"} <= former
    assert {"Key change details", "Family-directory export"} <= former
    assert not any(name.startswith("_") for name in former)
    # Current names, a non-page row, quoted wording and single words are not.
    assert not former & {"Ministry requests", "Every Admin page", "Message"}
    assert not any('"' in name or "'" in name for name in former)


# Templates that may link pages above their heading: Sent mail's links
# lead to its sibling pages, not back up its trail.
ABOVE_HEADING = {"deliveries.html"}


def test_no_hand_written_way_back_above_the_heading():
    """The breadcrumb is the way back; no Admin page repeats it above its
    ``<h1>`` (rule 3). The setup wizard has no trail, so its steps may."""
    found = set()
    for path in sorted(TEMPLATE_DIR.glob("*.html")):
        source = COMMENT.sub("", path.read_text(encoding="utf-8"))
        content = source.partition("{% block content %}")[2]
        if "<h1" not in content or path.name.startswith("setup-"):
            continue
        if "{% url 'admin:" in content.partition("<h1")[0]:
            found.add(path.name)
    assert found == ABOVE_HEADING, sorted(found ^ ABOVE_HEADING)


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
    # Renamed Go live by #1585 slice 2.
    "Go-live readiness",
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
    # Retired by #1061: it read as if those Families get Reminders.
    "Reminder WorkGroup",
    # Retired by #520: the page is the Active parishioner family directory,
    # and an integration's page is named after the integration.
    "Family directory",
    "ParishSoft settings page",
)

# Legitimate uses, as (path relative to the package, retired name), each
# with the reason it is not a page name.
# The review pipeline's worklist pages (#1684, #1685), whose help names the
# decision Back to review.
REVIEW_PIPELINE_PAGES = frozenset(
    {
        "ministry-requests.html",
        "ministry-followup.html",
        # Additional information and Limitations (#1685).
        "information.html",
        "limitations-report.html",
    }
)
ALLOWED = {
    # "Back to edit" steps back inside the Family form, outside the Admin
    # portal and its page names.
    ("accounts/static/stewardship/family-v1.js", "Back to "),
    # The Timeline's review lines (#1774) name the review pipeline's Back to
    # review action, not a page.
    ("reports/timeline_reviews.py", "Back to "),
    # "Back to To review" is the Census changes review button that undoes an
    # Approve or Ignore (#528), not a link back to a page.
    ("accounts/templates/stewardship/census-changes.html", "Back to "),
    # The review pipeline's decision Back to review (#1684), named in the
    # help of Ministry requests and of its request pages.
    *(
        (f"accounts/templates/stewardship/{template}", "Back to ")
        for template in sorted(REVIEW_PIPELINE_PAGES)
    ),
    # The review pipeline's decision Back to review (#1685), named in the
    # help of Additional information and Limitations.
    *(
        (f"accounts/templates/stewardship/{template}", "Back to ")
        for template in sorted(REVIEW_PIPELINE_PAGES)
    ),
    # ``pk-admin census review`` (#528) words that button's decision
    # (``unreviewed``) in its help, not a link back to a page.
    ("admin_cli.py", "Back to "),
    # "Distinct Families with Testing submissions": a readiness count and
    # the Testing submissions table's caption, not the page's name.
    (
        "accounts/templates/stewardship/go-live-check.html",
        "Families with Testing submissions",
    ),
    (
        "accounts/templates/stewardship/go-live-start-review.html",
        "Families with Testing submissions",
    ),
    (
        "accounts/templates/stewardship/go-live-families.html",
        "Families with Testing submissions",
    ),
    # ``pk-admin go-live readiness`` is a command's name, kept when the page
    # was renamed Go live (#1585), as ``content list`` below.
    ("admin_cli.py", "Go-live readiness"),
    # ``pk-admin content list`` (#463) is a command's name, as ``files
    # list``, not the retired wording for the Pages and emails page.
    ("admin_cli.py", "content list"),
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
# "Active parishioner family directory", "delivery controls" in general
# wording, "ministry activity" as a state,
# and "back to " in sentences.
CASE_SENSITIVE = {
    "Family directory",
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


def test_cancel_go_live_blocking_message_links_the_pages_it_names():
    """Work in flight names Pause and resume mail and Sent mail as links.

    Cancel go-live is a review dialog on Production activation (#1585
    slice 5): mail that blocks it is a refusal inside the dialog, which
    offers no Apply (no signed review).
    """
    inventory = dict.fromkeys(
        ("occurrences", "cancellable", "messages", "failed", "delivered"), 0
    )
    html = render_to_string(
        "stewardship/production-cancel-review.html",
        {"refused": "blocking", "inventory": inventory | {"blocking": 1}},
    )
    controls = reverse("admin:delivery_control")
    outgoing = reverse("admin:deliveries")
    assert f'<a href="{controls}">Pause and resume mail</a>' in html
    assert f'<a href="{outgoing}">Sent mail</a>' in html
    assert "data-review-refusal" in html and "data-review-preview" not in html
    assert "1 item of mail is being sent right now" in html


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
        # Ministry requests with no current campaign, as the retired
        # /admin/ministry-reports/ root showed (NAV-11). The reports root
        # opens Participation instead, whose greyed page says why (#522).
        ("parishkit.stewardship.reports.ministry_followup_views", "ministry_report"),
    ],
)
def test_empty_report_page_is_named_after_its_report(view, name):
    """The "no campaign" page takes the name of the report that shows it."""
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
        ("ministry_report", {}),
        ("ministry_followup_item", {"item": {"pk": 1}}),
        ("weekly_digest_snapshot", {}),
        ("weekly_digest_item", {"detail": True}),
        ("content_history", {}),
        ("content_history_revision", {"selected": {"id": "x"}}),
    ],
)
def test_shared_templates_name_each_route(name, context):
    """A template shared by a list and its item names each by the context
    its route's view gives it (``item``, ``detail``, ``action``,
    ``selected``)."""
    assert _title(TEMPLATES[name], context) == str(navigation.PAGES[name].label)
