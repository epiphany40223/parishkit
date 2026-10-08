"""The Family timeline's summary, events and page, without a database (#477).

The view's admission, read guard, Staff reduction and audit are covered
against PostgreSQL in ``database/test_family_timeline_postgresql.py``.
"""

import re
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.reports.family_timeline import (
    Email,
    Engagement,
    Submitted,
    Timeline,
    email_name,
    events,
    mode_at,
    scope_sign_ins,
    scope_values,
    summary,
)
from parishkit.stewardship.reports.family_timeline_views import (
    Identity,
    open_form,
    page_context,
    parse_query,
    timeline_url,
)
from parishkit.stewardship.reports.response_metrics import ResponseScope
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
    """No name, DUID or code: the opaque record id, the mode and the sort."""
    path = f"/admin/reports/{CAMPAIGN.pk}/families/{FAMILY}/"
    assert timeline_url(CAMPAIGN.pk, FAMILY) == path
    assert timeline_url(CAMPAIGN.pk, FAMILY, "testing") == path + "?mode=testing"
    # The default order (newest first) is left out; the other one is kept.
    assert timeline_url(CAMPAIGN.pk, FAMILY, sort="-when") == path
    assert timeline_url(CAMPAIGN.pk, FAMILY, "testing", "when") == (
        path + "?mode=testing&sort=when"
    )
    assert parse_query(QueryDict("")) == ("production", {})
    assert parse_query(QueryDict("mode=testing&sort=when&size=all")) == (
        "testing",
        {"sort": "when", "size": "all"},
    )
    for invalid in (
        "mode=live",
        "mode=testing&mode=production",
        "name=Adams",
        "sort=what",
        "size=25",
        "page=2",
    ):
        with pytest.raises(ValueError):
            parse_query(QueryDict(invalid))


def test_open_form_needs_a_code_and_production():
    """Open form is offered only when its code would be accepted."""
    assert open_form(IDENTITY, testing_codes=False) == (True, "")
    assert open_form(IDENTITY, testing_codes=True) == (False, "testing")
    no_code = Identity(1234)
    assert open_form(no_code, testing_codes=False) == (False, "no_code")
    assert Identity(1, reach="provider_suppressed").reach_label.startswith("No:")
    assert Identity(1, email_deliverable=True, reach="?").reach_label == "Yes"


def render(timeline, *, full=True, mode="production", identity=IDENTITY, **options):
    """The page as the view renders it; codes are shown unless told otherwise."""
    options.setdefault("show_codes", True)
    with using("us_long"):
        context = page_context(
            CAMPAIGN,
            FAMILY,
            identity,
            mode,
            timeline,
            START + timedelta(days=1),
            full=full,
            **options,
        )
        return render_to_string("stewardship/family-timeline.html", context)


TIMELINE = Timeline(
    summary([INVITATION, REMINDER, RECEIPT], [FIRST, SECOND]),
    events([INVITATION, REMINDER, RECEIPT], [FIRST, SECOND], engagement=ENGAGEMENT),
    ENGAGEMENT,
)


def test_administrator_page_shows_summary_and_timeline():
    """Data first; the timeline links each email to its Mail message page."""
    page = render(TIMELINE)
    assert "<title>" not in page or "Adams" not in page.split("</title>")[0]
    assert "<h1>Family timeline</h1>" in page
    assert page.index("<h1>") < page.index('data-about-page="family-timeline"')
    assert (
        "<strong>Adams, Ann</strong> · Family DUID 1234 · Envelope number 101" in page
    )
    # One shared table region: the mode switch and the When heading both
    # refresh it in place.
    assert '<div id="table" data-table-region>' in page
    assert (
        f'href="/admin/reports/{CAMPAIGN.pk}/families/{FAMILY}/?mode=testing'
        '#table" data-in-place="mode-testing"'
    ) in page
    # When is a sort heading, newest first by default; choosing it reverses.
    assert 'aria-sort="descending" data-sort-column="when"' in page
    assert 'href="?size=all&amp;sort=when#table"' in page
    assert "oldest first" not in page
    assert "Yes, <time" in page and "2 times; most recently" in page
    assert "Submission receipt, <time" in page and "<strong>Delivered</strong>" in page
    assert "<code>ABCD-EFGH</code>" in page
    # Open form posts its hand-off (#529); the code is in no URL.
    assert (
        f'action="/admin/reports/{CAMPAIGN.pk}/families/{FAMILY}/open-form"'
        ' target="_blank" rel="noopener" data-open-form data-submit-repeatable>'
    ) in page
    assert "#code=" not in page
    # The directory's notice is not repeated here (Administrator, #590).
    assert "signs this browser in" not in page and "data-open-form-notice" not in page
    assert "Campaign email can reach this Family" in page
    assert "Ministry stewardship, <time" in page
    assert f'<a href="/admin/deliveries/{INVITATION.id}">Invitation</a>' in page
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
    # The mode switch keeps the chosen order.
    assert "?mode=testing&amp;sort=when#table" in page


def test_an_empty_testing_view_keeps_the_chosen_sort():
    """With no rehearsal there is no table, but Production keeps sort=when."""
    page = render(None, mode="testing", values={"sort": "when", "size": "all"})
    assert "no Testing rehearsal now" in page
    assert (
        f'href="/admin/reports/{CAMPAIGN.pk}/families/{FAMILY}/?sort=when'
        '#table" data-in-place="mode-production"'
    ) in page


def test_staff_page_is_the_reduced_view():
    """Submitted, last email and code with Open form; nothing else."""
    reduced = Timeline(TIMELINE.summary)
    page = render(reduced, full=False)
    assert "Yes, <time" in page and "Submission receipt, <time" in page
    assert "<code>ABCD-EFGH</code>" in page and "data-open-form" in page
    for absent in (
        "data-in-place=",
        "/admin/deliveries/",
        "Timeline</h2>",
        "Campaign email can reach",
        "Furthest step",
        "Last seen",
        "mail scanner",
    ):
        assert absent not in page, absent


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
    assert "Not in the latest ParishSoft data · Family DUID 1234 ·" in page
    assert "None recorded" in page and "Never" in page
    page = render(None, mode="testing")
    assert "no Testing rehearsal now" in page and "Summary" not in page
    assert 'aria-current="page">Testing rehearsal</a>' in page


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
