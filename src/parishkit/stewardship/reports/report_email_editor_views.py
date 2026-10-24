"""New and Edit report email (#1786, delivery plan item 6).

The report-emails spec ("New and Edit report email", "Review and Apply",
"Skip one send", "Delete a report email"). One Administrator page at
``/admin/mail/report-emails/new/`` and ``/admin/mail/report-emails/<report>/``:

- GET draws the editor (``report_email_editor.EditorForm``), New blank (with
  Start from's starting points, ``report_email_editor.starting_points``) and
  Edit filled from the saved report, its time (and a one-time date) filled
  in this browser's zone by the page script from the moment drawn
  (``data-schedule-local``), and on Edit its past sends and Delete.
- ``action=preview`` (Add the report email, Review changes) reviews the
  form in the shared review dialog: a field the server refuses answers this
  page again with the errors (400), and an accepted review the dialog's
  fragment, built and signed by ``report_email_changes.build_review`` with
  the ``create`` or ``edit`` entry ``report-email preview`` takes too.
- ``action=skip_preview`` and ``action=unskip_preview`` (Skip selected
  sends and Send selected sends, from the Next sends table, each naming
  one or more ``send`` slot keys) and ``action=delete_preview`` (Delete)
  review those changes the same way.
- ``action=preview`` with ``render=yes`` (Preview, #2035) draws the
  unsaved form from current data in its own dialog
  (``report_email_preview_views.preview_answer``), saving nothing; a
  refused field answers as Review does.
- ``action=preview`` with ``test=yes`` (Send a test to me, #2036), a page
  script's post, sends the unsaved form as Preview draws it to the
  Administrator (``report_email_test_views.test_answer``), saving nothing
  but the test; it answers JSON, a refused field by its label.
- ``action=confirm`` (the dialog's Apply) records the signed change under
  the work lock (``confirm_intent``). A change whose report would send
  after the campaign's last day needs the dialog's "Send after the campaign
  ends" ticked (``after_campaign=yes``, decision 9), which the signed
  change records, so it cannot be left out.

``schedule_preview`` answers the editor's live Next sends table: the next
five sends of the schedule as the form holds it, read only.
"""

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import admin_navigation
from parishkit.stewardship.accounts.admin_editing import (
    confirm_intent,
    database_now,
    error_response,
    principal,
)
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    unexpected_fields,
    with_default_code,
)

from . import report_email_editor as editor
from .report_blocks import REGISTRY
from .report_blocks.registry import AFTER_BUTTON
from .report_email_changes import (
    AFTER_CAMPAIGN,
    EDITABLE,
    blocked,
    build_review,
    change_state,
    confirm_scope,
    needs_after_campaign,
    open_send,
    parse_entries,
    preview_salt,
    report_sent,
    send_worked,
)
from .report_email_list import paged_sends, report_records, sends_prefix

ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    PermissionError,
    ValueError,
    LookupError,
    StaleRecordError,
    StorageInvariantError,
    signing.BadSignature,
)
# The editor's fields, besides the repeated ones (``MULTIPLE``).
FIELDS = frozenset(
    {
        "action",
        "csrfmiddlewaretoken",
        "base_digest",
        # The editor's Preview (#2035) posts the form with render=yes, and
        # its Send a test to me (#2036) with test=yes.
        "render",
        "test",
        *editor.EditorForm.base_fields,
    }
)
MULTIPLE = frozenset({"recipient", "block", "weekdays", "charts"})
SKIP_FIELDS = frozenset({"action", "csrfmiddlewaretoken", "base_digest", "send"})
SKIP_ACTIONS = {"skip_preview": "skip", "unskip_preview": "unskip"}
DELETE_FIELDS = frozenset({"action", "csrfmiddlewaretoken", "base_digest"})
CONFIRM_FIELDS = frozenset({"action", "csrfmiddlewaretoken", "preview"})
# Where an error summary's link for a field leads: a choice group's first
# choice, or the control that adds to a list.
ERROR_TARGETS = {
    "sends": "report-sends_0",
    "as_of": "report-as_of_0",
    "preset": "report-preset_0",
    "frequency": "report-frequency_0",
    "block": "report-add-block",
    "weekdays": "report-weekdays",
    "charts": "report-charts_0",
}


