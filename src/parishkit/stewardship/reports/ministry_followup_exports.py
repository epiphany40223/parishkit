"""Export these requests: the worklist's filtered set as a file (#1157, #1684).

The Members joining and Members leaving lists, and their CSV, XLSX and PDF
exports, were retired (Administrator decision, 2026-10-12, option (a)). Their
download moved to the Ministry follow-up queue: every request the queue's
applied filters, search and sort select (not just the page shown), with each
Member's contact details as the request page shows them, leader-scoped
exactly as the queue is.

The file is rendered on request, as Roster changes to enter and Census
changes are, rather than queued as a stored export: a stored capture would
need a schema change, since the export capture trigger accepts only one
Ministry's join or leave list or a packet. The rows come from the queue's
own selection read whole (``followup_page``) and the details from
``ministry_contacts``, all inside one guarded read; the file uses the same
document and writers as the retired lists' exports, so its Report
information sheet, PDF report card and privacy line are theirs.

Like those exports it is not privileged (only the directory, postal,
financial and timeline files are, #547). It holds exact times (Last
contact), so it asks for no time zone: it writes them in the browser's zone
and states that zone in the file (#1679). Each download is audited with its
count, the sorted Ministry DUIDs in the file and whether the
contacts were operational or publish-restricted; never names, contact
values, filters or search text.
"""

import io
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.urls import reverse
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)
from parishkit.stewardship.web.exports import download_headers, page_download
from parishkit.stewardship.web.presentation import phone as format_phone
from parishkit.stewardship.web.refusals import Refusal
from parishkit.stewardship.web.responses import campaign_response

from .export_views import SAFE_FAILURES
from .information_rendering import render_information
from .ministries import reads_every_ministry
from .ministry_contacts import request_contacts
from .ministry_documents import MinistryDocument, new_member_labels
from .ministry_followup import EXPORT_PDF_LIMIT, STATES, FollowupQuery, followup_page
from .ministry_followup_views import _error, _principal
from .ministry_worklist import WorklistQuery, chosen, place, read_rows, reviewer_value
from .read_admission import admit_report_read

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
TITLE = "Ministry follow-up requests"
# The queue's columns in its order (Member, Member DUID, Ministry, Request,
# Status, Last contact), with Status split into Status and Outcome as the
# page's one cell shows them, then each request's contact details from its
# request page (UX conventions, Downloaded files). The join-only and
# leave-only details are blank on the other kind's rows. The file has no
# Submitted column, as the queue has none (Administrator, 2026-10-13, #1157).
HEADINGS = (
    "Member",
    "Member DUID",
    "Ministry",
    "Request",
    "Stage",
    "Decision",
    "Reviewer's value",
    "Last contact",
    "Email",
    "Phones",
    "Known primary Family address",
    "Gender",
    "Age",
    "Current Ministry role",
)
# The queue's own words for a request with no recorded contact attempt.
NO_CONTACT = "None recorded"
UNAVAILABLE = "Unavailable"
PRIVACY = "Sensitive parish information. Share only with authorized recipients."
# The fix for a PDF above EXPORT_PDF_LIMIT requests; the line below the
# queue's export row says the same while its filters select more
# (ministry-followup.html).
PDF_TOO_LARGE_FIX = _("Choose CSV or XLSX, or narrow the filters.")


class PdfTooLarge(ValueError):
    """A PDF was asked for more requests than one may hold (EXPORT_PDF_LIMIT)."""


def pdf_refusal():
    """The answer to a PDF above the limit: a typed refusal with a way back.

    The queue page offers no PDF while its filters select more than the
    limit, so this answers only a stale page or a hand-made form.
    """
    return validation_response(
        [FieldError(ErrorCode.INVALID, "format")],
        refusal=Refusal(
            format_lazy(
                _("The PDF is limited to {limit} requests."),
                limit=f"{EXPORT_PDF_LIMIT:,}",
            ),
            fix=PDF_TOO_LARGE_FIX,
            link=reverse("admin:ministry_report"),
            link_label=_("Return to Ministry requests"),
        ),
    )


def _contact_values(details, action):
    """A request's contact cells, in HEADINGS order from Email to role.

    ``action`` is the request's kind. No readable details (``details`` is
    None) reads Unavailable only in the cells that apply to that kind; the
    other kind's cells stay blank, as on a readable row.
    """
    if details is None:
        if action == "leave":
            return (UNAVAILABLE, UNAVAILABLE, "", "", "", UNAVAILABLE)
        return (UNAVAILABLE,) * 5 + ("",)
    emails = "\n".join(details["emails"])
    phones = "\n".join(
        f"{key}: {format_phone(value)}"
        for key, value in sorted(details["phones"].items())
    )
    email = (
        "Not published"
        if details["email_visibility"] == "not_published"
        else emails or UNAVAILABLE
    )
    phone = (
        "Not published"
        if details["phone_visibility"] == "not_published"
        else phones or UNAVAILABLE
    )
    if details["kind"] == "leave":
        return (email, phone, "", "", "", details["current_role"] or UNAVAILABLE)
    return (
        email,
        phone,
        "\n".join(details["address_lines"]) or UNAVAILABLE,
        details["gender"] or UNAVAILABLE,
        f"{details['age']:,}" if details["age"] is not None else UNAVAILABLE,
        "",
    )


