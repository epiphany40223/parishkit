"""Turning a saved Daily digest's comparison section off again (#1698).

The Schedules form saves ``comparison`` only while the Include Compared with
past campaigns checkbox is on (#1018). Through the Edit scheduled email page
and the ``schedule`` command line, each reviewed, confirmed and installed for
real: clearing the checkbox removes the saved key, the review says so, and
every other saved value stays as it was.
"""

from html import unescape
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from ..campaign_factory import schedule
from ..content_factory import content
from .auth_builders import signed_in
from .automation_builders import paired
from .campaign_builders import change
from .test_admin_schedule_cli_postgresql import admin, confirm, preview  # noqa: F401
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token
from .test_schedule_entry_postgresql import LIST, entry
from .test_schedule_entry_postgresql import setup as with_saved_emails

pytestmark = pytest.mark.django_db(transaction=True)

OFF = "Does not include Compared with past campaigns."
ON = "Includes Compared with past campaigns."


def with_comparison_digest(store):
    """A draft campaign with a Daily digest saved with the comparison on.

    Returns the digest's id, its saved values and its email's id.
    """
    campaign, _ = with_saved_emails(store)
    email = content(
        str(campaign.pk), kind="email", slot="daily_digest", subject="Digest"
    )
    row = schedule(
        str(campaign.pk),
        kind="daily_digest",
        date=None,
        weekday=None,
        time="07:00:00",
        template_version=email["id"],
        subject="Digest",
        comparison=True,
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **email},
                {"operation": "add", "section": "schedules", **row},
            ],
        ).state
        == "applied"
    )
    assert saved()["comparison"] is True
    return row["id"], row["values"], email["id"]


def definition():
    """The Daily digest's schedule definition, which names its Edit page."""
    return ScheduleDefinition.objects.get(
        kind="daily_digest", current_revision__isnull=False
    )


def saved():
    """The Daily digest's current saved values."""
    return definition().current_revision.values


def without_comparison(values):
    """``values`` as they are saved with the comparison off: no key at all."""
    return {name: value for name, value in values.items() if name != "comparison"}


def test_the_edit_page_turns_the_comparison_off_and_on(auth_service, google):
    """Clearing the checkbox removes the key; ticking it again restores it."""
    store = auth_service.store
    _, values, email = with_comparison_digest(store)
    browser, _ = signed_in()
    path = f"/admin/campaign/schedules/{definition().pk}/"
    assert 'name="schedule-comparison"' in browser.get(path).content.decode()
    shown = {"time": "07:00", "template_version": email}

    review = post(browser, path, entry(store, **shown))
    assert review.status_code == 200, review.content
    page = unescape(review.content.decode())
    assert ON in page and OFF in page  # The saved schedule, then the proposal.
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert saved() == without_comparison(values)

    review = post(browser, path, entry(store, **shown, comparison="on"))
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert saved() == values


def test_the_command_line_turns_the_comparison_off(admin, google):  # noqa: F811
    """``comparison: false`` previews the change and installs it as no key."""
    store = admin.service.store
    identifier, values, _ = with_comparison_digest(store)
    _, secret, _ = paired(admin.service)
    code, document = preview(
        admin, secret, {"schedules": [{"id": identifier, "comparison": False}]}
    )
    assert code == 0, document
    [changed] = document["result"]["changes"]
    assert changed["id"] == identifier and changed["operation"] == "update"
    assert changed["before"]["comparison"] is True
    assert changed["after"]["comparison"] is False
    code, confirmed = confirm(admin, secret, document["result"]["preview"]["token"])
    assert code == 0, confirmed
    install_request(
        store,
        request_id=UUID(confirmed["result"]["request"]["request_id"]),
        correlation_id=uuid4(),
    )
    assert saved() == without_comparison(values)
