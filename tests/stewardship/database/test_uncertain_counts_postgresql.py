"""Counts that link to Sent mail list exactly what they count (#1576).

The status bar's "Not sure it arrived — review needed" count, Home's Sent
mail step and the Background work counts endpoint read the operating mode's
uncertain emails, and link Sent mail filtered to that mode; the other
mode's get a separate link and are never added in. System health's
Waiting to retry is split the same way. These tests follow each link on the
real pages and compare the number shown with the rows Sent mail lists.

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
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_metadata import (
    alert_counts,
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


@pytest.fixture
def production_system(response_service, google):
    """A Production system with Family emails in both modes, all queued.

    Returns ``(browser, emails)``: ``emails`` maps a name to an email id:
    ``testing`` (made before Production was activated), ``initial`` and
    ``reminder`` (Production Family emails), and ``notice``, an operational
    notice to an Administrator, which Sent mail lists too.
    """
    testing = family_message(response_service, mode="testing")
    harness = activate_response_service(response_service)
    browser, _ = signed_in()
    emails = {
        "testing": testing,
        "initial": family_message(harness),
        "reminder": family_message(harness, purpose="reminder"),
    }
    notices(harness)
    emails["notice"] = OutboxMessage.objects.filter(purpose="operational").values_list(
        "pk", flat=True
    )[0]
    return browser, {name: str(value) for name, value in emails.items()}


@pytest.mark.parametrize(
    ("uncertain", "here", "other"),
    [
        # Both modes: two links, never one summed number.
        (("testing", "initial", "reminder", "notice"), 3, 1),
        # Only the other mode: its link alone; the operating mode's hides.
        (("testing",), 0, 1),
        # Only the operating mode: no second link.
        (("initial",), 1, 0),
        # None: the warning hides.
        ((), 0, 0),
    ],
)
def test_each_status_link_lists_exactly_what_it_counts(
    production_system, uncertain, here, other
):
    """Every count that links Sent mail equals the rows the link opens."""
    browser, emails = production_system
    set_state([emails[name] for name in uncertain], "delivery_unknown")
    expected = {"production": here, "testing": other}
    # The chrome's one statement and the endpoint's query agree with each
    # other (a mode with none has no entry).
    by_mode = {mode: count for mode, count in expected.items() if count}
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
    links = status_links(html)
    assert set(links) == {"here", "other"}
    for kind, mode, count in (
        ("here", "production", here),
        ("other", "testing", other),
    ):
        href, hidden, shown = links[kind]
        assert f"mode={mode}" in href
        assert (hidden, shown) == (count == 0, count)
        # The number shown is exactly the rows its link lists, and they are
        # the emails of that mode that were made uncertain.
        listed = rows(browser, href)
        assert len(listed) == shown
        assert listed == {
            emails[name]
            for name in uncertain
            if (name == "testing") == (mode == "testing")
        }
    # Home's Sent mail step links the same two lists with the same counts.
    step = home_step(html)
    links = dict(
        (text, href)
        for href, text in re.findall(r'<a href="([^"]+)">([^<]*)</a>', step)
    )
    if here:
        assert f"Sent mail</a> — {here} may not have arrived" in step
        assert len(rows(browser, links["Sent mail"])) == here
        assert "mode=production" in links["Sent mail"]
    else:
        assert links["Sent mail"] == "/admin/mail/sent/"
    other_text = f"{other} in Testing may not have arrived"
    if other:
        assert "mode=testing" in links[other_text]
        assert len(rows(browser, links[other_text])) == other
    else:
        assert set(links) == {"Sent mail"}


def test_system_health_retries_count_each_mode_apart(production_system):
    """Waiting to retry counts the operating mode's Family emails, the other
    mode's apart, and each count is what Sent mail lists in its mode."""
    browser, emails = production_system
    set_state([emails["testing"], emails["initial"]], "retry_wait")
    count, other, _ = _retries("production")
    assert (count, other) == (1, 1)
    assert _retries("testing")[:2] == (1, 1)
    for mode, ids in (
        ("production", {emails["initial"]}),
        ("testing", {emails["testing"]}),
    ):
        assert rows(browser, f"/admin/mail/sent/?state=retry_wait&mode={mode}") == ids


def home_step(html):
    """Home's Sent mail next step, as its list item's markup."""
    for item in re.findall(r"<li data-next-step>(.*?)</li>", html, re.S):
        if ">Sent mail</a>" in item:
            return item
    raise AssertionError("Home has no Sent mail step")
