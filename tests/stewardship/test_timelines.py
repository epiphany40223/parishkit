"""A Family's Timeline: summary, lines, filters and page, without a database.

#477 built the page as the Family timeline; #1570 renamed it Timeline, made it
Production only and added sign-outs, session ends and the Kind and day filters.

The view's admission, read guard, Staff reduction and audit are covered
against PostgreSQL in ``database/test_timelines_postgresql.py``.
"""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP_NAME
from parishkit.stewardship.reports.response_metrics import ResponseScope
from parishkit.stewardship.reports.timeline_views import (
    Identity,
    TimelineQuery,
    open_form,
    page_context,
    parse_query,
    timeline_url,
)
from parishkit.stewardship.reports.timelines import (
    Email,
    Engagement,
    Submitted,
    Timeline,
    email_name,
    events,
    mode_at,
    narrow,
    scope_sign_ins,
    scope_values,
    summary,
)
from parishkit.stewardship.web.dates import using

from .test_response_metrics import START

CAMPAIGN = SimpleNamespace(
    pk=UUID(int=477), active_configuration=SimpleNamespace(name="Sample campaign")
)
FAMILY = UUID(int=6)
MINUTE = timedelta(minutes=1)
INVITATION = Email(UUID(int=1), "initial", "Invitation", "delivered", START, START)
REMINDER = Email(
    UUID(int=2), "reminder", "Reminder 1", "delivery_unknown", START + 60 * MINUTE
)
RECEIPT = Email(
    UUID(int=3),
    "receipt",
    "Submission receipt",
    "delivered",
    START + 90 * MINUTE,
    START + 91 * MINUTE,
)
FIRST = Submitted(UUID(int=10), START + 30 * MINUTE, no_receipt=True)
SECOND = Submitted(UUID(int=11), START + 90 * MINUTE, RECEIPT)
ENGAGEMENT = Engagement(
    START + 25 * MINUTE, "ministry", START + 28 * MINUTE, START + 92 * MINUTE
)
IDENTITY = Identity(1234, "Adams, Ann", 101, "ABCD-EFGH", True, "deliverable")


def test_emails_have_plain_names_and_outcomes():
    """Invitation, numbered reminders, receipt and test; outcomes in words."""
    assert email_name("initial") == "Invitation"
    assert email_name("reminder", 2) == "Reminder 2"
    assert email_name("reminder") == "Reminder"
    assert email_name("receipt") == "Submission receipt"
    assert email_name("family_test") == "Chosen-Family test email"
    assert INVITATION.outcome == "Delivered"
    assert REMINDER.outcome == "Not sure it arrived"
    assert replace_state(REMINDER, "retry_wait").outcome == "Still sending"
    assert replace_state(REMINDER, "permanent_failure").outcome == "Failed"
    assert replace_state(REMINDER, "mystery").outcome == "Unknown status"
    # Still on its way: dated by when it was planned.
    assert REMINDER.at == REMINDER.created_at
    assert RECEIPT.at == RECEIPT.finished_at


def replace_state(email, state):
    """``email`` in another outbox state."""
    return Email(email.id, email.purpose, email.name, state, email.created_at)


def test_summary_counts_submissions_and_names_the_last_email():
    """Submitted or not and when, and the email sent last."""
    shown = summary([RECEIPT, INVITATION, REMINDER], [FIRST, SECOND])
    assert (shown.count, shown.first_at, shown.last_at) == (2, FIRST.at, SECOND.at)
    assert shown.last_email == RECEIPT
    # A cancelled email was never sent, so the one before it is the last.
    cancelled = replace_state(RECEIPT, "cancelled")
    assert summary([INVITATION, cancelled], []).last_email == INVITATION
    assert summary([cancelled], []).last_email is None
    empty = summary([], [])
    assert (empty.count, empty.first_at, empty.last_email) == (0, None, None)


