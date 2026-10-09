"""The scheduled emails table on Dates and mail schedules (#448).

The page lists every saved schedule in one short table, in the order the
schedule forms already use (``schedule_forms.schedule_order``), and pairs each
table row with its form so a click opens that schedule's editor in place. This
module only describes the saved schedules for display: what each one is,
when it sends, its status and its last send. It never changes what the forms
post or how the server validates, previews and applies a schedule change.

Status comes from the counts-only ``schedule_preview.work_summary`` (the
same aggregate the review page reads), keyed by schedule ID, so the page
never reads Family recipients or email content.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan

from .schedule_forms import WEEKDAYS, schedule_labels

DIGESTS = frozenset({"daily_digest", "weekly_digest"})
STATUS_LABELS = {
    "upcoming": _("Upcoming"),
    "preparing": _("Preparing"),
    "sending": _("Sending"),
    "sent": _("Sent"),
    "failed": _("Sent (failed)"),
    # Neutral: covered also counts Families whose email was combined with
    # another one (coalesced), not only Families with nothing to send.
    "done": _("Done"),
    # Its time passed with no work yet: the scheduler, a hold or catch-up
    # may still send it, and a draft that never went live never will.
    "missed": _("Not sent yet"),
    "repeats": _("Repeats"),
}
# A schedule in one of these states has run, is running, or has work
# prepared ahead of its time: it sent its email, failed to, or settled every
# Family without one of its own. Moving it would make a new
# revision and plan the send again, so the page shows it read-only; the
# server still blocks changes to work in progress on its own.
PAST = frozenset({"preparing", "sending", "sent", "failed", "done"})


@dataclass(frozen=True)
class ScheduleRow:
    """One saved schedule as the table shows it.

    ``due`` is the one-time UTC send time of an initial invitation or
    reminder; ``next_due`` is a digest's next UTC send time (None once the
    campaign has no more). ``open`` says whether the schedule's editor starts
    open: after a refused preview, an editor with an error or an unsaved
    change stays open so the reader sees it.
    """

    id: str
    label: str
    kind: str
    subject: str
    status: str
    due: datetime | None = None
    next_due: datetime | None = None
    weekday: str | None = None
    delivered: int = 0
    failed: int = 0
    open: bool = False

    @property
    def status_label(self):
        """The status in words."""
        return STATUS_LABELS[self.status]

    @property
    def read_only(self):
        """Whether this schedule has already sent and so cannot be changed here."""
        return self.status in PAST

    @property
    def digest(self):
        """Whether this is a repeating Admin digest rather than a Family send."""
        return self.kind in DIGESTS

    @property
    def last_send(self):
        """Whether a Family send has results to summarize and link to."""
        return not self.digest and bool(self.delivered or self.failed)

    @property
    def anchor(self):
        """The id of this schedule's editor, which its table row controls."""
        return f"schedule-editor-{self.id}"


def status(kind, due, counts, now):
    """The status of one schedule from its due time and its work counts.

    A digest repeats. Work that blocks a change (in progress or with an
    uncertain result) is checked first: before the send time it is
    preparing, after it sending. Otherwise an initial invitation or reminder
    is upcoming until its time; after it, the schedule is sending while any
    of its work is still pending or running. Once that work is finished it is sent
    when any email of it was delivered or prepared, failed when its only
    results are failures, and done when it ran but settled every Family
    without an email of its own (the work summary's ``covered`` also counts
    the "coalesced" and "empty" dispositions). Otherwise it is not sent yet:
    the scheduler, a hold or catch-up may still send it, or it belongs to a
    draft that never went live.
    """
    if kind in DIGESTS:
        return "repeats"
    if counts.get("blocking"):
        return "preparing" if due > now else "sending"
    if due > now:
        return "upcoming"
    if counts.get("cancellable"):
        return "sending"
    if counts.get("delivered"):
        return "sent"
    if counts.get("failed"):
        return "failed"
    if counts.get("outboxes"):
        return "sent"
    if counts.get("covered"):
        return "done"
    return "missed"


def schedule_rows(previous, campaign, summary, now):
    """Describe each saved schedule, in the given (sending) order.

    ``previous`` is the formset's sorted saved records, ``campaign`` the
    applied campaign values whose time zone resolves each send time, and
    ``summary`` the work counts by schedule ID. Reminders are numbered in
    sending order, as Family email history numbers them.
    """
    rows = []
    for record, label in zip(previous, schedule_labels(previous), strict=True):
        values = record["values"]
        kind = values["kind"]
        plan = SchedulePlan.from_values(values, campaign)
        counts = summary.get(str(record["id"]), {})
        slot = plan.next_slot(after=now) if kind in DIGESTS else None
        rows.append(
            ScheduleRow(
                id=str(record["id"]),
                label=label,
                kind=kind,
                subject=values["subject"],
                status=status(kind, plan.one_time_due, counts, now),
                due=plan.one_time_due,
                next_due=slot.due_at if slot else None,
                weekday=str(_(WEEKDAYS[values["weekday"]]))
                if values["weekday"] is not None
                else None,
                delivered=counts.get("delivered", 0),
                failed=counts.get("failed", 0),
            )
        )
    return rows


def attach(schedules, campaign, summary, now):
    """Pair each saved schedule's form with its table row; return the rows.

    The formset's first forms are its saved schedules, in the same order as
    ``schedules.previous``. Each such form gets ``schedule_row``, which the
    row template reads to start the editor closed (or open) and to keep a
    past send's editor closed. Forms without it, such as the blank new row
    and every form on other pages that share the row template, are unchanged.
    """
    rows = schedule_rows(schedules.previous, campaign, summary, now)
    for form, row in zip(schedules.forms, rows, strict=False):
        problem = form.is_bound and bool(form.errors)
        # Any editor holding a change opens, read-only ones included: a
        # schedule can start running while it is being edited, and a hidden
        # editor would keep posting a change the Admin can neither see nor undo.
        form.schedule_row = replace(row, open=problem or changed(form))
    return [form.schedule_row for form in schedules.forms[: len(rows)]]


def changed(form):
    """Whether a posted saved schedule differs from its saved values.

    Django's ``has_changed`` compares the saved strings (``"2054-10-01"``)
    with typed values and so reports every saved row as changed. Each field's
    saved value is cleaned the way the post was instead, and the two compared;
    a field that did not clean has an error, which opens the editor anyway.
    The hidden schedule ID is the row's identity, not an edit.
    """
    if not form.is_bound or form.errors:
        return False
    for name, field in form.fields.items():
        if name == "id":
            continue
        saved = form.get_initial_for_field(field, name)
        try:
            before = field.clean("" if saved is None else saved)
        except ValidationError:
            return True
        if before != form.cleaned_data.get(name):
            return True
    return False
