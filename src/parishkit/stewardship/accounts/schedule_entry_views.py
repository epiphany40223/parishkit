"""New, Edit and Delete for one or a few mail schedules (#878).

Dates and mail schedules lists the saved schedules (``schedule_views``); these
views act on them:

- **New scheduled email** and **Edit scheduled email** are one page with one
  schedule's fields, plus, on New, a Repeat rule for reminders. Review and
  save builds the whole set of schedules (the saved ones unchanged, plus this
  one added or changed, or one reminder per repeat date) and reviews it.
  The page shows and takes send times in the browser's time zone, which the
  page script posts as ``zone``; ``schedule_local`` converts them to and
  from the campaign-local values a schedule keeps (#558).
- **Delete** answers the list's confirmation dialog: it removes the chosen
  schedules in one configuration change.

Neither path has validation of its own. Each turns its request into the
change document the schedule commands take (``admin_changes.form_data``),
binds it to the date-change review's own forms and runs the same
``schedule_changes.build_preview``: the formset's field and collection rules,
the whole-configuration candidate, the blocking-work count and the signed
intent. A save then shows that review, confirmed on Dates and mail
schedules; a delete records the signed intent at once through
``admin_editing.confirm_intent``, which rechecks the applied configuration and
the campaign's work under the work lock, as a confirmation always does.

Both refuse a schedule that has run or has work prepared (``schedule_table``'s
read-only statuses), so a sent row can be neither changed nor removed here,
even from an old link.
"""

import json
from datetime import date
from uuid import UUID

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.intervals import local_day
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.presentation import parish_date
from parishkit.stewardship.web.refusals import (
    UserFacingDenied,
    UserFacingError,
    UserFacingMissing,
    unexpected_fields,
)

from . import admin_navigation, schedule_local
from .admin_editing import confirm_intent, error_response, form_action, principal
from .authentication import runtime
from .campaign_mail import admits_test_mail
from .content_views import _records
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .schedule_changes import NO_CHANGES, build_preview, confirm_scope, preview_salt
from .schedule_forms import (
    WEEKDAYS,
    ScheduleForm,
    Schedules,
    ScheduleWindow,
    email_usage,
    schedule_labels,
    schedule_order,
    window_text,
)
from .schedule_preview import work_summary
from .schedule_reads import campaign_schedules, schedule_state
from .schedule_table import schedule_rows
from .sessions import authenticated_admin

ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    PermissionError,
    ValueError,
    LookupError,
    StaleRecordError,
    signing.BadSignature,
)
# One repeat rule adds at most this many reminders (the page script's limit,
# REPEAT_LIMIT in ui-v1.js). A campaign holds at most Schedules.max_num
# schedules, so a delete names at most that many.
REPEAT_LIMIT = 30
SCHEDULE_LIMIT = Schedules.max_num
# The one schedule's fields on the page, and the repeat rule's. The rule's
# fields are posted only so a refused page can show them again; the dates it
# gave are posted as ``repeat-dates``.
ENTRY = "schedule"
ENTRY_FIELDS = ("kind", "date", "time", "weekday", "template_version")
REPEAT_FIELDS = (
    "repeat",
    "frequency",
    "weekdays",
    "by",
    "day",
    "ordinal",
    "month_weekday",
    "start",
    "until",
    "dates",
)
REPEAT_MULTIPLE = {"repeat-weekdays", "repeat-dates"}
# The browser's IANA time zone, which ui-v1.js fills in (#558).
ZONE = "zone"
ZONE_MISSING = _(
    "Your browser didn't report a time zone, so this send time can't be "
    "read. Check your computer's time zone setting, then try again."
)
# Edit's in-place refusal of a save that changes nothing, as Campaign
# settings says "No settings have changed." (#751).
UNCHANGED = _("Nothing has changed. Change the date, time or email, then save.")


