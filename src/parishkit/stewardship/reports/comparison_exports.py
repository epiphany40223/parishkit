"""The Campaign comparison downloads: CSV, XLSX and a PDF summary (#1018, slice 6).

The Campaign comparison page (``comparison_views``) shows Administrators and
Staff every figure, including the current campaign's pledge distribution.
Its downloads leave the Admin portal (the Administrator sends the PDF to
leadership), so what they hold is built here from the page's own read
(``comparison_reads.read_comparison``) with the campaign-history spec's
privacy rules applied first ("Privacy", "Small-count protection"):

- aggregates only, and none of the free-text notes, provenance or dedup
  texts the page's data notes show;
- the small-count protection (``comparison.withheld_years``): a year with a
  withheld pledge band shows "Withheld" for every average pledge, and the
  prior-years average pledge is withheld when it averages such a year;
- no current-campaign (or partial-year) quartile, which shows
  "On the page only", and no median so far on the tiles;
- the day-by-day charts only (Families, percent and pledges), never a
  pledge-distribution chart.

That protected **capture** is what the export stores: the web reads the
comparison under the campaign's read guard, builds the capture, and stores
it in an immutable ``ComparisonExportSnapshot`` (frozen migration 0071),
whose SQL capture guard checks who may capture it, the campaign, the
configuration and the document's closed shape. The shared export lifecycle
then renders it (``comparison_documents``) and publishes, downloads,
cancels and retries it like every other export. A retried file renders the
same capture: it never reads newer figures.

The capture's table is the page's **Every number** table: the same
columns, names and order (UX conventions, "Downloaded files"), counts as
numbers, money as exact amounts, and a value the page marks "(final)"
keeping that mark (so that cell is text, as an unavailable amount stays a
word). A figure the page shows as "—" is a blank cell.
"""

from datetime import date
from decimal import ROUND_HALF_UP
from uuid import UUID, uuid4

from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.work_locks import export_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .comparison import (
    CENT,
    PAGE_ONLY,
    SMALL_COUNT,
    WITHHELD,
    tiles,
    value_text,
    withheld_years,
    years_label,
)
from .export_models import ComparisonExportSnapshot, ExportRequest
from .export_services import (
    TASK_TYPE,
    ExportRequestBound,
    admit_campaign,
    audit,
    authorize,
)

REPORT = "campaign_comparison"
FORMATS = ("csv", "xlsx", "pdf")


# ------------------------------------------------------------------ table


def headings(comparison):
    """The Every number table's headings, as the page's template shows them.

    The on-the-day columns appear once a day is compared, and the pledge
    columns only with Financial stewardship, exactly as on the page.
    """
    result = ["Year", "Day 1", "Days"]
    if comparison.day:
        result += ["Families on the day", "Percent on the day"]
        if comparison.financial:
            result += ["Pledges on the day", "Average pledge on the day"]
        result.append("Renewal on the day")
    result += ["Final Families", "Final percent"]
    if comparison.financial:
        result += [
            "Final pledges",
            "Final average pledge",
            "First quartile",
            "Median",
            "Third quartile",
        ]
    return result


def money_cell(value):
    """An exact amount to the cent, as the capture stores money."""
    return {"money": str(value.quantize(CENT, ROUND_HALF_UP))}


def figure(kind, day, key, *, withheld=False):
    """One figure of ``day`` as a capture cell.

    None (a blank cell) where the page shows "—"; a count is a number and
    money an exact amount; a percentage is the page's text. A final year's
    value carried past its last day keeps the page's "(final)" mark, so it
    is the page's text. ``withheld`` replaces a known average pledge with
    "Withheld" (the small-count protection).
    """
    value = None if day is None else getattr(day, key)
    if value is None:
        return None
    if withheld:
        return WITHHELD
    if kind == "percent" and value.value is None:
        return None
    if day.carried:
        return value_text(kind, value) + " (final)"
    if kind == "count":
        return value
    if kind == "money":
        return money_cell(value)
    return value_text(kind, value)


