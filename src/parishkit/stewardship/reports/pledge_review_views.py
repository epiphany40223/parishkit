"""Pledges: the financial stream's review worklist and report (#1686).

One row per Family's current pledge answer (the rows of Financial
stewardship detail with no filter), drawn by the shared review worklist
(``web/review_worklist``) with Stage first in its always-open filters (To
review by default), then a Family search. Each row offers Accept, Edit (the
annual pledge and frequency), Ignore, Back to review and Entered by hand as
its stage allows (``pipeline``'s role table), one row or in bulk, recorded
by ``pledge_review.PledgeDecisions``. Stage To enter by hand is the
"Pledges" list Staff type into ParishSoft from: the shared export
buttons below the table download the applied Stage and search as CSV, XLSX
or PDF (review pipeline spec, "Manual handling reports").

The pledges come from the Financial stewardship selection
(``stewardship_financial_report_v1``, read whole, without the ParishSoft
comparison money) and the stages from ``pledge_review.read_items``, in the
same guarded read; filtering, sorting and paging happen in memory, as the
other worklists' do. The view and each download are audited as a count
only; a Ministry leader never reaches either (``Capability.FINANCIAL_DETAIL``).
"""

import csv
import io
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, connection, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.role_refusal import require
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import (
    authenticated_admin,
    automatic_read,
    database_now,
)
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.pipeline import TERMS, Stage, Stream
from parishkit.stewardship.responses.financial import FREQUENCIES, installment
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.source_status import (
    NAME_NOT_RECORDED,
    family_status,
    source_status_label,
)
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web import dates, review_live
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)
from parishkit.stewardship.web.exports import (
    DEFAULT_EXPORT_FORMAT,
    csv_cell,
    download_headers,
    page_download,
)
from parishkit.stewardship.web.refusals import Refusal, with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.review_worklist import (
    ALREADY_HELD,
    NOT_SEEN,
    Column,
    build,
    page_address,
    read_decision,
    record,
)
from parishkit.stewardship.web.tables import (
    Sorting,
    fold,
    name_order,
    paginate,
    table_parameters,
)

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .financial import FREQUENCY_LABELS, FinancialQuery, name_families, shape_result
from .money import MoneyAmount
from .pledge_refresh import display, pledge_check_states
from .pledge_review import (
    WITHHELD,
    EditRefusal,
    PledgeDecisions,
    placement,
    read_items,
    reader_roles,
    version_of,
)
from .read_admission import admit_report_read

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
# The Stage filter's choices, To review first and the default (review
# pipeline spec, "Review worklist"). The refresh places Conflict (#1747),
# Entered incorrectly and Confirmed, and sets the Not seen in ParishSoft
# flag, a choice of its own after the stages while some pledge has it
# (#1704).
STAGES = (
    Stage.TO_REVIEW,
    Stage.CONFLICT,
    Stage.TO_ENTER_BY_HAND,
    Stage.ENTERED_BY_HAND,
    Stage.ENTERED_INCORRECTLY,
    Stage.CONFIRMED,
    Stage.IGNORED,
)
EVERY = "all"
STAGE_VALUES = frozenset({*(stage.value for stage in STAGES), NOT_SEEN, EVERY})
DEFAULT_STAGE = Stage.TO_REVIEW.value
PREFIX = "pledges"
# The worklist's identity columns, in the shared order (UX conventions,
# "Table column order"): the Family's name, then its DUID. The worklist adds
# Family's answer, Reviewer's value, Stage, Submitted, Last decision and
# Actions.
COLUMNS = (
    Column(
        "family",
        "Family",
        sort="family",
        detail="family_duid",
        detail_heading="Family DUID",
        detail_sort="duid",
    ),
)
# The files' columns: the page's, without Actions, its combined cells split
# into their parts (UX conventions, "Downloaded files"), and the pledge to
# enter (the reviewer's value, else the Family's) spelled out.
HEADINGS = (
    "Family",
    # Blank while the Family is in the campaign; otherwise #1216's
    # phrase, the Family cell's badge, right after the name it describes
    # (UX conventions, "Members and Families no longer in ParishSoft").
    "ParishSoft status",
    "Family DUID",
    "Annual pledge",
    "Frequency",
    "Share methods",
    "Reviewer's annual pledge",
    "Reviewer's frequency",
    "Annual pledge to enter",
    "Frequency to enter",
    # ParishSoft's upcoming-year amount the latest refresh check found
    # (#1704); blank before any.
    "ParishSoft annual pledge",
    "Stage",
    "Decision",
    "Entered by",
    "Entered at",
    "Submitted",
    "Last decision",
    "Last decision at",
    "Note",
)
# Relative PDF column widths, in percent of the page's body width: money and
# DUIDs stay on one line; names, share methods and notes wrap.
PDF_WEIGHTS = {
    "Family": 8,
    "ParishSoft status": 4,
    "Family DUID": 6,
    "Annual pledge": 6,
    "Frequency": 5,
    "Share methods": 7,
    "Reviewer's annual pledge": 6,
    "Reviewer's frequency": 5,
    "Annual pledge to enter": 6,
    "Frequency to enter": 5,
    "ParishSoft annual pledge": 6,
    "Stage": 5,
    "Decision": 5,
    "Entered by": 5,
    "Entered at": 6,
    "Submitted": 6,
    "Last decision": 5,
    "Last decision at": 6,
    "Note": 4,
}
PRIVACY = "Sensitive parish report. Share only with authorized recipients."
FORMER = "Former portal user"
CANNOT_GIVE = "Cannot contribute"
# The stages at which a file names the pledge to enter: accepted, and
# not yet or already entered (or confirmed by a refresh, #1704). At the
# others nothing is to be entered.
ENTERING = frozenset(
    {
        Stage.TO_ENTER_BY_HAND.value,
        Stage.ENTERED_BY_HAND.value,
        Stage.ENTERED_INCORRECTLY.value,
        Stage.CONFIRMED.value,
    }
)
# The stages before a pledge's mark, where ParishSoft already holding the
# amount leaves it (strict phase order, #1890).
BEFORE_STEP = frozenset({Stage.TO_REVIEW, Stage.TO_ENTER_BY_HAND})
NO_FREQUENCY = "No frequency"
CHANGED = "The Family changed its pledge after the last decision."
# A To review or To enter by hand pledge ParishSoft already holds (refresh
# rule 1, strict phase order, #1890): it keeps its stage, saying so. Until
# #1890 such a To review pledge was Confirmed with no reviewer ("Already in
# ParishSoft."); its stage is derived, so it now reads as this too.
HELD = ALREADY_HELD
# The same at $0.00: no pledge in ParishSoft and none to enter (such as a
# Family that cannot contribute), so the words do not claim ParishSoft
# holds something.
NOTHING = "Nothing to enter: ParishSoft holds no pledge."
EDIT_REFUSED = "Correct the value, then save it again."
# The worklist's default filters, as the Financial stewardship selection
# takes them: every Family's current pledge.
SELECTION = "SELECT stewardship_financial_report_v1(%s,%s::jsonb,NULL,NULL)::text"


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