class RepeatRule(forms.Form):
    """New scheduled email's Repeat checkbox and rule, for reminders only.

    The page script expands the rule into dates (``window.ParishRecurrence``)
    and posts the dates that fit as ``dates``; only ``repeat`` and ``dates``
    decide what is saved. The rule's own fields are kept loose (any choice the
    page offers, or nothing) because they are only drawn again on a refused
    page, never trusted.
    """

    repeat = forms.BooleanField(required=False, label=_("Repeat"))
    frequency = forms.ChoiceField(
        required=False,
        label=_("How often"),
        choices=[
            ("weekly", _("Every week")),
            ("daily", _("Every day")),
            ("monthly", _("Every month")),
        ],
    )
    weekdays = forms.MultipleChoiceField(
        required=False,
        label=_("On these days"),
        choices=[(str(index), _(day)) for index, day in enumerate(WEEKDAYS)],
    )
    by = forms.ChoiceField(
        required=False,
        label=_("Repeat on"),
        choices=[
            ("day", _("A day of the month")),
            ("weekday", _("A weekday of the month")),
        ],
    )
    day = forms.IntegerField(required=False, min_value=1, max_value=31)
    ordinal = forms.ChoiceField(
        required=False,
        choices=[
            ("1", _("First")),
            ("2", _("Second")),
            ("3", _("Third")),
            ("4", _("Fourth")),
            ("-1", _("Last")),
        ],
    )
    month_weekday = forms.ChoiceField(
        required=False,
        choices=[(str(index), _(day)) for index, day in enumerate(WEEKDAYS)],
    )
    start = forms.DateField(required=False)
    until = forms.DateField(required=False)
    # Plain text values, checked below: the page script writes them.
    dates = forms.Field(required=False, widget=forms.MultipleHiddenInput)

    def clean_dates(self):
        """The posted dates: distinct calendar days, at most REPEAT_LIMIT."""
        values = list(self.cleaned_data.get("dates") or [])
        if len(values) > REPEAT_LIMIT or len(set(values)) != len(values):
            raise forms.ValidationError(
                _("One repeat adds at most %(limit)s reminders."),
                params={"limit": REPEAT_LIMIT},
            )
        try:
            return sorted(date.fromisoformat(value) for value in values)
        except ValueError:
            raise forms.ValidationError(_("Choose the dates again.")) from None


def _saved(state, campaign):
    """The campaign's saved schedules, in sending order."""
    return sorted(campaign_schedules(state[0], campaign.pk), key=schedule_order)


def _read_only(saved, campaign):
    """IDs of the saved schedules that have run or have work prepared."""
    rows = schedule_rows(
        saved,
        campaign.active_configuration.values,
        work_summary(campaign.pk),
        timezone.now(),
    )
    return {row.id for row in rows if row.read_only}


def _changeable(saved, campaign, identifiers):
    """Refuse an unknown or read-only schedule among ``identifiers``.

    Called inside the view's transaction. The table offers no action on
    either, so this is reached only from an old page or link.
    """
    known = {row["id"] for row in saved}
    if set(identifiers) - known:
        raise UserFacingMissing(
            _("That scheduled email no longer exists."),
            fix=_("Return to Dates and mail schedules to see the current list."),
            link=reverse("admin:schedule_settings"),
            link_label=_("Dates and mail schedules"),
        )
    if set(identifiers) & _read_only(saved, campaign):
        raise UserFacingDenied(
            _(
                "That scheduled email has already run, or is being prepared, "
                "so it can't be changed or deleted."
            ),
            fix=_("Return to Dates and mail schedules to see its status."),
            link=reverse("admin:schedule_settings"),
            link_label=_("Dates and mail schedules"),
        )


def review(service, actor, state, campaign, editable, entries, *, base_digest):
    """Bind a change document to the review's forms and build the review.

    ``entries`` are the change document's schedules (``admin_changes``): a
    saved ``id`` with the fields it changes, an ``id`` with ``delete``, or the
    fields of a new schedule. The window is posted unchanged. Returns
    ``(context, window, schedules, identifiers)``: ``context`` is
    ``build_preview``'s review, or None with the errors on the bound forms,
    and ``identifiers`` names each form (a saved ID, or ``new<n>``).
    """
    # The schedule commands' own binding, so the page and the command line
    # cannot read the same change differently.
    from parishkit.stewardship.admin_changes import form_data

    values = campaign.active_configuration.values
    saved = campaign_schedules(state[0], campaign.pk)
    data, identifiers = form_data(
        {"window": {}, "schedules": entries},
        saved,
        values,
        editable=editable,
        base_digest=base_digest,
    )
    window = ScheduleWindow(data, prefix="window", previous=values, editable=editable)
    schedules = Schedules(
        data,
        prefix="schedules",
        templates=_records(state[0], campaign.pk),
        campaign_id=campaign.pk,
        campaign=values,
        previous=saved,
    )
    context = build_preview(
        service,
        actor,
        state,
        campaign,
        window,
        schedules,
        base_digest=base_digest,
        salt=preview_salt(campaign.pk),
    )
    return context, window, schedules, identifiers


