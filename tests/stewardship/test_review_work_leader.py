"""A Ministry leader's My review work: two large steps (#2405).

Rendered from the browser fixtures' contexts (``review_work_components``)
with no database. A Ministry leader who is neither Staff nor an
Administrator gets Step 1 (Review in bulk's own download at Every stage)
and Step 2 (Review one at a time or Review in bulk, named as the leader's
menu names them); Staff keep the stream panels (admin portal spec, "A
Ministry leader's My review work").
"""

import html
import re
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.accounts import review_work
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.pipeline import Stream

from .browser.review_work_components import (
    CAMPAIGN,
    COUNTS,
    LEADER,
    LEADER_EMPTY,
    NO_MINISTRIES,
    chrome,
)
from .test_review_wizard_pages import ADMIN

LEADER_ACTOR = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9, 12}))
STAFF_ACTOR = Principal(uuid4(), frozenset({"staff"}), frozenset())
LEADER_HELP = (
    "This page is where you review the requests to join or leave the "
    "Ministries you lead."
)


def steps(actor=LEADER_ACTOR, counts=None, campaign=CAMPAIGN):
    """``leader_steps`` for ``actor`` with its menu on ``campaign``."""
    _configuration, admin_chrome = chrome(ADMIN, actor, {}, None, campaign)
    review = {"campaign": campaign, "counts": {"leader": LEADER} | (counts or {})}
    return review_work.leader_steps(admin_chrome, review, actor)


def page(actor=LEADER_ACTOR, counts=None, campaign=CAMPAIGN, pdf_limit=None):
    """My review work as ``actor`` sees it, from its heading on.

    ``pdf_limit`` is the page load's ``review_work.leader_pdf_limit``: the
    export's limit when Step 1's file would exceed it.
    """
    configuration, admin_chrome = chrome(ADMIN, actor, {}, None, campaign)
    if counts is None:
        counts = {"leader": LEADER} if actor is LEADER_ACTOR else COUNTS
    body = render_to_string(
        "stewardship/my-review-work.html",
        {
            "csrf_token": "a" * 64,
            "admin_chrome": admin_chrome,
            "principal": actor,
            "configuration": configuration,
            "review": {"campaign": campaign, "counts": counts, "pdf_limit": pdf_limit},
        },
    )
    return body[body.index("<h1>") :]


def text(markup):
    """``markup``'s words, tags stripped and spaces collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


@pytest.mark.parametrize(
    ("roles", "leader"),
    [
        ({"ministry_leader"}, True),
        ({"staff"}, False),
        ({"staff", "ministry_leader"}, False),
        ({"administrator", "staff", "ministry_leader"}, False),
    ],
)
def test_only_a_ministry_leader_gets_the_two_steps(roles, leader):
    """A leader who is neither Staff nor an Administrator gets the steps,
    judged by the leader test Ministry requests uses; nobody else does."""
    actor = Principal(uuid4(), frozenset(roles), frozenset({9}))
    assert review_work.is_leader_page(actor) is leader
    assert (steps(actor) is not None) is leader
    assert review_work.is_leader_page(None) is False


def test_the_choices_are_named_linked_and_counted_as_the_menu_has_them():
    """Both choices take the leader's menu entries' names and links, the
    shared label source (#2393): the same words as the menu and the page
    titles. Both count the leader's requests To review."""
    found = steps()
    assert str(found.one.label) == "Review one at a time"
    assert str(found.bulk.label) == "Review in bulk"
    assert found.one.label == navigation.page_label("review_wizard", LEADER_ACTOR)
    assert found.bulk.label == navigation.page_label("ministry_report", LEADER_ACTOR)
    assert found.one.url == reverse("admin:review_wizard")
    assert found.bulk.url == reverse("admin:ministry_report")
    # 2 joins and 1 leave in one Ministry, none in the other.
    assert (found.count, found.one.count, found.bulk.count) == (3, 3, 3)
    assert str(found.one.note) == str(found.bulk.note) == "to review"


def test_the_count_is_zero_for_nothing_and_none_when_unread():
    """Nothing To review counts 0; a leader of no Ministry here has nothing
    to count either; an unread count shows no number."""
    assert steps(counts={"leader": LEADER_EMPTY}).count == 0
    unread = steps(counts={"leader": None})
    assert (unread.count, unread.one.count, unread.bulk.count) == (None,) * 3


def test_a_leader_of_no_ministry_is_told_plainly_that_nothing_waits():
    """A leader who leads no Ministry here has neither page in the menu: no
    steps are drawn, only a plain sentence, with the leader's own help."""
    nobody = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset())
    found = steps(nobody, counts={"leader": None})
    assert (found.offered, found.count) == (False, 0)
    assert str(found.one.label) == "Review one at a time"
    assert str(found.bulk.label) == "Review in bulk"
    body = page(nobody, counts={"leader": None})
    assert "<p>Nothing is waiting for you to review.</p>" in body
    assert "leader-step" not in body and "data-review-stream" not in body
    assert LEADER_HELP in text(body)
    assert steps().offered is True


