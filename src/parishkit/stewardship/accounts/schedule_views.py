"""Dates and mail schedules: the scheduled emails list, and the date-change review.

A GET without proposed dates shows the list (#878): the campaign dates on one
line, New scheduled email, and the scheduled emails table with its row
actions (``schedule_entry_views`` owns the New, Edit and Delete actions).
Proposed campaign dates (sent here by Campaign settings) and this page's own
POSTs keep the combined date-change review: every schedule's editor with the
proposed dates, previewed and confirmed together, as the campaign
configuration rules require. The review of every schedule change, from any
of these pages, confirms here.
"""

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.tables import whole_table

from . import admin_navigation
from .admin_editing import confirm, error_response, principal
from .authentication import runtime
from .campaign_mail import admits_test_mail
from .content_views import _records
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .schedule_changes import build_preview, confirm_scope, preview_salt
from .schedule_forms import (
    KINDS,
    Schedules,
    ScheduleWindow,
    email_names,
    schedule_action,
    schedule_order,
    window_text,
)
from .schedule_preview import work_summary
from .schedule_reads import campaign_schedules, live_end_at, schedule_state
from .schedule_table import SORTING, attach, schedule_rows
from .sessions import authenticated_admin


def campaign_email_names(records):
    """Readable names of the campaign's schedulable emails, by email ID."""
    return email_names(
        [
            row
            for row in records
            if row["values"]["kind"] == "email" and row["values"]["slot"] in KINDS
        ]
    )


def _list(request, state, campaign, editable, sort, *, end_live=False):
    """The scheduled emails list (#878): dates on one line, the table, New.

    The rows are the saved schedules in sending order, described from the
    counts-only work summary (``schedule_table``); ``sort`` is the When
    heading's token. ``refresh_url`` is this page's own address, sort
    included, from which the confirmation dialog redraws the table after a
    deletion is applied. ``end_live`` says a live campaign's end date may
    still change (#912), so the dates line links to Campaign settings.
    """
    configuration = state[0]
    values = campaign.active_configuration.values
    previous = sorted(
        campaign_schedules(configuration, campaign.pk), key=schedule_order
    )
    rows = schedule_rows(
        previous,
        values,
        work_summary(campaign.pk),
        timezone.now(),
        campaign_email_names(_records(configuration, campaign.pk)),
    )
    return render(
        request,
        "stewardship/schedule-settings.html",
        {
            "campaign": campaign,
            "values": values,
            "editable": editable,
            "end_live": end_live,
            "base_digest": configuration.active_configuration.digest,
            "table": whole_table(rows, sorting=SORTING, sort=sort),
            "refresh_url": request.get_full_path(),
            "new_url": reverse("admin:schedule_new"),
            "delete_url": reverse("admin:schedule_delete"),
            "window_text": window_text(values),
        },
    )


def _page(request, campaign, window, schedules, digest, *, editable, status=200):
    """The date-change review: proposed dates and every schedule's editor.

    Civil dates and the time zone show separately from browser-local audit
    timestamps.
    """
    # Schedules stay editable when the dates are locked: step 1 of 3 (#196).
    admin_navigation.place(request, flow="change", step="edit")
    # The table of saved schedules (#448): what each is, when it sends and
    # whether it has already sent, from the counts the review also reads.
    rows = attach(
        schedules,
        campaign.active_configuration.values,
        work_summary(campaign.pk),
        timezone.now(),
    )
    response = render(
        request,
        "stewardship/schedule-reconcile.html",
        {
            "campaign": campaign,
            "window": window,
            "schedules": schedules,
            "schedule_rows": rows,
            "base_digest": digest,
            "editable": editable,
            "templates_url": reverse("admin:content_catalog"),
            # Proposed dates arrive in the query string from campaign
            # settings, but POSTs with a query string are refused, so the
            # form posts to the clean path and carries the dates as fields.
            "post_url": request.path,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(
    request, service, actor, state, campaign, window, schedules, editable, salt
):
    """Review the posted change (``schedule_changes.build_preview``), or show errors."""
    context = build_preview(
        service,
        actor,
        state,
        campaign,
        window,
        schedules,
        base_digest=request.POST.get("base_digest"),
        salt=salt,
    )
    if context is None:
        return _page(
            request,
            campaign,
            window,
            schedules,
            state[0].active_configuration.digest,
            editable=editable,
            status=400,
        )
    admin_navigation.place(request, flow="change", step="review")
    return render(
        request,
        "stewardship/schedule-preview.html",
        context | {"post_url": request.path},
    )


@require_http_methods(["GET", "HEAD", "POST"])
def schedule_settings(request, campaign_id):
    """Current Admins list and reconcile schedules; dates stay structurally guarded.

    A GET without proposed dates is the list; proposed dates, or a POST
    preview, the date-change review. A POST confirm applies any reviewed
    schedule change, whichever page reviewed it, since every review signs
    with this campaign's one schedule salt.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        salt = preview_salt(campaign_id)
        if request.FILES or (request.method == "POST" and request.GET):
            raise ValueError("Invalid schedule parameters.")
        proposed = filters(
            request.GET, allowed={"start_date", "end_date", "timezone", "sort"}
        )
        sort = SORTING.parse(proposed)
        proposed.pop("sort", None)
        if any(len(value) > 64 for value in proposed.values()):
            raise ValueError("Invalid proposed campaign window.")
        if request.method == "POST" and request.POST.get("action") == "confirm":
            schedule_action(request.POST, window_fields=set())
            return confirm(
                request,
                service,
                actor,
                salt=salt,
                current_scope=lambda service: confirm_scope(service, campaign_id),
            )
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            state, campaign, editable = schedule_state(service, campaign_id)
            # A live campaign's end date alone may still change (#912).
            live_at = None if editable else live_end_at(state, campaign)
            if proposed and not editable and live_at is None:
                raise StaleRecordError("Campaign dates are structurally locked.")
            listing = request.method in {"GET", "HEAD"} and not proposed
            previous = campaign.active_configuration.values
            window = ScheduleWindow(
                request.POST if request.method == "POST" else None,
                prefix="window",
                previous=previous,
                editable=editable,
                live_at=live_at,
                proposed=proposed,
            )
            if request.method == "POST":
                schedule_action(request.POST, window_fields=window.open_fields)
            schedules = Schedules(
                request.POST if request.method == "POST" else None,
                prefix="schedules",
                templates=_records(state[0], campaign_id),
                campaign_id=campaign_id,
                campaign=previous,
                previous=campaign_schedules(state[0], campaign_id),
                # Each email choice links to its preview, test and editor;
                # while the campaign admits no test mail, the description says
                # why instead of linking a test that cannot work (#923).
                email_links=True,
                test_mail=admits_test_mail(state[0].mode, campaign),
            )
            response = (
                _list(
                    request,
                    state,
                    campaign,
                    editable,
                    sort,
                    end_live=live_at is not None,
                )
                if listing
                else _preview(
                    request,
                    service,
                    actor,
                    state,
                    campaign,
                    window,
                    schedules,
                    editable,
                    salt,
                )
                if request.method == "POST"
                else _page(
                    request,
                    campaign,
                    window,
                    schedules,
                    state[0].active_configuration.digest,
                    editable=editable,
                )
            )
        # Recheck access after the observation ends, so a GET's read-only
        # snapshot cannot hide a revocation committed while it rendered.
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Schedule access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
