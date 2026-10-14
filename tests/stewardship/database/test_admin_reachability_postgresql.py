"""Reachability, way-back and no-UUID crawl of the Admin portal (ADM-12.20, NAV-17).

Signs in as Administrator and as Staff (with no current campaign, on a new
Testing draft, on a campaign with a complete ParishSoft capture, and on a
live Production campaign with every module and a configured Ministry) and as
a Ministry leader, and follows every GET link from Home, as a reader clicking
through the portal would. It checks the admin-portal spec's navigation rules
against the rendered pages, not only the registry
(``tests/stewardship/test_admin_navigation.py`` covers the registry):

- rule 1: every registered page that needs no route argument and that the
  role may open is reached by following links from Home;
- rule 3: every reached page except Home shows a breadcrumb whose parent
  crumb is a link, or a "Return to" link, that the viewer may open;
- the current campaign is implied: no ``href``, ``action``, ``src`` or
  ``data-*-url`` names its UUID.

A linked page that answers 401 or 403, or a reader no longer signed in at
the end, fails the crawl, so a lost session cannot empty the checks.

``PENDING`` lists the pages that still break rule 1 or rule 3 until #521
(NAV-16) gives them a way in or back. It may only shrink: a listed page that
now passes fails the test, so its entry is removed with the fix.
"""

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import pytest
from django.urls import Resolver404, resolve, reverse

