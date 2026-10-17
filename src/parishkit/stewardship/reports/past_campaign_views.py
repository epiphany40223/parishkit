"""Past campaigns (#1018, slice 4): the Administrator's archive import page.

The page lists the imported past campaigns and adds one archive workbook at
a time through Review and Apply, in place, with the import service the
command line uses (``archive.imports``; campaign-history spec, "Past
campaigns page"):

- ``page`` (GET) draws the table of imported years, the Add an archive file
  form and the review region. ``?preview=<id>`` shows the Review card of
  this Administrator's own pending preview; ``?applied=<id>&outcome=…`` what
  Apply did. Neither value identifies a Family.
- ``review`` (POST, ``review/``) takes one upload of at most 20 MB. It is
  the second route allowed a body over the ingress default (21 MB,
  ``runtime_ingress``), so it refuses everything it can before anything
  reads the body: the CSRF token comes from the X-CSRFToken header only
  (``csrf_header_only``), the role is checked, a missing or oversize
  ``Content-Length`` is refused, and the deployment-wide Review lock is
  taken, so the web container's ``/tmp`` spool holds at most one file. The
  spooled file is closed (and deleted) inside the lock, before the isolated
  check runs. A stored preview answers with a redirect to ``?preview=``; a
  refused file with this page and its located problems (never a value).
- ``apply`` (POST, ``imports/``) makes the preview active, with the replace
  acknowledgement when the year already has an import, then redirects to
  ``?applied=``.
- ``remove`` (POST, ``removal/``) is the table's Remove row action, which
  the shared confirmation dialog posts as ``<year>:<version>``; it leaves a
  tombstone and redirects back.
- ``archive`` (POST, ``archive/``) is This campaign's archive's Download
  campaign archive (#1018 part (a)): with a Google sign-in within five
  minutes it queues the current campaign's archive workbook
  (``archive_exports.create_archive_export``) and answers with the export's
  status page; a stale sign-in gets the step-up page, which returns here.

Every route needs an Administrator (``CONFIGURE`` and ``FINANCIAL_DETAIL``);
the service checks again. Every answer is ``no-store``. Note texts appear
only here, escaped; audit events come from the service.
"""

import re
from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.admin_editing import error_response, principal
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.sessions import csrf_header_only
from parishkit.stewardship.archive import imports
from parishkit.stewardship.archive.workbook import note_flagged
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.presentation import number
from parishkit.stewardship.web.refusals import (
    UserFacingStale,
    unexpected_fields,
    with_default_code,
)
from parishkit.stewardship.web.tables import Sorting, whole_table

from .archive_exports import archive_offer, create_archive_export
from .archive_models import MAX_ARCHIVE_BYTES
from .comparison import money_text

# The Review route's body limit: one 20 MB file plus form overhead. Caddy
# admits the same bound on this route only (runtime_ingress).
MAX_REVIEW_REQUEST = 21 * 1024 * 1024
# The review region's id, which every in-place control here names.
REGION = "archive-review"
REVIEW_FIELDS = {"keep_family_rows", "csrfmiddlewaretoken"}
APPLY_FIELDS = {"preview", "replace", "csrfmiddlewaretoken"}
OUTCOMES = {"imported", "replaced", "already"}
# A Remove row action's value: the year and the version it was shown at.
REMOVE_VALUE = re.compile(r"([0-9]{4}):([0-9]{1,9})")
# The longest provenance shown in the table's toggletip.
TIP_LIMIT = 500
ERRORS = (
    ConfigError,
    DatabaseError,
    LookupError,
    PermissionError,
    StaleRecordError,
    ValueError,
    signing.BadSignature,
)

# Problems the page words itself, in the import service's problem shape.
TOO_LARGE = {
    "code": "file_size",
    "sheet": None,
    "cell": None,
    "rule": "the file is larger than 20 MB",
}
BUSY = {
    "code": "busy",
    "sheet": None,
    "cell": None,
    "rule": "another archive is being checked; try again in a minute",
}
EXPIRED = {
    "code": "stale",
    "sheet": None,
    "cell": None,
    "rule": "this review expired or was already applied; choose Review again",
}

