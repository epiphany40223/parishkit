"""The scheduled emails table on Dates and mail schedules (#448, #878).

The page lists every saved schedule in one short table. The rows are
described in the order the schedule forms already use
(``schedule_forms.schedule_order``), which numbers the reminders; the list's
When heading sorts them by when they next send, or the reverse (``SORTING``,
#1652). On the list, an editable row links to its Edit scheduled email page
and offers Delete; on the date-change review each table row is paired with
its form, so a click opens that schedule's editor in place. This module only
describes the saved schedules for display: what each one is, when it sends,
its status and its last send, and which of them are read-only. It never
changes what the forms post or how the server validates, previews and
applies a schedule change.

Status comes from the counts-only ``schedule_preview.work_summary`` (the
same aggregate the review page reads), keyed by schedule ID, so the page
never reads Family recipients or email content.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.web.tables import Sorting

from .schedule_forms import schedule_labels

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
    # One send of a report email that it skips (#1658, the Campaign
    # calendar).
    "skipped": _("Skipped"),
    # A suggested schedule Dates and mail schedules proposes in the campaign
    # wizard (#1404 PR 6), listed but not saved.
    "suggested": _("Not saved yet"),
    # One send of a report email while it is paused (#1786, the calendar).
    "paused": _("Paused"),
    # A send a report email made, on the Campaign calendar (R43), in its
    # Past sends result words (``report_email_list.RESULT_LABELS``); "sent"
    # and "sending" are the shared words above.
    "report_failed": _("Failed"),
    "uncertain": _("Uncertain: check Sent mail"),
    "nothing": _("Not sent: nothing to report"),
    "nobody": _("Not sent: no current recipients"),
}
# A schedule in one of these states has run, is running, or has work
# prepared ahead of its time: it sent its email, failed to, or settled every
# Family without one of its own. Moving it would make a new
# revision and plan the send again, so the page shows it read-only; the
# server still blocks changes to work in progress on its own. On the
# Campaign calendar a report email's skipped or paused send, and each send
# it made, are shown read-only too.
PAST = frozenset(
    {
        "preparing",
        "sending",
        "sent",
        "failed",
        "done",
        "skipped",
        "paused",
        "report_failed",
        "uncertain",
        "nothing",
        "nobody",
    }
)


@dataclass(frozen=True)
class ScheduleRow:
    """One saved schedule as the table shows it.

    ``due`` is the UTC send time of an initial invitation or reminder, or
    of one report email send on the Campaign calendar. ``open`` says whether
    the schedule's editor starts open: after a refused preview, an editor
    with an error or an unsaved change stays open so the reader sees it.
    ``email`` is the readable name of the email it sends
    (``schedule_forms.email_names``), and ``position`` its place in sending
    order, which breaks ties in the When heading's sort.
    ``slot`` is one report email send's slot key on the Campaign calendar,
    which its Edit link names (``?send=``) so that send's box is ticked for
    Skip (or Send) selected sends.
    """

    id: str
    label: str
    kind: str
    subject: str
    status: str
    position: int = 0
    email: str = ""
    due: datetime | None = None
    delivered: int = 0
    failed: int = 0
    open: bool = False
    slot: str | None = None
    # A report email's send on the Campaign calendar (#1786) opens its own
    # Edit page on Report emails, not Edit scheduled email.
    link: str | None = None

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
        """Whether this is an Admin report rather than a Family send.

        A report email's send on the Campaign calendar (#1786): it is never
        moved from there, and has no Family send results.
        """
        return self.kind == "report"

    @property
    def own_moment(self):
        """Whether no other Family email may send at this send's moment.

        Every initial invitation and reminder: Edit's review refuses two
        Family emails at one date and time (``same_time``, as
        ``validate_campaign_sections`` checks), and a report email is not
        Family mail. The Campaign calendar marks these sends so Drag and
        Move… refuse a day where the moved send's kept time is one of their
        moments (PR #2095).
        """
        return not self.digest

    @property
    def movable(self):
        """Whether the Campaign calendar offers Move… for this send (#1521).

        Only a Family send that has not run and whose time has not passed
        without sending: a report email is moved on Report emails.
        """
        return not self.read_only and not self.digest and self.status != "missed"

    @property
    def last_send(self):
        """Whether a Family send has results to summarize and link to."""
        return not self.digest and bool(self.delivered or self.failed)

    @property
    def anchor(self):
        """The id of this schedule's editor, which its table row controls."""
        return f"schedule-editor-{self.id}"

    @property
    def edit_url(self):
        """This schedule's Edit page, or None when read-only.

        A report email's send on the Campaign calendar opens its Edit report
        email page naming its slot.
        """
        if self.read_only:
            return None
        url = self.link or reverse("admin:schedule_edit", args=[self.id])
        return f"{url}?send={self.slot}" if self.slot else url


def when(row):
    """The When heading's sort key: the moment the row sends (#1652).

    Rows at one moment keep sending order (``position``: the Initial
    invitation first, then the saved ID).
    """
    return (row.due, row.position)


# The When heading sorts chronologically or the reverse; chronological is the
# default and the first click. Reminder numbers stay in sending order.
SORTING = Sorting.by_column({"when": when}, default="when")


def status(kind, due, counts, now):
    """The status of one schedule from its due time and its work counts.

    Work that blocks a change (in progress or with an
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


def schedule_rows(previous, campaign, summary, now, names=None):
    """Describe each saved schedule, in the given (sending) order.

    ``previous`` is the formset's sorted saved records, ``campaign`` the
    applied campaign values whose time zone resolves each send time, and
    ``summary`` the work counts by schedule ID. Reminders are numbered in
    sending order, as Family email history numbers them. ``names`` maps an
    email's ID to its readable name; a schedule whose email is not in it
    (an unresolved legacy reference) shows its saved subject.
    """
    rows = []
    for position, (record, label) in enumerate(
        zip(previous, schedule_labels(previous), strict=True)
    ):
        values = record["values"]
        kind = values["kind"]
        plan = SchedulePlan.from_values(values, campaign)
        counts = summary.get(str(record["id"]), {})
        rows.append(
            ScheduleRow(
                id=str(record["id"]),
                label=label,
                kind=kind,
                subject=values["subject"],
                status=status(kind, plan.one_time_due, counts, now),
                position=position,
                email=(names or {}).get(values["template_version"], values["subject"]),
                due=plan.one_time_due,
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
    The hidden schedule ID is the row's identity, and its drawn moment
    (``drawn_at``, #558) how the page was drawn; neither is an edit.
    """
    if not form.is_bound or form.errors:
        return False
    for name, field in form.fields.items():
        if name in {"id", "drawn_at"}:
            continue
        saved = form.get_initial_for_field(field, name)
        try:
            before = field.clean("" if saved is None else saved)
        except ValidationError:
            return True
        if before != form.cleaned_data.get(name):
            return True
    return False
