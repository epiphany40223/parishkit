"""Skip one send of a Daily or Weekly Admin digest (#1658).

Edit scheduled email's Delete offers **This email only** for a digest send
opened from the Campaign calendar, and ``schedule preview`` takes a saved
digest's ``skip`` member. Either way the skip is an ordinary schedule
change: the digest's values gain the send's slot date in ``skip_dates``
(``configuration.skip_dates``), reviewed, signed, confirmed under the work
lock and audited like every other change (``schedule_changes.build_preview``
calls ``apply`` here). Every evaluator then leaves the slot out
(``SchedulePlan``), and the database refuses an occurrence for it (frozen
migration 0130).

A skip is checked here before anything is signed, with nothing recorded on
a refusal:

- the schedule must be a saved digest that the change does not delete, and
  the date one of its sends (``digest_sends``); anything else is an unknown
  request (400), as only a crafted or stale post names one;
- a date already skipped is a stale page (409);
- a send whose time has passed, or that has any occurrence (prepared,
  sending, sent or failed), is refused (403).

The reviewed send's time is signed into the review (``build_preview``'s
``due``), so Apply refuses once it has passed; an occurrence appearing after
the review changes the work fingerprint, so Apply refuses then too.
"""

from datetime import UTC, date, datetime

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.models import ScheduleOccurrence
from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.web.refusals import (
    UserFacingDenied,
    stale_page,
    unexpected_fields,
)

from .schedule_table import DIGESTS

# ``SchedulePlan.page`` examines at most this many dates a call; a digest's
# sends are read page by page to its last one, however long the campaign.
PAGE_DATES = 1000
SKIP_PASSED = _(
    "That send of the digest is already past, so it can't be skipped. Its "
    "later sends can still be skipped from the Campaign calendar."
)
SKIP_WORKED = _(
    "That send of the digest has already been prepared or sent, so it can't be skipped."
)


def digest_sends(values, campaign):
    """Every send of one digest, skipped ones included, in order.

    ``values`` are the digest's saved values and ``campaign`` the applied
    campaign values. Pages through the scheduler's own evaluation
    (``SchedulePlan.page``) without the digest's ``skip_dates``, so a
    skipped send is still listed (the Campaign calendar shows it as
    Skipped). It ends: a weekly digest stops at the campaign's close and a
    daily one the day after it.
    """
    unskipped = {name: value for name, value in values.items() if name != "skip_dates"}
    plan = SchedulePlan.from_values(unskipped, campaign)
    slots, cursor = [], None
    while True:
        page = plan.page(
            through=datetime.max.replace(tzinfo=UTC), after=cursor, limit=PAGE_DATES
        )
        slots.extend(page.slots)
        if page.exhausted:
            return slots
        cursor = page.cursor


def send_of(values, campaign, slot):
    """The digest's send at ``slot`` (a ``ScheduleSlot``), or None if none."""
    return next(
        (item for item in digest_sends(values, campaign) if item.key == slot), None
    )


def skipped(values):
    """The slot dates (ISO text) a digest's values skip; empty when none."""
    return frozenset(values.get("skip_dates", ()))


def _worked(identifier, slot):
    """Whether any occurrence of the schedule exists for this slot, any mode."""
    return ScheduleOccurrence.objects.filter(
        definition_id=identifier, slot=slot
    ).exists()


def skippable(identifier, values, campaign, slot, now):
    """The send of the digest at ``slot`` if This email only may skip it.

    The Edit page offers the choice only then: a digest's send that is not
    skipped yet, still ahead of ``now`` and without any occurrence. Returns
    the ``ScheduleSlot``, or None.
    """
    if values["kind"] not in DIGESTS or slot in skipped(values):
        return None
    send = send_of(values, campaign, slot)
    if send is None or send.due_at <= now or _worked(identifier, slot):
        return None
    return send


def refuse(identifier, values, campaign, slot, now):
    """Refuse a skip that may not be made; return the send's UTC time.

    ``values`` are the digest's saved values (None for a schedule that is
    not saved, or that the change deletes) and ``campaign`` the applied
    campaign values; ``now`` is the database's clock. See the module's
    refusals.
    """
    if values is None or values["kind"] not in DIGESTS:
        raise unexpected_fields()
    if slot in skipped(values):
        raise stale_page()
    send = send_of(values, campaign, slot)
    if send is None:
        raise unexpected_fields()
    if send.due_at <= now:
        raise _denied(SKIP_PASSED)
    if _worked(identifier, slot):
        raise _denied(SKIP_WORKED)
    return send.due_at


def _denied(message):
    """A 403 refusal of a skip, pointing back to the list of schedules."""
    return UserFacingDenied(
        message,
        fix=_("Nothing was changed. Return to Dates and mail schedules."),
        link=reverse("admin:schedule_settings"),
        link_label=_("Dates and mail schedules"),
    )


def apply(patch, skips, prior, campaign, now):
    """Add each skip to the change's schedule rows; return the sends' times.

    ``patch`` is the form set's change rows (``Schedules.patch``), changed
    in place; ``skips`` maps a saved digest's id to the slot date it skips
    (at most one), ``prior`` the saved values by id. A digest the change
    already updates gains the date in its new values; otherwise an update
    with its saved values and the date is added. Each skip is refused first
    (``refuse``). Returns the skipped sends' UTC times, which the review
    signs so Apply refuses once one has passed.
    """
    removed = {row["id"] for row in patch if row["operation"] == "remove"}
    due = []
    for identifier, slot in skips.items():
        values = None if identifier in removed else prior.get(identifier)
        due.append(refuse(identifier, values, campaign, slot, now))
        row = next((row for row in patch if row["id"] == identifier), None)
        if row is None:
            row = {
                "operation": "update",
                "section": "schedules",
                "id": identifier,
                "values": dict(values),
            }
            patch.append(row)
        dates = {*row["values"].get("skip_dates", ()), slot}
        row["values"] = row["values"] | {"skip_dates": sorted(dates)}
    return due


def slot_date(text):
    """A slot date as an address or post names one (``2054-11-03``), or None.

    Only the canonical ISO form, which the slot keys use; anything else is
    None, which the caller refuses as an unknown request.
    """
    try:
        day = date.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return text if day.isoformat() == text else None