# Rows equal in the chosen column keep the default order (UX conventions,
# "Default row order", #2124): the Family's name, its DUID, then the
# response's own id (a pledge names no Member).
SORTING = Sorting.by_column(
    {
        "family": lambda row: fold(row["family_name"]) or None,
        "duid": lambda row: row["family_duid"],
        "submitted": lambda row: row["submitted_at"],
    },
    default="family",
    descending_first={"submitted"},
    then=lambda row: name_order(row["family_name"], row["family_duid"], str(row["id"])),
)


def _principal(request, store, *, read_only=False):
    """Admin and Staff only; a Ministry leader never sees pledges."""
    # A review region's check (#2174) never extends the sign-in.
    principal = authenticated_admin(
        request,
        store=store,
        activity=not read_only
        and not automatic_read(request)
        and not review_live.checking(request),
        read_only=read_only,
    )
    return require(principal, Capability.FINANCIAL_DETAIL)


def _labels(identities):
    """Map retained actor UUIDs to current emails; removed users stay anonymous."""
    return {
        key: email
        for key, email in PortalUser.objects.filter(
            pk__in=set(identities) - {None}
        ).values_list("id", "email")
    }


def pledge_rows(campaign_id, principal, search=""):
    """Every Family's current pledge with its item, stage and last decision.

    The Financial stewardship selection (no filter but ``search``, which
    matches the Family name or DUID as that report's does) names, words and
    types each pledge; ``read_items`` gives its stage. Two reads for the
    whole campaign, plus one for the deciding users' emails and two for the
    decided pledges' check runs (Not seen, #1704). Returns the rows, each
    carrying ``item`` (with its latest counting check, #1747), ``check``
    (a decided pledge's check run, or None), ``placement`` and
    ``not_seen``, and the selection's metadata.
    """
    query = FinancialQuery(search=search)
    with connection.cursor() as cursor:
        cursor.execute(
            SELECTION,
            [campaign_id, json.dumps({"filters": query.form_values(), "proof": None})],
        )
        value = cursor.fetchone()
    if value is None or value[0] is None:
        raise ReadUnavailable("Pledges are unavailable.")
    result = json.loads(value[0])
    if result.get("disabled"):
        raise PermissionError("Financial stewardship is not enabled for this campaign.")
    if result.get("unavailable"):
        raise ReadUnavailable("Pledges are unavailable.")
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    system = SystemConfiguration.objects.select_related(
        "active_configuration__parish"
    ).get()
    result = shape_result(
        result,
        campaign_id=campaign_id,
        parish_name=system.active_configuration.parish.name,
        configuration=campaign.active_configuration.values,
    )
    name_families(result)
    items = read_items(campaign_id)
    checks = pledge_check_states(items.values())
    names = _labels(item.review.actor_id for item in items.values() if item.review)
    rows = []
    for row in result["rows"]:
        item = items.get(UUID(row["id"]))
        if item is None:
            # The Family answered again between the two reads; the next view
            # shows its new answer.
            continue
        review = item.review
        row["item"] = item
        row["check"] = checks.get(item.submission_id)
        row["placement"] = placement(item)
        row["stage"] = row["placement"].stage.value
        # The Not seen in ParishSoft flag, on Entered by hand only (#1704).
        row["not_seen"] = bool(
            row["check"]
            and row["check"].not_seen(row["placement"].stage is Stage.ENTERED_BY_HAND)
        )
        row["decided_by"] = names.get(review.actor_id, FORMER) if review else ""
        row["decided_at"] = review.created_at if review else None
        row["note"] = review.note if review else ""
        rows.append(row)
    if allows(principal, Capability.CAMPAIGN_REPORT):
        # Each Family name opens its Timeline (#1570): the item already
        # holds the Family's campaign record id.
        for row in rows:
            row["family_id"] = row["item"].family_id
    return rows, result["metadata"]


