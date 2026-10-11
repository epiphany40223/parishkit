"""The response funnel in the daily digest (#477, PR 7 slice 1).

The digest states every figure as of the end of its report day (#721), so
its funnel is the Response dashboard's (``response_metrics``), counted at
that instant, with one of the dashboard's side figures. The Administrator
trimmed the digest (#969, #1016): it has no caption, leaves out the "Link
followed" stage, "Invitations not sent (already responded)" and "Submitted
without a delivered invitation", which stay on the dashboard and in
``pk-admin report responses``, and names its first stage "Email invites
sent". The funnel reads
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


# The stages the digest shows, in funnel order. "Link followed" is left out
# (#1016): a mail scanner's prefetch counts the same as a Family's click, so
# the Administrator found it more confusing than useful. It stays on the
# Response dashboard and in ``pk-admin report responses``.
DIGEST_STAGES = ("invited", "form_opened", "progressed", "submitted")
# The digest's own stage names where they differ from the dashboard's. The
# Administrator renamed "Invited" (#1016) so it no longer reads as every
# Family of the campaign: it counts Families sent an email invitation, which
# is why it can be smaller than the Campaign totals' Families.
DIGEST_LABELS = {"invited": "Email invites sent"}


def stage_rows(metrics):
    """Each digest stage as (label, count, share of the first stage, note)."""
    if not isinstance(metrics, ResponseMetrics):
        raise TypeError("A digest funnel needs response metrics.")
    invited = metrics.stage("invited")
    return tuple(
        (
            DIGEST_LABELS.get(stage.key, STAGE_LABELS[stage.key]),
            stage.count,
            share(stage.count, invited),
            stage.note,
        )
        for stage in metrics.stages
        if stage.key in DIGEST_STAGES
    )


def figure_rows(metrics):
    """The digest's figures beside the funnel, as (label, count, whole, portion).

    Only "Submitted more than once" (#969), shown like a funnel line with a
    share (#1016): its share of the Families that submitted, which
    ``portion`` names ("2% of Submitted") because it is not the funnel's
    share of email invites sent. ``whole`` is what its bar is drawn against.
    The other dashboard figures are left out of the digest but still counted
    in ``ResponseMetrics``.
    """
    submitted = metrics.stage("submitted")
    again = metrics.submitted_again
    return (
        (
            "Submitted more than once",
            again,
            submitted,
            f"{share(again, submitted)} of {STAGE_LABELS['submitted']}",
        ),
    )


def funnel_text(metrics):
    """The funnel as plain-text lines for the email's text part."""
    lines = [FUNNEL_HEADING]
    lines += [
        f"{label}: {count:,} ({portion})" + (f" — {note.lower()}" if note else "")
        for label, count, portion, note in stage_rows(metrics)
    ]
    lines.append("")
    lines += [
        f"{label}: {count:,} ({portion})"
        for label, count, _whole, portion in figure_rows(metrics)
    ]
    return "\n".join(lines)
