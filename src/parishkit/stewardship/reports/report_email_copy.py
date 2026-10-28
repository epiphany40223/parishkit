"""A report email send's saved copy, drawn from its snapshot (#1946).

The report-emails spec ("Saved copies") draws a send's saved copy page from
its snapshot alone: each block's kept data through the block's saved copy
renderer (``Block.copy``), and, for the Participation charts, the fact
generation the snapshot pinned. Nothing here reads current figures, so the
page says what the email said however much later it is opened.

``copy_facts`` gathers what the page states about the send besides its
blocks (its title, "All data is as of" moment, how it was sent and its
recipients by kind); the command line's ``report-email copy`` prints the
same facts, so the two agree. ``copy_blocks`` draws the blocks.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from django.utils.translation import gettext as _

from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.web.dates import format_date

from .report_blocks import REGISTRY
from .report_blocks.registry import CopyFrame
from .report_document import PREVIOUS_DAY_END, ReportDocument, send_path
from .report_email_inputs import report_days, send_inputs
from .report_email_list import GROUP_LABELS, _how, version_user_names

HOW_LABELS = {
    "scheduled": "Scheduled",
    "manual": "Send now",
    "recovery": "Recovery",
}


@dataclass(frozen=True)
class CopyFacts:
    """What a saved copy states about its send, besides its blocks.

    ``as_of_day`` is the report day of a report as of the end of the
    previous day (a campaign date, never shifted), else None, when
    ``as_of`` (the capture moment) is shown in the browser's zone.
    ``covers`` names a recovery send's days ("October 4 through October 6,
    2026"), else "". ``recipients`` is ``recipients_by_kind``'s mapping.
    """

    title: str
    as_of_day: date | None
    as_of: datetime
    made_at: datetime
    how: str
    covers: str
    mode: str
    recipients: dict

    @property
    def as_of_line(self):
        """The "All data is as of …" line, as plain text (the command's).

        A capture moment is a UTC instant here, since text has no browser
        zone; the page shows it in the browser's zone instead.
        """
        if self.as_of_day is not None:
            return f"All data is as of the end of {format_date(self.as_of_day)}."
        return f"All data is as of {self.as_of.astimezone(UTC).isoformat()}."

    @property
    def how_label(self):
        """How the send was made, in the page's words."""
        return _(HOW_LABELS[self.how])


def recipients_by_kind(recipients, names, *, administrator):
    """A frozen cohort by kind: users by name, groups, outside addresses.

    ``recipients`` is the snapshot's ``[{address, kind, ref}]``; ``names``
    maps a user's record id to its name. A group lists how many people it
    reached. Outside addresses are listed only for an Administrator
    (decision 11 and the spec's "Saved copies"); everyone gets their count.
    """
    users = sorted(
        (
            names.get(member["ref"]) or _("a removed user")
            for member in recipients
            if member["kind"] == "user"
        ),
        key=str.casefold,
    )
    reached = {}
    for member in recipients:
        if member["kind"] == "group":
            reached[member["ref"]] = reached.get(member["ref"], 0) + 1
    groups = [
        {"name": str(GROUP_LABELS.get(ref, ref)), "people": count}
        for ref, count in sorted(reached.items())
    ]
    outside = sorted(
        member["address"] for member in recipients if member["kind"] == "outside"
    )
    return {
        "users": users,
        "groups": groups,
        "outside": outside if administrator else None,
        "outside_count": len(outside),
    }


def copy_document(snapshot, preparation, projection, slot):
    """The send's report document without its block data: its dates and words.

    It answers the email's own title, report day and covered days
    (``ReportDocument``), derived from the inputs the send recorded exactly
    as the compiler derived them (``send_inputs``, ``report_days``), under
    the send's own submission watermark, so a response entered after the
    send never moves its report day (#2298).
    """
    values = snapshot.values
    inputs = send_inputs(
        values,
        preparation.covered_slots,
        preparation.captured_at,
        projection,
        campaign_id=preparation.campaign_id,
        watermark=preparation.submission_watermark,
    )
    how = _how(slot)
    return ReportDocument(
        send_id=UUID(str(preparation.pk)),
        name=values["name"],
        as_of=values["as_of"],
        as_of_at=preparation.as_of,
        campaign_timezone=projection.timezone,
        mode=preparation.mode,
        covered_dates=report_days(
            values, preparation.covered_slots, inputs, projection.start_date
        ),
        manual=how == "manual",
        recovery=how == "recovery" or len(preparation.covered_slots) > 1,
        held=inputs.held,
        date_format=snapshot.configuration.parish.date_format,
    )


def copy_facts(snapshot, preparation, *, administrator):
    """The send's title, as-of moment, how it was sent and its recipients.

    Run inside the snapshot's pinned date format (``dates.using``), so the
    words match the email's.
    """
    projection = CampaignConfiguration.objects.get(
        pk=preparation.timezone_configuration_id
    )
    slot = (
        ScheduleOccurrence.objects.filter(pk=preparation.occurrence_id)
        .values_list("slot", flat=True)
        .first()
    )
    document = copy_document(snapshot, preparation, projection, slot)
    covered = document.covered_dates
    return CopyFacts(
        title=document.title,
        as_of_day=document.last_day if document.as_of == PREVIOUS_DAY_END else None,
        as_of=preparation.as_of,
        made_at=preparation.captured_at,
        how=_how(slot),
        covers=document.when if len(covered) > 1 else "",
        mode=preparation.mode,
        recipients=recipients_by_kind(
            snapshot.recipients,
            version_user_names(snapshot.configuration),
            administrator=administrator,
        ),
    )


def copy_blocks(snapshot, frame):
    """Each block of the send in its order, as the page draws it.

    Each is ``{key, label, template, empty, context}``: a block with no kept
    data draws its empty statement under its label (``empty``), as the
    email did, and one the spec leaves out instead (Compared with past
    campaigns without imported years) is not drawn, nor is one whose
    renderer finds nothing to draw in its kept data. A block this release
    does not know (only an Administrator may open such a copy) says so; the
    email as it was sent, beside the blocks, still shows it.
    """
    drawn = []
    for entry in snapshot.blocks:
        block = REGISTRY.get(entry["block"])
        if block is None:
            drawn.append(
                {
                    "key": entry["block"],
                    "label": _("A block this version cannot draw"),
                    "template": "",
                    "empty": _(
                        "This version of ParishKit cannot draw this block. "
                        "Open Show the email as it was sent to read it."
                    ),
                    "context": {},
                }
            )
            continue
        data = entry.get("data")
        if data is None and not block.empty:
            continue
        context = block.copy(data, frame) if data is not None else {}
        if context is None:
            continue
        drawn.append(
            {
                "key": block.key,
                "label": block.label,
                "template": block.copy_template if data is not None else "",
                "empty": block.empty if data is None else "",
                "context": context,
            }
        )
    return drawn


def listed_requests(snapshot):
    """The request ids (text) the send's requests block listed, in its order."""
    return [
        item[0]
        for entry in snapshot.blocks
        if entry["block"] == "additional_information" and entry.get("data")
        for item in entry["data"]["items"]
    ]


def copy_frame(snapshot, preparation, *, participation, linkable):
    """The ``CopyFrame`` of a send: its address, pinned facts and Family links.

    ``linkable`` says whether a Family's name may link its Timeline (only
    for the current campaign, and never for a Testing send's rehearsal
    Families). One read finds every listed request's Family record.
    """
    from parishkit.stewardship.responses.models import AdditionalInformationItem

    listed = listed_requests(snapshot)
    requests = {}
    if listed and linkable:
        requests = {
            str(pk): family_id
            for pk, family_id in AdditionalInformationItem.objects.filter(
                pk__in=listed, submission__campaign_id=snapshot.campaign_id
            ).values_list("pk", "submission__family_id")
        }
    return CopyFrame(
        send_path=send_path(preparation.pk),
        participation=participation,
        requests=requests,
    )