def queue_document(result, contacts, query, *, parish_name, requested_at, zone):
    """The file's one detached document: report details and one row a request.

    ``result`` is the queue's whole filtered selection (``followup_page``),
    ``contacts`` the details by request id (``request_contacts``) and
    ``zone`` the chosen display ``ZoneInfo``. Every value is text or an
    aware time placed in ``zone``, so the CSV, XLSX and PDF writers render
    the same cells.
    """

    def instant(value):
        """An aware time in the display zone, or blank."""
        return value.astimezone(zone) if value else ""

    rows = result["rows"]
    new_members = new_member_labels(
        [
            {
                "member_name": row["member_name"],
                "member_duid": row["member_duid"],
                "proposed_id": (contacts.get(row["id"]) or {}).get("proposed_id"),
            }
            for row in rows
        ]
    )
    lines = []
    for row in rows:
        details = contacts.get(row["id"])
        proposed = (details or {}).get("proposed_id")
        request = "Join" if row["action"] == "join" else "Leave"
        if row["action"] == "leave" and row.get("cannot_serve"):
            request += " (cannot participate)"
        lines.append(
            (
                row["member_name"],
                str(row["member_duid"])
                if row["member_duid"]
                else new_members.get(proposed, "New Member"),
                row["ministry_name"],
                request,
                # The review pipeline's words (#1684), as the worklist shows
                # them to this reader.
                row["stage_label"],
                row["decision_label"],
                reviewer_value(row),
                instant(row["last_contact_at"]) or NO_CONTACT,
            )
            + _contact_values(details, row["action"])
        )
    report_dates = {
        details["report_date"] for details in contacts.values() if details
    } - {None}
    metadata = (
        ("Report", TITLE),
        ("Parish", parish_name),
        ("Campaign", result["metadata"]["name"]),
        ("Source as of", instant(result["metadata"]["source_as_of"])),
        ("Requested at", instant(requested_at)),
        ("Display timezone", zone.key),
        *(
            (("Age reference date", date.fromisoformat(min(report_dates))),)
            if report_dates
            else ()
        ),
        ("Matching results", f"{len(rows):,}"),
        (
            "Filters and sort",
            json.dumps(query.form_values(), ensure_ascii=False, sort_keys=True),
        ),
        ("Privacy", PRIVACY),
        (
            "Contacts",
            "Publication flags and contact values are from the ParishSoft data "
            "as of the source time above. Ministry leaders receive the email "
            "and phones of each Member who asked to join or leave their "
            "Ministry, except a Member who cannot participate in any "
            "ministries, and otherwise only published values.",
        ),
        (
            "Addresses",
            "Known primary Family address, for join requests; separate mailing "
            "address unavailable in source.",
        ),
        # With replaced and withdrawn requests included, an earlier version's
        # row reads like its Member's current data, as the retired lists said.
        *(
            (
                (
                    "Request history",
                    "Replaced and withdrawn requests are included. Replaced and "
                    "cancelled rows are earlier versions, not extra current "
                    "requests. Names and contact details come from the current "
                    "ParishSoft data, not from when each version was submitted.",
                ),
            )
            if query.history == "all"
            else ()
        ),
    )
    return MinistryDocument(
        metadata,
        tuple(lines),
        len(lines),
        requested_at,
        HEADINGS,
        TITLE,
        sheet_name="Ministry requests",
    )


def _choices(parameters):
    """The queue's filters, and the file's format and display zone.

    The form posts the applied queue view's filters, search and sort (the
    page and page size only window the screen, so they are ignored), the
    format and the browser's zone, posted unasked (an explicit ``timezone``
    from an older tab or ``pk-admin`` wins, #1679); anything else, or a
    repeated value, is a 400.
    So is any Status choice but the queue's own statuses: a Member no longer
    in ParishSoft never goes into the file (Administrator, 2026-10-13), even
    from the Staff-only Status choice that lists them (#1217).
    """
    # The page's form posts the worklist's view (#1684), with its Stage;
    # ``pk-admin followup export`` still posts the queue's Status and
    # Outcome filters, until #1688 renames the commands.
    worklist = "stage" in parameters or "members" in parameters
    kind = WorklistQuery if worklist else FollowupQuery
    values = filters(
        parameters,
        allowed={
            "csrfmiddlewaretoken",
            "format",
            "timezone",
            "browser_timezone",
            *kind.__dataclass_fields__,
        },
    )
    values.pop("csrfmiddlewaretoken", None)
    fmt = values.pop("format", "")
    zone = dates.download_zone(
        values.pop("timezone", None), values.pop("browser_timezone", None)
    )
    if fmt not in FORMATS or (zone != "UTC" and zone not in timezone_names()):
        raise ValueError("Invalid follow-up export choice.")
    query = kind.parse(values)
    if worklist and query.members != "current":
        raise ValueError("Invalid follow-up export choice.")
    if not worklist and query.state not in {"any", "unresolved", *STATES}:
        raise ValueError("Invalid follow-up export choice.")
    return replace(query, page=1), fmt, ZoneInfo(zone)


