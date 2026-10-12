"""Write the archive workbook of a campaign run in this system (#1018 part (a)).

Slice A1 of the campaign-history specification's "Archive export": the
writer and its read, with no page, command or export lifecycle (slice A2
wires those). ``export_archive`` reads one campaign's live submissions and
its ready historical daily facts under a cutoff, builds the six sheets of
the archive format through ``workbook.build_workbook``, checks the derived
``daily`` sheet against the fact rows, writes reproducible bytes and reads
them back through the import's reader before returning them.

The work is split so most of it is pure:

- ``read_source`` is the only database read. It runs in one read-only,
  repeatable-read transaction and returns an ``ArchiveSource`` of plain
  values (no ORM objects).
- ``archive_sheets`` turns an ``ArchiveSource`` into metadata and sheet rows,
  ``check_facts`` compares the derived ``daily`` rows with the fact rows, and
  ``write_source`` combines them and runs the read-back.

Every figure follows the format's own rules (``workbook``), so the reader's
consistency checks accept the file by construction; the read-back proves it
for each file actually offered.
"""

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from parishkit import __version__
from parishkit.stewardship.campaigns.intervals import local_day
from parishkit.stewardship.reports.facts import FactUnavailable

from .workbook import SCHEMA, SCHEMA_VERSION, financial_answer, write_archive

SOURCE_SYSTEM = "ParishKit Stewardship"
SOURCE_FILES = "none (read from the campaign database)"
CONVERTER = f"parishkit {__version__} archive export"
DEDUP_RULE = (
    "Each Family's latest live submission version counts; earlier versions "
    "stay in submissions."
)
PROVENANCE = (
    "ParishKit Stewardship historical cohort: Families Portal-eligible at any "
    "time through day {day}"
)
# Installments per year of each frequency the Family form offers; the form
# shows annual / periods rounded half up (``responses.financial.installment``).
PERIODS = {"weekly": 52, "monthly": 12, "quarterly": 4, "annual": 1}
# A Ministry name's leading three-digit number, such as "105 Choir".
MINISTRY_CODE = re.compile(r"\s*([0-9]{3})(?![0-9])")
CENT = Decimal("0.01")
CLOSED_STATES = frozenset(("closed", "archived"))


class ArchiveUnavailable(Exception):
    """Nothing can be exported yet: before day 1 has ended, or no respondent."""


class ArchiveWriteRefused(Exception):
    """The written file failed the import's reader; nothing is offered.

    ``problems`` holds the reader's located problems (codes, rules and cells,
    never a cell value), for the log.
    """

    def __init__(self, problems):
        """Keep the located problems; the message names none of them."""
        self.problems = list(problems)
        super().__init__("The written campaign archive failed its read-back.")


@dataclass(frozen=True)
class Version:
    """One live submission version, reduced to what the archive writes.

    ``annual_pledge`` is None when the version holds no financial answer;
    then ``frequency`` and ``cannot_give`` are None too. ``ministries`` is
    the answer's normalized Ministry choices, and ``ministry_names`` maps a
    chosen Ministry DUID (text) to its name in the version's reviewed
    baseline source snapshot.
    """

    family_duid: int
    sequence: int
    submitted_at: datetime
    annual_pledge: Decimal | None
    frequency: str | None
    cannot_give: bool | None
    ministries: dict = field(default_factory=dict)
    ministry_names: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FactDay:
    """One historical fact row, as the archive check compares it."""

    local_date: date
    first_responses: int
    cumulative_responses: int
    cohort_denominator: int
    population_available: bool
    pledge_available: bool
    pledge_total: Decimal | None


@dataclass(frozen=True)
class ArchiveSource:
    """Everything one archive is written from, read under one cutoff."""

    campaign_name: str
    campaign_year: int
    timezone: str
    start_date: date
    cutoff_date: date
    final: bool
    financial: bool
    versions: tuple
    facts: tuple
    converted_at: datetime
    outside_cohort: int = 0

    @property
    def cutoff(self):
        """The cutoff instant: the end of the cutoff date in the campaign's zone."""
        return local_day(self.cutoff_date, self.timezone).interval.end


@dataclass(frozen=True)
class ArchiveFile:
    """A written, read-back archive: its name, bytes and checksum."""

    name: str
    data: bytes
    sha256: str
    families: int
    submissions: int
    ministry_rows: int
    unchecked: dict


