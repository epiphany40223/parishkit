"""Read the Campaign comparison report's figures (#1018, slice 3).

The page and ``pk-admin report comparison`` call ``read_comparison`` inside
the campaign read guard their response holds. It reads:

- the current campaign's ready "Historical as of day" fact set, through the
  Participation page's own selection (``guarded_participation``), so a
  generation being rebuilt shows the last ready one with the Updating
  notice, and the current figures are the participation graph's;
- the figures that need each Family's latest live submission (pledging
  Families, quartiles, bands and renewal) from the definer function
  ``stewardship_campaign_comparison_v1``, which returns aggregates only and
  whose Families responded and pledge total must equal the fact row's;
- the active imported archives (``reports.archive_models``): their daily
  rows, bands and, for renewal, ``stewardship_campaign_archive_renewal_v1``,
  since kept Family rows are read only inside the database.

The comparison day is the current campaign's last complete local day (the
day before today in its time zone, held at its end date once it has
closed), and never later than the last day of the fact set shown.
"""

import json
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import DatabaseError, OperationalError, connection

from parishkit.stewardship.storage import StorageInvariantError

from .comparison import (
    NO_FAMILY_ROWS,
    NO_LAST_YEAR,
    NO_PREVIOUS_YEAR,
    OLDER_BANDS,
    OTHER_ID_SYSTEM,
    Band,
    Comparison,
    Day,
    Distribution,
    Renewal,
    Year,
)
from .selection import guarded_participation

# The id system a renewal against the current campaign matches on.
DUID = "parishsoft_duid"
# A note's text is cut to this on the page (spec, "Page layout").
NOTE_LIMIT = 500


class DayOutOfRange(LookupError):
    """A requested comparison day the current campaign has no figures for."""


def stewardship_year_or_none():
    """The current campaign's stewardship year, or None when unresolvable.

    Without a year, "last year" cannot be resolved, so its lines are left
    out (spec, "Metrics"); every imported year still counts in the average.
    """
    from parishkit.stewardship.archive.imports import ArchiveRefusal, stewardship_year

    try:
        return stewardship_year()
    except ArchiveRefusal:
        return None


def stored_systems(archive):
    """The id systems of an archive's kept Family rows (envelopes are never kept)."""
    return set(archive.family_id_systems) - {"pds_envelope"}


def renewal_reason(archive, previous, *, duid_only=False):
    """Why renewal of ``previous``'s Families cannot be counted, or None."""
    if previous is None:
        return NO_LAST_YEAR if duid_only else NO_PREVIOUS_YEAR
    if not previous.family_rows_kept or (archive and not archive.family_rows_kept):
        return NO_FAMILY_ROWS
    systems = stored_systems(previous)
    if duid_only:
        return None if systems == {DUID} else OTHER_ID_SYSTEM
    if len(systems) != 1 or systems != stored_systems(archive):
        return OTHER_ID_SYSTEM
    return None


def _json(sql, arguments):
    """One definer function call's jsonb result, decoded (None for NULL)."""
    with connection.cursor() as cursor:
        cursor.execute(sql, arguments)
        value = cursor.fetchone()[0]
    # Django's cursor leaves jsonb as its text.
    return json.loads(value) if isinstance(value, str) else value


def current_figures(fact_set_id, fact, prior_archive_id):
    """The definer function's aggregates for one fact day, checked against it.

    The function derives the day's end itself and refuses unless its
    Families responded equal the fact row's. Here the pledge total (when the
    day has one) must equal the fact row's too, since both follow one rule;
    a difference means the two readings disagree, and the report is refused
    as unavailable rather than showing either.
    """
    from parishkit.stewardship.archive.workbook import BAND_EDGES

    found = _json(
        "SELECT public.stewardship_campaign_comparison_v1(%s,%s,%s::numeric[],%s)",
        [fact_set_id, fact.local_date, list(BAND_EDGES), prior_archive_id],
    )
    if found is None or found["responded"] != fact.cumulative_responses:
        raise StorageInvariantError("Comparison figures disagree with their facts.")
    if fact.pledge_available and (
        not found["pledge_complete"]
        or Decimal(found["pledge_total"]) != fact.pledge_total
    ):
        raise StorageInvariantError("Comparison pledges disagree with their facts.")
    return found