def test_a_leader_whose_ministries_are_all_outside_the_campaign_is_told_too():
    """A leader whose Ministries are all outside the campaign leads none of
    them here (#922): the menu still lists both pages, but ``my_ministries``
    reads no Ministries, so the page draws no steps, only the plain
    sentence, as for a leader of no Ministry at all."""
    outside = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({99}))
    none_here = {"leader": {"ministries": []}}
    found = steps(outside, counts=none_here)
    assert (found.offered, found.count) == (False, 0)
    assert found.one.url and found.bulk.url
    body = page(outside, counts=none_here)
    assert "<p>Nothing is waiting for you to review.</p>" in body
    assert "leader-step" not in body and "data-review-stream" not in body
    assert LEADER_HELP in text(body)
    # An unread count keeps the steps: both pages still open.
    assert steps(outside, counts={"leader": None}).offered is True


def test_a_greyed_choice_has_the_menus_reason_and_no_number():
    """A campaign without Ministries greys both of the leader's pages: each
    choice keeps its name, with no link, the menu's reason and no number."""
    found = steps(counts={"leader": None}, campaign=NO_MINISTRIES)
    _configuration, admin_chrome = chrome(ADMIN, LEADER_ACTOR, {}, None, NO_MINISTRIES)
    for card, name in ((found.one, "review_wizard"), (found.bulk, "ministry_report")):
        entry = admin_chrome["entries"][name]
        assert card.url is None and card.reason and card.reason == entry.reason
        assert card.count is None and card.label == entry.label


def test_step_one_posts_review_in_bulks_export_at_every_stage():
    """Step 1 is the Ministry requests export's own form and route. It posts
    exactly the #2406 contract: the CSRF token, stage=all, the browser's
    zone (which ui-v1.js fills) and each button's format, nothing else; the
    export's own choice check reads it as a worklist view of every stage,
    in its default Members, search and sort."""
    from parishkit.stewardship.reports.ministry_followup_exports import _choices
    from parishkit.stewardship.reports.ministry_worklist import WorklistQuery

    body = page()
    form = re.search(r'<form id="leader-export".*?</form>', body, re.S)[0]
    action = reverse("admin:ministry_followup_export")
    assert f'method="post" action="{action}"' in form
    assert sorted(re.findall(r'\bname="(\w+)"', form)) == [
        "browser_timezone",
        "csrfmiddlewaretoken",
        "format",
        "format",
        "stage",
    ]
    assert '<input type="hidden" name="stage" value="all">' in form
    assert '<input type="hidden" name="browser_timezone" data-browser-zone>' in form
    assert re.findall(r'name="format" value="(\w+)">([^<]+)<', form) == [
        ("xlsx", "Export XLSX"),
        ("pdf", "Export PDF"),
    ]
    assert 'role="group" aria-label="Download the requests"' in form
    for fmt in ("xlsx", "pdf"):
        posted = {
            "csrfmiddlewaretoken": "a" * 64,
            "stage": "all",
            "format": fmt,
            "browser_timezone": "UTC",
        }
        query, chosen, zone = _choices(
            MultiValueDict({key: [value] for key, value in posted.items()})
        )
        assert query == WorklistQuery(stage="all")
        assert (chosen, str(zone)) == (fmt, "UTC")