def _audit(principal, campaign_id, outcome, count=0, ministries=()):
    """Retain the download's count and Ministry scope, never its values.

    ``ministries`` are the sorted Ministry DUIDs actually in the file, so
    the Admin log's Ministry filter finds the download, as it finds a
    Ministry export's result scope; ``ministry_operational`` says whether
    its contacts were operational or publish-restricted.
    """
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.MINISTRY_FOLLOWUP_EXPORTED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={
                "outcome": outcome,
                "count": count,
                "ministry_duids": sorted(set(ministries)),
                "ministry_operational": reads_every_ministry(principal),
            },
        )


def export_file_name(fmt, now):
    """The download's file name, stamped with the UTC instant ``now``."""
    return f"stewardship-ministry-requests-{now.strftime('%Y%m%d-%H%M%SZ')}.{fmt}"


def export_body(campaign_id, query, principal, fmt, zone, progress):
    """The queue's filtered requests as the chosen file's bytes, times in ``zone``.

    Runs inside the guarded read. ``query``, ``fmt`` and ``zone`` are what
    ``_choices`` returned. ``progress`` is a dict that receives ``count``
    and ``ministries`` (the Ministry DUIDs of the rows) as soon as the
    selection is read, so a file that fails afterwards, a PDF above the
    limit included, is still audited with what it reached. The page's
    download and ``pk-admin followup export`` (#463) both render through
    here, so their CSV, XLSX and PDF cannot drift.
    """
    # The queue's own selection with its default Member presence, so a
    # Member no longer in ParishSoft is left out of the file as from the
    # queue (#1217, Administrator, 2026-10-13).
    if isinstance(query, WorklistQuery):
        result = read_rows(campaign_id, query, principal)
        result["rows"] = chosen(result["rows"], query.stage)
    else:
        result = place(
            followup_page(campaign_id, query, principal, whole=True), principal
        )
    progress["count"] = count = len(result["rows"])
    progress["ministries"] = tuple(row["ministry_duid"] for row in result["rows"])
    # Refused before any contact is read or page drawn. The page opens its
    # content before its response is returned, so this reaches the view as
    # an exception, never as a broken download.
    if fmt == "pdf" and count > EXPORT_PDF_LIMIT:
        raise PdfTooLarge("Too many requests for one PDF.")
    contacts = request_contacts(campaign_id, principal, result["rows"])
    parish = (
        SystemConfiguration.objects.select_related("active_configuration__parish")
        .get()
        .active_configuration.parish
    )
    document = queue_document(
        result,
        contacts,
        query,
        parish_name=parish.name,
        requested_at=database_now(),
        zone=zone,
    )
    output = io.BytesIO()
    # The PDF dates itself in the parish's date format, lent explicitly, as
    # the other on-request downloads do.
    with dates.using(parish.date_format):
        render_information(document, output, format=fmt)
    return output.getvalue()


@page_download("ministry_followup")
@require_POST
def export(request, campaign_id):
    """Download the queue's filtered requests as CSV, XLSX or PDF (#1157).

    Admission, the guarded read and failure handling follow the queue's own
    page: a reader the queue would refuse is refused here, and the rows are
    exactly those the queue's selection gives this reader.
    """
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request.GET or campaign_id is None:
            raise ValueError("The export takes the queue's form only.")
        query, fmt, zone = _choices(request.POST)
        admit_report_read(campaign_id)
        finalized, progress = False, {}

        def finish(completed):
            """Audit completion once, after releasing the read transaction."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    progress.get("count", 0),
                    progress.get("ministries", ()),
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the file is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Ministry follow-up access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Read, shape and render the file inside the guarded read."""
            return iter(
                (export_body(campaign_id, query, principal, fmt, zone, progress),)
            )

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
            # No filename: that would route the read through the download pool.
            content_type=FORMATS[fmt],
        )
        if response.status_code == 503 and not response.streaming:
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        if handed_off:
            for name, value in download_headers(
                export_file_name(fmt, datetime.now(UTC)),
                content_type=FORMATS[fmt],
            ).items():
                response[name] = value
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except PdfTooLarge:
        return pdf_refusal()
    except ValueError:
        return _error(campaign_id)
    finally:
        if finish is not None and not handed_off:
            finish(False)