def _recheck(request, service):
    """Recheck the Administrator's access after the read or review."""
    if not allows(
        authenticated_admin(request, store=service.store, read_only=True),
        Capability.CONFIGURE,
    ):
        raise PermissionError("Report email access was revoked.")


def _saved(state, campaign, report_id):
    """The saved report ``report_id`` names (None for New), and the others.

    An id that is no report of the campaign is a 404: a deleted report's
    Edit page is gone with it.
    """
    records = {
        row["id"]: row["values"] for row in report_records(state[0], campaign.pk)
    }
    if report_id is None:
        return None, list(records.values())
    saved = records.get(str(report_id))
    if saved is None:
        raise Http404("No such report email.")
    others = [values for key, values in records.items() if key != str(report_id)]
    return saved, others


def _charts(ticked, campaign):
    """The Participation charts block's Charts boxes: key, label and ticked.

    Only the charts the campaign offers (``editor.offered_charts``).
    """
    return [
        {"key": key, "label": editor.CHART_LABELS[key], "checked": key in ticked}
        for key in editor.offered_charts(campaign)
    ]


def _blocks(form, saved, campaign):
    """The Blocks field's rows in their order, and the blocks not in it.

    A refused page keeps the posted order and ticks; otherwise the saved
    blocks, or for New none (Add a block offers every block). A block added
    from Add a block starts with every chart ticked (spec, "Blocks field").
    """
    keys = (
        form.data.getlist("block")
        if form.is_bound
        else [entry["block"] for entry in (saved or {}).get("blocks", [])]
    )
    ticked = (
        form.data.getlist("charts")
        if form.is_bound
        else editor.ticked_charts(saved, campaign)
    )
    rows = [
        {
            "key": key,
            "label": REGISTRY[key].label,
            "help": REGISTRY[key].help,
            "last": REGISTRY[key].placement == AFTER_BUTTON,
            "charts": _charts(ticked, campaign),
        }
        for key in keys
        if key in REGISTRY
    ]
    chosen = {row["key"] for row in rows}
    available = [
        {
            "key": key,
            "label": block.label,
            "help": block.help,
            "last": block.placement == AFTER_BUTTON,
            "charts": _charts(editor.offered_charts(campaign), campaign),
        }
        for key, block in REGISTRY.items()
        if key not in chosen
    ]
    return rows, available


def _chips(form, saved, choices):
    """The recipients field's chosen entries: value and label each."""
    values = (
        form.data.getlist("recipient")
        if form.is_bound
        else [
            editor.entry_value(entry) for entry in (saved or {}).get("recipients", [])
        ]
    )
    return [
        {"value": value, "label": editor.chip_label(value, choices)} for value in values
    ]


def _bulk(report_id, saved):
    """Whether Edit's Next sends table offers Skip and Send selected sends:
    only a saved repeating report's sends can be skipped one by one."""
    return report_id is not None and saved["schedule"]["type"] == "repeat"


def _sends_table(form, campaign_id, campaign, now, *, report_id, saved):
    """The Next sends table of the schedule ``form`` holds (None while it
    holds none, ``editor.drawn_values``); on Edit of a repeating report
    with its selection column, whose rows take boxes only while the
    schedule is as saved, since a skip acts on the saved schedule."""
    values = editor.drawn_values(form, saved, campaign, campaign_id)
    if values is None:
        return None
    bulk = saved is not None and _bulk(report_id, saved)
    as_saved = saved is not None and values["schedule"] == saved["schedule"]
    return editor.sends_table(
        values,
        campaign,
        now,
        bulk=bulk,
        worked=(lambda key: send_worked(str(report_id), key))
        if bulk and as_saved
        else None,
    )