def test_a_leaders_page_is_two_steps_with_no_family_or_stream_words():
    """Step 1 and Step 2, each heading led by its "Step N" badge, so the
    number shows once and the heading reads "Step 1: Download the
    requests"; Step 2's two choices are the same card twice, with an "or"
    between; no stream panels and no Family or stream words anywhere on
    the page."""
    body = page()
    words = text(body)
    assert "data-review-stream" not in body and "data-review-wizard-card" not in body
    assert re.findall(
        r'class="leader-step-heading" aria-label="([^"]+)"><span class="leader-'
        r'step-badge">(Step \d)</span> <span class="leader-step-title">([^<]+)<',
        body,
    ) == [
        ("Step 1: Download the requests", "Step 1", "Download the requests"),
        ("Step 2: Review the requests", "Step 2", "Review the requests"),
    ]
    # One number per step: no second number badge beside the "Step N" one.
    assert body.count("leader-step-badge") == 2 and "leader-step-number" not in body
    assert (
        "Download a spreadsheet (XLSX) or a PDF of every request for the "
        "Ministries you lead. Both hold the same information." in words
    )
    assert (
        "Either one is enough for the whole review: both show the same requests "
        "and details." in words
    )
    choices = re.findall(r'<section class="leader-choice".*?</section>', body, re.S)
    assert [text(choice) for choice in choices] == [
        "Review one at a time One Member per page, with Previous and Next. 3 to review",
        "Review in bulk Every request in one table. 3 to review",
    ]
    wizard, bulk = reverse("admin:review_wizard"), reverse("admin:ministry_report")
    assert (
        f'<a href="{wizard}" class="button button-large" id="leader-choice-one">'
        "Review one at a time</a>" in choices[0]
    )
    assert (
        f'<a href="{bulk}" class="button button-large" id="leader-choice-bulk">'
        "Review in bulk</a>" in choices[1]
    )
    # The same structure: each card is a control group of one button, one
    # line and one count, its markup equal once its words are taken out.
    shapes = {
        re.sub(
            r">[^<]+<|\b(?:href|id|aria-labelledby|data-leader-choice)="
            r'"[^"]*"',
            "",
            choice,
        )
        for choice in choices
    }
    assert len(shapes) == 1
    assert (
        '<div class="review-work-or leader-choice-or" aria-hidden="true">or</div>'
        in body
    )
    assert not re.search(r"\b(?:famil(?:y|ies)|streams?)\b", words, re.I)
    # The About this page is the leader's short help, naming both choices.
    assert LEADER_HELP in words
    assert (
        "In Step 2, Review one at a time shows one Member per page and Review "
        "in bulk shows every request in one table." in words
    )
    assert "What Families sent" not in body


def test_nothing_to_review_is_said_and_both_choices_stay_open():
    """With nothing To review Step 2 says so instead of its usual line; both
    buttons still link, since both pages list the requests already
    decided."""
    body = page(counts={"leader": LEADER_EMPTY})
    words = text(body)
    assert (
        "Nothing is waiting for you to review right now. Either one still shows "
        "the requests already decided." in words
    )
    assert "Either one is enough" not in words
    assert words.count("0 to review") == 2
    assert body.count('class="button button-large"') == 2
    assert "disabled" not in re.search(r'<div class="leader-choices".*', body, re.S)[0]


