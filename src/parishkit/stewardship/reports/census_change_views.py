"""Admin/Staff Census changes worklist, with CSV, XLSX and PDF downloads (#528).

The page and its downloads follow the Limitations report
(``limitation_views``): filters, sorting and paging travel in CSRF-protected
POST bodies, so search text never reaches an address; the table pages and
sorts in place; a download is the filtered list rendered in memory, in the
timezone the page chose (this browser's); and every view and download is
audited as a count only.

A row's Entered in ParishSoft, Ignore or Reopen (#528), and an
Administrator's Approve, Ignore or Back to To review on an Automatic row
(ADM-09), Edit value or Clear edit on one (ADM-09.04), and Use the Family's
value anyway on a Conflict (ADM-09.03), post to the page itself with the
page's filters and table choices, so the answer is the same page, refreshed
in place, with a notice naming the row.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.responses.census_resolution import resolve_census_change
from parishkit.stewardship.responses.census_review import (
    MAX_BULK,
    BulkStale,
    InvalidEdit,
    KeyRebound,
    Unchanged,
    campaign_today,
    edit_census_change,
    resolve_census_conflict,
    review_census_change,
    review_census_changes,
)
from parishkit.stewardship.responses.models import ProposedChange
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.exports import (
    DEFAULT_EXPORT_FORMAT,
    download_headers,
    page_download,
)
from parishkit.stewardship.web.refusals import Refusal, with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from .census_changes import (
    KINDS,
    LABELS,
    REVIEW_CHOICES,
    ROUTES,
    STATUS_CHOICES,
    STATUSES,
    CensusQuery,
    census_changes,
    census_csv,
    census_pdf,
    census_xlsx,
)
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .read_admission import admit_report_read

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
# The filter's choices, in order, with their plain labels.
STATUS_LABELS = {
    "open": "Work to do",
    **{key: STATUSES[key] for key in STATUS_CHOICES if key in STATUSES},
    "all": "Every status",
}
ROUTE_LABELS = dict(
    zip(
        ROUTES,
        ("Automatic and by hand", "Automatic only", "By hand only"),
        strict=True,
    )
)
KIND_LABELS = dict(
    zip(
        KINDS,
        ("Every kind", "Contact details", "Moved", "Deceased", "New Member"),
        strict=True,
    )
)


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


# The list is complete in memory, so every column sorts here; the SQL order
# (Family DUID, then submission time) stays the tiebreak; the sort is stable.
SORTING = Sorting.by_column(
    {
        "family": lambda row: (_text(row["family_name"]), row["family_duid"]),
        "duid": lambda row: row["family_duid"],
        "who": lambda row: _text(row["who"]),
        # Members by DUID; New Members and household (Family) rows, which
        # have none, follow them.
        "member_duid": lambda row: (
            row["member_duid"] is None,
            row["member_duid"] or 0,
        ),
        "what": lambda row: _text(row["label"]),
        # Automatic first, then By hand, whatever the labels say.
        "route": lambda row: not row["automatic"],
        "status": lambda row: list(STATUSES).index(row["status"]),
        "submitted": lambda row: row["submitted_at"],
    },
    default="family",
    descending_first={"submitted"},
)


def paging_values(parameters):
    """Remove and return the table's page/size/sort values from the form.

    ``parameters`` is a mutable QueryDict. The values only choose what the
    screen shows, so they never reach the filters or a download.
    """
    values = {}
    for name in table_parameters():
        found = parameters.pop(name, None)
        if found is None:
            continue
        if len(found) != 1:
            raise ValueError("Table choices must be single values.")
        values[name] = found[0]
    # Refuse a bad choice now, as a 400, not later while rendering the page.
    paginate([], values, sorting=SORTING)
    return values


def _principal(request, store, *, read_only=False, export=False):
    """Every request reloads current policy; Ministry leaders never qualify."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.VIEW_CENSUS) or (
        export and not allows(principal, Capability.REPORT_EXPORT)
    ):
        raise PermissionError("Census changes are unavailable.")
    return principal