def test_sign_ins_are_attributed_to_the_mode_in_force():
    """A sign-in counts in the mode the system was in when it happened."""
    live = START + 10 * MINUTE
    transitions = [(live, "testing", "production")]
    before, at, after = live - MINUTE, live, live + MINUTE
    assert mode_at(transitions, before, "production") == "testing"
    # A transition at the very instant counts as made.
    assert mode_at(transitions, at, "production") == "production"
    assert mode_at([], before, "testing") == "testing"
    instants = [before, at, after]
    assert scope_sign_ins(
        instants, transitions, "production", "production", (None, None)
    ) == [at, after]
    # Testing keeps only the rehearsal epoch's lifetime.
    assert (
        scope_sign_ins(
            instants, transitions, "production", "testing", (before + MINUTE / 2, None)
        )
        == []
    )
    assert scope_sign_ins(
        instants, transitions, "production", "testing", (before, live)
    ) == [before]
    # A return to Testing and a second activation.
    again = [*transitions, (after, "production", "testing")]
    assert mode_at(again, after + MINUTE, "testing") == "testing"


def test_events_read_oldest_first_with_email_links():
    """Every line in time order; equal instants keep the Family's own order."""
    lines = events(
        [INVITATION, REMINDER, RECEIPT],
        [FIRST, SECOND],
        skips=[(START + 5 * MINUTE, UUID(int=99))],
        sign_ins=[START + 20 * MINUTE],
        forms=[START + 20 * MINUTE],
        engagement=ENGAGEMENT,
    )
    assert [(str(line.what), str(line.detail)) for line in lines] == [
        ("Invitation", "Delivered"),
        ("Invitation not sent", "already responded"),
        ("Signed in", "or a mail scanner checked the link"),
        ("Opened the form", ""),
        ("Got past the first step", ""),
        ("Reached the furthest step so far", "Ministry stewardship"),
        ("Submitted a response", "No receipt: no email address"),
        ("Reminder 1", "Not sure it arrived"),
        ("Submitted a response", ""),
        ("Submission receipt", "Delivered"),
    ]
    assert [line.at for line in lines] == sorted(line.at for line in lines)
    assert [line.message_id for line in lines if line.message_id] == [
        INVITATION.id,
        REMINDER.id,
        RECEIPT.id,
    ]
    # A skip replaces its own occurrence's cancelled email, and only that one.
    planned = Email(
        UUID(int=4), "initial", "Invitation", "cancelled", START, START, UUID(int=40)
    )
    other = Email(
        UUID(int=5), "reminder", "Reminder 1", "cancelled", START, START, UUID(int=50)
    )
    shown = events([planned, other], [], skips=[(START + MINUTE, UUID(int=40))])
    assert [(str(line.what), line.message_id) for line in shown] == [
        ("Reminder 1", other.id),
        ("Invitation not sent", None),
    ]
    # A Reminder WorkGroup skip (#861) replaces its reminder's cancelled email.
    shown = events(
        [planned, other], [], workgroup_skips=[(START + MINUTE, UUID(int=50))]
    )
    assert [(str(line.what), str(line.detail)) for line in shown] == [
        ("Invitation", planned.outcome),
        ("Reminder not sent", f"in the ParishSoft {REMINDER_WORKGROUP_NAME}"),
    ]
    # Nothing recorded: no lines; no engagement record: no step lines.
    assert events([], []) == ()
    assert len(events([], [FIRST], engagement=Engagement())) == 1


def test_scope_values_name_one_family_in_one_mode():
    """Production reads live and production; Testing names its epoch."""
    production = scope_values(ResponseScope(CAMPAIGN.pk), FAMILY)
    assert production == {
        "campaign": CAMPAIGN.pk,
        "family": FAMILY,
        "target": f"family:{FAMILY}",
        "epoch": None,
        "response_mode": "live",
        "mail_mode": "production",
    }
    epoch = uuid4()
    testing = scope_values(ResponseScope(CAMPAIGN.pk, "testing", epoch), FAMILY)
    assert (testing["epoch"], testing["response_mode"], testing["mail_mode"]) == (
        epoch,
        "test",
        "testing",
    )


