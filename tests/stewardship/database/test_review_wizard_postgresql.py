"""Review one at a time over HTTP under the real web login (#2123).

The start page starts a run from a stream's own read, fixed as its
sequence; a run's pages move with Previous, Next and Finish posts; the
start page lists a reader's unfinished runs with Continue and Discard;
My review work's card leads to the wizard. The run table's guard keeps a
started run's sequence fixed.
"""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import review_wizard
from parishkit.stewardship.reports.review_run_models import ReviewRun

from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import prepared
from .test_census_worklist_page_postgresql import staff_browser
from .test_information_followup_postgresql import search
from .test_report_workspace_postgresql import read

pytestmark = pytest.mark.django_db(transaction=True)

START = "/admin/reports/review/"


def start(browser, **fields):
    """Post the start page's Start with ``fields`` (Census changes, Basic)."""
    values = {"stream": "census", "mode": "basic", "sort": "name", "start": "1"}
    return search(browser, START, values | fields)


def location(response):
    """A redirect's address and its query."""
    parts = urlsplit(response["Location"])
    return parts.path, {key: value[0] for key, value in parse_qs(parts.query).items()}


def move(browser, url, came_from):
    """Previous or Next: post to the page it opens, naming the control used."""
    return search(browser, url, {"from": came_from})


def test_a_census_run_starts_moves_and_finishes(response_service, google):
    """Start fixes the Family page, then the Member's; Next and Previous move
    and keep focus on the control used; Finish opens My review work and the
    run is no longer open."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, START)
        assert response.status_code == 200
        page = body.decode()
        assert "<h1>Review one at a time</h1>" in page
        assert 'id="review-stream-census"' in page and "Unfinished reviews" not in page
        response, _ = start(browser)
        assert response.status_code == 303
        path, query = location(response)
        assert path == f"{START}1/"
        run = ReviewRun.objects.get(pk=query["run"])
        assert run.stream == "census" and run.position == 1
        assert run.filters == {
            "mode": "basic",
            "stage": "waiting",
            "search_used": False,
        }
        # The Family page (its household changes) first, then Member 3's.
        assert [(s["kind"], s["key"]) for s in run.subjects] == [
            ("family", ""),
            ("member", "3"),
        ]
        assert len(run.subjects[0]["items"]) == 2 == len(run.subjects[1]["items"])
        # The start is audited as a view of Census changes, with its count.
        viewed = AuditContext.objects.filter(
            event__event_type="census_changes_viewed"
        ).values_list("context", flat=True)
        assert [context["count"] for context in viewed] == [4]
        response, body = read(browser, response["Location"])
        assert response.status_code == 200
        page = body.decode()
        assert "<title>Review one at a time: Census changes, Family 1 of 1" in page
        assert page.count("Family 1 of 1</p>") == 2
        # The trail names the stream and progress once, not the parent twice.
        crumbs = page.split('class="breadcrumbs"')[1].split("</nav>")[0]
        assert crumbs.count("Review one at a time") == 1
        assert re.search(r'aria-current="page">Census changes, Family 1 of 1', page)
        # Page 1 has no Previous; Finish is in the middle; Next at the right.
        assert 'data-review-move="previous"' not in page
        assert page.count('data-review-move="next"') == 2
        assert page.count('data-review-move="finish"') == 2
        assert "Open Census changes" in page
        assert "Open this Family" in page and "/admin/reports/families/" in page
        response, _ = move(browser, f"{START}2/?run={run.pk}", "next-bottom")
        assert response.status_code == 303
        assert response["Location"] == f"{START}2/?run={run.pk}&from=next-bottom"
        run.refresh_from_db()
        assert run.position == 2
        _, body = read(browser, response["Location"])
        page = body.decode()
        assert "Family 1 of 1, Member 1 of 1" in page
        assert "Member of" in page
        # The last page: Next's slot is Finish, the middle slot empty; focus
        # is on the bottom row's Next slot, the control the move came from.
        assert 'data-review-move="next"' not in page
        bottom = page.split('data-review-nav="bottom"')[1]
        assert re.search(r'data-review-move="finish"[^>]*data-review-focus', bottom)
        assert "data-review-focus" not in page.split('data-review-nav="bottom"')[0]
        response, _ = move(browser, f"{START}1/?run={run.pk}", "previous-top")
        _, body = read(browser, response["Location"])
        # Page 1 has no Previous to take focus.
        assert "data-review-focus" not in body.decode()
        # Back to page 2 in the browser (a GET records nothing), then Finish
        # there: it finishes from any page, last write wins.
        run.refresh_from_db()
        assert run.position == 1
        response, _ = search(browser, f"{START}2/?run={run.pk}", {"finish": "1"})
        assert response.status_code == 303 and response["Location"] == "/admin/"
        run.refresh_from_db()
        assert run.finished_at is not None and run.position == 2
        # A move on the finished run answers as a move does; its page then
        # says the run is not open.
        response, _ = move(browser, f"{START}1/?run={run.pk}", "next-top")
        assert response.status_code == 303
        assert response["Location"] == f"{START}1/?run={run.pk}"
        response, body = read(browser, f"{START}1/?run={run.pk}")
        assert response.status_code == 200
        assert b"This review is no longer open." in body
        # A finished run is not offered again.
        _, body = read(browser, START)
        assert b"Unfinished reviews" not in body


def test_several_runs_are_listed_continued_and_discarded(response_service, google):
    """Each Start makes a new run; the start page lists them with Continue
    and Discard, and a discarded run's page is no longer open."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        first = location(start(browser)[0])[1]["run"]
        second = location(start(browser, sort="newest")[0])[1]["run"]
        assert first != second and ReviewRun.objects.count() == 2
        _, body = read(browser, START)
        page = body.decode()
        assert page.count("data-review-run=") == 2
        assert f'href="{START}1/?run={first}"' in page
        assert "Census changes, Family 1 of 1" in page
        response, _ = search(browser, START, {"discard": second})
        assert response.status_code == 303
        assert response["Location"] == f"{START}#review-runs"
        assert [str(pk) for pk in ReviewRun.objects.values_list("pk", flat=True)] == [
            first
        ]
        _, body = read(browser, f"{START}1/?run={second}")
        assert b"This review is no longer open." in body
        # Discarding it again changes nothing; the answer is the same list.
        assert search(browser, START, {"discard": second})[0].status_code == 303
        assert ReviewRun.objects.count() == 1
        # Once the last run is discarded the list's region is still drawn,
        # empty, so the dialog redraws it in place.
        assert search(browser, START, {"discard": first})[0].status_code == 303
        _, body = read(browser, START)
        assert b'id="review-runs" data-table-region' in body
        assert b"Unfinished reviews" not in body


