"""Templates do not hand-write back links that repeat the trail (#521).

Navigation rule 3 (admin-portal spec): a page's way back is its breadcrumb,
whose parent crumb is a link; a "Return to" link is only for a parent that
cannot be linked. A "Return to" link to the page's own parent, an ancestor or
Home repeats the trail, so two ways back could drift apart. This reads each
page's template source (as ``test_admin_page_names`` does), so it runs without
a database.
"""

import re

import pytest

from parishkit.stewardship.accounts import admin_navigation as navigation

from .test_admin_page_names import TEMPLATE_DIR, TEMPLATES, VARIABLE_RETURNS

# Pages whose templates ``TEMPLATES`` leaves out (a review step, a template
# shared by two views, a page whose heading the view supplies), each with the
# page whose trail the template is drawn under. The report roots' "no
# campaign" page sits under Home alone. Error pages are left out on purpose:
# rule 3 lets an error page offer a way back.
EXTRA = {
    ("content_edit", "content-settings.html"),
    ("content_edit", "content-preview.html"),
    ("daily_digest_snapshot", "daily-digest.html"),
    ("schedule_settings", "schedule-reconcile.html"),
    ("reports", "report-empty.html"),
}

# "Return to" links that name a page in the trail but keep the reader's place
# in it, which the crumb does not: each adds the queue position the item was
# opened from. Each is (template, target label).
KEPT = {
    ("information.html", "Additional information"),
    ("ministry-followup.html", "Ministry follow-up"),
}

# A "Return to" link: its href (often a reversed Admin URL name, sometimes a
# view-supplied URL) and the page name its text gives. Rule 4 has every such
# link name its target, so the name finds the target even when the href is a
# variable.
RETURN = re.compile(
    r"<a\b[^>]*?href=\"(?P<href>[^\"]*)\"[^>]*>"
    r"\s*{% translate ([\"'])Return to (?P<label>.+?)\2 %}"
)
URL_NAME = re.compile(r"{% url 'admin:(\w+)'")


def _trail(page):
    """The URL names a page's breadcrumb links back to: its ancestors and Home."""
    names = {"index"}
    parent = navigation.PAGES[page].parent
    while parent:
        names.add(parent)
        parent = navigation.PAGES[parent].parent
    return names


def _repeats(page, template):
    """The trail pages ``page``'s template links again in a "Return to" link.

    A link repeats the trail when its href reverses an ancestor or Home, or
    when its text names one, unless ``KEPT`` lists it.
    """
    trail = _trail(page)
    labels = {str(navigation.PAGES[name].label): name for name in trail}
    source = (TEMPLATE_DIR / template).read_text()
    repeats = set()
    for link in RETURN.finditer(source):
        if (template, link["label"]) in KEPT:
            continue
        repeats |= set(URL_NAME.findall(link["href"])) & trail
        if link["label"] in labels:
            repeats.add(labels[link["label"]])
    return sorted(repeats)


PAIRS = sorted(set(TEMPLATES.items()) | EXTRA)

# "Return to {{ … }}" links name their target with a variable, so the text
# check above cannot see them (#1345). ``test_admin_page_names`` keeps the
# reviewed list of them complete; here each says whether it can name the
# trail's parent crumb. A link that can is drawn only when its URL differs
# from the crumb's, as the export page's is, so a "Return to" link remains
# only where it carries a report's filters (the directory and participation
# exports). The others name a page outside the trail: Preview and test
# email's opener, which its view offers only when the trail does not lead
# there, and an integration's own settings page, of which its review is a
# step.
PARENT, ELSEWHERE = "parent", "elsewhere"
VARIABLE_TARGETS = {
    ("campaign-mail.html", "return_link.label"): ELSEWHERE,
    ("configuration-request.html", "admin_chrome.back.label"): PARENT,
    ("credential-status.html", "admin_chrome.back.label"): PARENT,
    ("integration-preview.html", "label"): ELSEWHERE,
    ("report-export.html", "report_name"): PARENT,
}
# Parent-crumb links drawn from the trail itself (``admin_chrome.back``), at
# the end of a change's flow, so they cannot drift from it. Whether they stay
# is #1346.
TRAIL_DRAWN = {"configuration-request.html", "credential-status.html"}
# A variable "Return to" link (its href and the variable it names its target
# with), and the condition that draws it only when it is not the crumb's.
VARIABLE_LINK = re.compile(
    r"<a\b[^>]*?href=\"(?P<href>[^\"]*)\"[^>]*>"
    r"{% blocktranslate(?: with (?P<alias>\w+)=(?P<value>[\w.]+))? %}"
    r"Return to {{ (?P<used>\w+) }}"
)
UNLESS_CRUMB = "{%% if %s != admin_chrome.back.url %%}"


@pytest.mark.parametrize(("page", "template"), PAIRS)
def test_no_return_link_repeats_the_trail(page, template):
    """A page's template links no ancestor or Home in a "Return to" link."""
    repeats = _repeats(page, template)
    assert not repeats, (page, template, repeats)


def test_kept_templates_are_pages_checked_here():
    """Every KEPT template is one this check reads, so the list cannot go stale."""
    checked = {template for _, template in PAIRS}
    assert checked >= {template for template, _ in KEPT}


@pytest.mark.parametrize(("template", "label"), sorted(KEPT))
def test_kept_links_are_still_drawn(template, label):
    """Every KEPT link is still in its template, so the list cannot go stale."""
    links = RETURN.finditer((TEMPLATE_DIR / template).read_text())
    assert label in {link["label"] for link in links}


@pytest.mark.parametrize(
    "template", ["schedule-settings.html", "schedule-reconcile.html"]
)
def test_dates_and_mail_schedules_does_not_return_to_campaign_settings(template):
    """Dates and mail schedules is its own menu page, not under Campaign settings.

    Since #1118 Campaign settings no longer leads there, so "Return to
    Campaign settings" named a page the reader did not come from.
    """
    source = (TEMPLATE_DIR / template).read_text()
    assert "Return to Campaign settings" not in source


def test_variable_return_links_are_classified():
    """Every "Return to {{ … }}" link the page-name check lists is classified
    here, so a new one cannot escape the rule-3 check."""
    assert set(VARIABLE_TARGETS) == VARIABLE_RETURNS
    parents = {key[0] for key, target in VARIABLE_TARGETS.items() if target == PARENT}
    assert parents >= TRAIL_DRAWN


@pytest.mark.parametrize(
    ("template", "variable"),
    sorted(key for key, target in VARIABLE_TARGETS.items() if target == PARENT),
)
def test_variable_return_link_never_repeats_the_parent_crumb(template, variable):
    """A "Return to {{ … }}" link that can name the parent crumb is drawn only
    when its URL differs from the crumb's, unless the trail draws it (#1345).
    """
    source = (TEMPLATE_DIR / template).read_text()
    links = [
        link
        for link in VARIABLE_LINK.finditer(source)
        if (link["value"] if link["alias"] == link["used"] else link["used"])
        == variable
    ]
    assert links, (template, variable)
    if template in TRAIL_DRAWN:
        return
    for link in links:
        href = link["href"].removeprefix("{{ ").removesuffix(" }}")
        guard = UNLESS_CRUMB % href
        # The guard opens just before the link, with only text between.
        before = source[: link.start()]
        assert guard in before, (template, link["href"])
        assert "{%" not in before[before.rindex(guard) + len(guard) :]