def _page(request, *, state, campaign, report_id, saved, form, status=200):
    """The editor page, drawn from ``form`` (bound when refused)."""
    from django.utils import timezone

    values = campaign.active_configuration.values
    now = timezone.now()
    portal = editor.portal_users(state[0])
    users = editor.eligible_users(state[0])
    choices = editor.recipient_choices(users)
    rows, available = _blocks(form, saved, values)
    mode = state[0].mode
    context = {
        "campaign": campaign,
        "values": values,
        "new": report_id is None,
        "report_id": report_id,
        "saved": saved,
        "form": form,
        "choices": choices,
        # Never offered; the picker refuses them as typed (decision 2).
        "leader_addresses": editor.leader_addresses(portal),
        "chips": _chips(form, saved, choices),
        "blocks": rows,
        "available_blocks": available,
        "base_digest": state[0].active_configuration.digest,
        # A repeating report's days are the campaign's (spec, "Schedule
        # fields"); the page says so when this browser's zone differs.
        "campaign_zone": values["timezone"],
        "post_url": request.path,
        "list_url": reverse("admin:report_emails"),
        "cancel_url": _cancel_url(request),
        "preview_url": reverse("admin:report_email_schedule_preview"),
        "change_url": reverse("admin:report_email_changes"),
        "drawn_at": None,
        "sends_table": _sends_table(
            form, campaign.pk, values, now, report_id=report_id, saved=saved
        ),
        "sent": bool(report_id) and report_sent(str(report_id)),
        "busy": bool(report_id) and open_send(str(report_id), mode),
        "editor_mode": form["editor_mode"].value() or "basic",
        "as_of_labels": editor.AS_OF_LABELS,
        # The Introduction's visual editor starts from the sanitized HTML.
        "visual": _visual(form),
        "date_time_error": _date_time_error(form),
        # New's Start from (spec, "New and Edit report email"): the starting
        # points as the page script fills them, from report_presets.
        "starting_points": editor.starting_points(values, campaign.pk, choices)
        if report_id is None
        else None,
    }
    if saved is not None and not form.is_bound:
        moment = editor.moment(saved, values, now)
        context["drawn_at"] = moment.isoformat() if moment else None
        # Past sends page with the shared navigator (spec, "Past sends"),
        # which keeps the calendar's ``send`` and ``calendar`` choices.
        paged = paged_sends(
            campaign.pk,
            mode,
            request.GET,
            [str(report_id)],
            carry=[
                (name, request.GET[name])
                for name in ("send", CALENDAR)
                if name in request.GET
            ],
        ).get(str(report_id))
        context["past"] = paged["table"] if paged else None
    if status == 400:
        context["errors"] = [
            {
                "message": message,
                "field_id": None
                if field == "__all__"
                else ERROR_TARGETS.get(field, f"report-{field}"),
            }
            for field, messages in form.errors.items()
            for message in messages
        ]
    response = render(
        request, "stewardship/report-email-entry.html", context, status=status
    )
    if status == 400:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


def _visual(form):
    """The Introduction's HTML as the shared visual editor shows it: the
    server's sanitizer's markup, never the raw source (empty if refused)."""
    from parishkit.stewardship.web.content import sanitize_html

    try:
        return sanitize_html(form["html"].value() or "")
    except ValueError:
        return ""


# A one-time send's refusals that concern its date and its time together.
DATE_AND_TIME = frozenset({"time_passed", "time_repeated"})


def _date_time_error(form):
    """Whether the Time field's refusal also marks the one-time Date.

    "This send time has passed" and "That time happens twice" are about the
    date and the time together, so both fields are marked and an edit of
    either clears both (UX conventions, "Field errors and live checks").
    """
    if not form.is_bound or form.data.get("sends") != "once":
        return False
    errors = form.errors.as_data().get("time", [])
    return any(error.code in DATE_AND_TIME for error in errors)


CALENDAR = "calendar"


def _calendar_month(request):
    """The Campaign calendar month an Edit link came from (``2054-11``), or None.

    Only a GET (or HEAD) carries it; anything but a year and month is refused as an
    unknown address (a ``ValueError``), as Family mail's Edit refuses it.
    """
    from parishkit.stewardship.accounts.schedule_entry_views import year_month

    if request.method not in ("GET", "HEAD"):
        return None
    return year_month(request.GET, CALENDAR)


def _cancel_url(request):
    """Where Cancel goes: the calendar month the page was opened from, if any.

    Apply and Delete still return to Report emails, whose status line says
    what was applied.
    """
    month = _calendar_month(request)
    if month is None:
        return reverse("admin:report_emails")
    return f"{reverse('admin:schedule_calendar')}?month={month}"