def quartile_cells(comparison, year):
    """A year's three quartile cells as they may leave the portal.

    Blank where the page shows "—"; "On the page only" for the current
    campaign and a partial year, whose quartiles stay inside the portal;
    "Withheld" for a year with fewer than 5 pledging Families.
    """
    found = year.distribution
    quartiles = found.quartiles if found is not None else None
    if quartiles is None:
        return [None, None, None]
    if year.current or not year.final:
        return [PAGE_ONLY] * 3
    if found.pledging < SMALL_COUNT:
        return [WITHHELD] * 3
    return [money_cell(value) for value in quartiles]


def table(comparison):
    """The Every number table's rows, current campaign first, protected."""
    withheld = withheld_years(comparison)
    years = ([comparison.current] if comparison.current else []) + list(
        comparison.imported
    )
    rows = []
    for year in years:
        hidden = not year.current and year.year in withheld
        at, end = year.at_day, year.end
        row = [year.label, {"date": year.start_date.isoformat()}, year.days]
        if comparison.day:
            row += [figure("count", at, "families"), figure("percent", at, "percent")]
            if comparison.financial:
                row += [
                    figure("money", at, "pledged"),
                    figure("money", at, "average", withheld=hidden),
                ]
            renewal = year.renewal.percent
            row.append(None if renewal is None else value_text("percent", renewal))
        row += [figure("count", end, "families"), figure("percent", end, "percent")]
        if comparison.financial:
            row += [
                figure("money", end, "pledged"),
                figure("money", end, "average", withheld=hidden),
                *quartile_cells(comparison, year),
            ]
        rows.append(row)
    return rows


# ----------------------------------------------------------------- charts


def charts(comparison):
    """The day-by-day charts' lines, as the PDF summary draws them.

    The same lines the page draws (``comparison_charts.lines``): each with
    its role (current, last year, average or another year), name and
    (day, value) points, a value being the chart's float (a percentage as a
    fraction) or None for a gap. Only these three measures leave the
    portal; the pledge-distribution charts stay on the page.
    """
    from .comparison_charts import AVERAGE, MEASURES, lines, plotted

    result = []
    for measure in MEASURES:
        if measure.financial and not comparison.financial:
            continue
        drawn, last_day = lines(measure, comparison)
        if not drawn:
            continue
        result.append(
            {
                "title": measure.title,
                "axis": measure.axis,
                "kind": measure.kind,
                "last_day": last_day,
                "lines": [
                    {
                        "role": line.role,
                        # The average names no years (they vary by day).
                        "name": line.name if line.role != AVERAGE else line.legend,
                        "points": [
                            [day, plotted(measure, value)]
                            for day, value, _ in line.points
                        ],
                    }
                    for line in drawn
                ],
            }
        )
    return result


# ---------------------------------------------------------------- capture


def compared_with(comparison):
    """ "2026 and the 2020–2026 average", as the page's data line names them.

    The averages cover the years with a figure on the comparison day.
    """
    average = years_label(
        [year for year in comparison.imported if year.at_day is not None]
    )
    parts = []
    if comparison.last_year is not None:
        parts.append(str(comparison.last_year.year))
    if average:
        parts.append(f"the {average} average")
    return " and ".join(parts)


def capture(comparison):
    """The protected, JSON-ready document the export stores (see the module).

    Every value is an aggregate or a fixed word; nothing names a Family,
    and no note text from an archive is carried.
    """
    return {
        "campaign": comparison.campaign_name,
        "day": comparison.day,
        "report_date": comparison.report_date.isoformat()
        if isinstance(comparison.report_date, date)
        else None,
        "financial": comparison.financial,
        "updating": comparison.status != "current",
        "compared_with": compared_with(comparison) if comparison.day else "",
        "tiles": [
            {"label": tile["label"], "value": tile["value"], "lines": tile["lines"]}
            for tile in tiles(comparison, outside=True)
        ],
        "columns": headings(comparison),
        "rows": table(comparison),
        "charts": charts(comparison),
    }


