"""The header's Find a Family box (#561, NAV-19) without a database.

Who is offered the box, the markup its script and screen readers rely on,
and the results fragment. The search itself, its role checks and audit run
against PostgreSQL in ``database/test_directories_postgresql.py``; the
keyboard and load behavior in ``browser/test_find_family.py``.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from django.template import engines
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from parishkit.stewardship.accounts import admin_context, admin_navigation
from parishkit.stewardship.accounts.policy import Principal

CAMPAIGN = SimpleNamespace(
    pk=UUID(int=561),
    state="active",
    ever_active=True,
    structural_locked=True,
    active_configuration=SimpleNamespace(values={"modules": ["financial"]}),
)
FAMILY = UUID(int=7)
PAGE = "{% extends 'stewardship/admin-base.html' %}{% block content %}{% endblock %}"


def _chrome(roles, campaign=CAMPAIGN, ministries=frozenset()):
    """The header's box for a viewer with ``roles``, as the chrome builds it."""
    actor = Principal(UUID(int=1), frozenset(roles), ministries)
    admin = "administrator" in roles
    items = admin_context._navigation_items(
        actor, admin, campaign, SimpleNamespace(mode="production")
    )
    return admin_context._find_family(actor, items, campaign)


def test_only_administrators_and_staff_with_a_campaign_get_the_box():
    """Ministry leaders get none, and nobody does without a current campaign."""
    expected = {"url": reverse("admin:find_family")}
    assert _chrome({"administrator", "staff", "ministry_leader"}) == expected
    assert _chrome({"staff"}) == expected
    assert _chrome({"ministry_leader"}, ministries=frozenset({5})) is None
    assert _chrome({"staff"}, campaign=None) is None


def test_the_search_route_is_a_non_page_action():
    """It is no menu page and has no breadcrumbs of its own."""
    assert "find_family" in admin_navigation.NON_PAGES
    assert "find_family" not in admin_navigation.PAGES


def _page(find_family):
    """An Admin page's header with (or without) the box."""
    now = datetime(2026, 10, 6, tzinfo=UTC)
    chrome = {
        "sections": [],
        "breadcrumbs": [],
        "find_family": find_family,
        "server_now": now,
        "idle_deadline": now,
        "absolute_deadline": now,
    }
    return (
        engines["django"]
        .from_string(PAGE)
        .render({"admin_chrome": chrome}, request=RequestFactory().get("/admin/"))
    )


def test_the_header_box_is_a_labelled_post_search():
    """A CSRF POST form with a labelled field, a live status and its results."""
    url = reverse("admin:find_family")
    html = _page({"url": url})
    box = html[html.index("data-find-family>") : html.index("data-find-family-results")]
    assert f'<form method="post" action="{url}"' in box
    assert 'role="search"' in box and 'name="csrfmiddlewaretoken"' in box
    assert '<label for="find-family-search"' in box
    assert 'id="find-family-search" name="search"' in box
    assert 'aria-controls="find-family-results"' in box and "aria-expanded" not in box
    assert 'role="status" aria-live="polite"' in box
    assert 'placeholder="Find a Family"' in box
    assert "Search by name, DUID or address." in box
    assert '<script src="/static/stewardship/find-family-v1.js" defer>' in html
    assert "data-find-family" not in _page(None)


def _results(rows, total, search="ex"):
    """The results fragment for ``rows`` out of ``total`` matches."""
    return render_to_string(
        "stewardship/find-family-results.html",
        {
            "rows": rows,
            "total": total,
            "more": total > len(rows),
            "search": search,
            "campaign_id": CAMPAIGN.pk,
            "directory_url": "/admin/directory/",
        },
    )


def test_results_open_the_timeline_and_offer_the_rest_in_the_directory():
    """Each match links its timeline; more matches post the search there."""
    rows = [
        {
            "family_id": str(FAMILY),
            "display_name": "Example, Anna and Ben",
            "family_duid": 12,
            "envelope": "345",
        },
        {
            "family_id": None,
            "display_name": "Newcomer",
            "family_duid": 13,
            "envelope": None,
        },
    ]
    html = _results(rows, 1234, search='"><b>x')
    assert "1,234 Families found" in html
    timeline = reverse("admin:family_timeline", args=[FAMILY])
    assert f'<a href="{timeline}" data-find-family-result>' in html
    assert "DUID 12 · Envelope 345" in html
    # A Family without a campaign record has no timeline to open.
    assert "Newcomer</span> <span" in html and html.count("<a ") == 1
    assert "DUID 13 · No campaign record yet" in html
    # The rest: a POST to the directory carrying the escaped search text.
    assert '<form method="post" action="/admin/directory/"' in html
    assert 'name="search" value="&quot;&gt;&lt;b&gt;x"' in html
    assert "See all 1,234 in the Family directory" in html


def test_results_say_when_one_or_none_match():
    """Singular wording, and a hint when nothing matches; no See all."""
    one = {"family_id": str(FAMILY), "display_name": "A", "family_duid": 1}
    html = _results([one], 1)
    assert "1 Family found" in html and "See all" not in html
    html = _results([], 0)
    assert "No Family matches." in html and "<ul" not in html


def test_the_script_waits_for_a_pause_and_posts():
    """No request per keystroke, and the text never goes in a URL."""
    script = (
        Path(admin_context.__file__).parent
        / "static"
        / "stewardship"
        / "find-family-v1.js"
    ).read_text(encoding="utf-8")
    assert "const PAUSE = 300;" in script and "const MINIMUM = 2;" in script
    assert 'method: "POST"' in script and "AbortController" in script
    assert "fetch(form.action, {" in script and "?search" not in script