def _error(campaign_id, *, status):
    """No private filter or exception values, and a way back to the page."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/census-changes-error.html",
            {"campaign_id": campaign_id, "status": status},
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response)


def _audit(action, principal, campaign_id, outcome, count=0):
    """Retain access evidence as a count only, never a name or filter."""
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            action,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome, "count": count},
        )


def _choices(parameters, *, export):
    """Parse the posted form: paging (page) or format and timezone (download)."""
    extra = {}
    if export:
        fmt = parameters.pop("format", [DEFAULT_EXPORT_FORMAT])
        zone = parameters.pop("timezone", ["UTC"])
        if (
            len(fmt) != 1
            or fmt[0] not in FORMATS
            or len(zone) != 1
            or (zone[0] != "UTC" and zone[0] not in timezone_names())
        ):
            raise ValueError("Invalid census change export choice.")
        extra = {"format": fmt[0], "zone": ZoneInfo(zone[0])}
    else:
        extra["paging"] = paging_values(parameters)
    return extra


# The fields a row's resolution form adds to the page's own POST (#528).
RESOLUTION_FIELDS = ("resolve", "proposal", "version", "request_key", "note")
RESOLVED = {
    "entered": "marked entered in ParishSoft.",
    "ignored": "marked ignored.",
    "reopened": "reopened.",
}
# An Administrator's review of an Automatic row (#528, ADM-09): the decision
# each form value records (census_changes.REVIEW_CHOICES), and how the notice
# words it.
REVIEWS = dict(REVIEW_CHOICES)
REVIEWED = {
    "approve": "approved.",
    "ignore_automatic": "marked ignored.",
    "to_review": "sent back to To review.",
}
# An Administrator's Edit value and Clear edit on an Automatic row (#528,
# ADM-09.04), and how the notice words each; an edit equal to the Family's
# answer clears the edit, and says why.
EDITS = {
    "edit": "now has an edited value.",
    "clear_edit": "no longer has an edited value.",
}
EDIT_MATCHED = "no longer has an edited value: it matched the Family's answer."
EDIT_UNCHANGED = "already has that value to publish, so nothing was recorded."
# An Administrator's Use the Family's value anyway on a Conflict (#528,
# ADM-09.03): the Review form's button value, and how the notice words it.
RESOLVE_CONFLICT = "use_family"
CONFLICT_RESOLVED = "approved over ParishSoft's current value."
# PostgreSQL's SQLSTATE for a unique violation.
UNIQUE_VIOLATION = "23505"
STALE = (
    "was updated by someone else, so nothing was recorded. "
    "The list shows its current status."
)


def _resolution(parameters):
    """Remove a row's resolution fields from the form; None when there are none.

    A resolution is sent with the page's own filters and table choices, so
    the answer is the same page, refreshed in place.
    """
    found = {name: parameters.pop(name, None) for name in RESOLUTION_FIELDS}
    # Edit value's field (ADM-09.04): its form sends it with Save value and
    # with Clear edit, which ignores it; no other form has it.
    edited = parameters.pop("value", None)
    if edited is None and all(value is None for value in found.values()):
        return None
    if any(value is None or len(value) != 1 for value in found.values()):
        raise ValueError("A census change resolution needs every field once.")
    values = {name: value[0] for name, value in found.items()}
    if (
        (edited is None and values["resolve"] == "edit")
        or (edited is not None and values["resolve"] not in EDITS)
        or (edited is not None and len(edited) != 1)
    ):
        raise ValueError("Only Edit value's form sends an edited value, once.")
    if values["resolve"] == "clear_edit":
        edited = None
    resolution = {
        "action": values["resolve"],
        "proposal_id": UUID(values["proposal"]),
        "expected_version": int(values["version"]),
        "request_key": UUID(values["request_key"]),
        "note": values["note"].strip(),
    }
    if values["resolve"] in EDITS:
        resolution["value"] = edited[0] if edited is not None else None
    return resolution


# The fields the bulk review dialog adds to the page's own POST (#528,
# ADM-09): the button's decision, the page's bulk key, the optional note and
# the ticked rows, each "<proposal id>:<version shown>".
BULK_FIELDS = ("bulk", "bulk_key", "bulk_note")
BULK_TOO_MANY = Refusal(
    f"Choose at most {MAX_BULK:,} changes at a time. Nothing was recorded.",
    "Show fewer rows per page, then try again.",
)
REBOUND = Refusal(
    "This page was already used to record different choices, so nothing was recorded.",
    "Reload the page, then try again.",
)


def _bulk(parameters):
    """Remove a bulk review's fields from the form; None when there are none.

    Each ticked row carries the version it showed, so every change is
    checked against its own version. No row twice; anything else is a
    malformed form (a 400). More rows than one bulk review may cover raise
    ``TooMany``, which the dialog explains.
    """
    found = {name: parameters.pop(name, None) for name in BULK_FIELDS}
    rows = parameters.pop("change", None)
    if rows is None and all(value is None for value in found.values()):
        return None
    if rows is None or any(
        value is None or len(value) != 1 for value in found.values()
    ):
        raise ValueError("A bulk census review needs every field once.")
    values = {name: value[0] for name, value in found.items()}
    if values["bulk"] not in REVIEWS:
        raise ValueError("Invalid bulk census review.")
    if len(rows) > MAX_BULK:
        raise TooMany
    versions = {}
    for row in rows:
        identity, separator, version = row.partition(":")
        if (
            not separator
            or len(row) > 64
            or not version.isascii()
            or not version.isdecimal()
        ):
            raise ValueError("Invalid census change selection.")
        versions[UUID(identity)] = int(version)
    if not versions or len(versions) != len(rows):
        raise ValueError("A census change was chosen twice.")
    return {
        "action": values["bulk"],
        "versions": versions,
        "bulk_key": UUID(values["bulk_key"]),
        "note": values["bulk_note"].strip(),
    }


class TooMany(Exception):
    """More rows were ticked than one bulk review may cover."""


def _row_name(proposal):
    """The changed row as the notice names it: what changed, and whose.

    DUIDs, not names, so the notice reads the same whatever the snapshot
    holds; the row itself shows the names.
    """
    who = (
        "the Family"
        if proposal.entity_kind == "family"
        else "a New Member"
        if proposal.entity_kind == "proposed_member"
        else f"Member DUID {proposal.entity_key}"
    )
    label = LABELS.get(proposal.field, proposal.field)
    return f"{label} for {who} in Family DUID {proposal.submission.family.family_duid}"


def _resolve(service, principal, campaign_id, resolution):
    """Record a resolution; the notice the page shows about it.

    The proposal must belong to this page's campaign (otherwise it is denied,
    as an unknown one is). A change someone else updated first is not an
    error the reader caused: the page answers with its current state and
    says so, whether the service saw the newer version or the database
    refused a racing second write. Every other refusal (an action not allowed
    from the row's state, a forged or rebound request) is a 400, as a bad
    filter is.
    """
    action, proposal_id = resolution.pop("action"), resolution.pop("proposal_id")
    proposal = (
        ProposedChange.objects.select_related("submission__family")
        .filter(pk=proposal_id, submission__campaign_id=campaign_id)
        .filter(submission__mode="live")
        .first()
    )
    if proposal is None:
        raise PermissionError("This census change is unavailable.")
    name = _row_name(proposal)
    try:
        if action in EDITS:
            try:
                review = edit_census_change(
                    service.store, principal.identity, proposal_id, **resolution
                )
            except Unchanged:
                return f"{name} {EDIT_UNCHANGED}"
            if action == "edit" and not review.admin_value_set:
                return f"{name} {EDIT_MATCHED}"
            return f"{name} {EDITS[action]}"
        if action == RESOLVE_CONFLICT:
            resolve_census_conflict(
                service.store, principal.identity, proposal_id, **resolution
            )
            return f"{name} {CONFLICT_RESOLVED}"
        if action in REVIEWS:
            review_census_change(
                service.store,
                principal.identity,
                proposal_id,
                decision=REVIEWS[action],
                **resolution,
            )
        else:
            resolve_census_change(
                service.store,
                principal.identity,
                proposal_id,
                action=action,
                **resolution,
            )
    except StaleRecordError:
        return f"{name} {STALE}"
    except IntegrityError as error:
        # A racing write normally surfaces as StaleRecordError above, under
        # the row lock. Only the expected race (a second resolution of the
        # same version: a unique violation) becomes the stale notice. Any
        # other refusal (a guard or audit mismatch) is logged and fails the
        # request, so a future bug is never hidden behind the race wording.
        if getattr(error.__cause__, "sqlstate", None) == UNIQUE_VIOLATION:
            return f"{name} {STALE}"
        emit_failure(error, event=Event.TASK_FAILED)
        raise
    return f"{name} {REVIEWED[action] if action in REVIEWS else RESOLVED[action]}"


def _review_many(service, principal, campaign_id, bulk):
    """Record a bulk review; the notice the page shows about it.

    Every chosen change must belong to this page's campaign (otherwise the
    whole request is denied, as an unknown change is). All or none: one
    someone else updated first leaves every change as it is, and the notice
    names it, as a single row's would; a racing second write the database
    refuses reads the same, without a name. Any other refusal is a 400.
    """
    versions = bulk["versions"]
    proposals = {
        proposal.pk: proposal
        for proposal in ProposedChange.objects.select_related("submission__family")
        .filter(pk__in=list(versions), submission__campaign_id=campaign_id)
        .filter(submission__mode="live")
    }
    if len(proposals) != len(versions):
        raise PermissionError("A census change is unavailable.")
    action = bulk["action"]
    try:
        review_census_changes(
            service.store,
            principal.identity,
            versions,
            decision=REVIEWS[action],
            note=bulk["note"],
            bulk_key=bulk["bulk_key"],
        )
    except BulkStale as error:
        return f"{_row_name(proposals[error.proposal_id])} {STALE}"
    except IntegrityError as error:
        # As for one row (_resolve): only the expected race is the notice.
        if getattr(error.__cause__, "sqlstate", None) == UNIQUE_VIOLATION:
            return f"A chosen change {STALE}"
        emit_failure(error, event=Event.TASK_FAILED)
        raise
    if len(versions) == 1:
        return f"{_row_name(next(iter(proposals.values())))} {REVIEWED[action]}"
    return f"{len(versions):,} changes {REVIEWED[action]}"


def bulk_actions():
    """The bulk bar's buttons, one per review decision, in the Review's order.

    Each opens the page's one confirmation dialog, which takes its title
    and announcement from the button and counts the ticked rows (ui-v1.js).
    Its value is also its kind (data-confirm-kind), which makes it
    available only while every ticked row allows that decision (each row's
    box lists its Review's choices), so the dialog always acts on every
    ticked row; the service checks each row again.
    """
    # The button, then the dialog's title for one row and for several.
    words = {
        "approve": ("Approve", "Approve {name}?", "Approve {count} changes?"),
        "ignore_automatic": ("Ignore", "Ignore {name}?", "Ignore {count} changes?"),
        "to_review": (
            "Back to To review",
            "Send {name} back to To review?",
            "Send {count} changes back to To review?",
        ),
    }
    actions = []
    for action, _decision in REVIEW_CHOICES:
        label, title_one, title_many = words[action]
        actions.append(
            {
                "value": action,
                "label": label,
                "title_one": title_one,
                "title_many": title_many,
                "done_one": f"{{name}} {REVIEWED[action]}",
                "done_many": f"{{count}} changes {REVIEWED[action]}",
            }
        )
    return actions


def row_controls(rows):
    """Give each shown row what its Record outcome form needs (#528).

    A fresh request key per drawn form makes a repeated submission of the
    same form a replay, never a second resolution. The ids a row's controls
    are described by name who and what changed, and Entered in ParishSoft
    also points at the line saying it is final; they are built here because
    the {% button %} tag takes each attribute as one value.
    """
    for row in rows:
        row["request_key"] = uuid4()
        # Edit value's form has its own key, so its replay is never taken
        # for the Review form's (ADM-09.04).
        row["edit_key"] = uuid4()
        row["describedby"] = f"who-{row['id']} what-{row['id']}"
        row["final_describedby"] = f"{row['describedby']} final-{row['id']}"
        # Use the Family's value anyway is also described by its help line.
        row["conflict_describedby"] = f"{row['describedby']} conflict-{row['id']}"
        # Save value is also described by its complete-gate hint.
        row["edit_describedby"] = f"{row['describedby']} edit-complete-{row['id']}"
        # How the confirmation dialog names the row: what changed, and whose.
        row["confirm_name"] = f"{row['label']} for {row['who']}"


def _respond(request, campaign_id, *, export, render):
    """Shared admission, purge protection, audit and failure handling.

    ``render(result, query, extra)`` returns the bytes. Both the page and a
    download read on the web connection under the interactive campaign read
    guard; a download is rendered in memory, not a stored export file, as
    the Limitations report's is (see ``limitation_views._respond``).
    """
    finish, handed_off = None, False
    action = Action.CENSUS_CHANGES_EXPORTED if export else Action.CENSUS_CHANGES_VIEWED
    try:
        service = runtime()
        principal = _principal(request, service.store, export=export)
        admit_report_read(campaign_id)
        try:
            if request.GET:
                raise ValueError("Census change filters require private POST state.")
            parameters = request.POST.copy()
            parameters.pop("csrfmiddlewaretoken", None)
            bulk = None if export else _bulk(parameters)
            resolution = None if export else _resolution(parameters)
            if bulk is not None and resolution is not None:
                raise ValueError("One census change action at a time.")
            extra = _choices(parameters, export=export)
            query = CensusQuery.parse(parameters)
            # Each is recorded in its own work transaction, before the
            # page's read, so the page that answers shows the result.
            if resolution is not None:
                extra["notice"] = _resolve(service, principal, campaign_id, resolution)
            elif bulk is not None:
                extra["notice"] = _review_many(service, principal, campaign_id, bulk)
        except TooMany:
            return validation_response(
                [FieldError(ErrorCode.INVALID)], refusal=BULK_TOO_MANY
            )
        except KeyRebound:
            # The same drawn form was sent again with other choices (its
            # first answer was lost): nothing was saved, and a reload draws
            # fresh forms with fresh keys.
            return validation_response(
                [FieldError(ErrorCode.STALE)], status=409, refusal=REBOUND
            )
        except InvalidEdit as error:
            # An edited value the change cannot take (ADM-09.04): its static
            # reason, shown in place after the row's Edit value form, and
            # logged by the shared refusal hook with the field's name only.
            return validation_response(
                [FieldError(ErrorCode.INVALID, "value")],
                refusal=Refusal(str(error), "Correct the value, then save it again."),
            )
        except ValueError:
            return _error(campaign_id, status=400)
        finalized, count = False, 0

        def finish(completed):
            """Audit completion once, after releasing the read transaction."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    action,
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the list is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True, export=export)
            if fresh.identity != principal.identity:
                raise PermissionError("Census change access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data reaches the template or file writer."""
            nonlocal count
            result = census_changes(campaign_id, query, principal)
            count = len(result["rows"])
            return iter((render(result, query, extra),))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
            # No filename: that would route the read through the download pool.
            **({"content_type": FORMATS[extra["format"]]} if export else {}),
        )
        if response.status_code == 503 and not response.streaming:
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        if export and handed_off:
            for name, value in download_headers(
                export_file_name(extra["format"], datetime.now(UTC)),
                content_type=FORMATS[extra["format"]],
            ).items():
                response[name] = value
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except ValueError as error:
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return _error(campaign_id, status=503)
    finally:
        if finish is not None and not handed_off:
            finish(False)


@require_http_methods(["GET", "POST"])
def report(request, campaign_id):
    """The default GET shows the work to do; filters use POST."""

    def render(result, query, extra):
        """The native page: one table that pages and sorts in place."""
        action = reverse("admin:census_changes")
        table = paginate(
            result["rows"],
            extra["paging"],
            carry=list(query.form_values().items()),
            sorting=SORTING,
        )
        row_controls(table.rows)
        # Other campaign work (a restore review, cleanup, purge or go-live
        # step) holds the campaign: the page offers no Record outcome, as
        # the service would refuse it, and says why.
        mutable = True
        try:
            admit_campaign(campaign_id, mutating=True)
        except PermissionError:
            mutable = False
        return render_to_string(
            "stewardship/census-changes.html",
            result
            | {
                "table": replace(table, method="post", action=action),
                "campaign_id": campaign_id,
                "query": query,
                "query_fields": query.form_values(),
                "status_choices": STATUS_LABELS.items(),
                "route_choices": ROUTE_LABELS.items(),
                "kind_choices": KIND_LABELS.items(),
                "export_timezones": sorted(timezone_names()),
                "notice": extra.get("notice"),
                "action": action,
                # The latest date an edited birth or death date may hold:
                # the campaign's today, as the server checks it.
                "edit_today": campaign_today(
                    Campaign.objects.select_related("active_configuration").get(
                        pk=campaign_id
                    )
                ).isoformat(),
                "mutable": mutable,
                # Bulk review is the Administrator's, on rows a Review is
                # offered for, while the page shows no more rows than one
                # bulk review covers (All rows can show more); one key per
                # drawn page makes a repeated post a replay
                # (census_review.bulk_key_for).
                "bulk": mutable
                and result["administrator"]
                and len(table.rows) <= MAX_BULK,
                "bulk_too_many": mutable
                and result["administrator"]
                and len(table.rows) > MAX_BULK,
                "max_bulk": MAX_BULK,
                "bulk_actions": bulk_actions(),
                "bulk_key": uuid4(),
            },
            request=request,
        ).encode()

    return _respond(request, campaign_id, export=False, render=render)


def export_file_name(fmt, now):
    """The download's file name, stamped with the UTC instant ``now``."""
    return f"stewardship-census-changes-{now.strftime('%Y%m%d-%H%M%SZ')}.{fmt}"


def export_body(campaign_id, result, fmt, zone):
    """The filtered list as the chosen file's bytes, times in ``zone``.

    The page's download and ``pk-admin export census`` (#463) both render
    through here, so their CSV, XLSX and PDF cannot drift. The PDF's page
    frame and the XLSX's "Report information" sheet (#926) also name the
    parish, campaign and download time; the PDF dates itself in the
    parish's date format, lent explicitly so the file never depends on the
    request's date middleware (as the response lists' downloads do). The
    CSV is only the table, so it reads none of them.
    """
    if fmt == "csv":
        return census_csv(result, zone)
    parish = (
        SystemConfiguration.objects.select_related("active_configuration__parish")
        .get()
        .active_configuration.parish
    )
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    context = {
        "parish": parish.name,
        "campaign": campaign.active_configuration.name,
        "as_of": database_now(),
    }
    if fmt == "xlsx":
        return census_xlsx(result, zone, **context)
    with dates.using(parish.date_format):
        return census_pdf(result, zone, **context)


@page_download("census_changes")
@require_http_methods(["POST"])
def export(request, campaign_id):
    """Download the filtered list as CSV, XLSX or PDF, rendered on request."""

    def render(result, query, extra):
        """The chosen file format, with times in the chosen timezone."""
        return export_body(campaign_id, result, extra["format"], extra["zone"])

    return _respond(request, campaign_id, export=True, render=render)