def archive_cutoff(state, start_date, end_date, timezone, now):
    """(cutoff date, final) for a campaign at ``now``, or None before day 1 ends.

    A ``closed`` or ``archived`` campaign exports through its end date as a
    final file; otherwise the cutoff is the last complete local day (the
    comparison report's comparison day), never today's unfinished day.
    """
    if state in CLOSED_STATES:
        return end_date, True
    last = min(now.astimezone(ZoneInfo(timezone)).date() - timedelta(days=1), end_date)
    return None if last < start_date else (last, False)


def archive_name(year, final):
    """The file name the format's convention gives a final or partial archive."""
    return f"stewardship-{year}.xlsx" if final else f"stewardship-{year}-partial.xlsx"


# --- Building the sheets ------------------------------------------------------------


def _ordered(versions):
    """Versions oldest first: submitted_at, then campaign sequence for a tie.

    The format orders rows by time, while the facts pick a Family's latest
    version by campaign sequence. The fact calculation refuses a Family
    whose times contradict its sequence, so the two orders agree; if they
    ever did not, the derived ``daily`` would differ from the facts and
    ``check_facts`` would fail the export rather than offer the file.
    """
    return sorted(versions, key=lambda v: (v.submitted_at, v.sequence))


def _installment(annual, frequency):
    """The per-installment amount the Family form showed, or None."""
    if annual is None or frequency not in PERIODS:
        return None
    periods = PERIODS[frequency]
    cents = int(annual * 100)
    return Decimal((cents + periods // 2) // periods) / 100


def _ministry_rows(key, version):
    """The Ministry join and leave rows of one Family's last version.

    Slots number every person on the form: existing Members in ascending
    Member DUID (the order the form inputs carry), then proposed Members by
    their local key, so a person with no choice still takes a slot.
    """
    choices = version.ministries or {}
    people = [
        (int(duid), entry)
        for duid, entry in sorted(
            (choices.get("members") or {}).items(), key=lambda item: int(item[0])
        )
    ] + [
        (None, entry)
        for _, entry in sorted((choices.get("proposed_members") or {}).items())
    ]
    rows = []
    for slot, (member, entry) in enumerate(people, start=1):
        for response in ("join", "leave"):
            for ministry in (entry or {}).get(response, ()):
                name = version.ministry_names.get(str(ministry)) or ""
                code = MINISTRY_CODE.match(name)
                rows.append(
                    {
                        "family_key": key,
                        "member_slot": slot,
                        "member_id": member,
                        "ministry": str(ministry),
                        "ministry_code": code.group(1) if code else None,
                        "response": response,
                    }
                )
    return rows


def _daily(source, submissions, families):
    """The daily rows, derived from ``submissions`` by the format's rule.

    Day N's figures use each Family's latest answer by the end of day N; a
    blank annual pledge adds nothing. The series runs from day 1 to the day
    of the last submission.
    """
    last_day = submissions[-1]["campaign_day"]
    firsts = Counter(f["first_campaign_day"] for f in families)
    active = source_active_families(source)
    latest, position, running, rows = {}, 0, 0, []
    for day in range(1, last_day + 1):
        while (
            position < len(submissions) and submissions[position]["campaign_day"] <= day
        ):
            row = submissions[position]
            latest[row["family_key"]] = row["annual_pledge"] or Decimal(0)
            position += 1
        running += firsts[day]
        rows.append(
            {
                "campaign_day": day,
                "local_date": source.start_date + timedelta(days=day - 1),
                "first_submissions": firsts[day],
                "cumulative_families": running,
                "cumulative_pct_of_active": running / active,
                "cumulative_pledging_families": sum(v > 0 for v in latest.values()),
                "cumulative_annual_pledged": sum(latest.values(), Decimal(0)),
                "source": "stewardship_daily_fact",
            }
        )
    return rows


def source_active_families(source):
    """The cutoff day's cohort denominator, the archive's active_families."""
    fact = {f.local_date: f for f in source.facts}.get(source.cutoff_date)
    if fact is None or not fact.population_available or fact.cohort_denominator < 1:
        raise FactUnavailable("The cutoff day's population is unavailable.")
    return fact.cohort_denominator


def archive_sheets(source):
    """(families, submissions, daily, ministry_responses) rows of ``source``.

    Raises ``ArchiveUnavailable`` when no Family has responded by the cutoff.
    """
    if not source.versions:
        raise ArchiveUnavailable("No Family has responded by the cutoff.")
    zone = ZoneInfo(source.timezone)

    def when(instant):
        """(instant, local date, campaign day) as the sheets write them."""
        local = instant.astimezone(zone).date()
        return instant, local, (local - source.start_date).days + 1

    ordered = _ordered(source.versions)
    by_family = defaultdict(list)
    for version in ordered:
        by_family[version.family_duid].append(version)
    submissions = []
    for row_id, version in enumerate(ordered, start=1):
        instant, local, day = when(version.submitted_at)
        submissions.append(
            {
                "row_id": row_id,
                "family_key": f"duid:{version.family_duid}",
                "form": "combined",
                "submitted_at": instant,
                "local_date": local,
                "campaign_day": day,
                "annual_pledge": version.annual_pledge,
                "frequency": version.frequency,
                "cannot_give": version.cannot_give,
                "is_last_for_family": by_family[version.family_duid][-1] is version,
                "attributed": True,
            }
        )
    families, ministry = [], []
    for duid in sorted(by_family):
        versions, key = by_family[duid], f"duid:{duid}"
        first, last = versions[0], versions[-1]
        status, annual, frequency, cannot_give = financial_answer(
            {
                "annual_pledge": last.annual_pledge,
                "frequency": last.frequency,
                "cannot_give": last.cannot_give,
            }
        )
        installment = _installment(annual, frequency)
        first_at, first_local, first_day = when(first.submitted_at)
        last_at, last_local, last_day = when(last.submitted_at)
        families.append(
            {
                "family_key": key,
                "family_id": duid,
                "family_id_system": "parishsoft_duid",
                "submission_count": len(versions),
                "first_submitted_at": first_at,
                "first_local_date": first_local,
                "first_campaign_day": first_day,
                "last_submitted_at": last_at,
                "last_local_date": last_local,
                "last_campaign_day": last_day,
                "pledge_status": status,
                "annual_pledge": annual,
                "frequency": frequency,
                "installment_amount": installment,
                "installment_source": None if installment is None else "computed",
                "cannot_give": cannot_give,
            }
        )
        ministry += _ministry_rows(key, last)
    daily = _daily(source, submissions, families)
    return families, submissions, daily, ministry


def check_facts(source, daily):
    """Compare the derived ``daily`` rows with the fact rows; count unchecked days.

    Returns {"population": n, "pledge": m}: days whose fact population or
    pledge total is unavailable, which are left unchecked rather than called
    a disagreement. Raises ``FactUnavailable`` for a missing fact row or a
    disagreement, so the generation is retried and then failed like any
    fact failure, and a file that disagrees with the report is never offered.
    """
    facts = {f.local_date: f for f in source.facts}
    unchecked = {"population": 0, "pledge": 0}
    cumulative = 0
    days = (source.cutoff_date - source.start_date).days + 1
    for number in range(1, days + 1):
        day = source.start_date + timedelta(days=number - 1)
        fact = facts.get(day)
        if fact is None:
            raise FactUnavailable("A fact row the archive needs is missing.")
        row = daily[number - 1] if number <= len(daily) else None
        if row is not None:
            cumulative = row["cumulative_families"]
            first = row["first_submissions"]
        else:
            # After the last submission no fact row may add a response.
            first = 0
        if fact.population_available:
            if (fact.first_responses, fact.cumulative_responses) != (first, cumulative):
                raise FactUnavailable(
                    "The archive's responses disagree with its facts."
                )
        else:
            unchecked["population"] += 1
        if row is None:
            continue
        if fact.pledge_available:
            if fact.pledge_total != row["cumulative_annual_pledged"]:
                raise FactUnavailable("The archive's pledges disagree with its facts.")
        else:
            unchecked["pledge"] += 1
    return unchecked


def _as_of(source):
    """The instant the file describes: the last submission, or the cutoff."""
    if source.final:
        return max(v.submitted_at for v in source.versions).replace(microsecond=0)
    return source.cutoff


def archive_metadata(source, families, submissions, daily, ministry, unchecked):
    """The metadata sheet's (key, value, note) rows."""
    notes = []
    if source.outside_cohort:
        notes.append(
            "Live responses of Families outside the campaign's historical cohort "
            "were left out of every sheet (see count.outside_cohort)."
        )
    if not source.financial:
        notes.append(
            "The campaign collected no pledges (no financial module), so the "
            "pledge columns are blank and the daily pledge totals are zero."
        )
    else:
        if unchecked["pledge"]:
            notes.append(
                f"The pledge total of {unchecked['pledge']} day(s) was unavailable "
                "in the report's facts and was not checked against them."
            )
    if unchecked["population"]:
        notes.append(
            f"The population of {unchecked['population']} day(s) was unavailable "
            "in the report's facts and was not checked against them."
        )
    last = max(v.submitted_at for v in source.versions)
    rows = [
        ("schema", SCHEMA, None),
        ("schema_version", SCHEMA_VERSION, None),
        ("campaign_name", source.campaign_name, None),
        ("campaign_year", source.campaign_year, None),
        ("is_final", source.final, None),
        ("as_of", _as_of(source), None),
        ("timezone", source.timezone, None),
        ("campaign_start_date", source.start_date, None),
        (
            "campaign_last_submission_date",
            last.astimezone(ZoneInfo(source.timezone)).date(),
            None,
        ),
        ("active_families", source_active_families(source), None),
        (
            "active_families_provenance",
            PROVENANCE.format(day=(source.cutoff_date - source.start_date).days + 1),
            None,
        ),
        ("source_system", SOURCE_SYSTEM, None),
        ("source_files", SOURCE_FILES, None),
        ("converted_at", source.converted_at.replace(microsecond=0), None),
        ("converter", CONVERTER, None),
        ("dedup_rule", DEDUP_RULE, None),
        ("count.families", len(families), None),
        ("count.rows_read", len(submissions), None),
        ("count.unattributed_rows", 0, None),
        ("count.ministry_response_rows", len(ministry), None),
    ]
    if source.outside_cohort:
        rows.append(("count.outside_cohort", source.outside_cohort, None))
    rows += [(f"note.{index:02d}", text, None) for index, text in enumerate(notes, 1)]
    return rows


def write_source(source, *, check, on_timeout=None):
    """Build, check and write ``source``'s archive; read it back; return the file.

    ``check`` is the import's reader entry point (``check.check_archive`` in
    production, which runs the isolated helper with its limits); a refusal
    raises ``ArchiveWriteRefused``.
    """
    families, submissions, daily, ministry = archive_sheets(source)
    unchecked = check_facts(source, daily)
    metadata = archive_metadata(
        source, families, submissions, daily, ministry, unchecked
    )
    data = write_archive(
        metadata,
        {
            "families": families,
            "submissions": submissions,
            "daily": daily,
            "ministry_responses": ministry,
        },
        as_of=_as_of(source),
    )
    document = check(data) if on_timeout is None else check(data, on_timeout=on_timeout)
    if not document.get("valid"):
        raise ArchiveWriteRefused(document.get("problems", ()))
    return ArchiveFile(
        archive_name(source.campaign_year, source.final),
        data,
        hashlib.sha256(data).hexdigest(),
        len(families),
        len(submissions),
        len(ministry),
        unchecked,
    )


# --- The database read ------------------------------------------------------------


def _version(row, names):
    """A ``Version`` from one submission row and its snapshot's Ministry names."""
    answers = row["answers"] if isinstance(row["answers"], dict) else {}
    financial = (
        answers.get("financial") if isinstance(answers.get("financial"), dict) else None
    )
    amount = row["annual_pledge"]
    if amount is None or financial is None:
        annual = frequency = cannot_give = None
    else:
        annual = Decimal(amount).quantize(CENT)
        frequency = financial.get("frequency") or None
        cannot_give = financial.get("cannot_give") is True
    return Version(
        row["family__family_duid"],
        row["campaign_sequence"],
        row["submitted_at"],
        annual,
        frequency,
        cannot_give,
        answers.get("ministries") or {},
        names,
    )


def _ministry_names(rows):
    """{snapshot id: {Ministry DUID text: name}} for the choices of ``rows``."""
    from parishkit.stewardship.source.version_models import SnapshotMinistry

    wanted = defaultdict(set)
    for row in rows:
        answers = row["answers"] if isinstance(row["answers"], dict) else {}
        choices = answers.get("ministries")
        choices = choices if isinstance(choices, dict) else {}
        for group in ("members", "proposed_members"):
            for entry in (choices.get(group) or {}).values():
                for response in ("join", "leave"):
                    for ministry in (entry or {}).get(response, ()):
                        wanted[row["reviewed_source_id"]].add(str(ministry))
    names = defaultdict(dict)
    for snapshot_id, keys in wanted.items():
        for key, payload in SnapshotMinistry.objects.filter(
            snapshot_id=snapshot_id, source_key__in=keys
        ).values_list("source_key", "payload__payload"):
            name = payload.get("name") if isinstance(payload, dict) else None
            names[snapshot_id][key] = name if isinstance(name, str) else ""
    return names


def read_source(fact_set_id, *, cutoff_date, final, converted_at):
    """Read one archive's ``ArchiveSource`` in a read-only repeatable-read transaction.

    ``fact_set_id`` is a ready historical fact set holding ``cutoff_date``.
    The read takes the live versions with a campaign sequence up to the fact
    set's submission watermark and submitted before the cutoff, of Families
    in the historical cohort at the cutoff day (the participation graph's
    respondents). Raises ``FactUnavailable`` when the fact set is not a ready
    historical one, lacks the cutoff day, or its watermark does not yet cover
    every version submitted before the cutoff (the export waits).
    """
    from django.db import connection, transaction

    from parishkit.stewardship.archive.imports import ArchiveRefusal, year_from_values
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.campaigns.models import Campaign, CampaignConfiguration
    from parishkit.stewardship.reports.models import (
        CampaignDailyFact,
        CampaignDailyFactSet,
    )
    from parishkit.stewardship.responses.models import Submission

    if connection.in_atomic_block:
        raise RuntimeError("The archive read must start its own transaction.")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        record = CampaignDailyFactSet.objects.filter(
            pk=fact_set_id, state="ready", population_scope="historical"
        ).first()
        if record is None:
            raise FactUnavailable("The archive needs a ready historical fact set.")
        projection = CampaignConfiguration.objects.get(
            pk=record.timezone_configuration_id
        )
        active = (
            Campaign.objects.select_related("active_configuration")
            .get(pk=record.campaign_id)
            .active_configuration
        )
        cutoff = local_day(cutoff_date, projection.timezone).interval.end
        facts = tuple(
            FactDay(
                row.local_date,
                row.first_responses,
                row.cumulative_responses,
                row.cohort_denominator,
                row.population_available,
                row.pledge_available,
                row.pledge_total,
            )
            for row in CampaignDailyFact.objects.filter(
                fact_set=record,
                local_date__gte=projection.start_date,
                local_date__lte=cutoff_date,
            ).order_by("local_date")
        )
        at_cutoff = facts[-1] if facts and facts[-1].local_date == cutoff_date else None
        if at_cutoff is None:
            raise FactUnavailable("The fact set does not reach the cutoff day.")
        live = Submission.objects.filter(
            campaign_id=record.campaign_id, mode="live", submitted_at__lt=cutoff
        )
        if live.filter(campaign_sequence__gt=record.submission_watermark).exists():
            raise FactUnavailable("The facts have not yet absorbed every response.")
        generation = (
            CampaignDailyFact.objects.filter(fact_set=record, local_date=cutoff_date)
            .values_list("source_generation", flat=True)
            .get()
        )
        cohort = set(
            FamilyCampaign.objects.filter(
                campaign_id=record.campaign_id,
                first_eligible_at__isnull=False,
                first_eligible_at__lt=cutoff,
                first_eligible_source_generation__lte=generation or 0,
            ).values_list("id", flat=True)
        )
        rows = list(
            live.filter(campaign_sequence__lte=record.submission_watermark).values(
                "family_id",
                "family__family_duid",
                "campaign_sequence",
                "submitted_at",
                "annual_pledge",
                "answers",
                "reviewed_source_id",
            )
        )
        outside = {row["family_id"] for row in rows if row["family_id"] not in cohort}
        rows = [row for row in rows if row["family_id"] in cohort]
        names = _ministry_names(rows)
        versions = tuple(
            _version(row, names.get(row["reviewed_source_id"], {})) for row in rows
        )
        try:
            year = year_from_values(active.values)
        except ArchiveRefusal:
            raise ArchiveUnavailable(
                "The campaign's stewardship year cannot be resolved."
            ) from None
        return ArchiveSource(
            campaign_name=active.name,
            campaign_year=year,
            timezone=projection.timezone,
            start_date=projection.start_date,
            cutoff_date=cutoff_date,
            final=final,
            financial=projection.values.get("financial") is not None,
            versions=versions,
            facts=facts,
            converted_at=converted_at,
            outside_cohort=len(outside),
        )


def export_archive(fact_set_id, *, cutoff_date, final, converted_at, on_timeout=None):
    """The checked archive file of one campaign under the given pins.

    Reads (``read_source``), then builds, checks, writes and reads back
    (``write_source``) outside the database transaction, through the
    import's isolated reader helper.
    """
    from .check import check_archive

    source = read_source(
        fact_set_id, cutoff_date=cutoff_date, final=final, converted_at=converted_at
    )
    return write_source(source, check=check_archive, on_timeout=on_timeout)
