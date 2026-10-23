"""Dates and mail schedules' suggested report emails (#2038, #1786 item 8b).

While the campaign wizard proposes its suggested Family schedules
(``schedule_suggestions``), the same panel lists the report emails' two
starting points (``campaigns.report_presets``: Daily report and Weekly
report), each with a checkbox ticked when drawn and a time field, "Not saved
yet" (report-emails spec, "Suggested report emails"). **Add these schedules
and continue** posts the ticks and times with the page's version, and
``schedule_entry_views.schedule_suggested`` adds each ticked report, as a
``report-email preview`` ``create`` entry (``entries``), in the one change
that adds the Family schedules.

A starting point whose name a report of the campaign already has is not
offered (``offered``). The time is typed in this browser's zone, posted as
``zone``, and stored in the campaign's, as New report email stores it.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.report_presets import (
    STARTING_POINTS,
    starting_point,
)
from parishkit.stewardship.wall_clock import WEEKDAYS
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.time_entry_fields import FlexibleTimeField

from . import schedule_local

TIME_MISSING = _("Enter the time it sends, or untick it.")


def tick_name(key):
    """The posted name of starting point ``key``'s checkbox."""
    return f"report_{key}"


def time_name(key):
    """The posted name of starting point ``key``'s time field."""
    return f"report_{key}_time"


def offered(reports, campaign_id):
    """The starting points to suggest: those no report of the campaign names.

    ``reports`` are the campaign's saved report values. Names compare as
    New report email compares them (``report_email_editor.name_taken``).
    """
    from parishkit.stewardship.reports.report_email_editor import name_taken

    return [
        key
        for key in STARTING_POINTS
        if name_taken(starting_point(key, campaign_id)["name"], reports) is None
    ]


def fields(keys):
    """Every posted name the suggestions of ``keys`` may send, and ``zone``."""
    return {
        "zone",
        *(name for key in keys for name in (tick_name(key), time_name(key))),
    }


class SuggestedReports(AdminForm):
    """The suggested report emails' ticks and times, read into entries.

    ``keys`` are the starting points offered (``offered``), ``campaign`` the
    applied campaign values, ``campaign_id`` its id and ``now`` the clock a
    repeating time is converted at (``schedule_local.to_campaign``). An
    unticked row's time is ignored; a ticked row without a readable time is
    refused at its time field. ``entries()`` is the result once valid.
    """

    zone = forms.CharField(required=False)

    def __init__(self, data, *, keys, campaign, campaign_id, now):
        """Add one checkbox (ticked unless posted unticked) and time per key."""
        super().__init__(data, auto_id="suggested-%s")
        self.keys, self.campaign = list(keys), campaign
        self.campaign_id, self.now = campaign_id, now
        self._entries = []
        for key in self.keys:
            self.fields[tick_name(key)] = forms.BooleanField(
                required=False, initial=True
            )
            self.fields[time_name(key)] = FlexibleTimeField(
                label=_("Time"), required=False
            )

    def clean(self):
        """Build each ticked report's ``create`` entry, or its time's error."""
        from parishkit.stewardship.reports.report_email_changes import EDITABLE
        from parishkit.stewardship.reports.report_email_editor import ZONE_MISSING

        data = super().clean()
        for key in self.keys:
            name = time_name(key)
            if not data.get(tick_name(key)):
                # An unticked row's time is not sent by the page (it is
                # greyed); one sent anyway is ignored, refused or not.
                self.errors.pop(name, None)
                continue
            if name in self.errors:
                continue
            clock = data.get(name)
            if clock is None:
                self.add_error(name, ValidationError(TIME_MISSING, code="time_missing"))
                continue
            try:
                _day, local, _weekday = schedule_local.to_campaign(
                    None,
                    clock,
                    None,
                    zone=data.get("zone") or "",
                    campaign_zone=self.campaign["timezone"],
                    now=self.now,
                )
            except UnknownZone:
                self.add_error(name, ValidationError(ZONE_MISSING, code="zone_missing"))
                continue
            values = starting_point(key, self.campaign_id)
            values["schedule"]["time"] = local.isoformat(timespec="seconds")
            self._entries.append(
                {
                    "operation": "create",
                    "values": {field: values[field] for field in EDITABLE},
                }
            )
        return data

    def entries(self):
        """The ticked reports as ``report-email preview`` ``create`` entries."""
        return list(self._entries)


def rule_text(schedule):
    """A starting point's repeat rule in plain words, without its time.

    "Every day, from the day after the campaign starts until the day after
    it ends." Both starting points' bounds follow the campaign, so each is
    one of the Report emails list's bound labels. Each ends after the
    campaign's last day (decision 9), so the text says it keeps sending then.
    """
    from parishkit.stewardship.reports.report_email_list import (
        FROM_LABELS,
        UNTIL_LABELS,
    )

    if schedule["frequency"] == "daily":
        lead = _("Every day")
    else:
        days = " and ".join(str(WEEKDAYS[day]) for day in schedule["weekdays"])
        lead = _("Every %(days)s") % {"days": days}
    bounds = f"{FROM_LABELS[schedule['from']]} {UNTIL_LABELS[schedule['until']]}"
    after = _("It keeps sending after the campaign's last day.")
    return f"{lead}, {bounds}. {after}"


def rows(form):
    """The suggested report emails as the page draws them, one per key.

    Each row names the report, says its rule, recipients and blocks in
    plain words, and carries its checkbox and time field (bound fields of
    ``form``, so a refused save shows what was posted).
    """
    from parishkit.stewardship.reports.report_email_editor import block_labels
    from parishkit.stewardship.reports.report_email_list import entry_label

    result = []
    for key in form.keys:
        values = starting_point(key, form.campaign_id)
        result.append(
            {
                "key": key,
                "name": values["name"],
                "rule": rule_text(values["schedule"]),
                "recipients": ", ".join(
                    entry_label(entry, {}) for entry in values["recipients"]
                ),
                "blocks": ", ".join(block_labels(values, form.campaign)),
                "tick": form[tick_name(key)],
                "time": form[time_name(key)],
            }
        )
    return result