# The table of imported years sorts on the server over every active import
# (a short table shown whole); newest year first by default.
SORTING = Sorting.by_column(
    {
        "year": lambda row: row["archive"].campaign_year,
        "campaign": lambda row: row["archive"].campaign_name.casefold(),
        "final": lambda row: row["archive"].is_final,
        "start": lambda row: row["archive"].start_date,
        "days": lambda row: row["archive"].days,
        "families": lambda row: row["archive"].families_responded,
        "pledged": lambda row: row["archive"].annual_pledged,
        "active": lambda row: row["archive"].active_families,
        "kept": lambda row: row["archive"].family_rows_kept,
        "imported": lambda row: row["archive"].imported_at,
    },
    default="-year",
    descending_first={"year", "days", "families", "pledged", "active", "imported"},
)


def _no_store(response):
    """The page holds giving totals; browsers keep no copy."""
    response["Cache-Control"] = "no-store"
    return response


def _administrator(request):
    """The signed-in Administrator: ``CONFIGURE`` and ``FINANCIAL_DETAIL``."""
    actor = principal(request, runtime())
    if not allows(actor, Capability.FINANCIAL_DETAIL):
        raise PermissionError("Past campaigns are the Administrator's.")
    return actor


def _emails(ids):
    """Portal user emails by id, for the "by whom" of imports and removals."""
    from parishkit.stewardship.accounts.policy_models import PortalUser

    ids = {value for value in ids if value is not None}
    return dict(PortalUser.objects.filter(pk__in=ids).values_list("id", "email"))


def _compact(value):
    """A calendar date in the parish format's compact variant."""
    return format_date(value, compact=True)


def _split_email(email):
    """An email as (local part, "@domain"), or None for no email."""
    if not email:
        return None
    local, at, domain = email.rpartition("@")
    return (local, at + domain) if at else ("", email)


def _row(archive, emails):
    """One table row: the import and its display texts."""
    provenance = archive.active_families_provenance
    if len(provenance) > TIP_LIMIT:
        provenance = provenance[: TIP_LIMIT - 1] + "…"
    return {
        "archive": archive,
        "day_1": _compact(archive.start_date),
        "days": number(archive.days),
        "families": number(archive.families_responded),
        "pledged": money_text(archive.annual_pledged),
        "active": number(archive.active_families),
        "provenance": provenance,
        # Split before the "@", where the narrow column may break the address.
        "imported_by": _split_email(emails.get(archive.imported_by_id, "")),
        "label": str(archive.campaign_year),
        "remove_value": f"{archive.campaign_year}:{archive.version}",
    }


def _tombstone(archive, emails):
    """One import in Technical details: its file, checksum and state."""
    return {
        "archive": archive,
        "removed_by": emails.get(archive.removed_by_id, ""),
    }


def note_rows(archive):
    """The Review card's notes in full: (label, text, flagged) triples.

    These become the report's data notes. A note longer than 500
    characters, or holding a dollar amount or five digits in a row, is
    flagged for a look before Apply, as the helper flags it
    (``workbook.flagged_notes``); kept ``count.*`` values are not notes the
    file's author wrote, so they are listed without a flag.
    """
    rows = [
        (
            _("Active Families counted from"),
            archive.active_families_provenance,
            note_flagged(archive.active_families_provenance),
        ),
        (_("Source system"), archive.source_system, False),
        (
            _("Repeat submissions"),
            archive.dedup_rule,
            note_flagged(archive.dedup_rule),
        ),
    ]
    for key, value in sorted(archive.notes.items()):
        text = str(value)
        rows.append((key, text, key.startswith("note.") and note_flagged(text)))
    return rows