def _messages(errors):
    """The plain messages of a form's (or formset's) error list."""
    return [str(message) for message in errors]


def _carry_errors(entry, window, schedules, identifiers, targets):
    """Show the review's errors on the page's one schedule form.

    ``targets`` maps a reviewed form's identifier to the date it stands for
    (a repeat date) or None (the page's own schedule). A target's field
    errors go on the matching field, except a repeat date's own date error,
    which names that date at the top; every other form's errors, the
    formset's rules between schedules and the window's catch-all go at the
    top too, so nothing the server found is hidden.
    """

    def add(field, message):
        """Add one message, once."""
        existing = entry.errors.get(field or "__all__", [])
        if message not in existing:
            entry.add_error(field, message)

    labels = schedule_labels(schedules.previous)
    for index, form in enumerate(schedules.forms):
        identifier = identifiers[index]
        for field, messages in form.errors.items():
            for message in _messages(messages):
                if identifier in targets:
                    day = targets[identifier]
                    if day is not None and field == "date":
                        add(None, f"{parish_date(day)}: {message}")
                    elif field in entry.fields and not entry.fields[field].disabled:
                        add(field, message)
                    else:
                        add(None, message)
                else:
                    name = labels[index] if index < len(labels) else ""
                    add(None, f"{name}: {message}" if name else message)
    for message in _messages(schedules.non_form_errors()):
        add(None, message)
    for message in _messages(window.non_field_errors()):
        add(None, message)


def entry_form(data, templates, saved, schedule=None, kept=None, test_mail=True):
    """The page's one schedule form: blank for New, filled in for Edit.

    It offers the campaign's emails labelled by the schedules that send them,
    with their preview, test and edit links (#446). For Edit it carries the
    saved values, so its mail type is fixed, and the schedule's name, so the
    email summary can say which other schedules share its email. The saved
    date and time are campaign-local; the page script shows them in the
    browser's zone (``entry_times``), and ``kept`` is the saved time in that
    zone, so a legacy time with seconds posted back unchanged still reads.
    ``templates`` are the campaign's content records and ``saved`` its saved
    schedules in sending order. ``test_mail`` false (the campaign admits no
    test mail, see ``campaign_mail.admits_test_mail``) replaces each test link
    with a note saying why, so the page never links a test that cannot work
    (#923).
    """
    labels = dict(
        zip((row["id"] for row in saved), schedule_labels(saved), strict=True)
    )
    form = ScheduleForm(
        data,
        prefix=ENTRY,
        templates=templates,
        usage=email_usage(saved),
        links=True,
        test_mail=test_mail,
        label=labels.get(schedule["id"]) if schedule else None,
        initial=schedule["values"] | {"id": schedule["id"]} if schedule else None,
    )
    # The address names the schedule; a posted ID is never read.
    del form.fields["id"]
    if kept is not None:
        form.fields["time"].keep(kept)
    # This page takes times in the browser's zone, unlike the date-change
    # review and the first-campaign step, which share the form.
    form.fields["time"].help_text = _(
        "The time of day it is sent, in this computer's time zone, for example "
        "9:00 AM, 9am or 21:00."
    )
    return form


def entry_times(values, saved, schedule, now, *, bound):
    """The moments the page script shows in the browser's zone (#558).

    ``values`` is the campaign's configuration and ``saved`` its saved
    schedules. Returns the page's context: the saved invitations and
    reminders (``mailings_json``) and the campaign's first and last moments
    (``window_start``, ``window_end``) for the repeat rule's checks, the
    schedules still free (``room``) of the campaign's ``limit``, and for an
    unbound Edit page the schedule's own moment (``local_from``), which the
    script fills the fields from. A bound page shows what was posted, which
    is already in the browser's zone, so it has none.
    """
    zone = values["timezone"]
    return {
        "mailings_json": json.dumps(mailings(saved, schedule, zone, now)),
        "window_start": local_day(
            date.fromisoformat(values["start_date"]), zone
        ).interval.start.isoformat(),
        "window_end": local_day(
            date.fromisoformat(values["end_date"]), zone
        ).interval.end.isoformat(),
        "room": SCHEDULE_LIMIT - len(saved),
        "limit": SCHEDULE_LIMIT,
        "local_from": schedule_local.instant(schedule["values"], zone, now).isoformat()
        if schedule and not bound
        else "",
    }