from parishkit.stewardship.accounts.admin_navigation import (
    NAMESPACE,
    PAGES,
    route_parameters,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.source.models import SourceCurrent

from ..campaign_factory import campaign, financial
from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import add_draft, campaign_clock, change, command
from .leader_builders import make_leader
from .test_daily_digest_dispatch_postgresql import allocated
from .test_daily_digest_planning_postgresql import INSTANT
from .test_debug_production_banner_postgresql import force_mode
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_hosted_files_postgresql import media  # noqa: F401
from .test_report_workspace_postgresql import read
from .user_builders import convert

pytestmark = pytest.mark.django_db(transaction=True)

ROLES = ("administrator", "staff")
# Pages of one URL name opened per crawl: enough to see each page's links and
# trail, without opening every Family's timeline or every outgoing message.
PER_NAME = 2
# Pages linked only in a state these crawls do not render, each with where
# its link is proven instead.
ELSEWHERE = {
    # Home links it only while a restore left a held email undecided:
    # test_held_emails_page_postgresql.py.
    "held_emails",
}
# Pages these fixtures cannot open, so a crumb linking one is trusted to the
# registry's linkable flag instead of a 200, each with where it is rendered.
UNOPENED = {
    # Preview and test email needs an applied email integration:
    # test_campaign_mail_postgresql.py.
    "campaign_mail",
}
# (scenario, rule, URL name) waiting for #521. May only shrink.
PENDING = frozenset()


class _Page(HTMLParser):
    """Collects a rendered page's links, URL-bearing attributes and trail."""

    def __init__(self):
        """Start with no links, no URL attributes and no breadcrumb."""
        super().__init__(convert_charrefs=True)
        self.links = []
        self.urls = []
        self.crumbs = []
        self.returns = []
        self._nav = 0
        self._in_trail = False
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        """Record URL attributes, anchors and breadcrumb items."""
        attrs = dict(attrs)
        for name, value in attrs.items():
            if value and (
                name in ("href", "action", "src")
                or (name.startswith("data-") and name.endswith("-url"))
            ):
                self.urls.append(value)
        if tag == "nav":
            self._nav += 1
            if attrs.get("aria-label") == "Breadcrumb":
                self._in_trail = True
        elif tag == "li" and self._in_trail:
            self.crumbs.append(False)
        elif tag == "a":
            href = attrs.get("href")
            if href:
                self.links.append(href)
                if self._in_trail and self.crumbs:
                    self.crumbs[-1] = href
                self._anchor = (href, [])

    def handle_data(self, data):
        """Gather the text of the anchor being read."""
        if self._anchor:
            self._anchor[1].append(data)

    def handle_endtag(self, tag):
        """Close a breadcrumb nav or finish an anchor's text."""
        if tag == "nav" and self._nav:
            self._nav -= 1
            self._in_trail = False
        elif tag == "a" and self._anchor:
            href, text = self._anchor
            if " ".join("".join(text).split()).startswith("Return to"):
                self.returns.append(href)
            self._anchor = None


def _page_name(url):
    """The Admin registry name of a same-site URL, or None when it is no page."""
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path.startswith("/admin/"):
        return None
    try:
        match = resolve(parts.path)
    except Resolver404:
        return None
    if match.namespace != NAMESPACE or match.url_name not in PAGES:
        return None
    return match.url_name


def _sign_in(store, google, role):
    """Sign in as ``role`` on a deployment converted to users (#952).

    Production runs on users, and the Users page refuses before the
    conversion, so the crawl converts first. Any role but Administrator is
    reader@example.org, given its sign-in rule before the conversion; a
    Ministry leader also leads Ministry 9 in the current campaign through a
    ParishSoft role (#922).

    The source pointer is put back as the migration seeds it: an earlier
    transactional test's flush deletes that singleton row, and without it
    pages that read the current source (Families the form cannot open)
    refuse with 403 rather than answer "no ParishSoft data yet".
    """
    SourceCurrent.objects.get_or_create(singleton=True)
    if role == "ministry_leader":
        make_leader(store, Campaign.objects.get(), "reader@example.org")
        google[0]["email"] = "reader@example.org"
    elif role != "administrator":
        _add_reader(store, google, role)
    convert(store)
    browser, _ = signed_in()
    return browser


def _add_reader(store, google, role):
    """Give reader@example.org a login rule for ``role`` and sign in as it next."""
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"


def _crawl(browser):
    """Follow every GET link to an Admin page from Home.

    Returns ``{path: (url name, parsed page)}`` for each page that answered
    200, opening at most ``PER_NAME`` paths per URL name. A redirect's target
    is followed as a link; a redirect off the registry's pages (the sign-in,
    after a page signed the reader out or refused the session) fails the
    crawl, since every later page would then silently drop out of rules 1
    and 3. Links resolve against the page they are on; query strings are
    dropped, so filters and pagination do not multiply the pages opened.
    Pages are read through the report tests' ``read``, which also drains a
    streamed report. Only anchors and redirects are followed, never forms,
    so Sign out and every other POST action are left alone.
    """
    seen, opened, reached = {"/admin/"}, {}, {}
    queue = ["/admin/"]
    while queue:
        path = queue.pop(0)
        name = _page_name(path)
        if name is None or opened.get(name, 0) >= PER_NAME:
            continue
        opened[name] = opened.get(name, 0) + 1
        response, content = read(browser, path)
        # A link the viewer may not follow (rule 6), or a session lost midway.
        assert response.status_code not in (401, 403), (path, response.status_code)
        targets = []
        if response.status_code in (301, 302, 303, 307, 308):
            targets = [response["Location"]]
            assert _page_name(urljoin(path, targets[0])), (path, targets[0])
        elif response.status_code == 200 and "html" in response.get("Content-Type", ""):
            page = _Page()
            page.feed(content.decode())
            reached[path] = (name, page)
            targets = page.links
        for target in targets:
            target = urljoin(path, target)
            if _page_name(target) is None:
                continue
            target = urlsplit(target).path
            if target and target not in seen:
                seen.add(target)
                queue.append(target)
    return reached


def _rule_one(browser, reached):
    """Names of parameterless pages the viewer may open that no link reaches.

    "May open" means the page answers 200 when its address is typed; a page
    that redirects or refuses in this state has nothing to reach.
    """
    parameters = route_parameters()
    found = {name for name, _page in reached.values()}
    missing = set()
    for name, page in PAGES.items():
        if name in found or name in ELSEWHERE:
            continue
        if not page.linkable or parameters.get(name):
            continue
        response, _content = read(browser, reverse(f"{NAMESPACE}:{name}"))
        if response.status_code == 200:
            missing.add(name)
    return missing


def _rule_three(browser, reached):
    """Reached pages, except Home, with no way back the viewer may open.

    The way back is the parent crumb's link, or else a "Return to" link; it
    must answer 200 (rule 3: "a link the viewer may open").
    """
    stuck = set()
    for path, (name, page) in reached.items():
        if name == "index":
            continue
        parent = page.crumbs[-2] if len(page.crumbs) >= 2 else None
        back = parent or (page.returns[0] if page.returns else None)
        if back is None or not _opens(browser, reached, urljoin(path, back)):
            stuck.add(name)
    return stuck


def _opens(browser, reached, url):
    """Whether ``url`` is a page the crawl reached or that answers 200.

    A linkable page these fixtures cannot open (``UNOPENED``) counts too.
    """
    path = urlsplit(url).path
    if path in reached:
        return True
    name = _page_name(url)
    if name in UNOPENED:
        return PAGES[name].linkable
    return read(browser, url)[0].status_code == 200


def _check(browser, scenario):
    """Crawl from Home and check rules 1 and 3 and the no-UUID rule.

    Returns the URL names reached, so a scenario can prove it rendered the
    pages it exists for.
    """
    # Every campaign's UUID, in both its dashed and its hex form.
    campaigns = {
        form
        for pk in Campaign.objects.values_list("pk", flat=True)
        for form in (str(pk), pk.hex)
    }
    reached = _crawl(browser)

    failures = {(scenario, "reachable", name) for name in _rule_one(browser, reached)}
    failures |= {(scenario, "way back", name) for name in _rule_three(browser, reached)}
    # Rule 1 counts only pages that open when typed, so a session lost midway
    # would empty it; prove the reader is still signed in after both checks.
    assert read(browser, "/admin/")[0].status_code == 200, "signed out midway"
    pending = {entry for entry in PENDING if entry[0] == scenario}
    assert failures - pending == set(), "pages breaking navigation rules"
    assert pending - failures == set(), "now fixed: remove from PENDING"

    named = {
        path: [url for url in page.urls if any(c in url.lower() for c in campaigns)]
        for path, (_name, page) in reached.items()
    }
    assert {path: urls for path, urls in named.items() if urls} == {}
    return {name for name, _page in reached.values()}


@pytest.mark.parametrize("role", ROLES)
def test_a_new_testing_draft_is_navigable_without_naming_the_campaign(
    auth_service,
    google,
    media,  # noqa: F811
    role,
):
    """A draft with no ParishSoft data yet: most reports refuse, setup is open."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser = _sign_in(store, google, role)
    reached = _check(browser, f"draft {role}")
    # The crawl gets past Home into the pages the role is offered.
    assert "index" in reached and len(reached) > 5
    admin = {"campaign_settings", "users", "hosted_files"}
    assert (admin <= reached) == (role == "administrator")


@pytest.mark.parametrize("role", ROLES)
def test_a_census_backed_campaign_is_navigable_without_naming_the_campaign(
    family_mail,  # noqa: F811
    google,
    media,  # noqa: F811
    role,
):
    """A campaign with a complete ParishSoft capture, so the reports open too."""
    with campaign_clock(INSTANT):
        allocated(family_mail)
        browser = _sign_in(family_mail.service.store, google, role)
        reached = _check(browser, f"census {role}")
    assert {"participation", "family_directory", "family_timeline"} <= reached


@pytest.mark.parametrize("role", ROLES)
def test_no_current_campaign_is_navigable(auth_service, google, media, role):  # noqa: F811
    """A deployment before its first campaign: campaign pages are greyed out,
    and every page that opens is still reached and has a way back."""
    browser = _sign_in(auth_service.store, google, role)
    reached = _check(browser, f"no campaign {role}")
    assert "index" in reached and not {"campaign_settings", "participation"} & reached


def test_a_ministry_leader_reaches_their_ministry_pages(auth_service, google, media):  # noqa: F811
    """A ParishSoft leader of a configured Ministry reaches its pages, and no
    Administrator page.

    Rule 1 already fails for any parameterless page that answers the leader
    200 but is not reached, so an exact reached set proves no other such
    page opens for them, linked or typed.
    """
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser = _sign_in(store, google, "ministry_leader")
    reached = _check(browser, "leader")
    assert reached == {"index", "ministry_report", "ministry_followup"}


def _go_live_in_production(store):
    """A live campaign with every module and Ministry 9, in Production mode.

    The campaign is activated through its lifecycle command. Only a whole
    go-live may switch the deployment to Production, so the mode is set
    directly, as the debug-banner test does; every request runs guarded.
    No Production confirmation is stored, so Production activation (which
    the menu links in this state) and Cancel go-live answer "unavailable"
    and are not crawled; test_withdrawal_postgresql.py opens both after a
    real confirmation.
    """
    row = campaign(
        modules=["census", "financial", "ministry"],
        financial=financial(),
        ministry_duids=[9],
    )
    assert add_draft(store, store.active(), uuid4(), row)[0].state == "applied"
    draft = Campaign.objects.get()
    # A leader of Ministry 9 gives the Ministry pages someone to list.
    make_leader(store, draft, "leader@example.org")
    command(draft, uuid4(), Action.ACTIVATE)
    force_mode("production")


@pytest.mark.parametrize("role", ROLES)
def test_a_live_production_campaign_with_every_module_is_navigable(
    auth_service,
    google,
    media,  # noqa: F811
    role,
):
    """The pages the other states grey out open here and are crawled too:
    Financial stewardship, Pause and resume mail and the Ministry pages."""
    store = auth_service.store
    _go_live_in_production(store)
    browser = _sign_in(store, google, role)
    reached = _check(browser, f"production {role}")
    assert {"financial_report", "ministry_report", "limitations_report"} <= reached
    assert ("delivery_control" in reached) == (role == "administrator")