def _card(actor, preview):
    """The Review card of this Administrator's pending preview, or None.

    What Apply will do is worked out again from the imports now (another
    Administrator may have imported or removed the year since the Review):
    ``already`` (the same file is active), ``replace`` (the year has another
    import) or ``add``. An expired, applied or someone else's preview gives
    None.
    """
    from .archive_models import CampaignArchive

    pending = CampaignArchive.objects.filter(
        pk=preview, state="pending", actor_id=actor.identity
    ).first()
    if pending is None:
        return None
    expires = pending.created_at + timedelta(seconds=imports.PREVIEW_SECONDS)
    if expires <= imports._now():
        return None
    active = CampaignArchive.objects.filter(state="active")
    already = active.filter(checksum=pending.checksum).first()
    current = already or active.filter(campaign_year=pending.campaign_year).first()
    action = "already" if already else "replace" if current else "add"
    emails = _emails([current.imported_by_id] if current else [])
    return {
        "archive": pending,
        "action": action,
        "current": current,
        "current_by": emails.get(current.imported_by_id, "") if current else "",
        "replace_blocked": bool(
            action == "replace" and current.is_final and not pending.is_final
        ),
        "day_1": format_date(pending.start_date),
        "last_day": format_date(pending.last_submission_date),
        "families": number(pending.families_responded),
        "pledged": money_text(pending.annual_pledged),
        "active": number(pending.active_families),
        "notes": note_rows(pending),
        "family_rows": number(
            pending.family_rows.count() if pending.family_rows_kept else 0
        ),
        "expires_at": expires,
    }


def _applied(selected):
    """What an Apply did, from ``?applied=<id>&outcome=…``, or None."""
    from .archive_models import CampaignArchive

    if "applied" not in selected:
        return None
    outcome = selected.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError("Unknown Apply outcome.")
    archive = CampaignArchive.objects.filter(
        pk=UUID(selected["applied"]), state="active"
    ).first()
    if archive is None:
        return None
    return {
        "outcome": outcome,
        "archive": archive,
        "imported_by": _emails([archive.imported_by_id]).get(
            archive.imported_by_id, ""
        ),
    }


def _year_problem():
    """The year refusal every import would get now, or None.

    Without a single four-digit stewardship year for the current campaign
    every Review is refused, so the page shows Review unavailable with the
    reason and a link to Campaign settings rather than letting it refuse.
    """
    try:
        imports.stewardship_year()
    except imports.ArchiveRefusal as refusal:
        return refusal.problems[0]
    return None


def _archive_offer():
    """This campaign's archive section: what Download would make now."""
    from parishkit.stewardship.web.admin_routes import _current_campaign_id

    from .archive_exports import campaign_now

    return archive_offer(_current_campaign_id(), now=campaign_now())


def _page_url(selected=None):
    """This page's address, keeping the given query values."""
    query = urlencode(selected or {})
    return reverse("admin:past_campaigns") + (f"?{query}" if query else "")