def frequency_label(frequency):
    """A stored frequency's word ("" or None is No frequency)."""
    return FREQUENCY_LABELS[frequency or "none"]


def answer_parts(row):
    """The Family's answer as (annual pledge, frequency, share methods) text."""
    if row["cannot_give"]:
        return CANNOT_GIVE, "", ""
    shares = ", ".join(
        f"{share['label']}: {share['text']}" if share["text"] else share["label"]
        for share in row["shares"]
    )
    return row["annual"].display, frequency_label(row["frequency"]), shares


def reviewed_parts(row):
    """The reviewer's annual pledge and frequency, or blanks without an Edit.

    An Edit counts only while it is in force: a pledge the Family changed
    since is To review again, with its own answer alone.
    """
    item = row["item"]
    review = item.review
    if not (item.in_force and review and review.decision == "accepted_edited"):
        return "", ""
    amount = MoneyAmount(int(review.reviewed_annual_pledge * 100))
    return amount.display, frequency_label(review.reviewed_frequency)


def reviewed_installment(row):
    """The installment of an Edit in force ("$100.00"), or "" without one.

    Computed as Financial stewardship computes the Family's own
    (``responses.financial.installment``), so the Family hub can word the
    reviewer's frequency as it words the Family's (#2064).
    """
    item = row["item"]
    review = item.review
    if not reviewed_parts(row)[0] or review.reviewed_frequency not in FREQUENCIES:
        return ""
    amount = MoneyAmount(int(review.reviewed_annual_pledge * 100))
    return installment(amount, review.reviewed_frequency).display