def test_greyed_pages_make_the_buttons_unavailable_with_the_reason():
    """While the menu greys the leader's pages, both choices' buttons and
    the download's are unavailable, each described by the menu's reason.
    No export can start, so the row reserves no status line and the reason
    sits right under the download buttons."""
    body = page(counts={"leader": None}, campaign=NO_MINISTRIES)
    found = steps(counts={"leader": None}, campaign=NO_MINISTRIES)
    for key, card in (("one", found.one), ("bulk", found.bulk)):
        assert (
            f'<button type="button" class="button-large" id="leader-choice-{key}" '
            f'disabled aria-describedby="leader-choice-{key}-reason">'
        ) in body
        reason = html.escape(str(card.reason))
        assert f'<p class="help" id="leader-choice-{key}-reason">{reason}</p>' in body
    form = re.search(r'<form id="leader-export".*?</form>', body, re.S)[0]
    assert len(re.findall(r"\sdisabled\b", form)) == 2
    assert form.count('aria-describedby="leader-export-reason"') == 2
    reason = html.escape(str(found.bulk.reason))
    assert (
        '<p class="help export-status" id="leader-export-status" role="status" '
        f'data-export-status></p>\n</form>\n<p class="help" id="leader-export-reason">'
        f"{reason}</p>"
    ) in body
    assert "to review" not in text(body)


def test_a_file_too_large_for_a_pdf_leaves_only_xlsx():
    """While Step 1's file would hold more requests than one PDF may, Export
    PDF is unavailable and the line below the row says why in this page's
    words (no CSV, no filters), as Review in bulk does; XLSX stays. Under
    the limit there is no such line and the row keeps its reserved status
    line for a download's note or refusal."""
    body = page(pdf_limit=1000)
    form = re.search(r'<form id="leader-export".*?</form>', body, re.S)[0]
    assert re.findall(r'<button type="submit"[^>]*value="(\w+)"', form) == [
        "xlsx",
        "pdf",
    ]
    assert 'value="pdf" disabled' in form and 'value="xlsx" disabled' not in form
    assert form.count('aria-describedby="leader-export-pdf-limit"') == 2
    assert (
        '</form>\n<p class="help" id="leader-export-pdf-limit">The PDF is limited '
        "to 1,000 requests: choose XLSX.</p>"
    ) in body
    assert "reserve-lines-wrap export-status" in form
    plain = page()
    assert "leader-export-pdf-limit" not in plain
    assert "reserve-lines-wrap export-status" in plain
    assert not re.search(r"\sdisabled\b", plain.split('<form id="leader-export"')[1])


def test_staff_keep_the_stream_panels_and_their_help():
    """Staff get no steps: the One at a time pane beside the stream panels
    and the page's own help, as before #2405."""
    body = page(STAFF_ACTOR)
    assert "leader-step" not in body and "leader-choice" not in body
    assert "data-review-wizard-card" in body and "data-review-stream" in body
    assert "Every stream's waiting items, one Family or Member per page." in body
    assert "What Families sent that is waiting for you, by kind." in body
    assert LEADER_HELP not in text(body)


def test_the_live_choices_are_one_item_with_the_steps_token():
    """Step 2 is one item of the page's review region, keyed and tokened as
    the view's live read (``_review_counts``) compares it."""
    from parishkit.stewardship.web.review_live import item_key, item_token

    found = steps()
    body = page()
    assert '<div data-live-items="/admin/">' in body
    assert (
        f'data-live-item="{item_key("choices", "leader")}" '
        f'data-live-token="{item_token(found)}"'
    ) in body
    # A changed count changes the token.
    assert item_token(found) != item_token(steps(counts={"leader": LEADER_EMPTY}))


def test_staff_panels_still_read_the_ministry_stream():
    """The steps replace only the leader's page: Staff's panels still read
    every stream they may open (a guard that the steps took nothing from
    them)."""
    _configuration, admin_chrome = chrome(ADMIN, STAFF_ACTOR, {})
    panels = review_work.panels(admin_chrome, {"campaign": CAMPAIGN, "counts": COUNTS})
    assert Stream.MINISTRY.value in {panel.stream for panel in panels}
    assert (
        review_work.leader_steps(admin_chrome, {"counts": COUNTS}, STAFF_ACTOR) is None
    )