def _render(request, actor, selected, *, status=200, problems=(), more=0):
    """Draw the page: the table, the form and the review region.

    ``selected`` holds the validated query (sort, preview, applied and
    outcome); ``problems`` the located problems of a refused Review or
    Apply, which the review region lists instead of a card.
    """
    rows = imports.archives()
    active = [row for row in rows if row.state == "active"]
    emails = _emails(
        [row.imported_by_id for row in rows] + [row.removed_by_id for row in rows]
    )
    # The review card or Apply outcome shown stays when the table is sorted.
    view = {key: value for key, value in selected.items() if key != "sort"}
    table = whole_table(
        [_row(archive, emails) for archive in active],
        sorting=SORTING,
        sort=SORTING.parse(selected),
        carry=tuple(view.items()),
    )
    preview = selected.get("preview")
    card = _card(actor, UUID(preview)) if preview else None
    problems = list(problems)
    if preview and card is None and not problems:
        problems = [EXPIRED]
    # Each problem's place ("families!J17"), never the value found there.
    problems = [dict(problem, place=imports.place(problem)) for problem in problems]
    response = render(
        request,
        "stewardship/past-campaigns.html",
        {
            "table": table,
            "tombstones": [_tombstone(archive, emails) for archive in rows],
            "refresh_url": _page_url(selected),
            "card": card,
            "applied": _applied(view),
            "problems": problems,
            "more_problems": more,
            "year_problem": _year_problem(),
            "max_bytes": MAX_ARCHIVE_BYTES,
            "archive": _archive_offer(),
            "request_key": uuid4(),
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return _no_store(response)


@require_http_methods(["GET", "HEAD"])
def page(request):
    """The imported years, the upload form and any Review card or outcome."""
    try:
        actor = _administrator(request)
        selected = filters(
            request.GET, allowed={"sort", "preview", "applied", "outcome"}
        )
        return _render(request, actor, selected)
    except ERRORS as error:
        return error_response(error)


def _close_uploads(request):
    """Close every uploaded file, which deletes a spooled one from ``/tmp``."""
    for values in request.FILES.lists():
        for upload in values[1]:
            upload.close()


def _read_upload(request):
    """The one workbook's bytes, Keep Family rows and the file's name.

    Exactly one ``file`` and at most the Keep Family rows box (and the
    form's token) are accepted. The spool is closed before returning, so
    the lock that bounds it covers the whole time it exists.
    """
    try:
        try:
            files = request.FILES.lists()
        except MultiPartParserError:
            # A malformed multipart body: Django leaves POST and FILES empty.
            # No sources: a malformed body, which Django leaves unread.
            raise unexpected_fields() from None
        fields = request.POST.lists()
        if (
            set(request.POST) - REVIEW_FIELDS
            or set(request.FILES) != {"file"}
            or any(len(values) != 1 for _, values in fields)
            or any(len(values) != 1 for _, values in files)
        ):
            raise unexpected_fields(
                request.POST, request.FILES, allowed=REVIEW_FIELDS | {"file"}
            )
        upload = request.FILES["file"]
        keep = request.POST.get("keep_family_rows") == "yes"
        if upload.size > MAX_ARCHIVE_BYTES:
            return None, keep, upload.name
        return upload.read(MAX_ARCHIVE_BYTES + 1), keep, upload.name
    finally:
        _close_uploads(request)


@csrf_header_only
@require_http_methods(["POST"])
def review(request):
    """Check one uploaded workbook and keep it as a pending preview.

    Everything that can refuse without the body does so first: the token in
    the X-CSRFToken header (middleware), the role, the ``Content-Length``
    and the deployment-wide lock. Only then is ``request.FILES`` read.
    """
    try:
        actor = _administrator(request)
        filters(request.GET, allowed=set())
        length = request.META.get("CONTENT_LENGTH") or ""
        if not length.isdecimal():
            raise ValueError("A Review needs its Content-Length.")
        if int(length) > MAX_REVIEW_REQUEST:
            return _render(request, actor, {}, status=413, problems=[TOO_LARGE])
        try:
            with imports.ReviewLock():
                data, keep, name = _read_upload(request)
                if data is None or len(data) > MAX_ARCHIVE_BYTES:
                    return _render(request, actor, {}, status=413, problems=[TOO_LARGE])
                outcome = imports.review(
                    runtime().store,
                    actor.identity,
                    data,
                    keep_family_rows=keep,
                    file_name=name,
                )
        except imports.ReviewBusy:
            return _render(request, actor, {}, status=503, problems=[BUSY])
        except imports.ArchiveRefusal as refusal:
            return _render(
                request,
                actor,
                {},
                status=400,
                problems=refusal.problems,
                more=refusal.more,
            )
        return _no_store(
            HttpResponseRedirect(
                _page_url({"preview": outcome.archive.pk}) + f"#{REGION}"
            )
        )
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["POST"])
def apply(request):
    """Apply one pending preview: add, replace (acknowledged) or already."""
    try:
        actor = _administrator(request)
        filters(request.GET, allowed=set())
        if (
            request.FILES
            or set(request.POST) - APPLY_FIELDS
            or any(len(values) != 1 for _, values in request.POST.lists())
        ):
            raise unexpected_fields(request.POST, request.FILES, allowed=APPLY_FIELDS)
        preview = UUID(request.POST.get("preview") or "")
        try:
            outcome = imports.apply(
                runtime().store,
                actor.identity,
                preview,
                replace_acknowledged=request.POST.get("replace") == "yes",
            )
        except imports.ArchiveRefusal as refusal:
            return _render(
                request,
                actor,
                {"preview": str(preview)},
                status=400,
                problems=refusal.problems,
            )
        except StaleRecordError:
            return _render(request, actor, {}, status=409, problems=[EXPIRED])
        query = {"applied": outcome.archive.pk, "outcome": outcome.outcome}
        return _no_store(HttpResponseRedirect(_page_url(query) + f"#{REGION}"))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["POST"])
