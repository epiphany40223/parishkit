"""Mail › Report emails: the list, its row actions and past sends (#1786).

The report-emails spec ("Report emails page", "Report list", delivery plan
item 5). The page lists the current campaign's report emails with their
status and actions, each report's past sends under its row and the deleted
reports' sends under the table (``report_email_list``). Administrators and
Staff may open it; Staff see no recipients and no actions. An archived
campaign's page is read-only for everyone. The page shows no Family data
and records no audited read, so it re-reads itself every 30 seconds
(UX conventions, "Pages that update themselves").

Its row actions act in place through the row actions' confirmation dialog:

- **Pause**, **Resume** and **Delete** post here (``changes/``): the change
  is built and signed as ``pk-admin report-email preview`` builds it
  (``report_email_changes``), then recorded at once under the work lock
  (``confirm_intent``), and the dialog follows the change's status until it
  is applied, then redraws the table.
- **Send now** posts to ``manual-sends/``: one Send now request, keyed by
  the page's command id (``report_email_manual.request_send_now``), whose
  database guard refuses a changed report or one with another send open.
  It answers with this page naming the request (``?requested=``), whose
  status line links the send's saved copy once it is made.

A row's name and Edit open Edit report email, and New report email, above
the table, opens New (``report_email_editor_views``); New is unavailable at
the limit of 20, with its reason.
"""

from uuid import UUID, uuid4

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import admin_navigation
from parishkit.stewardship.accounts.admin_editing import (
    confirm_intent,
    error_response,
    principal,
)
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, passive_read
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.jobs.delivery_views import _command_scope
from parishkit.stewardship.jobs.delivery_views import _principal as admin_principal
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingStale,
    unexpected_fields,
)

from .export_views import _principal as report_principal
from .report_email_changes import (
    blocked,
    build_review,
    change_state,
    confirm_scope,
    parse_entries,
    preview_salt,
    refuse_archived,
)
from .report_email_list import listing, report_records
from .report_email_manual import request_send_now
from .report_email_models import ReportEmailManualRequest, ReportEmailSnapshot

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
CHANGE_FIELDS = frozenset({"base_digest", "operation", "report"})
SEND_NOW_FIELDS = frozenset({"report", "revision", "command_id", "configuration_id"})
# Send now's refusal by its database guard: the report changed since the
# page was drawn, or another send of it is still open (0161).
SEND_REFUSED = _(
    "This report email can't be sent now: it changed since this page was drawn, "
    "or another send of it is still being prepared or delivered."
)
SEND_REFUSED_FIX = _(
    "Wait for the other send to finish, then reload the page and try again."
)


def _requested(value, campaign_id):
    """The current campaign's Send now request ``value`` names, or None.

    ``value`` is the page's ``?requested=`` text (ValueError when it is
    not an id). Returns its report's id, its background task and, once its
    email is made, the send whose saved copy to link; a key that is no
    request of ``campaign_id`` answers None.
    """
    manual = (
        ReportEmailManualRequest.objects.filter(
            pk=UUID(str(value)), campaign_id=campaign_id
        )
        .values("pk", "task_id", "definition_id")
        .first()
    )
    if manual is None:
        return None
    # A report send is named by its preparation, which shares the request's key.
    made = ReportEmailSnapshot.objects.filter(preparation_id=manual["pk"]).exists()
    return {
        "report": str(manual["definition_id"]),
        "task_id": manual["task_id"],
        "send_id": manual["pk"] if made else None,
    }


def _context(system, *, administrator, requested):
    """The page's template context for the current campaign (or none)."""
    campaign = system.current_campaign
    context = {
        "campaign": campaign,
        "administrator": administrator,
        "refresh_url": reverse("admin:report_emails"),
    }
    if campaign is None:
        return context
    view = listing(
        system, campaign, system.mode, timezone.now(), administrator=administrator
    )
    names = {row["id"]: row["name"] for row in view["rows"]}
    if requested is not None:
        requested = _requested(requested, campaign.pk)
        if requested is not None:
            requested["name"] = names.get(requested["report"], "")
    from parishkit.stewardship.campaigns.report_values import REPORT_LIMIT

    return (
        context
        | view
        | {
            "at_limit": len(view["rows"]) >= REPORT_LIMIT,
            "limit": REPORT_LIMIT,
            "requested": requested,
            "base_digest": system.active_configuration.digest,
            "configuration_id": system.active_configuration_id,
            # A fresh key per drawing: a replayed Send now returns the same send.
            "command_id": uuid4(),
            "change_url": reverse("admin:report_email_changes"),
            "send_now_url": reverse("admin:report_email_send_now"),
        }
    )


def _recheck(request, service):
    """Recheck access after the read, so a revocation during it still refuses."""
    if not allows(
        authenticated_admin(request, store=service.store, read_only=True),
        Capability.CAMPAIGN_REPORT,
    ):
        raise PermissionError("Report email access was revoked.")


