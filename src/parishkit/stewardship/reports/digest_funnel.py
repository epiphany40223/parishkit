"""The response funnel in the daily digest (#477, PR 7 slice 1).

The digest states every figure as of the end of its report day (#721), so
its funnel is the same five stages and three side figures as the Response
dashboard (``response_metrics``), counted at that instant. The funnel reads
durable timestamps only, so counting it again later gives the same numbers:
the compiling worker counts it for the email and the saved report page
counts it again when it is opened, the way both read the ParishSoft data age
(``data_age_at``), and the two agree without the counts being stored, unless
the engagement record is backfilled later (its first instants can only move
earlier) or a manual send's cutoff is its own observation, when rows still
committing may be missed. A purged campaign's digest pages are not shown at
all. A digest sent before the funnel existed (before this release) gains
the funnel, or the Testing note, on its saved page although its email never
had it; that was accepted rather than letting the web login read compiled
mail prose to tell old pages from new.

Only a Production digest shows the funnel. Testing data is per rehearsal
epoch and is cleaned up at go-live, so a Testing funnel could not be counted
again later; a Testing digest says where the Testing funnel is instead.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .chart_specs import STAGE_LABELS, share
from .response_metrics import ResponseMetrics, ResponseScope, response_metrics

# What a Testing digest says in place of the funnel.
TESTING_NOTE = (
    "Testing digests leave out the response funnel. To see this rehearsal's "
    "funnel, open the Response dashboard's Testing view."
)
# Under the funnel's heading: what the percentages are out of, and why its
# Submitted can differ from the campaign totals above it.
CAPTION = (
    "Families at each stage by the end of the day. Percentages are of the "
    "Families invited. The campaign totals above count the participating "
    "Families instead, so their numbers can differ."
)
# The funnel's heading in the email's text part.
FUNNEL_HEADING = "Response funnel (Families):"


def report_day_end(local_date, timezone, observed_at):
    """The instant the report day ends in the campaign's zone.

    Never later than ``observed_at``: a digest observed before its day ended
    (an Administrator's manual send) counts up to its observation.
    """
    zone = ZoneInfo(timezone)
    end = datetime.combine(local_date + timedelta(days=1), time.min, zone)
    return min(end, observed_at)


def digest_funnel(campaign_id, mode, as_of):
    """The funnel of a Production digest's campaign at ``as_of``, or None.

    The caller runs inside the report's read guard (the compiling worker's
    guarded effect, or the saved page's campaign read barrier).
    """
    if mode != "production":
        return None
    return response_metrics(ResponseScope(campaign_id), as_of)


def stage_rows(metrics):
    """Each stage as (label, count, share of Invited, note) for display."""
    if not isinstance(metrics, ResponseMetrics):
        raise TypeError("A digest funnel needs response metrics.")
    invited = metrics.stage("invited")
    return tuple(
        (
            STAGE_LABELS[stage.key],
            stage.count,
            share(stage.count, invited),
            stage.note,
        )
        for stage in metrics.stages
    )


def figure_rows(metrics):
    """The three figures reported beside the funnel, as (label, count)."""
    return (
        ("Invitations not sent (already responded)", metrics.skipped_responded),
        ("Submitted without a delivered invitation", metrics.submitted_uninvited),
        ("Submitted more than once", metrics.submitted_again),
    )


def funnel_text(metrics):
    """The funnel as plain-text lines for the email's text part."""
    lines = [FUNNEL_HEADING, CAPTION]
    lines += [
        f"{label}: {count:,} ({portion})" + (f" — {note.lower()}" if note else "")
        for label, count, portion, note in stage_rows(metrics)
    ]
    lines.append("")
    lines += [f"{label}: {count:,}" for label, count in figure_rows(metrics)]
    return "\n".join(lines)
