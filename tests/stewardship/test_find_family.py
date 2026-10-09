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
from parishkit.stewardship.reports.directories import FIND_LIMIT

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
    help_text = box[box.index('id="find-family-help"') :]
    help_text = help_text[: help_text.index("</span>")]
    for words in ("Family name", "member's name", "DUID", "envelope number", "address"):
        assert words in help_text, words
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
    assert "See all 1,234 in the active parishioner family directory" in html


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


def _row(duid, envelope, name="Family", family_id=None):
    """One directory selection row with only what the box reads."""
    return {
        "family_id": family_id,
        "family_name": name,
        "heads": [],
        "family_duid": duid,
        "envelope": envelope,
    }


def _find(monkeypatch, text, page, total, exact=()):
    """Run ``find_families`` against a fake database; return it and its statements.

    The selection returns ``page`` (with ``total`` matches in all) unless the
    call is restricted to one Family, when it returns that Family from
    ``exact``. The exact-number lookup returns the ids of ``exact``.
    """
    import json

    from parishkit.stewardship.reports import directories

    statements = []

    class Cursor:
        """Answers the selection and the exact-number lookup."""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, parameters):
            statements.append((sql, parameters))

        def _report(self, rows):
            """A selection answer holding ``rows``."""
            report = {"rows": rows, "total": total, "metadata": {"source_id": "s"}}
            return (json.dumps(report),)

        def fetchone(self):
            options = json.loads(statements[-1][1][1])
            if options.get("exact"):
                chosen = options["family_id"]
                return self._report([r for r in exact if r["family_id"] == chosen])
            return self._report(page)

        def fetchall(self):
            assert statements[-1][0] is directories.EXACT_FAMILIES
            assert statements[-1][1]["number"] == text
            return [(row["family_id"],) for row in exact]

    monkeypatch.setattr(
        directories, "connection", SimpleNamespace(cursor=lambda: Cursor())
    )
    monkeypatch.setattr(
        directories,
        "selection_parameters",
        lambda *args, **kwargs: {"exact": False, "family_id": None},
    )
    found = directories.find_families(
        UUID(int=561), directories.DirectoryQuery(search=text)
    )
    # Every selection call reads page 1: an unpaged read (None) builds every
    # matched row, which took close to a minute for a broad digit search.
    pages = [
        parameters[2] for sql, parameters in statements if isinstance(parameters, tuple)
    ]
    assert pages == [1] * len(pages), pages
    return found, statements


def test_an_exact_match_on_the_page_is_listed_first(monkeypatch):
    """Exact envelope or DUID Families lead; the rest keep their order (#712)."""
    page = [_row(1120, "5"), _row(512, None), _row(9, "12"), _row(12, "77")]
    found, statements = _find(monkeypatch, "12", page, total=4)
    assert [row["family_duid"] for row in found["rows"]] == [9, 12, 1120, 512]
    # The page holds every match, so nothing more is read.
    assert len(statements) == 1 and statements[0][1][2] == 1
    # Text that is not a whole number, or no exact match, never reorders.
    for text in ("Smith", "45"):
        found, statements = _find(monkeypatch, text, page, total=40)
        assert [row["family_duid"] for row in found["rows"]] == [1120, 512, 9, 12]
    assert len(_find(monkeypatch, "Smith", page, total=400)[1]) == 1


def test_a_broad_number_search_reads_a_bounded_number_of_statements(monkeypatch):
    """Page 1, one lookup and one one-Family read, however many match (#712).

    The exact Family is past the selection's first page of 50, so it is
    looked up and listed first; the total is still the selection's.
    """
    from parishkit.stewardship.reports import directories

    page = [_row(1000 + index, str(index + 100)) for index in range(50)]
    exact = [_row(4022, "7", family_id=str(UUID(int=4022)))]
    found, statements = _find(monkeypatch, "4022", page, total=2700, exact=exact)
    assert found["total"] == 2700 and len(found["rows"]) == FIND_LIMIT
    assert found["rows"][0]["family_duid"] == 4022
    assert [row["family_duid"] for row in found["rows"][1:]] == list(
        range(1000, 1000 + FIND_LIMIT - 1)
    )
    assert len(statements) == 3
    assert [parameters[2] for _sql, parameters in statements[::2]] == [1, 1]
    assert statements[1][0] is directories.EXACT_FAMILIES
    # An exact Family already on the page is not read again.
    page[3] = exact[0]
    found, statements = _find(monkeypatch, "4022", page, total=2700, exact=exact)
    assert found["rows"][0]["family_duid"] == 4022
    assert [row["family_duid"] for row in found["rows"]].count(4022) == 1
    assert len(statements) == 2