@require_http_methods(["GET", "HEAD"])
def report_emails(request):
    """List the current campaign's report emails, their sends and actions."""
    try:
        service = runtime()
        # The page re-reads itself every 30 seconds; that quiet re-read is
        # admitted passively, so it never extends the sign-in (UX
        # conventions, "Pages that update themselves"). An ordinary open
        # admits as every page does, so a session whose authority changed is
        # refreshed rather than refused.
        actor = report_principal(
            request, service.store, read_only=passive_read(request)
        )
        selected = filters(request.GET, allowed={"requested"})
        administrator = allows(actor, Capability.CONFIGURE)
        # The rows are read in one snapshot; the page is drawn after it, as
        # its chrome reads its own state.
        with read_transaction():
            system = SystemConfiguration.objects.select_related(
                "active_configuration", "current_campaign__active_configuration"
            ).get()
            context = _context(
                system,
                administrator=administrator,
                # Only an Administrator's page names a Send now request.
                requested=selected.get("requested") if administrator else None,
            )
        response = render(request, "stewardship/report-emails.html", context)
        _recheck(request, service)
        response["Cache-Control"] = "no-store"
        return response
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)


def _posted(parameters, required):
    """Refuse a post with unknown, missing or repeated fields; its values.

    ``required`` are the post's fields; the CSRF token may come as a field
    too (a form) or only in its header (a script).
    """
    allowed = required | {"csrfmiddlewaretoken"}
    if not required <= set(parameters) <= allowed or any(
        len(values) != 1 for _name, values in parameters.lists()
    ):
        raise unexpected_fields(parameters, allowed=allowed)
    return {name: parameters[name] for name in required}


@require_POST
def report_email_changes(request, campaign_id):
    """Pause, Resume or Delete one report email, as its dialog confirmed.

    The change is built and signed as its review would be, then recorded
    at once (``confirm_intent`` rechecks the applied configuration and the
    campaign's work under the work lock). A change that work in progress
    or uncertain blocks is refused, naming Sent mail. The answer is the
    change's status page, which the dialog follows until it is applied.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields(request.FILES, allowed=())
        posted = _posted(request.POST, CHANGE_FIELDS)
        if posted["operation"] not in ("pause", "resume", "delete"):
            # No sources: an unknown operation value, not an unknown field.
            raise unexpected_fields()
        entries = parse_entries(
            [{"operation": posted["operation"], "id": posted["report"]}]
        )
        if campaign_id is None:
            raise LookupError("There is no current campaign.")
        listing_url = reverse("admin:report_emails")
        with work_transaction():
            state, campaign = change_state(service, campaign_id)
            reviewed = build_review(
                service,
                actor,
                state,
                campaign,
                entries,
                base_digest=posted["base_digest"],
            )
        if reviewed["preview"] is None:
            raise blocked()
        receipt = confirm_intent(
            request,
            service,
            actor,
            token=reviewed["preview"],
            salt=preview_salt(campaign_id),
            link=listing_url,
            current_scope=lambda service: confirm_scope(service, campaign_id),
        )
        admin_navigation.remember_origin(request, receipt.request_id, path=listing_url)
        return HttpResponseRedirect(
            reverse("admin:configuration_request", args=[receipt.request_id])
        )
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)


def _guard_refused(error):
    """Whether Send now's database guard (not an outage) refused the request."""
    return isinstance(error, IntegrityError) and getattr(
        error.__cause__, "sqlstate", None
    ) in {"23514", "23505"}


@require_POST
def report_email_send_now(request, campaign_id):
    """Send one report email now, once per command id, and return to the list.

    The post names the report and the revision the page drew (a changed
    report is refused as stale by the database guard), the page's command
    id and applied configuration. The answer is the list naming the
    request, whose status line follows it to the send's saved copy.
    """
    try:
        service = runtime()
        actor = admin_principal(request, service.store, activity=True)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields(request.FILES, allowed=())
        posted = _posted(request.POST, SEND_NOW_FIELDS)
        report = str(UUID(posted["report"]))
        command_id = UUID(posted["command_id"])
        system = SystemConfiguration.objects.select_related("current_campaign").get()
        if campaign_id is None or system.current_campaign_id != campaign_id:
            raise LookupError("There is no current campaign.")
        refuse_archived(system.current_campaign)
        if report not in {row["id"] for row in report_records(system, campaign_id)}:
            # No sources: the fields were allowed; the id is no report's.
            raise unexpected_fields()
        try:
            with _command_scope(request, service, actor):
                request_send_now(
                    service.store,
                    actor.identity,
                    campaign_id,
                    UUID(report),
                    UUID(posted["revision"]),
                    command_id=command_id,
                    configuration_id=UUID(posted["configuration_id"]),
                )
        except DatabaseError as error:
            if _guard_refused(error):
                raise UserFacingStale(SEND_REFUSED, fix=SEND_REFUSED_FIX) from None
            raise
        return HttpResponseRedirect(
            reverse("admin:report_emails") + f"?requested={command_id}"
        )
    except ObjectDoesNotExist:
        return error_response(PermissionError("No configuration."))
    except ERRORS as error:
        return error_response(error)