def _form(request, state, campaign, saved, others, report_id):
    """The editor form, bound to the post (or drawn from the saved report)."""
    from django.utils import timezone

    values = campaign.active_configuration.values
    users = editor.portal_users(state[0])
    data = request.POST if request.method == "POST" else None
    form = editor.EditorForm(
        data,
        campaign=values,
        campaign_id=campaign.pk,
        saved=saved,
        others=others,
        users=users,
        now=database_now() if data is not None else timezone.now(),
        sent=bool(report_id) and report_sent(str(report_id)),
    )
    if data is None:
        form.initial = editor.initial(saved, values, timezone.now())
    return form


def _review(request, service, actor, report_id, entries, *, kind):
    """Build the review of ``entries`` and answer the dialog's fragment."""
    from django.utils import timezone

    with work_transaction():
        state, campaign = change_state(service, request.campaign_id)
        reviewed = build_review(
            service,
            actor,
            state,
            campaign,
            entries,
            base_digest=request.POST.get("base_digest"),
        )
        users = editor.eligible_users(state[0])
    if reviewed["preview"] is None:
        raise blocked()
    values = campaign.active_configuration.values
    now = timezone.now()
    choices = editor.recipient_choices(users)
    change = reviewed["changes"][0]
    return render(
        request,
        "stewardship/report-email-review.html",
        {
            "kind": kind,
            "change": change,
            "before": editor.describe(change.get("before"), values, now, choices),
            "after": editor.describe(change.get("after"), values, now, choices),
            "preview": reviewed["preview"],
            "after_campaign": change.get("after_campaign"),
            "end_date": values["end_date"],
        },
    )


def _confirm(request, service, actor, campaign_id):
    """Apply a reviewed change; the answer is its status page."""
    posted = request.POST
    allowed = CONFIRM_FIELDS | {"after_campaign"}
    if set(posted) - allowed or any(len(v) != 1 for _n, v in posted.lists()):
        raise unexpected_fields(posted, allowed=allowed)
    token = posted.get("preview", "")
    if (
        needs_after_campaign(token, campaign_id)
        and posted.get("after_campaign") != "yes"
    ):
        raise UserFacingError(AFTER_CAMPAIGN, fix=_("Nothing was changed."))
    listing = reverse("admin:report_emails")
    receipt = confirm_intent(
        request,
        service,
        actor,
        token=token,
        salt=preview_salt(campaign_id),
        link=request.path,
        current_scope=lambda service: confirm_scope(service, campaign_id),
    )
    admin_navigation.remember_origin(request, receipt.request_id, path=listing)
    return HttpResponseRedirect(
        reverse("admin:configuration_request", args=[receipt.request_id])
    )