def test_a_reader_never_sees_another_readers_run(response_service, google):
    """Another reader's run reads as no longer open, and Discard leaves it."""
    prepared(response_service)
    admin = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(start(admin)[0])[1]["run"]
    from .auth_builders import signed_in

    google[0].update(email="admin@example.org", sub="another-google-subject")
    other, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = read(other, f"{START}1/?run={run}")
        assert b"This review is no longer open." in body
        response, _ = search(other, f"{START}2/?run={run}", {"from": "next-top"})
        assert response.status_code == 303
        assert search(other, START, {"discard": run})[0].status_code == 303
    assert ReviewRun.objects.get(pk=run).position == 1


def test_start_refusals(response_service, google, monkeypatch):
    """Nothing to review says so; a malformed start is a 400; too many pages
    is refused in plain words; nothing is stored for any of them."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = start(browser, search="no such family")
        assert response.status_code == 200
        assert b"Nothing matches these filters." in body
        for fields in (
            {"mode": "expert"},
            {"sort": "random"},
            {"stream": "nope"},
            {"stage": "to_review"},
            {"mode": "advanced", "stage": "nope"},
            {"route": "any"},
        ):
            assert start(browser, **fields)[0].status_code == 400, fields
        monkeypatch.setattr(review_wizard, "MAX_SUBJECTS", 1)
        response, body = start(browser)
        assert response.status_code == 400
        assert b"This selection has more than 5,000 pages." in body
        # Advanced at a Stage the table offers starts as the table reads.
        monkeypatch.setattr(review_wizard, "MAX_SUBJECTS", 5000)
        response, _ = start(browser, mode="advanced", stage="all", kind="contact")
        assert response.status_code == 303
        run = ReviewRun.objects.get()
        assert run.filters["stage"] == "all" and run.filters["kind"] == "contact"
        # A position outside the run is a 400; a GET of one too.
        assert search(browser, f"{START}9/?run={run.pk}", {})[0].status_code == 400
        assert read(browser, f"{START}9/?run={run.pk}")[0].status_code == 400
        assert read(browser, f"{START}1/?run=nope")[0].status_code == 400
        assert read(browser, f"{START}?stream=census")[0].status_code == 400


def test_the_run_guard_keeps_the_sequence_fixed(response_service, google):
    """Only the position (inside the run), finish and use time may change."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = ReviewRun.objects.get(pk=location(start(browser)[0])[1]["run"])
        for statement in (
            "UPDATE stewardship_review_run SET subjects='[]'::jsonb WHERE id=%s",
            "UPDATE stewardship_review_run SET stream='pledges' WHERE id=%s",
            "UPDATE stewardship_review_run SET position=9 WHERE id=%s",
            "UPDATE stewardship_review_run SET filters='{}'::jsonb WHERE id=%s",
        ):
            with (
                pytest.raises(IntegrityError),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, [run.pk])
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE stewardship_review_run SET position=2, finished_at=now() "
                "WHERE id=%s",
                [run.pk],
            )
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE stewardship_review_run SET position=1 WHERE id=%s", [run.pk]
            )