def test_the_url_carries_only_closed_choices():
    """No name, DUID or code: the opaque record id, the filters and the sort."""
    path = reverse("admin:timeline", args=[FAMILY])
    assert path == f"/admin/reports/families/{FAMILY}/"
    assert timeline_url(FAMILY) == path
    # The default order (newest first) is left out; the other one is kept.
    assert timeline_url(FAMILY, sort="-when") == path
    assert timeline_url(FAMILY, "when") == path + "?sort=when"
    assert parse_query(QueryDict("")) == (TimelineQuery(), {})
    assert parse_query(
        QueryDict(
            "kind=emails&start=2054-10-01&end=2054-10-02&zone=America/New_York"
            "&sort=when&size=all"
        )
    ) == (
        TimelineQuery("emails", "2054-10-01", "2054-10-02", "America/New_York"),
        {"sort": "when", "size": "all"},
    )
    # The hub's tab is the tab component's own (#1678): any key is accepted
    # here, and an unknown one shows the first tab.
    assert parse_query(QueryDict("tab=family&kind=emails"))[0] == TimelineQuery(
        "emails"
    )
    assert parse_query(QueryDict("tab=nonsense")) == (TimelineQuery(), {})
    # Without a day the zone is dropped, so it never stays in the links.
    assert parse_query(QueryDict("zone=America/New_York"))[0] == TimelineQuery()
    for invalid in (
        # Production only since #1570: any mode is an unknown filter.
        "mode=production",
        "mode=testing",
        "kind=everything",
        "kind=all&kind=emails",
        "start=2054-10-1&zone=UTC",
        "start=20541001&zone=UTC",
        "start=2054-W40-1&zone=UTC",
        "start=1999-12-31&zone=UTC",
        "start=2054-10-02&end=2054-10-01&zone=UTC",
        # A day needs the browser's zone from the IANA catalog.
        "start=2054-10-01",
        "start=2054-10-01&zone=Mars/Olympus",
        "name=Adams",
        "sort=what",
        "size=25",
        "page=2",
        "tab=timeline&tab=family",
    ):
        with pytest.raises(ValueError):
            parse_query(QueryDict(invalid))


def test_days_are_browser_local_and_through_is_inclusive():
    """From and Through are whole days in the browser's zone, as UTC bounds.

    The day clocks fall back (November 1, 2054 in New York) is 25 hours long,
    so its Through bound is the next local midnight, not 24 hours later.
    """
    query = TimelineQuery("all", "2054-11-01", "2054-11-01", "America/New_York")
    assert query.bounds == (
        datetime(2054, 11, 1, 4, 0, tzinfo=UTC),
        datetime(2054, 11, 2, 5, 0, tzinfo=UTC),
    )
    assert TimelineQuery().bounds == (None, None)
    only_from = TimelineQuery("all", "2054-11-07", "", "UTC")
    assert only_from.bounds == (datetime(2054, 11, 7, tzinfo=UTC), None)
    assert TimelineQuery().carry == [] and not TimelineQuery().applied
    assert query.carry == [
        ("start", "2054-11-01"),
        ("end", "2054-11-01"),
        ("zone", "America/New_York"),
    ]
    assert TimelineQuery("sessions").carry == [("kind", "sessions")]


def test_open_form_needs_a_code_and_production():
    """Open form is offered only when its code would be accepted."""
    assert open_form(IDENTITY, testing_codes=False) == (True, "")
    assert open_form(IDENTITY, testing_codes=True) == (False, "testing")
    no_code = Identity(1234)
    assert open_form(no_code, testing_codes=False) == (False, "no_code")


def test_open_form_needs_the_current_campaign_and_an_eligible_family():
    """The hand-off's other refusals are known on the page too (#1331)."""
    assert open_form(IDENTITY, testing_codes=False, current=False) == (
        False,
        "not_current",
    )
    assert open_form(IDENTITY, testing_codes=False, eligible=False) == (
        False,
        "not_eligible",
    )
    assert Identity(1, reach="provider_suppressed").reach_label.startswith("No:")
    assert Identity(1, email_deliverable=True, reach="?").reach_label == "Yes"


def render(timeline, *, full=True, identity=IDENTITY, **options):
    """The page as the view renders it; codes are shown unless told otherwise."""
    options.setdefault("show_codes", True)
    with using("us_long"):
        context = page_context(
            CAMPAIGN,
            FAMILY,
            identity,
            timeline,
            START + timedelta(days=1),
            full=full,
            **options,
        )
        return render_to_string("stewardship/timeline.html", context)


