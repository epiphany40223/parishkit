"""The report emails' two starting points (report-emails spec, #1786).

The spec's "Campaign wizard starting points": Daily report and Weekly
report, each the converted digest's values with no time chosen yet. They
are defined here once, as report values in their stored form
(``report_values``) without the schedule's ``time``, so New report
email's Start from (#2037), the campaign wizard's suggestions (#2038) and
any later command draw exactly the same values.
"""

import copy

DAILY = "daily"
WEEKLY = "weekly"
# Monday (the stored form's weekdays run Monday 0 to Sunday 6).
MONDAY = 0

# The stored values of each starting point, without ``campaign_id`` (the
# campaign's) and the schedule's ``time`` (the Administrator's choice).
# Blocks are named here with their options; ``starting_point`` adds each
# block's registered version.
_POINTS = {
    DAILY: {
        "name": "Daily report",
        "subject": "{{ campaign_name }} — daily report",
        "schedule": {
            "type": "repeat",
            "frequency": "daily",
            "from": "day_after_campaign_start",
            "until": "day_after_campaign_end",
        },
        "as_of": "previous_day_end",
        # Participation charts with no "which charts" option: all of them.
        "blocks": (
            ("campaign_totals", {}),
            ("response_funnel", {}),
            ("participation_charts", {}),
            ("day_by_day", {"days": 5}),
        ),
        "content_required": False,
    },
    WEEKLY: {
        "name": "Weekly report",
        "subject": "{{ campaign_name }} — weekly report",
        "schedule": {
            "type": "repeat",
            "frequency": "weekly",
            "weekdays": [MONDAY],
            "from": "campaign_start",
            "until": "first_send_after_campaign_end",
        },
        "as_of": "send_time",
        "blocks": (("additional_information", {}), ("response_funnel", {})),
        "content_required": True,
    },
}
STARTING_POINTS = tuple(_POINTS)


def starting_point(key, campaign_id):
    """The stored report values of starting point ``key``, without the time.

    Every other key of the stored form is present: no introduction, All
    Administrators, no skips or pauses, and the send-date slot basis every
    report created after the migration has. A fresh copy each call, so a
    caller may add the time or change a value freely.
    """
    # The registry draws report content, so it is imported only when a
    # starting point is built (as ``report_values._blocks`` does).
    from parishkit.stewardship.reports.report_blocks import REGISTRY

    point = copy.deepcopy(_POINTS[key])
    return {
        "campaign_id": str(campaign_id),
        "kind": "report",
        "name": point["name"],
        "subject": point["subject"],
        "intro_html": "",
        "intro_text": "",
        "recipients": [{"group": "administrators"}],
        "schedule": point["schedule"],
        "as_of": point["as_of"],
        "blocks": [
            {"block": block, "version": REGISTRY[block].version, "options": options}
            for block, options in point["blocks"]
        ],
        "content_required": point["content_required"],
        "skip_dates": [],
        "paused_ranges": [],
        "slot_basis": "send_date",
    }
