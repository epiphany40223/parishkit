"""Campaign settings and Home follow the campaign work hold (#1619).

The markup live-status-v1.js and ui-v1.js rely on, and which pages wait. The
behaviour is checked in real browsers (browser/test_campaign_work_hold.py)
and the server's answers under PostgreSQL
(database/test_campaign_work_status_postgresql.py).
"""

import re

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.campaign_end_date import END_LOCKED
from parishkit.stewardship.accounts.campaign_views import (
    END_HELD,
    LOCKED_CLOSED,
    LOCKED_HELD,
    LOCKED_LIVE,
    LOCKED_OTHER,
    MINISTRIES_HELD,
    _waiting,
    locked_refusal,
)
from parishkit.stewardship.accounts.campaign_work_status import settings_context

STATUS = "/admin/campaign/work-status/"
RELOAD = "Reload this page to start again."


def test_the_status_address():
    """One passive status for both pages, told apart by ``view``."""
    assert reverse("admin:campaign_work_status") == STATUS


def test_a_page_waits_only_while_a_hold_explains_its_locks():
    """A draft locked by the hold waits, and so does a live campaign whose
    end date or Ministries the hold alone keeps; an open draft and locks no
    hold explains (live, closed, not the current draft) do not."""
    assert _waiting(False, LOCKED_HELD, {})
    assert _waiting(False, LOCKED_LIVE, {"end_date": END_HELD})
    assert _waiting(False, LOCKED_LIVE, {"ministry_duids": MINISTRIES_HELD})
    assert not _waiting(True, None, {})
    for reason in (LOCKED_LIVE, LOCKED_CLOSED, LOCKED_OTHER):
        assert not _waiting(False, reason, {}), reason


def test_held_reasons_never_say_to_reload():
    """The reasons a hold gives wait for the page to update by itself."""
    for reason in (LOCKED_HELD, END_HELD, MINISTRIES_HELD):
        assert "Reload" not in str(reason)


def test_a_refused_review_still_says_to_reload():
    """A Review refused because a setting became locked comes from a page
    drawn before the hold, which does not follow it: its fix says the reason
    and to reload, once; END_LOCKED already says to reload."""
    for reason in (LOCKED_HELD, END_HELD, MINISTRIES_HELD, LOCKED_LIVE):
        fix = str(locked_refusal("name", reason, {}).refusal.fix)
        assert fix == f"{reason} {RELOAD}"
    end = locked_refusal("end_date", LOCKED_LIVE, {})
    assert str(end.refusal.fix) == str(END_LOCKED)


def test_settings_status_region_pending_then_follow_up():
    """Pending while held, with no follow-up; settled once the hold ends,
    with one hidden a[data-reread] link to the form's in-place region. The
    region is hidden, so it takes no space, and names its passive status."""
    held = render_to_string(
        "stewardship/campaign-work-status.html", settings_context(True)
    )
    assert re.search(r'<div hidden data-live-status="campaign-work"', held)
    assert f'data-live-url="{STATUS}?view=settings"' in held
    assert "data-live-pending" in held
    assert "data-reread" not in held
    assert 'data-live-announce=""' in held
    ended = render_to_string(
        "stewardship/campaign-work-status.html", settings_context(False)
    )
    assert "data-live-pending" not in ended
    assert re.search(
        r'<a hidden data-live-follow data-reread href="#settings-editor">', ended
    )
    assert "can change now" in ended


def home(offer):
    """Home's first-campaign panel for ``offer`` (first_campaign_offer)."""
    return render_to_string(
        "stewardship/home-first-campaign.html", {"first_campaign": offer}
    )


def test_home_panel_is_followed_only_while_held():
    """Held: pending, with Create the campaign unavailable and its reason,
    which does not say to reload. Ready: settled, offering the page, and its
    announcement says so. A campaign that exists by then is said instead."""
    held = home("held")
    assert 'data-live-status="first-campaign"' in held
    assert f'data-live-url="{STATUS}?view=home"' in held
    assert "data-live-pending" in held
    assert "becomes available here by itself" in held
    assert "Reload" not in held
    assert 'aria-describedby="create-campaign-reason"' in held
    ready = home("ready")
    assert "data-live-pending" not in ready
    assert f'href="{reverse("admin:campaign_create")}"' in ready
    assert 'data-live-announce="Create the campaign is available now."' in ready
    # Each sentence keeps the other's room, drawn invisibly, so the swap
    # moves nothing below the panel.
    assert '<p aria-hidden="true">Create the campaign becomes' in ready
    assert '<p aria-hidden="true">Setup is complete. Next' in held
    assert "data-live-pending" not in home("source")
    created = home(None)
    assert "Campaign created" in created and "No campaign yet" not in created
