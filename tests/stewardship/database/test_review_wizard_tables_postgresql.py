"""Review these one at a time on the review tables, under the web login (#2123).

Review pipeline spec, "Wizard entry points": each review table's button
posts the table's current view, and the run it starts is the run the start
page gives for the same filters and sort. An empty table draws it
unavailable with its reason; it never asks about unfinished runs, which
the start replaces (R52 Part W).
"""

import html
import re

import pytest

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.review_run_models import ReviewRun

from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import prepared
from .test_census_worklist_page_postgresql import staff_browser
from .test_information_followup_postgresql import search
from .test_review_wizard_postgresql import START, location

pytestmark = pytest.mark.django_db(transaction=True)

CENSUS = "/admin/reports/census/"


def entry(page):
    """The table's button form: its id prefix and its posted fields."""
    found = re.search(
        r'<form method="post" action="/admin/reports/review/" id="([^"]+)-wizard"'
        r"[^>]*>(.*?)</form>",
        page,
        re.S,
    )
    assert found, "no Review these one at a time form"
    fields = {
        name: html.unescape(value)
        for name, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">', found.group(2)
        )
    }
    fields.pop("csrfmiddlewaretoken", None)
    return found.group(1), fields


def button(page, prefix):
    """The table's Review these one at a time button."""
    return re.search(rf'<button[^>]*form="{prefix}-wizard"[^>]*>', page).group(0)


def run_of(response):
    """The run a start's redirect opened, which must be its page 1."""
    assert response.status_code == 303
    path, query = location(response)
    assert path == f"{START}1/"
    return ReviewRun.objects.get(pk=query["run"])


def same_run(browser, fields):
    """The start page's own run for the table's fields (its Advanced mode)."""
    values = {k: v for k, v in fields.items() if k != "table"} | {"start": "1"}
    return run_of(search(browser, START, values)[0])


def test_census_carries_its_filters_and_sort(response_service, google):
    """The census table's button posts its Stage, filters, search and sort;
    the run equals the start page's for the same view, and opens page 1."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        view = {"stage": "all", "route": "by_hand", "search": "", "sort": "-submitted"}
        response, body = search(browser, CENSUS, view)
        assert response.status_code == 200
        page = body.decode()
        prefix, fields = entry(page)
        assert fields == {
            "stream": "census",
            "mode": "advanced",
            "sort": "newest",
            "table": "1",
            "stage": "all",
            "route": "by_hand",
            "kind": "any",
        }
        # First in the selection bar, ahead of the bulk buttons; available.
        bar = page.split('class="bulk-bar"')[1]
        assert bar.index(f'form="{prefix}-wizard"') < bar.index("data-bulk-action")
        assert "disabled" not in button(page, prefix)
        assert "data-wizard-ask" not in button(page, prefix)
        run = run_of(search(browser, START, fields)[0])
        assert run.stream == "census" and run.sort == "newest"
        assert run.filters == {
            "mode": "advanced",
            "stage": "all",
            "route": "by_hand",
            "kind": "any",
            "search_used": False,
        }
        # By hand only: the prefix and the household's two changes.
        assert sum(len(s["items"]) for s in run.subjects) == 3
        ReviewRun.objects.all().delete()
        assert same_run(browser, fields).subjects == run.subjects


def test_a_search_travels_only_in_the_post(response_service, google):
    """The table's name search is a hidden field of the button's POST form,
    never in an address, and the run notes only that one was used."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = search(browser, CENSUS, {"stage": "all", "search": "a"})
        page = body.decode()
        _, fields = entry(page)
        assert fields["search"] == "a"
        assert not re.search(r'href="[^"]*search=', page)
        run = run_of(search(browser, START, fields)[0])
        assert run.filters["search_used"] is True and "search" not in run.filters