def _fields(new):
    """Every field the page may post, for ``form_action``'s closed check."""
    names = {"base_digest", ZONE, *(f"{ENTRY}-{name}" for name in ENTRY_FIELDS)}
    if new:
        names.update(f"repeat-{name}" for name in REPEAT_FIELDS)
    return names


def _days(entry, repeat):
    """The send dates typed on a valid page, in the browser's zone.

    One per repeat date when the page repeats; otherwise the one date field
    (None for a digest). Repeat dates are also what ``_targets`` names.
    """
    if repeat is not None and repeat.cleaned_data["repeat"]:
        return repeat.cleaned_data["dates"]
    return [entry.cleaned_data.get("date")]


def _entries(entry, repeat, schedule, *, zone, campaign_zone, now):
    """The change document's schedules for a valid page: one, or one per date.

    Each typed date and time (in the browser's ``zone``) becomes the
    campaign-local values a schedule keeps (``schedule_local.to_campaign``);
    an edited schedule whose date and time were left as shown keeps its saved
    values. Raises ``UnknownZone`` for a blank or unknown zone, and ``ValueError``
    for a date too far out to convert.
    """
    data = entry.cleaned_data
    repeating = repeat is not None and repeat.cleaned_data["repeat"]
    result = []
    for day in _days(entry, repeat):
        sent_on, clock, weekday = schedule_local.to_campaign(
            day,
            data["time"],
            data.get("weekday"),
            zone=zone,
            campaign_zone=campaign_zone,
            now=now,
            saved=schedule["values"] if schedule is not None else None,
        )
        fields = {
            "date": sent_on.isoformat() if sent_on else None,
            "time": clock.isoformat(timespec="seconds"),
            "weekday": weekday,
            "template_version": data["template_version"],
        }
        if schedule is not None:
            fields = {"id": schedule["id"], **fields}
        else:
            fields["kind"] = "reminder" if repeating else data["kind"]
        result.append(fields)
    return result


def _targets(entries, schedule, days):
    """Which reviewed form stands for which entry (see ``_carry_errors``).

    The changed schedule keeps its ID; added ones are ``new<n>`` in order,
    each standing for its repeat date (as typed, in the browser's zone) when
    ``days`` lists them.
    """
    if schedule is not None:
        return {schedule["id"]: None}
    return {
        f"new{offset}": days[offset] if days else None for offset in range(len(entries))
    }


def _check_repeat(entry, repeat):
    """Repeat applies to reminders only and needs at least one date."""
    if repeat is None or not repeat.cleaned_data.get("repeat"):
        return
    if entry.cleaned_data.get("kind") != "reminder":
        repeat.add_error("repeat", _("Only reminders repeat."))
    elif not repeat.cleaned_data.get("dates"):
        repeat.add_error(
            "repeat",
            _(
                "This rule gives no date that can be added. Change the rule, "
                "or uncheck Repeat to choose one date."
            ),
        )


def _summary(entry, repeat):
    """The page's error summary: every message, linked to its field.

    The shared summary (components/errors.html) takes focus when the refused
    page loads, so the reader hears what to fix and can jump to each field.
    """
    errors = []
    for form in (entry, repeat):
        if form is None:
            continue
        for field, messages in form.errors.items():
            target = None if field == "__all__" else form[field].auto_id
            errors.extend(
                {"message": message, "field_id": target} for message in messages
            )
    return errors