def _current_day(fact, number, found, financial):
    """The current campaign's Day from its fact row and checked aggregates."""
    pledged = fact.pledge_total if financial and fact.pledge_available else None
    return Day(
        number,
        fact.local_date,
        fact.cumulative_responses,
        fact.cohort_denominator,
        found["pledging"] if pledged is not None else None,
        pledged,
    )


def _distribution(found):
    """The current campaign's distribution from the function's aggregates."""
    bands = tuple(
        Band(row["band"], row["families"], Decimal(row["total"]))
        for row in found["bands"]
    )
    quartiles = found["quartiles"]
    return Distribution(
        found["pledging"],
        sum((band.total for band in bands), Decimal(0)),
        bands,
        tuple(Decimal(value) for value in quartiles) if quartiles else None,
    )


def comparison_day(document, start, end, timezone, selected_at):
    """(number, date) of the comparison day, or None before day 1 has ended."""
    today = selected_at.astimezone(ZoneInfo(timezone)).date()
    last = min(today - timedelta(days=1), end)
    if document is not None and document.last_date is not None:
        last = min(last, document.last_date)
    if last < start:
        return None
    return (last - start).days + 1, last


def _archive_years(archives, days, bands, number, financial):
    """The imported years at day ``number`` (None: final results only)."""
    from parishkit.stewardship.archive.workbook import BAND_SET_VERSION

    by_year = {archive.campaign_year: archive for archive in archives}
    years = []
    for archive in archives:
        rows = days[archive.pk]
        last = rows[max(rows)]

        def figures(row, carried=False, archive=archive):
            """One stored daily row as a Day."""
            return Day(
                row.day,
                row.local_date,
                row.cumulative_families,
                archive.active_families,
                row.cumulative_pledging_families if financial else None,
                row.cumulative_annual_pledged if financial else None,
                carried,
            )

        at_day = None
        if number is not None:
            if number in rows:
                at_day = figures(rows[number])
            elif archive.is_final and number > max(rows):
                at_day = figures(last, carried=True)
        previous = by_year.get(archive.campaign_year - 1)
        reason = renewal_reason(archive, previous)
        renewal = Renewal(reason=reason)
        if reason is None and number is not None and at_day is not None:
            found = _json(
                "SELECT public.stewardship_campaign_archive_renewal_v1(%s,%s,%s)",
                [archive.pk, previous.pk, number],
            )
            if found is not None:
                renewal = Renewal(found["renewed"], found["prior_families"])
        distribution = None
        if financial:
            stored = bands.get(archive.pk, ())
            quartiles = (
                (archive.first_quartile, archive.median, archive.third_quartile)
                if archive.median is not None
                else None
            )
            distribution = Distribution(
                archive.pledging_families,
                archive.annual_pledged,
                tuple(stored),
                quartiles,
                None if archive.band_set_version == BAND_SET_VERSION else OLDER_BANDS,
            )
        years.append(
            Year(
                archive.campaign_year,
                archive.campaign_name,
                current=False,
                final=archive.is_final,
                start_date=archive.start_date,
                days=archive.days,
                at_day=at_day,
                end=figures(last),
                renewal=renewal,
                distribution=distribution,
                notes=notes(archive),
                series=tuple(figures(rows[day]) for day in sorted(rows)),
            )
        )
    return years