def to_enter(row):
    """The pledge to type into ParishSoft: the reviewer's value, else the Family's."""
    reviewed = reviewed_parts(row)
    if reviewed[0]:
        return reviewed
    amount, frequency, _shares = answer_parts(row)
    if row["cannot_give"]:
        return MoneyAmount(0).display, NO_FREQUENCY
    return amount, frequency


def family_answer(row):
    """The Family's answer cell: its pledge and frequency, then its share methods."""
    amount, frequency, shares = answer_parts(row)
    first = f"{amount}, {frequency}" if frequency else amount
    if not shares:
        return first
    return format_html('{}<br><span class="help">{}</span>', first, shares)


def reviewer_value(row):
    """The Reviewer's value cell: an Edit's pledge and frequency, else blank."""
    amount, frequency = reviewed_parts(row)
    return f"{amount}, {frequency}" if amount else ""


def mark(row):
    """Who marked the pledge entered by hand and when, or (None, None).

    A marked pledge a refresh Confirmed or found Entered incorrectly keeps
    its mark (#1704).
    """
    item = row["item"]
    review = item.review
    if not (item.in_force and review and review.entered):
        return None, None
    return row["decided_by"], review.created_at


def parishsoft_amount(row):
    """ParishSoft's upcoming-year amount the latest counting check found, or ""."""
    observed = row["item"].observed
    return display(observed) if observed is not None else ""


def stage_text(row):
    """The row's stage as a file words it, with the Not seen flag."""
    label = row["placement"].stage.label
    return f"{label}, {TERMS[NOT_SEEN]}" if row.get("not_seen") else label


def iso_time(moment):
    """A file's machine-readable time: ISO 8601 to the second, with offset."""
    return moment.isoformat(timespec="seconds")


def export_rows(rows, zone, *, time_text=iso_time):
    """The list as plain text, times placed in ``zone``.

    ``time_text`` words a time: ISO 8601 for CSV and XLSX, the parish's
    compact date format for the PDF. Money keeps its cents.
    """

    def instant(value):
        """One time in the display zone, or blank."""
        return time_text(value.astimezone(zone)) if value else ""

    lines = []
    for row in rows:
        amount, frequency, shares = answer_parts(row)
        decision = row["placement"].decision
        entered_by, entered_at = mark(row)
        in_force = row["item"].in_force
        lines.append(
            (
                row["family_name"] or "",
                source_status_label(
                    family_status(row["family_name"], active=row["active"] is not False)
                ),
                str(row["family_duid"]),
                amount,
                frequency,
                shares,
                *reviewed_parts(row),
                *(to_enter(row) if row["stage"] in ENTERING else ("", "")),
                parishsoft_amount(row),
                stage_text(row),
                decision.label if decision else "",
                entered_by or "",
                instant(entered_at),
                instant(row["submitted_at"]),
                row["decided_by"] if in_force else "",
                instant(row["decided_at"]) if in_force else "",
                row["note"] if in_force else "",
            )
        )
    return lines