def _entry_page(request, context, *, status=200):
    """Render New or Edit scheduled email; a refusal gets its summary."""
    if status == 400:
        context = context | {"errors": _summary(context["entry"], context["repeat"])}
    response = render(
        request, "stewardship/schedule-entry.html", context, status=status
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _kept(posted, schedule, values, now):
    """A posted Edit page's saved time in the browser's zone, or None.

    Only a POST needs it (``entry_form``'s ``kept``); a missing or unknown
    zone is refused later, with the page's own message.
    """
    if posted is None or schedule is None:
        return None
    try:
        return schedule_local.in_browser(
            schedule["values"], posted.get(ZONE, ""), values["timezone"], now
        )
    except UnknownZone:
        return None


def _entry(request, campaign_id, schedule_id=None):
    """New (``schedule_id`` None) or Edit scheduled email, GET or POST.

    A POST previews (``review``) and shows the review page, whose Apply all
    changes posts to Dates and mail schedules; a refused preview shows this
    page again with its errors. The posted date and time are in the
    browser's zone (``zone``) and are converted to the campaign's before
    the review, so its rules compare the moments the Admin chose.
    """
    service = runtime()
    actor = principal(request, service)
    filters(request.GET, allowed=set())
    if request.FILES:
        raise unexpected_fields()
    new = schedule_id is None
    # The page posts only a preview; its review confirms on the list.
    if (
        request.method == "POST"
        and form_action(
            request.POST,
            preview_fields=_fields(new),
            multiple_fields=REPEAT_MULTIPLE if new else frozenset(),
        )
        != "preview"
    ):
        raise unexpected_fields()
    posted = request.POST if request.method == "POST" else None
    now = timezone.now()
    with work_transaction() if posted is not None else read_transaction():
        state, campaign, editable = schedule_state(service, campaign_id)
        values = campaign.active_configuration.values
        saved = _saved(state, campaign)
        schedule = None
        if not new:
            _changeable(saved, campaign, {str(schedule_id)})
            schedule = next(row for row in saved if row["id"] == str(schedule_id))
        entry = entry_form(
            posted,
            _records(state[0], campaign.pk),
            saved,
            schedule,
            kept=_kept(posted, schedule, values, now),
            test_mail=admits_test_mail(state[0].mode, campaign),
        )
        repeat = RepeatRule(posted, prefix="repeat") if new else None
        context = {
            "campaign": campaign,
            "values": values,
            "entry": entry,
            "repeat": repeat,
            "new": new,
            "schedule": schedule,
            "base_digest": state[0].active_configuration.digest,
            "post_url": request.path,
            "list_url": reverse("admin:schedule_settings"),
            "window_text": window_text(values),
        } | entry_times(values, saved, schedule, now, bound=posted is not None)
        admin_navigation.place(request, flow="change", step="edit")
        if posted is None:
            return _entry_page(request, context)
        valid = entry.is_valid() & (repeat is None or repeat.is_valid())
        if valid:
            _check_repeat(entry, repeat)
            valid = repeat is None or not repeat.errors
        if not valid:
            return _entry_page(request, context, status=400)
        try:
            entries = _entries(
                entry,
                repeat,
                schedule,
                zone=posted.get(ZONE, ""),
                campaign_zone=values["timezone"],
                now=now,
            )
        except UnknownZone:
            entry.add_error(None, ZONE_MISSING)
            return _entry_page(request, context, status=400)
        except ValueError:
            # Only a date far outside any campaign cannot be converted.
            entry.add_error(
                "date",
                _("Choose a date within the campaign (%(window)s).")
                % {"window": window_text(values)},
            )
            return _entry_page(request, context, status=400)
        reviewed, window, schedules, identifiers = review(
            service,
            actor,
            state,
            campaign,
            editable,
            entries,
            base_digest=request.POST.get("base_digest"),
        )
        if reviewed is None and str(NO_CHANGES) in _messages(window.non_field_errors()):
            # Only Edit can post its schedule as saved; say so plainly.
            entry.add_error(None, UNCHANGED)
            return _entry_page(request, context, status=400)
        if reviewed is None:
            repeating = repeat is not None and repeat.cleaned_data["repeat"]
            days = _days(entry, repeat) if repeating else None
            _carry_errors(
                entry,
                window,
                schedules,
                identifiers,
                _targets(entries, schedule, days),
            )
            return _entry_page(request, context, status=400)
        admin_navigation.place(request, flow="change", step="review")
        # Apply all changes confirms on Dates and mail schedules, so the
        # change's status page returns to the list, not to this form.
        return render(
            request,
            "stewardship/schedule-preview.html",
            reviewed | {"post_url": reverse("admin:schedule_settings")},
        )


def mailings(saved, schedule, campaign_zone, now):
    """The saved invitations and reminders, for the repeat rule's checks.

    Each is ``{"kind", "at"}``, ``at`` the UTC moment it sends (ISO 8601),
    which the page script compares repeat dates with in the browser's zone.
    The schedule being edited is left out (Repeat is offered on New only,
    so it is None there).
    """
    return [
        {
            "kind": row["values"]["kind"],
            "at": schedule_local.instant(row["values"], campaign_zone, now).isoformat(),
        }
        for row in saved
        if row["values"]["kind"] in {"initial", "reminder"}
        and row["values"]["date"]
        and (schedule is None or row["id"] != schedule["id"])
    ]


def _recheck(request, service):
    """Refuse a response whose access was revoked while it was built."""
    if not allows(
        authenticated_admin(request, store=service.store, read_only=True),
        Capability.CONFIGURE,
    ):
        raise PermissionError("Schedule access was revoked.")


@require_http_methods(["GET", "HEAD", "POST"])
def schedule_new(request, campaign_id):
    """New scheduled email: one schedule, or a repeating reminder."""
    try:
        response = _entry(request, campaign_id)
        _recheck(request, runtime())
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD", "POST"])
def schedule_edit(request, campaign_id, schedule_id):
    """Edit scheduled email: the New page filled in from one saved schedule."""
    try:
        response = _entry(request, campaign_id, schedule_id)
        _recheck(request, runtime())
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)


