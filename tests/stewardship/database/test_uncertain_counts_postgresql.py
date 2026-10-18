"""Counts that link to Sent mail list exactly what they count (#1576).

The status bar's "Not sure it arrived — review needed" count, Home's Sent
mail step and the Background work counts endpoint read the operating mode's
uncertain emails an Administrator can resolve (Sent mail's Not sure it
arrived — review needed), and link Sent mail filtered to that mode; the
other mode's get a separate link and are never added in. An operational or
security notice, or an archived campaign's email, offers no resolution and
is in no such count. System health's Waiting to retry counts and links
Family invitations and reminders, split by mode the same way (#1632). These
tests follow each link on the real pages, on a Production and a Testing
system, and compare the number shown with the rows Sent mail lists.

Uncertain and waiting emails are seeded by setting the state of queued
emails with the SQL guards paused (``unguarded``): what is under test is
which emails are counted and listed, not how an email becomes uncertain.
"""

import re
from datetime import timedelta
from html import unescape
from uuid import uuid4

import pytest
from django.utils import timezone

from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.critical_events import ACKNOWLEDGE_LIMIT
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_metadata import (
    alert_counts,
    matching,
    uncertain_by_mode,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.system_health import _retries

from .auth_builders import signed_in, unguarded
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_sent_mail_postgresql import family_message, notices

pytestmark = pytest.mark.django_db(transaction=True)

# One status-bar link: its address, which one it is, whether it is hidden,
# and the number it shows.
STATUS_LINK = re.compile(
    r'<a href="([^"]+)" data-delivery-(here|other)( hidden)?>[^<]*'
    r"<span data-delivery-unknown(?:-other)?>([0-9,]+)</span></a>"
)
ROW = re.compile(r'href="/admin/mail/sent/([0-9a-f-]{36})/"')


def set_state(ids, state):
    """Put the emails ``ids`` in ``state``, as only a superuser could.

    Both states follow a first attempt, so the email gets one, as the
    table's checks require (``outbox_attempt_shape``), pinned to an existing
    Task run (the triggers that guard a real attempt are paused).
    """
    now = timezone.now()
    with unguarded():
        OutboxMessage.objects.filter(pk__in=ids).update(
            state=state,
            attempt=1,
            run_id=TaskRun.objects.values_list("pk", flat=True).first(),
            task_fence=1,
            worker_id=uuid4(),
            submitted_at=now,
            provider_deadline=now + timedelta(hours=1),
            not_before=now,
        )


def get(browser, path):
    """``path`` as the web login serves it: (status, HTML)."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(path)
    return response.status_code, response.content.decode()


def rows(browser, href):
    """The emails Sent mail lists at the link ``href`` (one page is enough)."""
    status, html = get(browser, unescape(href))
    assert status == 200, href
    return set(ROW.findall(html))


def status_links(html):
    """The status bar's links: {"here"/"other": (href, hidden, count)}."""
    return {
        kind: (href, bool(hidden), int(count.replace(",", "")))
        for href, kind, hidden, count in STATUS_LINK.findall(html)
    }


# What the second link and Home's second count say, by the other mode.
OTHER_NAMES = {"testing": "Testing", "production": "Production"}


@pytest.fixture(params=["production", "testing"])
def system(request, response_service, google):
    """A system in the parametrized operating mode, with queued emails.

    Returns ``(mode, browser, emails)``: ``emails`` maps a name to an email
    id: ``other`` (a Family email of the other mode), ``initial`` and
    ``reminder`` (Family emails of the operating mode), and ``notice``, an
    operational notice to an Administrator, which Sent mail lists but no
    Administrator can resolve. A Production system makes its Testing email
    before Production is activated; a Testing system has a Production one
    seeded directly, as a system that left Production could keep.
    """
    mode = request.param
    harness = response_service
    if mode == "production":
        emails = {"other": family_message(harness, mode="testing")}
        harness = activate_response_service(harness)
    else:
        emails = {"other": family_message(harness, mode="production")}
    browser, _ = signed_in()
    emails |= {
        "initial": family_message(harness, mode=mode),
        "reminder": family_message(harness, mode=mode, purpose="reminder"),
    }
    notices(harness)
    emails["notice"] = OutboxMessage.objects.filter(purpose="operational").values_list(
        "pk", flat=True
    )[0]
    return mode, browser, {name: str(value) for name, value in emails.items()}


@pytest.mark.parametrize(
    ("uncertain", "here", "other"),
    [
        # Both modes: two links, never one summed number. The uncertain
        # notice is in neither: nobody can resolve it (#1576 decision).
        (("other", "initial", "reminder", "notice"), 2, 1),
        # Only the other mode: its link alone; the operating mode's hides.
        (("other",), 0, 1),
        # Only the operating mode: no second link.
        (("initial",), 1, 0),
        # Only a notice: nothing to review, so the warning hides.
        (("notice",), 0, 0),
        # None: the warning hides.
        ((), 0, 0),
    ],
)
def test_each_status_link_lists_exactly_what_it_counts(system, uncertain, here, other):
    """Every count that links Sent mail equals the rows the link opens."""
    mode, browser, emails = system
    other_mode = "testing" if mode == "production" else "production"
    set_state([emails[name] for name in uncertain], "delivery_unknown")
    expected = {mode: here, other_mode: other}
    # The chrome's one statement and the endpoint's query agree with each
    # other (a mode with none has no entry).
    by_mode = {key: count for key, count in expected.items() if count}
    assert alert_counts(database_now(), limit=ACKNOWLEDGE_LIMIT)[2] == by_mode
    assert uncertain_by_mode() == by_mode
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        counts = browser.get("/admin/background/counts").json()
    assert (counts["delivery_unknown"], counts["delivery_unknown_other"]) == (
        here,
        other,
    )
    status, html = get(browser, "/admin/")
    assert status == 200
    warning = re.search(r"<p class=\"notice\" data-delivery-warning( hidden)?>", html)
    assert bool(warning.group(1)) == (here + other == 0)
    assert f"Not sure it arrived in {OTHER_NAMES[other_mode]}:" in html
    links = status_links(html)
    # The other mode's link comes first, so it never slides (#1576 review).
    assert list(links) == ["other", "here"]
    for kind, link_mode, count in (
        ("here", mode, here),
        ("other", other_mode, other),
    ):
        href, hidden, shown = links[kind]
        assert f"state=needs_review&amp;mode={link_mode}" in href
        assert (hidden, shown) == (count == 0, count)
        # The number shown is exactly the rows its link lists, and they are
        # the emails of that mode that were made uncertain (never a notice).
        listed = rows(browser, href)
        assert len(listed) == shown
        assert listed == {
            emails[name]
            for name in uncertain
            if name != "notice" and (name == "other") == (link_mode == other_mode)
        }
    # Sent mail's plain Not sure it arrived still lists the notice.
    if "notice" in uncertain:
        assert emails["notice"] in rows(
            browser, f"/admin/mail/sent/?state=delivery_unknown&mode={mode}"
        )
    # Home's Sent mail step links the same two lists with the same counts.
    # A Testing system's draft campaign has no such step (Home page spec).
    step = home_step(html)
    if mode == "testing":
        assert step is None
        return
    links = dict(
        (text, href)
        for href, text in re.findall(r'<a href="([^"]+)">([^<]*)</a>', step)
    )
    if here:
        assert f"Sent mail</a> — {here} may not have arrived" in step
        assert len(rows(browser, links["Sent mail"])) == here
        assert f"mode={mode}" in links["Sent mail"]
    else:
        assert links["Sent mail"] == "/admin/mail/sent/"
    other_text = f"{other} in {OTHER_NAMES[other_mode]} may not have arrived"
    if other:
        assert f"mode={other_mode}" in links[other_text]
        assert len(rows(browser, links[other_text])) == other
    else:
        assert set(links) == {"Sent mail"}


def test_archived_campaign_uncertain_emails_need_no_review(system):
    """An archived campaign's uncertain email offers no resolution, so it is
    in no "review needed" count and no list that count links (#1576)."""
    mode, browser, emails = system
    set_state([emails["initial"]], "delivery_unknown")
    assert uncertain_by_mode() == {mode: 1}
    with unguarded():
        Campaign.objects.update(state="archived")
    assert uncertain_by_mode() == {}
    assert alert_counts(database_now(), limit=ACKNOWLEDGE_LIMIT)[2] == {}
    assert not matching(state="needs_review").exists()
    # Sent mail's plain Not sure it arrived still lists it.
    assert matching(state="delivery_unknown", mode=mode).count() == 1


def test_system_health_retries_count_each_mode_apart(system):
    """Waiting to retry counts the operating mode's Family emails, the other
    mode's apart, and each count is what its Sent mail link lists: a notice
    waiting to retry is in neither (#1632)."""
    mode, browser, emails = system
    other_mode = "testing" if mode == "production" else "production"
    set_state([emails["other"], emails["initial"], emails["notice"]], "retry_wait")
    count, other, due = _retries(mode)
    assert (count, other) == (1, 1) and due is not None
    assert _retries(other_mode)[:2] == (1, 1)
    # The notice is listed under Waiting to retry, but not as Family email.
    assert emails["notice"] in rows(
        browser, f"/admin/mail/sent/?state=retry_wait&mode={mode}"
    )
    status, html = get(browser, "/admin/system/health/")
    assert status == 200
    # The page draws the panel's links in more than one place; each once.
    hrefs = list(
        dict.fromkeys(re.findall(r'href="([^"]*state=retry_wait[^"]*)"', html))
    )
    assert len(hrefs) == 2
    for href, link_mode, name in (
        (hrefs[0], mode, "initial"),
        (hrefs[1], other_mode, "other"),
    ):
        assert f"purpose=family&amp;mode={link_mode}" in href
        assert rows(browser, href) == {emails[name]}


def home_step(html):
    """Home's Sent mail next step, as its list item's markup, or None."""
    for item in re.findall(r"<li data-next-step>(.*?)</li>", html, re.S):
        if ">Sent mail</a>" in item:
            return item
    return None