def test_my_review_work_card_leads_to_the_wizard(response_service, google):
    """The card opens the start page; with one unfinished run it continues it."""
    prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get("/admin/")
        page = response.content.decode()
        card = page.split("data-review-wizard-card")[1].split("</ul>")[0]
        assert (
            f'<a href="{START}"><span class="link-card-label">Review one at a time'
            in (card)
        )
        assert page.index("data-review-wizard-card") < page.index("data-review-stream=")
        run = location(start(browser)[0])[1]["run"]
        page = browser.get("/admin/").content.decode()
        card = page.split("data-review-wizard-card")[1].split("</ul>")[0]
        assert f'href="{START}1/?run={run}"' in card
        assert "Continue: Census changes, Family 1 of 1" in card
        # The menu lists Review one at a time first in Review work.
        group = re.search(r'data-menu-group="review".*?</ul>', page, re.S).group(0)
        assert re.findall(r"<li><a [^>]*>([^<]*)</a>", group)[:2] == [
            "Review one at a time",
            "My review work",
        ]


def test_a_leader_reviews_only_the_ministries_they_lead(response_service, google):
    """A Ministry leader is offered Ministry requests alone, with no stream
    choice; a run lists only their Ministry's requests, one Member a page,
    and their pages draw no Timeline link. Staff get every request."""
    from parishkit.stewardship.workflows.models import MinistryRequest

    from .test_ministry_exports_postgresql import leader
    from .test_ministry_reports_postgresql import setup

    harness = setup(response_service)  # A join for Ministry 9, a leave for 4.
    browser, *_ = leader(harness, google)
    mine = MinistryRequest.objects.get(ministry_duid=9)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = read(browser, START)
        page = body.decode()
        assert '<input type="hidden" name="stream" value="ministry">' in page
        assert 'id="review-stream-census"' not in page
        assert page.count("data-review-filters=") == 1
        # A stream the leader's menu does not offer is refused.
        assert start(browser)[0].status_code == 400
        response, _ = start(browser, stream="ministry")
        assert response.status_code == 303
        run = ReviewRun.objects.get()
        assert [s["items"] for s in run.subjects] == [[str(mine.pk)]]
        assert run.subjects[0]["kind"] == mine.entity_kind
        _, body = read(browser, response["Location"])
        page = body.decode()
        assert "Ministry requests, Member 1 of 1" in page
        assert "Open Ministry requests" in page and "Timeline" not in page


def test_staff_review_every_ministry_request(response_service, google):
    """Staff's Ministry requests run lists every Ministry's requests."""
    from parishkit.stewardship.workflows.models import MinistryRequest

    from .test_ministry_exports_postgresql import leader
    from .test_ministry_reports_postgresql import setup

    harness = setup(response_service)
    staff, *_ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = read(staff, START)
        assert b'id="review-stream-ministry"' in body
        assert start(staff, stream="ministry")[0].status_code == 303
    found = ReviewRun.objects.get()
    assert sorted(item for s in found.subjects for item in s["items"]) == sorted(
        str(pk) for pk in MinistryRequest.objects.values_list("pk", flat=True)
    )


def test_information_and_limitations_runs(response_service, google):
    """Additional information has one Family page; Limitations has the
    Family page for the Family's own answer (Cannot attend Mass), then the
    Member's page for theirs."""
    from parishkit.stewardship.responses.models import AdditionalInformationItem

    from .response_builders import activate_response_service
    from .test_ministry_exports_postgresql import leader
    from .test_ministry_responses_postgresql import start as started
    from .test_review_work_postgresql import submit

    harness = response_service
    started(harness)
    harness = activate_response_service(harness)
    submission = submit(harness)
    browser, *_ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert start(browser, stream="information")[0].status_code == 303
        information = ReviewRun.objects.get(stream="information")
        item = AdditionalInformationItem.objects.get(submission_id=submission.pk)
        assert information.subjects == [
            {
                "family": str(submission.family_id),
                "kind": "family",
                "key": "",
                "items": [str(item.pk)],
            }
        ]
        assert start(browser, stream="limitations")[0].status_code == 303
        limitations = ReviewRun.objects.get(stream="limitations")
        assert [(s["kind"], s["key"]) for s in limitations.subjects] == [
            ("family", ""),
            ("member", "3"),
        ]
        response, body = read(browser, f"{START}1/?run={limitations.pk}")
        assert response.status_code == 200
        assert b"Limitations, Family 1 of 1" in body


def test_a_pledges_run(response_service, google):
    """Pledges: one Family page per pledge waiting."""
    from .test_pledge_review_postgresql import pledged
    from .test_pledge_review_postgresql import staff_browser as pledge_staff

    harness, submission = pledged(response_service)
    browser, _ = pledge_staff(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert start(browser, stream="pledges")[0].status_code == 303
    run = ReviewRun.objects.get()
    assert [(s["family"], s["kind"]) for s in run.subjects] == [
        (str(submission.family_id), "family")
    ]
    assert len(run.subjects[0]["items"]) == 1