def test_an_empty_table_draws_the_button_unavailable(response_service, google):
    """Nothing listed: disabled, with its reason beside it; nothing started."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = search(browser, CENSUS, {"stage": "all", "search": "zzzz-nobody"})
        page = body.decode()
        prefix, _ = entry(page)
        found = button(page, prefix)
        assert " disabled" in found
        assert f'aria-describedby="{prefix}-wizard-reason"' in found
        assert (
            f'id="{prefix}-wizard-reason">Nothing is listed to review one at a time.'
            in page
        )
        assert not ReviewRun.objects.exists()


def test_a_table_start_never_asks_about_earlier_runs(response_service, google):
    """With an unfinished run, the table's button still posts at once (no
    dialog) and the server starts the new run, replacing the earlier one;
    a tampered post is refused (R52 Part W)."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = search(browser, CENSUS, {"stage": "all"})
        _, fields = entry(body.decode())
        first = run_of(search(browser, START, fields)[0])
        _, body = search(browser, CENSUS, {"stage": "all"})
        page = body.decode()
        prefix, again = entry(page)
        assert again == fields
        assert (
            "data-wizard-ask" not in button(page, prefix)
            and "<dialog id="
            not in (page.split('class="bulk-bar"')[1].split("</form>")[0])
        )
        second = run_of(search(browser, START, fields)[0])
        assert second.pk != first.pk and second.subjects == first.subjects
        assert list(ReviewRun.objects.values_list("pk", flat=True)) == [second.pk]
        # A tampered table post is refused, and the old answer field too.
        for extra in ({"page": "2"}, {"mode": "other"}, {"new": "1"}):
            assert search(browser, START, fields | extra)[0].status_code == 400, extra
        assert ReviewRun.objects.count() == 1


def test_a_leaders_ministry_run_holds_only_their_ministries(response_service, google):
    """A leader's table posts its view; the run, read through the leader's
    own scope, holds only the requests of the Ministry they lead."""
    from parishkit.stewardship.workflows.models import MinistryRequest

    from .test_ministry_exports_postgresql import leader
    from .test_ministry_reports_postgresql import setup

    harness = setup(response_service)  # A join for Ministry 9, a leave for 4.
    browser, *_ = leader(harness, google)
    mine = MinistryRequest.objects.get(ministry_duid=9)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = search(browser, "/admin/reports/ministries/", {"stage": "all"})
        _, fields = entry(body.decode())
        assert fields["stream"] == "ministry" and fields["stage"] == "all"
        run = run_of(search(browser, START, fields)[0])
        assert [s["items"] for s in run.subjects] == [[str(mine.pk)]]
        # A stream the leader's menu does not offer is refused, and the
        # leader's run is kept.
        census = fields | {"stream": "census"}
        census.pop("ministry", None)
        assert search(browser, START, census)[0].status_code == 400
        assert ReviewRun.objects.count() == 1


def test_staff_ministry_table_starts_every_ministry(response_service, google):
    """Staff's Ministry requests table starts a run over every request."""
    from parishkit.stewardship.workflows.models import MinistryRequest

    from .test_ministry_exports_postgresql import leader
    from .test_ministry_reports_postgresql import setup

    harness = setup(response_service)
    staff, *_ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = search(staff, "/admin/reports/ministries/", {"stage": "all"})
        _, fields = entry(body.decode())
        run = run_of(search(staff, START, fields)[0])
    assert sorted(item for s in run.subjects for item in s["items"]) == sorted(
        str(pk) for pk in MinistryRequest.objects.values_list("pk", flat=True)
    )


def test_information_and_limitations_tables(response_service, google):
    """Additional information's and Limitations' tables each start their own
    stream's run, the start page's for the same view."""
    from .response_builders import activate_response_service
    from .test_ministry_exports_postgresql import leader
    from .test_ministry_responses_postgresql import start as started
    from .test_review_work_postgresql import submit

    started(response_service)
    harness = activate_response_service(response_service)
    submit(harness)
    browser, *_ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for route, stream in (
            ("/admin/reports/information/", "information"),
            ("/admin/reports/limitations/", "limitations"),
        ):
            response, body = search(browser, route, {"stage": "all"})
            assert response.status_code == 200
            _, fields = entry(body.decode())
            assert fields["stream"] == stream and fields["stage"] == "all"
            run = run_of(search(browser, START, fields)[0])
            assert run.stream == stream and run.subjects
            assert same_run(browser, fields).subjects == run.subjects


def test_the_pledges_table(response_service, google):
    """Pledges' table starts a Pledges run with its Stage."""
    from .test_pledge_review_postgresql import pledged
    from .test_pledge_review_postgresql import staff_browser as pledge_staff

    harness, submission = pledged(response_service)
    browser, _ = pledge_staff(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, "/admin/reports/financial/pledges/", {})
        assert response.status_code == 200
        _, fields = entry(body.decode())
        assert fields["stream"] == "pledges"
        run = run_of(search(browser, START, fields)[0])
    assert [(s["family"], s["kind"]) for s in run.subjects] == [
        (str(submission.family_id), "family")
    ]