def _chosen(parameters):
    """The schedule IDs a delete names: 1–SCHEDULE_LIMIT distinct UUIDs.

    Each way a list can be wrong has its own refusal, so the dialog says
    what happened: nothing chosen, one schedule twice (a stale or crafted
    page), or more than a campaign can hold.
    """
    if set(parameters) - {"csrfmiddlewaretoken", "base_digest", "schedule_id"} or any(
        len(values) != 1 for name, values in parameters.lists() if name != "schedule_id"
    ):
        raise unexpected_fields()
    values = parameters.getlist("schedule_id")
    if not values:
        raise UserFacingError(
            _("Choose at least one scheduled email to delete."),
            fix=_("Reload the page and try again."),
        )
    if len(values) > SCHEDULE_LIMIT:
        raise UserFacingError(
            _("Choose at most %(limit)s scheduled emails to delete at once.")
            % {"limit": SCHEDULE_LIMIT},
            fix=_("Nothing was deleted. Reload the page and choose again."),
        )
    if len(set(values)) != len(values):
        raise UserFacingError(
            _("The same scheduled email was chosen more than once."),
            fix=_("Nothing was deleted. Reload the page and choose again."),
        )
    return [str(UUID(value)) for value in values]


def _refusal(window, schedules):
    """A refused delete, with every rule it would break, as one message."""
    messages = [
        *_messages(schedules.non_form_errors()),
        *_messages(window.non_field_errors()),
        *(
            message
            for form in schedules.forms
            for messages in form.errors.values()
            for message in _messages(messages)
        ),
    ]
    return UserFacingError(
        " ".join(dict.fromkeys(messages))
        or _("These scheduled emails can't be deleted together."),
        fix=_("Nothing was deleted. Change the other schedules first, then try again."),
    )


@require_http_methods(["POST"])
def schedule_delete(request, campaign_id):
    """Delete the chosen schedules in one change, as the dialog confirmed.

    The change is built and signed as the review builds it, then recorded at
    once (``confirm_intent``: the applied configuration and the campaign's
    work are rechecked under the work lock). A refusal changes nothing. The
    answer is the change's status page, which the dialog follows until the
    change is applied.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields()
        identifiers = _chosen(request.POST)
        listing = reverse("admin:schedule_settings")
        with work_transaction():
            state, campaign, editable = schedule_state(service, campaign_id)
            saved = _saved(state, campaign)
            _changeable(saved, campaign, set(identifiers))
            reviewed, window, schedules, _forms = review(
                service,
                actor,
                state,
                campaign,
                editable,
                [{"id": identifier, "delete": True} for identifier in identifiers],
                base_digest=request.POST.get("base_digest"),
            )
        if reviewed is None:
            raise _refusal(window, schedules)
        if reviewed["preview"] is None:
            raise UserFacingDenied(
                _(
                    "Email from these schedules is being sent right now, or has an "
                    "uncertain result, so they can't be deleted yet."
                ),
                fix=_(
                    "Wait for it to finish, or resolve it on the Outgoing mail "
                    "page, then try again."
                ),
            )
        receipt = confirm_intent(
            request,
            service,
            actor,
            token=reviewed["preview"],
            salt=preview_salt(campaign_id),
            link=listing,
            current_scope=lambda service: confirm_scope(service, campaign_id),
        )
        admin_navigation.remember_origin(request, receipt.request_id, path=listing)
        _recheck(request, service)
        return HttpResponseRedirect(
            reverse("admin:configuration_request", args=[receipt.request_id])
        )
    except ERRORS as error:
        return error_response(error)