def _editor(request, campaign_id, report_id=None):
    """The editor's GET and its four posts (see the module docstring)."""
    service = runtime()
    actor = principal(request, service)
    if campaign_id is None:
        raise LookupError("There is no current campaign.")
    request.campaign_id = campaign_id
    if request.FILES:
        raise unexpected_fields(request.FILES, allowed=())
    if request.method == "POST":
        filters(request.GET, allowed=set())
        action = request.POST.get("action")
        if action == "confirm":
            return _confirm(request, service, actor, campaign_id)
        if action in SKIP_ACTIONS and report_id is not None:
            if set(request.POST) - SKIP_FIELDS or any(
                len(values) != 1
                for name, values in request.POST.lists()
                if name != "send"
            ):
                raise unexpected_fields(
                    request.POST, allowed=SKIP_FIELDS, multiple={"send"}
                )
            operation = SKIP_ACTIONS[action]
            entries = parse_entries(
                [
                    {
                        "operation": operation,
                        "id": str(report_id),
                        "sends": request.POST.getlist("send"),
                    }
                ]
            )
            return _review(request, service, actor, report_id, entries, kind=operation)
        if action == "delete_preview" and report_id is not None:
            if set(request.POST) - DELETE_FIELDS:
                raise unexpected_fields(request.POST, allowed=DELETE_FIELDS)
            entries = [{"operation": "delete", "id": str(report_id)}]
            return _review(request, service, actor, report_id, entries, kind="delete")
        if action != "preview":
            # No sources: an unknown action; a known one counts its fields.
            raise unexpected_fields()
        if set(request.POST) - FIELDS - MULTIPLE or any(
            len(values) != 1
            for name, values in request.POST.lists()
            if name not in MULTIPLE
        ):
            raise unexpected_fields(
                request.POST, allowed=FIELDS | MULTIPLE, multiple=MULTIPLE
            )
    else:
        # The Campaign calendar names a send (``send``) and the month it
        # showed (``calendar``, added to every Edit link it draws, #1656).
        # Its Past sends navigator adds its page and size.
        paging = (
            {f"{sends_prefix(report_id)}{key}" for key in ("page", "size")}
            if report_id is not None
            else set()
        )
        filters(request.GET, allowed={"send", CALENDAR} | paging)
        _calendar_month(request)
    with read_transaction() if request.method == "GET" else work_transaction():
        state, campaign = change_state(service, campaign_id)
        saved, others = _saved(state, campaign, report_id)
        form = _form(request, state, campaign, saved, others, report_id)
        testing = request.method == "POST" and request.POST.get("test") == "yes"
        if testing and not form.is_valid():
            from .report_email_test_views import form_errors

            return form_errors(form)
        if request.method == "GET" or not form.is_valid():
            return _page(
                request,
                state=state,
                campaign=campaign,
                report_id=report_id,
                saved=saved,
                form=form,
                status=200 if request.method == "GET" else 400,
            )
        values = form.values()
    if request.POST.get("render") == "yes":
        # Preview of the unsaved form (spec, "Preview"): drawn, never saved.
        from .report_email_preview_views import preview_answer

        return preview_answer(
            request, actor, values, subject_id=report_id or campaign_id
        )
    if testing:
        # Send a test to me of the unsaved form (spec): only the test is kept.
        from .report_email_test_views import test_answer

        return test_answer(request, service, actor, values, definition_id=report_id)
    given = {name: value for name, value in values.items() if name in EDITABLE}
    entries = [
        {"operation": "create", "values": given}
        if report_id is None
        else {"operation": "edit", "id": str(report_id), "values": given}
    ]
    return _review(request, service, actor, report_id, entries, kind="write")


@require_http_methods(["GET", "HEAD", "POST"])
def report_email_new(request, campaign_id):
    """New report email."""
    try:
        response = _editor(request, campaign_id)
        _recheck(request, runtime())
        response["Cache-Control"] = "no-store"
        return response
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD", "POST"])
def report_email_edit(request, campaign_id, report_id):
    """Edit report email: New's page, filled in from the saved report."""
    try:
        response = _editor(request, campaign_id, report_id)
        _recheck(request, runtime())
        response["Cache-Control"] = "no-store"
        return response
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)


@require_POST
def schedule_preview(request, campaign_id):
    """The editor's live Next sends table of the posted fields (read only).

    The posted form is read as Review reads it; the schedule fields alone
    decide the table. While they cannot be read (a time not typed yet),
    it says so instead. ``report`` names an Edit page's report, whose skips
    and pauses the table keeps and whose sends still ahead take boxes for
    Skip and Send selected sends only while the schedule is as saved.
    """
    from django.utils import timezone

    try:
        service = runtime()
        principal(request, service, read_only=True)
        filters(request.GET, allowed=set())
        if campaign_id is None:
            raise LookupError("There is no current campaign.")
        with read_transaction():
            state, campaign = change_state(service, campaign_id)
            report = request.POST.get("report") or None
            saved, others = _saved(state, campaign, report)
            values = campaign.active_configuration.values
            now = timezone.now()
            form = editor.EditorForm(
                request.POST,
                campaign=values,
                campaign_id=campaign.pk,
                saved=saved,
                others=others,
                users=editor.portal_users(state[0]),
                now=now,
            )
            form.is_valid()
            table = _sends_table(
                form, campaign.pk, values, now, report_id=report, saved=saved
            )
        return render(
            request,
            "stewardship/report-email-schedule-preview.html",
            {"sends_table": table},
        )
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)