TIMELINE = Timeline(
    summary([INVITATION, REMINDER, RECEIPT], [FIRST, SECOND]),
    events([INVITATION, REMINDER, RECEIPT], [FIRST, SECOND], engagement=ENGAGEMENT),
    ENGAGEMENT,
)


def test_administrator_page_shows_summary_and_timeline():
    """Data first; the timeline links each email to its Mail message page."""
    page = render(TIMELINE)
    assert "<title>" not in page or "Adams" not in page.split("</title>")[0]
    # The heading names the Family alone, above the hub's tabs (#1678); the
    # old page name is gone (#1570).
    assert "<h1>Adams, Ann</h1>" in page
    assert "Family timeline" not in page
    assert page.index("<h1>") < page.index('data-about-page="timeline"')
    # The header strip's Last portal action: the newest submission.
    assert (
        "<p>Family DUID 1234 · Envelope number 101 · Last portal action: "
        f'Submitted a response, <time datetime="{SECOND.at.isoformat()}"'
    ) in page
    # One shared table region: the filter bar and the When heading both
    # refresh it in place. No Production / Testing control is drawn.
    assert '<div id="table" data-table-region>' in page
    assert "mode-testing" not in page and "Testing rehearsal</a>" not in page
    assert (
        '<form id="table-filters" data-table-sync method="get" '
        f'action="/admin/reports/families/{FAMILY}/#table" class="filter-bar"'
    ) in page
    assert '<input type="hidden" name="size" value="all">' in page
    assert '<input type="hidden" name="sort" value="-when">' in page
    for kind in ("all", "form", "emails", "sessions"):
        assert f'<option value="{kind}"' in page
    assert '<option value="all" selected>All</option>' in page
    assert 'name="mode"' not in page
    # When is a sort heading, newest first by default; choosing it reverses.
    assert 'aria-sort="descending" data-sort-column="when"' in page
    assert 'href="?size=all&amp;sort=when#table"' in page
    assert "oldest first" not in page
    assert "Yes, <time" in page and "2 times; most recently" in page
    assert "Submission receipt, <time" in page and "<strong>Delivered</strong>" in page
    assert "<code>ABCD-EFGH</code>" in page
    # Open form posts its hand-off (#529); the code is in no URL.
    assert (
        f'action="/admin/reports/families/{FAMILY}/open-form/"'
        ' target="_blank" rel="noopener" data-open-form data-submit-repeatable>'
    ) in page
    assert "#code=" not in page
    # The directory's notice is not repeated here (Administrator, #590).
    assert "signs this browser in" not in page and "data-open-form-notice" not in page
    assert "Campaign email can reach this Family" in page
    assert "Ministry stewardship, <time" in page
    assert f'<a href="/admin/mail/sent/{INVITATION.id}/">Invitation</a>' in page
    assert page.count("<tbody>") == 1 and page.count("<tr>") == 1 + len(TIMELINE.events)
    # Newest first: the receipt (the latest event) leads, the invitation ends.
    rows = re.findall(r'<tr><td><time datetime="([^"]+)"', page)
    assert rows == sorted(rows, reverse=True) and len(rows) == len(TIMELINE.events)
    assert page.index("Submission receipt</a>") < page.index(">Invitation</a>")
    # Every time is a browser-local instant, never labelled UTC.
    assert page.count("<time ") == page.count("data-local-instant")
    assert " UTC" not in page
    # No inline script or style under the CSP.
    assert not re.search(r"<script(?![^>]*\bsrc=)", page)
    assert " style=" not in page and "<style" not in page


def test_the_when_heading_reverses_the_order():
    """sort=when shows the oldest first, and the heading offers newest first."""
    page = render(TIMELINE, values={"sort": "when", "size": "all"})
    rows = re.findall(r'<tr><td><time datetime="([^"]+)"', page)
    assert rows == sorted(rows)
    assert 'aria-sort="ascending" data-sort-column="when"' in page
    assert 'href="?size=all&amp;sort=-when#table"' in page
    # The filter bar keeps the chosen order.
    assert '<input type="hidden" name="sort" value="when">' in page