def pledges_csv(rows, zone):
    """One CSV with the page's columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADINGS)
    writer.writerows(
        [csv_cell(value) for value in row] for row in export_rows(rows, zone)
    )
    return buffer.getvalue().encode("utf-8")


def pledges_xlsx(rows, zone):
    """A workbook with one literal-text sheet of the list."""
    # Imported here, like the other export writers, to keep web startup light.
    from .xlsx_design import table_workbook

    return table_workbook(
        HEADINGS,
        export_rows(rows, zone),
        sheet_title="Pledges",
        freeze_first_column=True,
    )


def pledges_pdf(rows, zone, *, parish, campaign, as_of):
    """The list as the shared framed zebra table (``pdf_design``).

    The table is ``HEADINGS`` over ``export_rows``, the CSV's and XLSX's
    columns and values (#966); only the times read in the parish's compact
    date format, which the caller lends (``dates.using``).
    """
    from .pdf_design import PdfFrame, Table, draw_table, table_pages

    count = len(rows)
    frame = PdfFrame(
        title="Pledges",
        eyebrow=f"{parish} · {campaign}",
        stamp=f"Downloaded {dates.display_text(as_of.astimezone(zone))} ({zone.key})",
        details=(f"{count:,} {'pledge' if count == 1 else 'pledges'} in this file.",),
        notice=PRIVACY,
    )
    table = Table.weighted(HEADINGS, [PDF_WEIGHTS[name] for name in HEADINGS])
    lines = export_rows(
        rows, zone, time_text=lambda moment: dates.display_text(moment, compact=True)
    )
    pages = list(table_pages(table, lines, frame))
    output = io.BytesIO()
    frame.write(output, pages, len(pages), draw_table(table, frame), requested_at=as_of)
    return output.getvalue()


def export_file_name(fmt, now):
    """The download's file name, stamped with the UTC instant ``now``."""
    return f"stewardship-pledges-{now.strftime('%Y%m%d-%H%M%SZ')}.{fmt}"


def export_body(campaign_id, rows, fmt, zone):
    """The shown rows as the chosen file's bytes, times in ``zone``.

    The PDF names the parish and campaign in its page header and dates
    itself in the parish's date format, lent explicitly.
    """
    if fmt != "pdf":
        writer = pledges_xlsx if fmt == "xlsx" else pledges_csv
        return writer(rows, zone)
    parish = (
        SystemConfiguration.objects.select_related("active_configuration__parish")
        .get()
        .active_configuration.parish
    )
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    with dates.using(parish.date_format):
        return pledges_pdf(
            rows,
            zone,
            parish=parish.name,
            campaign=campaign.active_configuration.name,
            as_of=database_now(),
        )


def _error(*, status):
    """No private values, and a way back to the list."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string("stewardship/pledges-error.html", {"status": status}),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response)


def _edit_refusal(error):
    """The page's answer to an Edit value it cannot take, shown at its field.

    Its static reason, shown in place at the editor's field and logged by
    the shared refusal hook with the field's name only.
    """
    fields = [FieldError(ErrorCode.INVALID, name) for name in error.fields]
    return validation_response(fields, refusal=Refusal(error.message, EDIT_REFUSED))


def _audit(action, principal, campaign_id, outcome, count=0, *, automatic=False):
    """Retain access evidence as a count only.

    ``automatic`` marks a review region's check that brought changed rows in
    (#2174), as a report's automatic view is marked (#1620).
    """
    context = {"outcome": outcome, "count": count}
    if automatic:
        context["automatic"] = True
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
            context=context,
        )


def export_choice(fmt, zone_name):
    """The download's format and display zone, as ``{"format", "zone"}``.

    UTC or one supported timezone name, and one of ``FORMATS``; anything
    else is a ``ValueError`` (a 400).
    """
    if fmt not in FORMATS or (zone_name != "UTC" and zone_name not in timezone_names()):
        raise ValueError("Invalid pledge export choice.")
    return {"format": fmt, "zone": ZoneInfo(zone_name)}


def _choices(parameters, *, export, private):
    """The request's closed choices: Stage, search, and paging or format.

    The view is its Stage (To review by default) and search, with the
    table's sort, page and size; a download's is the same Stage and search
    with its format and the browser's zone. The search names a Family, so
    it travels only in a POST body (``private``), never an address: a GET
    may carry only the Stage and table choices (a My review work card's
    ``?stage=``). Anything else, or a repeated value, is a ValueError.
    """
    allowed = {"stage"} | ({"search"} if private else set())
    allowed |= (
        {"format", "timezone", "browser_timezone", "csrfmiddlewaretoken"}
        if export
        else table_parameters()
    )
    values = filters(parameters, allowed=allowed)
    stage = values.pop("stage", DEFAULT_STAGE)
    search = values.pop("search", "")
    bounded_text(search)
    if stage not in STAGE_VALUES or len(search) > 200:
        raise ValueError("Invalid pledge list choice.")
    if export:
        chosen = export_choice(
            values.get("format", DEFAULT_EXPORT_FORMAT),
            dates.download_zone(values.get("timezone"), values.get("browser_timezone")),
        )
        return stage, chosen | {"search": search}
    # Refuse a bad sort, page or size now, as a 400, not while rendering.
    paginate([], values, sorting=SORTING)
    return stage, {"paging": values, "search": search}


def at_stage(rows, stage):
    """The rows at the chosen Stage (Every stage keeps every row).

    Not seen in ParishSoft is a flag, not a stage: it keeps the flagged rows.
    """
    if stage == NOT_SEEN:
        return [row for row in rows if row.get("not_seen", False)]
    return [row for row in rows if stage == EVERY or row["stage"] == stage]


def _respond(request, campaign_id, *, export, parameters, private, render):
    """Shared admission, guarded read, audit and failure handling.

    ``render(rows, stage, extra, principal)`` returns the bytes; ``rows``
    are every pledge matching the search, at every stage.
    """
    finish, handed_off = None, False
    action = (
        Action.PLEDGES_TO_ENTER_EXPORTED if export else Action.PLEDGES_TO_ENTER_VIEWED
    )
    try:
        service = runtime()
        principal = _principal(request, service.store)
        admit_report_read(campaign_id)
        try:
            # A review region's check (#2174) carries its drawn items alone:
            # every pledge at every stage, unpaged, kept to those items.
            live = None if export else review_live.drawn(request)
            if live is None:
                stage, extra = _choices(parameters, export=export, private=private)
            elif not review_live.alone(request):
                raise ValueError("A live read carries nothing else.")
            else:
                stage, extra = EVERY, {"paging": review_live.EVERY_ROW, "search": ""}
            extra["live"] = live
        except ValueError:
            return _error(status=400)
        finalized, count = False, 0

        def finish(completed):
            """Audit completion once, after releasing the read transaction."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            # A check that found nothing changed showed nothing (#2174).
            if live is not None and completed and not count:
                return
            try:
                _audit(
                    action,
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count,
                    automatic=live is not None,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the list is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Pledge list access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data reaches the template or file."""
            nonlocal count
            rows, _metadata = pledge_rows(campaign_id, principal, extra["search"])
            rows = review_live.pick(rows, live)
            count = len(at_stage(rows, stage))
            page = render(rows, stage, extra, principal)
            # A check counts only the changed rows it brought in.
            count = extra.get("live_count", count)
            return iter((page,))

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
            return _error(status=503)
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
        return _error(status=503)
    except ValueError as error:
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return _error(status=503)
    finally:
        if finish is not None and not handed_off:
            finish(False)


def row_label(row):
    """How the list names a pledge: the Family's name, or its DUID without one."""
    return row["family_name"] or f"Family {row['family_duid']}"


def _stage_line(row, decision):
    """The line under a row's Stage, as safe HTML (or "").

    The decision the stage word does not already say (Accepted (edited)),
    that a To review or To enter by hand pledge ParishSoft already holds
    keeps its stage (#1890; its latest counting check, or the submit's,
    found the amount to enter), or at $0.00 that there is nothing to enter
    (#1747), who marked an entered
    pledge and when, and, for a pledge the Family changed after its last
    decision, that the decision is no longer in force.
    """
    parts = [format_html("{}", decision)] if decision else []
    found = row["placement"]
    item = row["item"]
    if found.stage in BEFORE_STEP and item.check == "confirmed":
        nothing = item.observed is not None and item.observed == 0
        parts.append(format_html("{}", NOTHING if nothing else HELD))
    if item.review and not item.in_force:
        parts.append(format_html("{}", CHANGED))
    by, at = mark(row)
    if by:
        when = format_html(
            '<time datetime="{}" data-local-instant data-compact>{}</time>',
            at.isoformat(),
            at.isoformat(),
        )
        parts.append(format_html("Entered by {}, {}", by, when))
    return format_html_join(mark_safe("<br>"), "{}", ((part,) for part in parts))


def decorate(rows):
    """Give each shown row what the worklist component reads.

    The row's id is the Family's current response and its version the
    item's latest row's sequence plus its refresh checks (``version_of``),
    so a decision made against an earlier answer, after someone else's, or
    over a check the reader never saw, is stale (``PledgeDecisions``). The
    Last decision cell shows the latest row only while it is in force.
    """
    decorated = []
    for row in rows:
        item = row["item"]
        in_force = item.in_force
        amount, frequency = reviewed_parts(row) if in_force else ("", "")
        carried = to_enter(row)
        decorated.append(
            {
                "id": str(item.submission_id),
                "version": version_of(item),
                "name": row_label(row),
                "placement": row["placement"],
                "family": row["family_name"] or source_status_label("missing"),
                "family_name": row["family_name"],
                "family_id": row.get("family_id") if row["family_name"] else None,
                "family_duid": row["family_duid"],
                "baseline": "",
                "family_answer": family_answer(row),
                "reviewer_value": reviewer_value(row),
                # Its parts, which the Family hub's Financial tab shows
                # beside the fields they replace (#2064), with the
                # installment its Frequency row words.
                "reviewer_parts": (
                    amount,
                    frequency,
                    reviewed_installment(row) if amount else "",
                ),
                # Shown beside Entered incorrectly and Not seen in
                # ParishSoft (the component's shows_now), #1704.
                "parishsoft_now": parishsoft_amount(row),
                "not_seen": row.get("not_seen", False),
                # Refresh rule 1 on an Ignored pledge: noted, never a stage.
                "now_matches": (
                    row["placement"].stage is Stage.IGNORED
                    and item.check == "ignored_now_matches"
                ),
                "withheld": WITHHELD.get(row["placement"].stage, ()),
                "submitted_at": row["submitted_at"],
                "decided_by": row["decided_by"] if in_force else "",
                "decided_at": row["decided_at"] if in_force else None,
                "note": row["note"] if in_force else "",
                # The Edit editor starts from the value carried forward.
                "edit_amount": (amount or carried[0])
                .removeprefix("$")
                .replace(",", ""),
                "edit_frequency": (
                    item.review.reviewed_frequency if amount else row["frequency"]
                )
                or "",
                "_source": row,
            }
        )
    return decorated


def stage_table(rows, stage, extra, *, action):
    """The chosen Stage's table page, and every Stage's count.

    ``rows`` are every pledge matching the search; the Stage choices count
    them all, and the table holds the chosen Stage's, sorted and paged in
    memory, its controls posting the view to ``action``.
    """
    counts = Counter(row["stage"] for row in rows)
    # A flag on Entered by hand, counted beside it while some row has it.
    flagged = sum(row.get("not_seen", False) for row in rows)
    if flagged:
        counts[NOT_SEEN] = flagged
    table = paginate(
        at_stage(rows, stage),
        extra["paging"],
        carry=[("stage", stage)],
        sorting=SORTING,
    )
    return replace(table, method="post", action=action), counts


def page_context(rows, stage, extra, roles, *, mutable, notice, prefix=PREFIX):
    """The page's template context for the applied view.

    ``roles`` are the reader's pipeline roles, or none while other campaign
    work holds the list (``mutable`` False): every row is then drawn with no
    action and the selection bar is gone, and the page's notice says why.
    ``prefix`` keeps the element ids apart where another page draws these
    rows (the Family hub's Financial tab, #2064).
    """
    action = reverse("admin:pledges_to_enter")
    table, counts = stage_table(rows, stage, extra, action=action)
    worklist = build(
        Stream.FINANCIAL,
        decorate(table.rows),
        roles,
        prefix=prefix,
        action=action,
        columns=COLUMNS,
        stage=stage,
        counts=counts,
        stages=STAGES,
        items="pledges",
        caption="Pledges",
        baseline=False,
        # Four toggles on one line squeezed the Family's answer onto one
        # more line, pushing the first row below a laptop's window (#2131).
        paired_actions=True,
        fields={"search": extra["search"], "page": str(table.number)},
        filters_template="stewardship/pledges-filters.html",
        editor_template="stewardship/pledges-edit.html",
        export_template="stewardship/pledges-export.html",
        # The view's address carries its Stage alone (To review is the plain
        # address), the one choice the page's GET takes, so a reload after an
        # in-place Stage change draws the Stage on screen (#1820).
        address=page_address(
            action, [("stage", "" if stage == DEFAULT_STAGE else stage)]
        ),
    )
    for row in worklist.rows:
        row["decision_label"] = _stage_line(row.pop("_source"), row["decision_label"])
    return {
        "worklist": worklist,
        # The component draws the table's rows: the decorated ones.
        "table": replace(table, rows=worklist.rows),
        "notice": notice,
        "search": extra["search"],
        "frequencies": [(key, FREQUENCY_LABELS[key]) for key in FREQUENCIES],
        # The export's hidden filters: the applied Stage and search.
        "export_fields": {"stage": stage, "search": extra["search"]},
        "mutable": mutable,
    }


def _render_page(request, campaign_id, rows, stage, extra, principal, notice):
    """The native page: the worklist over the applied view, and its notice."""
    mutable = True
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        mutable = False
    context = page_context(
        rows,
        stage,
        extra,
        reader_roles(principal) if mutable else frozenset(),
        mutable=mutable,
        notice=notice,
    )
    live = extra.get("live")
    if live is not None:
        # A review region's check: only the rows that changed, or nothing
        # (#2174).
        context["worklist"], context["table"], extra["live_count"] = review_live.narrow(
            context["worklist"], context["table"], live
        )
        if not context["table"].rows:
            return b""
    return render_to_string(
        "stewardship/pledges-to-enter.html", context, request=request
    ).encode()


def pledge_name(campaign_id, item_id):
    """A pledge as its row names it (its Family's name), or None.

    Read from the current ParishSoft data through the shared Family name
    helper; any failure falls back to a notice that names no row.
    """
    from parishkit.stewardship.responses.models import Submission
    from parishkit.stewardship.source.snapshot_names import snapshot_family_names

    try:
        with transaction.atomic():
            duid = (
                Submission.objects.filter(pk=item_id, campaign_id=campaign_id)
                .values_list("family__family_duid", flat=True)
                .first()
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT snapshot_id FROM stewardship_source_current WHERE singleton"
                )
                found = cursor.fetchone()
            if duid is None:
                return None
            names = (
                snapshot_family_names(found[0], {duid}, NAME_NOT_RECORDED)
                if found
                else {}
            )
            return names.get(duid) or f"Family {duid}"
    except (DatabaseError, *SAFE_FAILURES, StorageInvariantError):
        return None


def _decide(request, campaign_id, decision):
    """Record one decision; its notice, or the refusal response.

    A race is the notice's to say (``record``). A pledge of another
    campaign, or a reader who may not see pledges, is denied like any
    unknown one; an Edit value it cannot take is shown at its field; a
    malformed decision is the plain 400 page; an outage is 503.
    """
    try:
        service = runtime()
        principal = _principal(request, service.store)
        decisions = PledgeDecisions(
            service.store,
            campaign_id,
            namer=lambda item_id: pledge_name(campaign_id, item_id),
        )
        return record(decisions, principal, decision)
    except EditRefusal as error:
        return _edit_refusal(error)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(status=503)
    except ValueError:
        return _error(status=400)


@require_http_methods(["GET", "POST"])
def page(request, campaign_id):
    """The list, filtered, sorted and paged in place, with each row's actions.

    A GET is the default view (or the Stage its query string names). The
    worklist's controls post its view to the page itself; a POST carrying a
    decision (a row's icon, an Edit's Save or the bulk bar) records it first
    and answers with the page redrawn from the same view, its notice saying
    what was recorded (``web.review_worklist``).
    """
    notice = None
    private = request.method == "POST"
    if private:
        if request.GET:
            return _error(status=400)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        try:
            decision = read_decision(parameters)
            # The view the answer is drawn from is checked before anything
            # is recorded, so a malformed post records nothing.
            _choices(parameters, export=False, private=True)
        except ValueError:
            return _error(status=400)
        if decision is not None:
            notice = _decide(request, campaign_id, decision)
            if not isinstance(notice, str):
                return notice
    else:
        parameters = request.GET

    def render(rows, stage, extra, principal):
        """The page for the applied view."""
        return _render_page(request, campaign_id, rows, stage, extra, principal, notice)

    return _respond(
        request,
        campaign_id,
        export=False,
        parameters=parameters,
        private=private,
        render=render,
    )


@page_download("pledges_to_enter")
@require_POST
def export(request, campaign_id):
    """Download the list as CSV, XLSX or PDF, rendered on request."""

    def render(rows, stage, extra, principal):
        """The chosen file format, with times in the chosen timezone."""
        return export_body(
            campaign_id, at_stage(rows, stage), extra["format"], extra["zone"]
        )

    if request.GET:
        return _error(status=400)
    return _respond(
        request,
        campaign_id,
        export=True,
        parameters=request.POST,
        private=True,
        render=render,
    )
