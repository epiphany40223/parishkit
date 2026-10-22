"""The conversion of the Daily and Weekly digests into report emails (#1786).

Pure functions over one canonical configuration document, so the offline
conversion (``reports_upgrade``), its request format's builder
(``request_patch``) and the tests compute exactly the same thing. Each
digest schedule record keeps its ID (its ``ScheduleDefinition`` UUID, so
every occurrence, fulfillment, skip, post-close resolution and restore
hold still applies) and gets the report values of the report-emails
spec's "Conversion" table:

- the Daily becomes "Daily report": every day at its saved time, from the
  day after the campaign's first day until the day after its last day, as
  of the end of the previous day, keyed by the day it reports on, with
  Campaign totals, Response funnel, Participation charts, Day by day table
  (5 days) and, when it was on, Compared with past campaigns;
- the Weekly becomes "Weekly report": every week on its saved weekday at
  its saved time, from the campaign's first day until the first send after
  its last day, as of when it is made, keyed by its send date, with the
  open additional information requests and the Response funnel, sending
  only when there is something to report.

Both go to All Administrators, keep their saved subject and skip dates,
and take the introduction of the email template they send. An
introduction still holding a built-in default (current or retired)
becomes empty for the Daily, which already left such an intro out, and
becomes the reworded default for the Weekly (decision 17); an edited
introduction is kept as written.
"""

from parishkit.stewardship.web.content import prepare_content

from .content_defaults import EMAILS, RETIRED_EMAILS

DIGEST_KINDS = ("daily_digest", "weekly_digest")
NAMES = {"daily_digest": "Daily report", "weekly_digest": "Weekly report"}
# Decision 17: the converted Weekly's reworded default introduction.
WEEKLY_INTRO = (
    "<p>Here are the additional information requests that are still open.</p>"
)
DAILY_BLOCKS = ("campaign_totals", "response_funnel", "participation_charts")
DAY_BY_DAY_DAYS = 5


def default_intro(kind, html, text):
    """Whether a digest template's intro is exactly a built-in default.

    Current or retired (``content_defaults``), compared as saved content
    holds it (sanitized), as ``jobs.digest_content.default_daily_intro``
    compares the Daily's at render time. Any edit keeps the intro.
    """
    defaults = (EMAILS[kind], *RETIRED_EMAILS[kind])
    return any(
        (html, text) == (prepared.html, prepared.text)
        for prepared in (prepare_content(email.html) for email in defaults)
    )


def _intro(kind, template):
    """The converted report's introduction, both parts, from its template."""
    html = template["html"] if template else ""
    text = template["text"] if template else ""
    if not default_intro(kind, html, text):
        return html, text
    if kind == "daily_digest":
        return "", ""
    reworded = prepare_content(WEEKLY_INTRO)
    return reworded.html, reworded.text


def _block(key, **options):
    """One stored block entry at its first data version."""
    return {"block": key, "version": 1, "options": options}


def converted_values(values, template):
    """The report values of one Daily or Weekly digest record.

    ``template`` is the content record's values of the email template the
    digest sends (``template_version``), or None when the document has none.
    """
    kind = values["kind"]
    daily = kind == "daily_digest"
    if daily:
        schedule = {
            "type": "repeat",
            "frequency": "daily",
            "from": "day_after_campaign_start",
            "until": "day_after_campaign_end",
            "time": values["time"],
        }
        blocks = [_block(key) for key in DAILY_BLOCKS]
        blocks.append(_block("day_by_day", days=DAY_BY_DAY_DAYS))
        if values.get("comparison") is True:
            blocks.append(_block("compared_with_past_campaigns"))
    else:
        schedule = {
            "type": "repeat",
            "frequency": "weekly",
            "weekdays": [values["weekday"]],
            "from": "campaign_start",
            "until": "first_send_after_campaign_end",
            "time": values["time"],
        }
        blocks = [_block("additional_information"), _block("response_funnel")]
    intro_html, intro_text = _intro(kind, template)
    return {
        "campaign_id": values["campaign_id"],
        "kind": "report",
        "name": NAMES[kind],
        "subject": values["subject"],
        "intro_html": intro_html,
        "intro_text": intro_text,
        "recipients": [{"group": "administrators"}],
        "schedule": schedule,
        "as_of": "previous_day_end" if daily else "send_time",
        "blocks": blocks,
        "content_required": not daily,
        "skip_dates": list(values.get("skip_dates", [])),
        "paused_ranges": [],
        "slot_basis": "report_date" if daily else "send_date",
    }


def upgrade_patch(document):
    """The conversion's request patch: one update per digest record, in ID order.

    Each update carries the record's complete report values, which replace
    its digest values whole. Empty when the document holds no digest.
    """
    content = {
        row["id"]: row["values"] for row in document["sections"].get("content", [])
    }
    return [
        {
            "operation": "update",
            "section": "schedules",
            "id": row["id"],
            "values": converted_values(
                row["values"], content.get(row["values"].get("template_version"))
            ),
        }
        for row in sorted(
            document["sections"].get("schedules", []), key=lambda row: row["id"]
        )
        if row["values"].get("kind") in DIGEST_KINDS
    ]