def test_the_filters_narrow_the_lines_and_the_heading_keeps_them():
    """Kind and days narrow the table only; the summary covers everything."""
    query = TimelineQuery("emails", "2026-10-03", "2026-10-03", "UTC")
    page = render(TIMELINE, query=query)
    rows = re.findall(r"<th scope=\"row\">(?:<a [^>]*>)?([^<]+)", page)
    assert rows == ["Submission receipt", "Reminder 1", "Invitation"]
    assert "2 times; most recently" in page
    assert '<option value="emails" selected>Emails</option>' in page
    assert 'name="start" value="2026-10-03"' in page
    # The When heading carries the filters back.
    assert (
        'href="?kind=emails&amp;start=2026-10-03&amp;end=2026-10-03&amp;zone=UTC'
        '&amp;size=all&amp;sort=when#table"'
    ) in page
    # Filtered out: the empty table says so and names the way back.
    page = render(TIMELINE, query=TimelineQuery("sessions"))
    assert "No lines match these filters." in page
    assert "Nothing recorded for this Family yet." not in page


def test_staff_page_is_the_reduced_view():
    """Submitted, last email and code with Open form; nothing else."""
    reduced = Timeline(TIMELINE.summary)
    page = render(reduced, full=False)
    assert "Yes, <time" in page and "Submission receipt, <time" in page
    assert "<code>ABCD-EFGH</code>" in page and "data-open-form" in page
    for absent in (
        "data-table-region",
        "table-filters",
        "/admin/mail/sent/",
        "Timeline</h2>",
        "Campaign email can reach",
        "Furthest step",
        "Last seen",
        "A sign-in can also be a mail scanner",
    ):
        assert absent not in page, absent
    # The Last portal action and its About text are for Staff too
    # (Administrator decision, 2026-10-18).
    assert "Last portal action: None recorded" in page
    assert "Last portal action is the newest action" in page


def test_open_form_is_unavailable_with_its_reason():
    """In Testing mode, or without a code, the button is disabled and says why."""
    page = render(TIMELINE, testing_codes=True, family_test_url="/admin/test")
    assert (
        '<button type="button" disabled aria-describedby="open-form-reason">'
        "Open form</button>"
    ) in page
    assert "accepts only Testing codes" in page
    assert '<a href="/admin/test">Try the Family form as a chosen Family</a>' in page
    assert "data-open-form" not in page
    # Without the page's permission the view passes no link.
    assert "Try the Family form" not in render(TIMELINE, testing_codes=True)
    page = render(TIMELINE, identity=Identity(1234, "Adams, Ann"))
    assert "Unavailable for this campaign" in page
    assert "this Family has no code for this campaign" in page
    page = render(TIMELINE, eligible=False)
    assert "data-open-form" not in page
    assert "no longer lists this Family as active and registered" in page
    page = render(TIMELINE, current=False)
    assert "this is not the current campaign" in page


def test_codes_are_left_out_for_roles_without_them():
    """No Family code permission: no code, no Open form, no reason."""
    page = render(TIMELINE, show_codes=False, identity=Identity(1234, "Adams, Ann"))
    for absent in ("Family code", "Open form", "ABCD", "open-form-reason"):
        assert absent not in page, absent
    assert "Yes, <time" in page


def test_empty_states_say_what_to_do():
    """No records yet; no Testing rehearsal; no name in ParishSoft."""
    page = render(Timeline(summary([], [])), identity=Identity(1234))
    assert "Not yet" in page and "None sent yet" in page
    assert "Nothing recorded for this Family yet." in page
    assert "No lines match" not in page
    # No name: the heading is the page's name alone, the badge leads the line.
    assert "<h1>Timeline</h1>" in page
    assert (
        'data-source-status="missing">Not in ParishSoft</span> · Family DUID 1234 ·'
        in page
    )
    assert "None recorded" in page and "Never" in page


