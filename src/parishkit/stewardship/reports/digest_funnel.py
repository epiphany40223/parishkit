"""The response funnel in the daily digest and the weekly report (#477).

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

The weekly report (``weekly_digest``, slice 3a) shows the same section and
saved-page panel, counted at its capture instant (``observed_at``) instead
of a report-day end, by the fan-out worker once per page and again by its
saved list pages; the same agreement caveats apply.

Only a Production digest shows the funnel. Testing data is per rehearsal
epoch and is cleaned up at go-live, so a Testing funnel could not be counted
again later; a Testing digest says where the Testing funnel is instead.

The daily digest's section starts with two charts (slice 2, visuals first
per #720): the funnel and the response activity series (``digest_charts``),
drawn by the Response dashboard's own spec functions but trimmed as the
section's table is (#1016): the four digest stages under the digest's names,
and no "Link followed" line. The email carries them as ``cid:`` PNGs from the
chart engine's server renderer (``funnel_images``), drawn once by the
compiling worker and retained with the ready row, so a retry resends the same
bytes; the saved page draws the same specs in the browser from the funnel
counted again. The weekly report keeps the table only until slice 3b.
"""

from dataclasses import replace
from datetime import datetime, time, timedelta
from html import escape
from zoneinfo import ZoneInfo

from parishkit.stewardship.web.digest_content import (
    EMAIL_CHART_WIDTH,
    FUNNEL_CHART_IDS,
    MAX_ALT_CHARACTERS,
)

from .chart_specs import (
    SERIES,
    STAGE_LABELS,
    STAGE_PHRASES,
    activity_chart,
    funnel_chart,
    share,
)
from .digest_comparison import DIGEST_CHART
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
# How the digest chart's summary (its alt text) reads that stage.
DIGEST_PHRASES = {"invited": "were sent an email invite"}


# The activity lines the digest draws: form opens and submissions, without
# link follows (#1016, as for the funnel's stages).
DIGEST_SERIES = tuple(line for line in SERIES if line[0] != "links")
# The digest's activity chart covers at most this many days up to its
# cutoff. A campaign's activity spans weeks; the bound only keeps one
# stray early instant (a clock or backfill mistake) from stretching the
# chart over years of empty days and past the renderer's size limit,
# which would fail every compile of the digest.
ACTIVITY_DAYS = 120
# The closed failure words (audit.schemas.FAILURES) naming each chart when
# it cannot be drawn, in FUNNEL_CHART_IDS order.
CHART_FAILURES = ("digest_funnel_chart", "digest_activity_chart")
# The email images' layout: fit-x autosizing puts the axes and legend
# inside the width, so every PNG is exactly the email's width (at the
# renderer's scale), as the compiled-body validator requires.
EMAIL_AUTOSIZE = {"type": "fit-x", "contains": "padding"}


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


def funnel_context(metrics, mode, *, charts=False):
    """The saved report page's funnel panel: its stages, figure and Testing note.

    The daily and weekly report pages share it (and the
    ``components/digest-funnel.html`` panel), so both show the funnel alike.
    ``charts`` adds the two charts the daily email shows (``digest_charts``),
    drawn in the browser without the title inside them (the panel's
    headings name them); the weekly page has none until slice 3b.
    """
    from .response_dashboard import untitled

    return {
        "funnel_charts": tuple(untitled(chart) for chart in digest_charts(metrics))
        if charts and metrics
        else (),
        "funnel_stages": stage_rows(metrics) if metrics else (),
        "funnel_figures": figure_rows(metrics) if metrics else (),
        "funnel_testing_note": TESTING_NOTE
        if metrics is None and mode == "testing"
        else "",
    }


def digest_charts(metrics):
    """The funnel and activity charts of a digest's funnel, as ``ChartDocument``.

    The digest's stages under its own names and the activity without link
    follows (#1016), at the dashboard's automatic grain: hourly while the
    activity spans three days or less, daily after that, over at most the
    last ``ACTIVITY_DAYS`` days (the funnel itself counts everything). The
    email's images and the saved page's browser charts both come from these.
    """
    from .response_dashboard import AUTO, at_grain, chosen_grain

    labels = STAGE_LABELS | DIGEST_LABELS
    metrics = at_grain(metrics, chosen_grain(metrics, AUTO))
    since = metrics.as_of - timedelta(days=ACTIVITY_DAYS)
    metrics = replace(
        metrics,
        activity=tuple(bucket for bucket in metrics.activity if bucket.start >= since),
        sends=tuple(send for send in metrics.sends if send.scheduled >= since),
    )
    return (
        funnel_chart(
            metrics,
            keys=DIGEST_STAGES,
            labels=labels,
            phrases=STAGE_PHRASES | DIGEST_PHRASES,
        ),
        activity_chart(metrics, series=DIGEST_SERIES),
    )


def email_spec(chart):
    """The chart's spec as the email image draws it.

    Without the title drawn inside it (the email's heading or caption names
    the chart, as on the dashboard page), at the email's width with the axes
    and legend inside that width, so the PNG is exactly twice it.
    """
    spec = {key: value for key, value in chart.spec.items() if key != "title"}
    return spec | {"width": int(EMAIL_CHART_WIDTH), "autosize": EMAIL_AUTOSIZE}


def alt_text(chart):
    """The chart's summary, which carries its figures, cut to the alt limit."""
    text = " ".join(chart.summary.split())
    if len(text) > MAX_ALT_CHARACTERS:
        text = text[: MAX_ALT_CHARACTERS - 1] + "…"
    return text


def funnel_images(charts):
    """Each chart as (content id, PNG bytes), in ``FUNNEL_CHART_IDS`` order.

    Starts the chart engine's server renderer once per chart; a kill at its
    time limit is logged under ``DIGEST_CHART``. A failure raises
    ``ChartRenderError`` naming the chart, which fails the compile so it is
    retried; the last attempt sends the digest without its charts
    (``digest_tasks``).
    """
    from .chart_rendering import render_digest_chart

    if not charts:
        return ()
    return tuple(
        (
            name,
            render_digest_chart(email_spec(chart), what=DIGEST_CHART, chart=failure),
        )
        for name, failure, chart in zip(
            FUNNEL_CHART_IDS, CHART_FAILURES, charts, strict=True
        )
    )


def charts_html(charts, style):
    """The two chart images of the email's funnel section, funnel first.

    The section's heading names the funnel chart; the activity chart gets a
    caption with its title (which names the grain). ``style`` gives the
    compiler-owned style attributes; every value is escaped.
    """
    html = ""
    if not charts:
        return html
    for index, (name, chart) in enumerate(zip(FUNNEL_CHART_IDS, charts, strict=True)):
        if index:
            html += f"<p{style('caption')}>{escape(chart.title, quote=False)}</p>"
        alt = escape(alt_text(chart), quote=False).replace('"', "&quot;")
        html += (
            f'<img src="cid:{name}" alt="{alt}" width="{EMAIL_CHART_WIDTH}"'
            f"{style('image')}>"
        )
    return html