def current_series(document, start, last, financial):
    """The current campaign's days 1 to ``last`` (a date) from its fact rows.

    The day-by-day charts draw these: Families responded, the cohort
    denominator and, with Financial stewardship, the pledge total, each the
    participation graph's own figure. A day whose population is unavailable
    is left out of the series; the charts draw it as a null point, so the
    line breaks there and the table shows "—" (``comparison_charts.
    year_points``). A day without a pledge total has none.
    """
    days = []
    for fact in document.days:
        if not start <= fact.local_date <= last or not fact.population_available:
            continue
        pledged = fact.pledge_total if financial and fact.pledge_available else None
        days.append(
            Day(
                (fact.local_date - start).days + 1,
                fact.local_date,
                fact.cumulative_responses,
                fact.cohort_denominator,
                None,
                pledged,
            )
        )
    return tuple(sorted(days, key=lambda day: day.day))


def notes(archive):
    """The data notes of one imported year: (label, text) pairs, each cut.

    They never leave the Admin portal (spec, "Privacy"); the template
    escapes them.
    """
    pairs = [
        ("Source system", archive.source_system),
        ("Campaign Families counted from", archive.active_families_provenance),
        ("Repeat submissions", archive.dedup_rule),
    ]
    pairs += [(key, str(value)) for key, value in sorted(archive.notes.items())]
    return tuple(
        (label, text if len(text) <= NOTE_LIMIT else text[: NOTE_LIMIT - 1] + "…")
        for label, text in pairs
    )


def _archives(current_year):
    """The active imports, newest first, with their day and band rows."""
    from parishkit.stewardship.archive.workbook import BAND_SET_VERSION

    from .archive_models import CampaignArchive, CampaignArchiveBand, CampaignArchiveDay

    archives = list(
        CampaignArchive.objects.filter(state="active")
        .exclude(campaign_year=current_year)
        .order_by("-campaign_year")
    )
    days = {archive.pk: {} for archive in archives}
    for row in CampaignArchiveDay.objects.filter(archive__in=archives):
        days[row.archive_id][row.day] = row
    bands = {}
    for row in CampaignArchiveBand.objects.filter(
        archive__in=archives, band_set_version=BAND_SET_VERSION
    ).order_by("band"):
        bands.setdefault(row.archive_id, []).append(
            Band(row.band, row.families, row.total)
        )
    return archives, days, bands


def read_comparison(guard, campaign_id, *, day=None):
    """The report's figures at the comparison day, or at ``day`` (1 to it).

    The caller owns authorization and the campaign read guard ``guard``.
    Raises ``DayOutOfRange`` for a ``day`` outside 1 to the comparison day,
    and ``StorageInvariantError`` when the live figures disagree with their
    facts. A database error is logged (``log_read_failure``) and raised; the
    page and ``pk-admin report comparison`` answer it as unavailable.
    """
    try:
        return _read_comparison(guard, campaign_id, day)
    except DatabaseError as error:
        log_read_failure(error)
        raise


def log_read_failure(error):
    """Record a database error that every retry of the read would repeat.

    The callers answer a database error with their 503 page or exit 3
    (retry) and log nothing, which hid #1286's unknown time zone. A
    connection loss or the read guard's own cancellation (an
    ``OperationalError``) passes on a retry, and the guard logs its own
    timeout, so only the other errors (a refusal in the definer function, an
    invalid value) get a ``report_shaping_failed`` line with their category
    and class, never their text.
    """
    from parishkit.stewardship.observability import Event, emit_failure

    if not isinstance(error, OperationalError):
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED, name_class=True)


def _read_comparison(guard, campaign_id, day):
    """``read_comparison``'s read, inside the caller's guard."""
    from parishkit.stewardship.campaigns.models import Campaign

    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    projection = campaign.active_configuration
    with guarded_participation(
        guard,
        campaign_id=campaign_id,
        population_scope="historical",
        browser_timezone="UTC",
    ) as selection:
        document = selection.document
        latest = comparison_day(
            document,
            projection.start_date,
            projection.end_date,
            projection.timezone,
            selection.selected_at,
        )
        if day is not None and (latest is None or not 1 <= day <= latest[0]):
            raise DayOutOfRange("That day has no figures yet.")
        number = day if day is not None else (latest[0] if latest else None)
        comparison = compare(
            document,
            projection,
            latest,
            number,
            status=selection.status,
            current_year=stewardship_year_or_none(),
        )
        guard.check()
        return comparison