def test_staff_entered_responses_are_marked_without_a_name():
    """Entered by Staff shows on the event, combined with no receipt (#529).

    The summary counts how many of the Family's responses Staff entered:
    "Entered by Staff for the Family" when all were, a count when only some
    were, and nothing when none was.
    """
    staffed = Submitted(FIRST.id, FIRST.at, no_receipt=True, by_staff=True)
    lines = events([], [staffed, SECOND])
    assert [str(line.detail) for line in lines] == [
        "Entered by Staff for the Family; No receipt: no email address",
        "",
    ]
    partial = summary([], [staffed, SECOND])
    assert (partial.count, partial.by_staff) == (2, 1)
    page = render(Timeline(partial, lines))
    assert "1 entered by Staff for the Family" in page
    whole = summary([], [staffed])
    page = render(Timeline(whole, events([], [staffed])))
    assert "· Entered by Staff for the Family" in page
    assert "1 entered by Staff" not in page
    assert "entered by Staff" not in render(TIMELINE).replace(
        "marked as entered by Staff", ""
    )


def test_a_staff_sign_in_reads_staff_opened_the_form():
    """An Open form sign-in is not a link the Family followed (#795)."""
    # Decided per sign-in, so two at the same instant are never confused.
    same = START + 20 * MINUTE
    lines = events([], [], sign_ins=[(same, True), (same, False)])
    assert [(str(line.what), str(line.detail)) for line in lines] == [
        ("Staff opened the form", "through Open form, signed in as the Family"),
        ("Signed in", "or a mail scanner checked the link"),
    ]
    # Never names who: the line carries no actor.
    page = render(Timeline(summary([], []), lines))
    assert "Staff opened the form" in page and "admin@" not in page


def test_sign_outs_and_session_ends_are_session_lines():
    """Signed out and Session ended follow the Family's steps at one instant."""
    same = START + 20 * MINUTE
    lines = events(
        [INVITATION],
        [],
        sign_ins=[same],
        forms=[same],
        session_ends=[(same, "family_logout"), (same + MINUTE, "family_session_ended")],
    )
    assert [(str(line.what), line.kind) for line in lines] == [
        ("Invitation", "emails"),
        ("Signed in", "sessions"),
        ("Opened the form", "form"),
        ("Signed out", "sessions"),
        ("Session ended", "sessions"),
    ]
    assert "ended by the system" in str(lines[-1].detail)


def test_a_session_replaced_by_a_new_sign_in_ends_before_it():
    """A new sign-in's revocation of the earlier session sorts before it."""
    # The login path revokes the earlier session in its own transaction, so
    # the end has the new sign-in's instant; it must not read as the new
    # session ending. An end at another instant keeps the generic wording.
    first, again = START + 20 * MINUTE, START + 30 * MINUTE
    lines = events(
        [],
        [],
        sign_ins=[first, again],
        session_ends=[(again, "family_session_ended"), (again, "family_logout")],
    )
    assert [(str(line.what), str(line.detail)) for line in lines] == [
        ("Signed in", "or a mail scanner checked the link"),
        ("Earlier session ended", "replaced by a new sign-in"),
        ("Signed in", "or a mail scanner checked the link"),
        ("Signed out", ""),
    ]
    assert {line.kind for line in lines} == {"sessions"}


def test_the_page_shows_a_replaced_session_ending_first_both_ways():
    """In both sort directions the replaced end reads as before the sign-in."""
    # Newest first lists the later line above, so the earlier session's end
    # must sit below the new sign-in there and above it oldest first. The
    # newest-first view is exactly the oldest-first one reversed, so the
    # sign-out at the same instant is listed above its sign-in too.
    first, again = START + 20 * MINUTE, START + 30 * MINUTE
    lines = events(
        [],
        [],
        sign_ins=[first, again],
        session_ends=[(again, "family_session_ended"), (again, "family_logout")],
    )
    timeline = Timeline(summary([], []), lines, None)

    def shown(sort):
        """The rendered "What happened" cells for one sort, in row order."""
        page = render(timeline, values={"sort": sort, "size": "all"})
        return re.findall(r'<th scope="row">(.*?)</th>', page)

    assert shown("when") == [
        "Signed in",
        "Earlier session ended",
        "Signed in",
        "Signed out",
    ]
    assert shown("-when") == [
        "Signed out",
        "Signed in",
        "Earlier session ended",
        "Signed in",
    ]