def read_capture(store, user_id, campaign_id, *, day=None):
    """Read the page's comparison under the campaign's read guard; capture it.

    The read guard owns its transaction, so this runs before the export's
    own transaction opens. Inside the guard the reader is admitted again
    (a role change ends the read), as the page's view does. ``day`` is the
    Compare on day value; ``comparison_reads.DayOutOfRange`` refuses one
    outside 1 to the comparison day. Returns (day shown, capture).
    """
    from parishkit.stewardship.campaigns.read_guards import (
        CampaignReadGuard,
        GuardedResponse,
    )

    from .comparison_reads import read_comparison
    from .read_admission import admit_report_read

    holder = {}

    def admit(guard):
        """Fresh admission inside the guard, as the page's view does."""
        authorize(store, user_id)
        admit_report_read(campaign_id)

    def content():
        """The page's read, reduced at once to the protected capture."""
        comparison = read_comparison(guard, campaign_id, day=day)
        holder["result"] = (comparison.day, capture(comparison))
        guard.check()
        return iter(())

    def abort():
        """Nothing is streaming: the guard's expiry fails the read itself.

        The guard cancels its SQL and records what it stopped, the limit
        and the elapsed time (``read_guards.CampaignReadGuard._expire``).
        """

    guard = CampaignReadGuard([campaign_id], authorize=admit, abort=abort)
    GuardedResponse(guard, content).close()
    return holder["result"]


def create_comparison_export(
    store,
    user_id,
    *,
    campaign_id,
    day,
    format,
    browser_timezone,
    request_key,
    document,
):
    """Store a comparison capture and queue its file, as the page's form does.

    ``day`` is the comparison day the capture ``document`` shows (from
    ``read_capture``), never a form's. Administrators and Staff may export,
    as both see the page (``CAMPAIGN_REPORT``). A request key already used
    for the same export returns it; for another it is ``ExportRequestBound``
    (the page's 409). Records ``campaign_comparison_exported`` with the day
    as ``count``, no figure.
    """
    if any(
        not isinstance(value, UUID) for value in (user_id, campaign_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if day is not None and (type(day) is not int or day < 1):
        raise ValueError("A comparison day is a whole number from 1.")
    if type(format) is not str or format not in FORMATS:
        raise ValueError("Unsupported comparison export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    if document is None:
        raise ValueError("An export needs a fresh capture.")
    parameters = {"day": day}

    def admit(*args):
        """Both enqueue and its owning transaction recheck current authorization."""
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=True)
        return True

    with export_transaction(campaign_id):
        admit()
        previous = ExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            if (
                previous.report,
                previous.campaign_id,
                previous.parameters,
                previous.format,
                previous.browser_timezone,
            ) != (REPORT, campaign_id, parameters, format, browser_timezone):
                raise ExportRequestBound("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
        snapshot = ComparisonExportSnapshot.objects.create(
            campaign_id=campaign_id,
            configuration_id=configuration_id,
            actor_id=user_id,
            correlation_id=correlation_id,
            parameters=parameters,
            document=document,
        )
        # The insert trigger sets the time and the row count.
        snapshot.refresh_from_db(fields=["created_at", "row_count"])
        identifier = uuid4()
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=user_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=admit,
        )
        request = ExportRequest.objects.create(
            id=identifier,
            campaign_id=campaign_id,
            requester_id=user_id,
            request_key=request_key,
            task_id=task.run_id,
            comparison_snapshot=snapshot,
            configuration_id=configuration_id,
            report=REPORT,
            format=format,
            browser_timezone=browser_timezone,
            parameters=parameters,
            authorization_scope={"capability": Capability.CAMPAIGN_REPORT.value},
            actor_id=user_id,
            correlation_id=correlation_id,
        )
        audit(
            Action.CAMPAIGN_COMPARISON_EXPORTED,
            request,
            user_id,
            outcome=Outcome.STARTED,
            count=day,
        )
        return request