def digest_comparison(document, projection):
    """The comparison on a daily digest's report day, or None (#1018 slice 7).

    ``document`` is the digest's pinned participation document, whose last
    day is the report day, and ``projection`` the campaign configuration
    its snapshot pinned. The comparison day is the report day (held at the
    campaign's end date, as the page holds it), and the current figures
    come from the digest's own fact set, so the section matches the rest of
    the digest. The stewardship year is the pinned configuration's. Returns
    None before day 1 or when no past campaign is imported (the section is
    left out). The caller runs this inside its own owned transaction.
    """
    from parishkit.stewardship.archive.imports import ArchiveRefusal, year_from_values

    last = min(document.last_date, projection.end_date)
    if last < projection.start_date:
        return None
    try:
        current_year = year_from_values(projection.values or {})
    except ArchiveRefusal:
        current_year = None
    latest = ((last - projection.start_date).days + 1, last)
    comparison = compare(
        document,
        projection,
        latest,
        latest[0],
        status="current",
        current_year=current_year,
    )
    return comparison if comparison.imported else None


def compare(document, projection, latest, number, *, status, current_year):
    """The Comparison at day ``number`` from one participation document.

    ``latest`` is (number, date) of the comparison day, or None before day 1
    has ended; ``number`` the day compared (that day or an earlier one).
    The page and the digest share this, so their figures cannot differ.
    """
    financial = (projection.values or {}).get("financial") is not None
    archives, days, bands = _archives(current_year)
    imported = _archive_years(archives, days, bands, number, financial)
    last_year = next(
        (
            year
            for year in imported
            if current_year is not None and year.year == current_year - 1
        ),
        None,
    )
    current = None
    if number is not None and document is not None:
        last_archive = next(
            (a for a in archives if a.campaign_year == (current_year or 0) - 1),
            None,
        )
        reason = renewal_reason(None, last_archive, duid_only=True)
        by_date = {fact.local_date: fact for fact in document.days}
        fact = by_date.get(projection.start_date + timedelta(days=number - 1))
        final = by_date.get(latest[1])
        if fact is None or not fact.population_available:
            at_day, found = None, None
        else:
            found = current_figures(
                document.fact_set_id,
                fact,
                last_archive.pk if reason is None else None,
            )
            at_day = _current_day(fact, number, found, financial)
        end, so_far = at_day, found
        if final is not fact and final is not None and final.population_available:
            so_far = current_figures(document.fact_set_id, final, None)
            end = _current_day(final, latest[0], so_far, financial)
        renewal = Renewal(reason=reason)
        if reason is None and found is not None:
            renewal = Renewal(found["renewed"], found["prior_families"])
        current = Year(
            current_year,
            projection.name,
            current=True,
            final=False,
            start_date=projection.start_date,
            days=latest[0],
            at_day=at_day,
            end=end,
            renewal=renewal,
            distribution=(
                _distribution(found)
                if financial and found is not None and at_day.pledged is not None
                else None
            ),
            series=current_series(
                document, projection.start_date, latest[1], financial
            ),
        )
    return Comparison(
        campaign_name=projection.name,
        day=number,
        report_date=(projection.start_date + timedelta(days=number - 1))
        if number
        else None,
        financial=financial,
        status=status,
        current=current,
        imported=tuple(imported),
        last_year=last_year,
        latest_day=latest[0] if latest else None,
        checksums=tuple((a.campaign_year, a.checksum) for a in archives),
        fact_set_id=document.fact_set_id if document is not None else None,
    )