def test_narrow_keeps_one_kind_within_half_open_bounds():
    """``narrow`` keeps the kind and the [start, end) interval, in order."""
    lines = TIMELINE.events
    assert narrow(lines) == lines
    assert {line.kind for line in narrow(lines, "form")} == {"form"}
    assert [str(line.what) for line in narrow(lines, "emails")] == [
        "Invitation",
        "Reminder 1",
        "Submission receipt",
    ]
    assert narrow(lines, "sessions") == ()
    # The receipt finished at START + 91 minutes: an end bound there leaves
    # it out, a start bound there keeps only it.
    at = START + 91 * MINUTE
    assert RECEIPT.id not in {
        line.message_id for line in narrow(lines, "all", (None, at))
    }
    assert [line.message_id for line in narrow(lines, "all", (at, None))] == [
        RECEIPT.id
    ]


def test_last_portal_action_ignores_what_an_email_reader_makes():
    """Only the Family's own steps count, never a sign-in or form open (#1678).

    A mail scanner fetching the personal link signs in and opens the form
    exactly as the Family would, so those lines (and the system's session
    end, every email and a response Staff entered) are never the Family's
    Last portal action, however late they are.
    """
    staffed = Submitted(UUID(int=12), START + 50 * MINUTE, by_staff=True)
    out = START + 40 * MINUTE
    later = START + 60 * MINUTE
    lines = events(
        [replace_state(REMINDER, "delivered")],
        [FIRST, staffed],
        sign_ins=[later, (later, True)],
        forms=[later],
        engagement=Engagement(START + 25 * MINUTE, "ministry", START + 28 * MINUTE),
        session_ends=[(out, "family_logout"), (later, "family_session_ended")],
    )
    action = Timeline(summary([], [FIRST, staffed]), lines).last_action
    assert (str(action.what), action.at) == ("Signed out", out)
    # Without the sign-out, the newest is the Family's own submission, then
    # its furthest step (built after the first step at a shared instant).
    lines = events([], [FIRST], sign_ins=[later], forms=[later])
    assert str(Timeline(summary([], [FIRST]), lines).last_action.what) == (
        "Submitted a response"
    )
    same = START + 25 * MINUTE
    lines = events([], [], engagement=Engagement(same, "census", same))
    assert str(Timeline(summary([], []), lines).last_action.what) == (
        "Reached the furthest step so far"
    )
    # The first step is reported as the form page loads, with nothing
    # chosen (a script-running mail scanner too): never an action.
    lines = events([], [], engagement=Engagement(None, "welcome", same))
    assert Timeline(summary([], []), lines).last_action is None
    # A Sign out in a session Staff opened through Open form is Staff's.
    lines = events(
        [],
        [FIRST],
        sign_ins=[(later, True)],
        session_ends=[(later + MINUTE, "family_logout", True)],
    )
    assert [str(line.what) for line in lines][-1] == "Signed out"
    assert str(Timeline(summary([], [FIRST]), lines).last_action.what) == (
        "Submitted a response"
    )
    # Nothing but a link fetch: none recorded.
    lines = events([INVITATION], [], sign_ins=[later], forms=[later])
    assert Timeline(summary([INVITATION], []), lines).last_action is None
    page = render(Timeline(summary([INVITATION], []), lines))
    assert "Last portal action: None recorded</p>" in page
    # Staff's reduced view reads none of these lines and shows no value.
    assert Timeline(TIMELINE.summary).last_action is None