def remove(request):
    """Remove one year's import, leaving its tombstone; then redirect back.

    The confirmation dialog posts ``archive=<year>:<version>``; a year no
    longer imported, or changed since the page was drawn, is refused with
    nothing changed, and the dialog shows why.
    """
    try:
        actor = _administrator(request)
        filters(request.GET, allowed=set())
        allowed = {"archive", "csrfmiddlewaretoken"}
        values = request.POST.getlist("archive")
        if request.FILES or set(request.POST) - allowed or len(values) != 1:
            raise unexpected_fields(request.POST, request.FILES, allowed=allowed)
        match = REMOVE_VALUE.fullmatch(values[0])
        if match is None:
            # No sources: the fields were allowed; a value is malformed.
            raise unexpected_fields()
        try:
            imports.remove(
                runtime().store,
                actor.identity,
                int(match.group(1)),
                expected_version=int(match.group(2)),
            )
        except (LookupError, StaleRecordError):
            raise UserFacingStale(
                _("That year's import changed since the page was shown."),
                fix=_("Reload the page to see the imports now."),
            ) from None
        return _no_store(HttpResponseRedirect(_page_url()))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["POST"])
def archive(request):
    """Queue the current campaign's archive workbook and show its status page.

    The form posts only its one-time ``request_key``. The file holds each
    Family's giving, so, as for the financial export, it needs a Google
    sign-in within the last five minutes (#547): a stale one gets the
    step-up page, which returns to Past campaigns, and nothing is queued.
    A download unavailable now (or a reused form) redraws the page, whose
    section says why, with 409.
    """
    from parishkit.stewardship.accounts import admin_navigation
    from parishkit.stewardship.accounts.admin_editing import step_up_response
    from parishkit.stewardship.accounts.sessions import (
        FreshAuthenticationRequired,
        require_fresh,
    )
    from parishkit.stewardship.web.admin_routes import _current_campaign_id

    from .export_services import ExportRequestBound
    from .export_ui import _redirect
    from .facts import FactUnavailable
    from .read_admission import admit_report_read

    try:
        actor = _administrator(request)
        if (
            request.GET
            or set(request.POST) - {"csrfmiddlewaretoken"} != {"request_key"}
            or len(request.POST.getlist("request_key")) != 1
        ):
            raise unexpected_fields(
                request.GET,
                request.POST,
                allowed={"request_key", "csrfmiddlewaretoken"},
            )
        request_key = UUID(request.POST["request_key"])
        campaign_id = _current_campaign_id()
        if campaign_id is None:
            return _render(request, actor, {}, status=409)
        admit_report_read(campaign_id)
        try:
            require_fresh(request)
        except FreshAuthenticationRequired:
            return step_up_response(
                reverse("admin:past_campaigns"),
                admin_navigation.PAGES["past_campaigns"].label,
            )
        try:
            result = create_archive_export(
                runtime().store,
                actor.identity,
                campaign_id=campaign_id,
                request_key=request_key,
            )
        except (ExportRequestBound, FactUnavailable):
            return _render(request, actor, {}, status=409)
        return _redirect(result.pk)
    except ERRORS as error:
        return error_response(error)