def test_the_pdf_limit_counts_step_ones_file_as_the_export_reads_it(monkeypatch):
    """``leader_pdf_limit`` counts the worklist's Every stage rows, History's
    left out, as the export writes them, against the export's own limit; a
    refused read leaves the PDF offered (the export still refuses)."""
    from parishkit.stewardship.reports import (
        ministry_followup_exports,
        ministry_worklist,
    )

    reads = []

    def rows(campaign_id, query, principal):
        """Three rows at Every stage and one in History."""
        reads.append(query)
        stages = ("to_review", "accepted", "ignored", "history")
        return {"rows": [{"stage": stage} for stage in stages]}

    monkeypatch.setattr(ministry_worklist, "read_rows", rows)
    monkeypatch.setattr(ministry_followup_exports, "EXPORT_PDF_LIMIT", 2)
    assert review_work.leader_pdf_limit(uuid4(), LEADER_ACTOR) == 2
    assert reads == [ministry_worklist.WorklistQuery(stage="all")]
    monkeypatch.setattr(ministry_followup_exports, "EXPORT_PDF_LIMIT", 3)
    assert review_work.leader_pdf_limit(uuid4(), LEADER_ACTOR) is None

    def refused(*args):
        """The selection refuses this reader."""
        raise PermissionError("not here")

    monkeypatch.setattr(ministry_worklist, "read_rows", refused)
    assert review_work.leader_pdf_limit(uuid4(), LEADER_ACTOR) is None


def test_a_pdf_too_large_from_step_one_is_refused_in_its_own_words(monkeypatch):
    """A stale Step 1 asking for too large a PDF gets the export's refusal,
    its fix naming only XLSX (the page has no CSV and no filters) and its
    way back My review work; Review in bulk's own form keeps its words."""
    from unittest.mock import Mock

    from django.test import RequestFactory

    from parishkit.stewardship.reports import ministry_followup_exports as exports
    from parishkit.stewardship.reports.ministry_worklist import WorklistQuery

    patch = monkeypatch.setattr
    patch(exports, "EXPORT_PDF_LIMIT", 1)
    for name in ("runtime", "admit_report_read", "_audit", "request_contacts"):
        patch(exports, name, Mock())
    patch(exports, "_principal", lambda request, store, **options: LEADER_ACTOR)
    rows = [{"stage": "to_review", "ministry_duid": 9}] * 2
    patch(exports, "read_rows", lambda *args: {"rows": list(rows)})
    patch(exports, "current_ministry", lambda row: row["ministry_duid"])
    patch(
        exports,
        "campaign_response",
        lambda request, campaigns, **options: (options["open_content"](), Mock())[1],
    )
    landing = {
        "csrfmiddlewaretoken": "a" * 64,
        "stage": "all",
        "format": "pdf",
        "browser_timezone": "UTC",
    }
    response = exports.export(RequestFactory().post("/", landing), uuid4())
    refusal = response.stewardship_refusal
    assert response.status_code == 400
    assert str(refusal.message) == "The PDF is limited to 1 requests."
    assert str(refusal.fix) == "Choose XLSX."
    assert (refusal.link, str(refusal.link_label)) == (
        "/admin/",
        "Return to My review work",
    )
    bulk = WorklistQuery(stage="all").form_values() | {
        "format": "pdf",
        "browser_timezone": "UTC",
    }
    response = exports.export(RequestFactory().post("/", bulk), uuid4())
    refusal = response.stewardship_refusal
    assert str(refusal.fix) == "Choose CSV or XLSX, or narrow the filters."
    assert (refusal.link, str(refusal.link_label)) == (
        reverse("admin:ministry_report"),
        "Return to Review in bulk",
    )