def test_hub_tabs_follow_the_campaign_modules():
    """Timeline, Family, the Members, Financial, then the last tab (#1678)."""
    from parishkit.stewardship.reports.hub_census import HubCensus, Subject
    from parishkit.stewardship.reports.timeline_views import hub_sections, hub_tabs

    assert hub_sections({"modules": ["census", "financial", "ministry"]}) == (
        "family",
        "members",
        "financial",
        "other",
    )
    assert hub_sections({"modules": ["financial"]}) == ("financial",)
    assert hub_sections({"modules": ["census"], "additional_information": True}) == (
        "family",
        "members",
        "other",
    )
    assert hub_sections({"modules": ["ministry"]}) == ("members", "other")
    path = reverse("admin:timeline", args=[FAMILY])
    members = (Subject("member-1", "Adams, Ann"), Subject("member-2", "Adams, Bo"))
    census = HubCensus(Subject("family", "Family"), members)
    tabs = hub_tabs(path, {}, ("family", "members", "other"), census)
    assert [(tab.key, str(tab.label), str(tab.unavailable)) for tab in tabs.tabs] == [
        ("timeline", "Timeline", ""),
        ("family", "Family", ""),
        ("member-1", "Adams, Ann", ""),
        ("member-2", "Adams, Bo", ""),
        ("other", "Cannot participate and Additional information", ""),
    ]
    assert tabs.selected == "timeline" and tabs.tabs[0].href == path + "?tab=timeline"
    assert hub_tabs(path, {"tab": "member-2"}, ("members",), census).selected == (
        "member-2"
    )
    # Before any response the Family tab is still drawn, with no Member tabs.
    tabs = hub_tabs(path, {"tab": "family"}, ("family", "members"))
    assert [tab.key for tab in tabs.tabs] == ["timeline", "family"]
    assert tabs.selected == "family"
    # Without census, Member tabs are available too: their panels show
    # Ministry joins and leaves (slice 4). No hub tab is "Coming soon".
    ministry = HubCensus(members=members, census=False)
    tabs = hub_tabs(path, {"tab": "member-1"}, ("members", "financial"), ministry)
    assert [tab.unavailable for tab in tabs.tabs] == [""] * 4
    assert tabs.selected == "member-1" and tabs.tabs[3].key == "financial"
    # A tab that is not drawn, or an unknown one, opens the Timeline tab.
    for asked in ("member-1", "family", "nonsense"):
        assert hub_tabs(path, {"tab": asked}, ("other",)).selected == "timeline"


def test_the_page_is_a_hub_with_the_timeline_tab():
    """The header strip sits above the tabs; the Timeline panel holds the rest."""
    page = render(TIMELINE, sections=("financial", "other"))
    strip = page.index("Last portal action:")
    row = page.index('role="tablist" aria-label="Family information"')
    assert page.index("<h1>") < strip < row
    # The strip has no directory link: the Admin menu has it (2026-10-19).
    assert "Active parishioner family directory</a>" not in page[strip:row]
    # The summary, the filter bar, the table region and the export are all
    # inside the Timeline's panel, which is the one drawn.
    panel = page.index('id="family-tabs-panel-timeline"')
    for part in ('id="summary-heading"', 'id="table-filters"', 'id="table"'):
        assert row < panel < page.index(part), part
    assert page.count("data-tab-loaded") == 1
    assert (
        f'href="/admin/reports/families/{FAMILY}/?tab=timeline'
        '#family-tabs-panel-timeline"'
    ) in page
    # Every tab is built: none is unavailable, and only the chosen one's
    # panel is drawn.
    assert 'aria-disabled="true"' not in page and "Coming soon" not in page
    for key in ("financial", "other"):
        assert f'id="family-tabs-panel-{key}"' in page, key
    for label in ("Financial", "Cannot participate and Additional"):
        assert f">{label}" in page, label
    # A campaign without any content tab: Timeline alone.
    page = render(TIMELINE)
    assert page.count('role="tab"') == 1
    # Staff get the tabs, the summary and the Last portal action
    # (Administrator decision, 2026-10-18), from the portal lines alone.
    portal = tuple(line for line in TIMELINE.events if line.portal)
    page = render(
        Timeline(TIMELINE.summary, portal), full=False, sections=("financial",)
    )
    assert 'id="family-tabs-panel-timeline"' in page
    assert 'id="family-tabs-tab-financial"' in page
    assert (
        "Last portal action: "
        f'Submitted a response, <time datetime="{SECOND.at.isoformat()}"'
    ) in page
    assert 'id="table-filters"' not in page
    page = render(Timeline(TIMELINE.summary), full=False, sections=("financial",))
    assert "Last portal action: None recorded</p>" in page
