"""The campaign archive export's writer (#1018 part (a), slice A1).

Every value is invented: no real Family, pledge or archive is used. The
read-back runs the import's reader in this process (``check_worker.document``
through JSON, as the helper's reply carries it); the PostgreSQL tests cover
the database read and the helper.
"""

import json
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest

from parishkit.stewardship.archive import export
from parishkit.stewardship.archive.check_worker import document
from parishkit.stewardship.archive.export import (
    ArchiveSource,
    ArchiveUnavailable,
    ArchiveWriteRefused,
    FactDay,
    Version,
    archive_cutoff,
    archive_sheets,
    write_source,
)
from parishkit.stewardship.archive.workbook import (
    build_workbook,
    read_archive,
    save_workbook,
)
from parishkit.stewardship.reports.facts import FactUnavailable

ZONE = "America/New_York"
START = date(2054, 10, 4)
COHORT = 40
CONVERTED = datetime(2054, 10, 9, 12, 30, 15, 123456, tzinfo=UTC)


def at(day, hour=14):
    """A UTC instant on a campaign day (14:00 UTC is 10:00 in New York)."""
    return datetime.combine(START, datetime.min.time(), tzinfo=UTC) + timedelta(
        days=day - 1, hours=hour
    )


def version(
    duid, sequence, day, annual=None, frequency=None, cannot_give=None, **extra
):
    """One live version; ``annual`` None is a version with no financial answer."""
    return Version(
        duid,
        sequence,
        at(day, 14 + sequence % 5).replace(microsecond=250000),
        None if annual is None else Decimal(annual),
        frequency,
        cannot_give if annual is not None else None,
        **extra,
    )


def versions():
    """A revision, a zero, a cannot-give, a no-answer and Ministry choices."""
    return (
        version(1001, 1, 1, "1200.00", "monthly", False),
        version(1002, 2, 1, "0.00", None, False),
        version(1003, 3, 2, "0.00", None, True),
        version(1004, 4, 2),
        version(1001, 5, 3, "2400.00", "monthly", False),
        version(
            1005,
            6,
            4,
            "260.25",
            "weekly",
            False,
            ministries={
                "members": {
                    "21": {"join": [105], "leave": []},
                    "20": {"join": [], "leave": [7]},
                },
                "proposed_members": {"p-b": {"join": [105]}},
            },
            ministry_names={"105": "105 Invented Choir", "7": "Invented Ushers"},
        ),
    )


# (first responses, cumulative responses, pledge total) of days 1 to 5.
EXPECTED = (
    (2, 2, "1200.00"),
    (2, 4, "1200.00"),
    (0, 4, "2400.00"),
    (1, 5, "2660.25"),
    (0, 5, "2660.25"),
)


def facts(days=EXPECTED, *, population=True, pledge=True):
    """Fact rows of days 1 to len(days), as the participation graph has them."""
    return tuple(
        FactDay(
            START + timedelta(days=index),
            first,
            cumulative,
            COHORT,
            population,
            pledge,
            Decimal(total) if pledge else None,
        )
        for index, (first, cumulative, total) in enumerate(days)
    )


def source(**changes):
    """A partial campaign through day 5 with consistent facts."""
    values = dict(
        campaign_name="Stewardship 2055",
        campaign_year=2055,
        timezone=ZONE,
        start_date=START,
        cutoff_date=START + timedelta(days=4),
        final=False,
        financial=True,
        versions=versions(),
        facts=facts(),
        converted_at=CONVERTED,
    )
    values.update(changes)
    return ArchiveSource(**values)


def in_process(data, on_timeout=None):
    """The reader's document for ``data``, as the helper's JSON reply carries it."""
    return json.loads(json.dumps(document(data)))


def metadata_of(data):
    """{key: value text} of a written workbook's metadata sheet."""
    import openpyxl

    sheet = openpyxl.load_workbook(BytesIO(data), read_only=True)["metadata"]
    return {row[0]: row[1] for row in sheet.iter_rows(min_row=2, values_only=True)}


def test_the_cutoff_is_the_end_date_once_closed_and_else_the_last_complete_day():
    """Closed or archived: final at the end date; otherwise yesterday, capped."""
    end = START + timedelta(days=30)
    now = datetime(2054, 10, 9, 3, 0, tzinfo=UTC)  # 23:00 on Oct 8 in New York
    assert archive_cutoff("closed", START, end, ZONE, now) == (end, True)
    assert archive_cutoff("archived", START, end, ZONE, now) == (end, True)
    assert archive_cutoff("active", START, end, ZONE, now) == (date(2054, 10, 7), False)
    assert archive_cutoff("active", START, end, ZONE, at(1)) is None
    later = datetime(2054, 12, 1, tzinfo=UTC)
    assert archive_cutoff("active", START, end, ZONE, later) == (end, False)
    assert export.archive_name(2055, True) == "stewardship-2055.xlsx"
    assert export.archive_name(2055, False) == "stewardship-2055-partial.xlsx"


def test_a_written_archive_reads_back_with_this_systems_values():
    """The reader accepts the file; each column follows the export's mapping."""
    written = write_source(source(), check=in_process)
    archive = read_archive(written.data)
    families = {f["family_key"]: f for f in archive.families}
    assert [f["pledge_status"] for f in families.values()] == [
        "pledged",
        "zero",
        "cannot_give",
        "no_answer",
        "pledged",
    ]
    revised = families["duid:1001"]
    assert revised["submission_count"] == 2
    assert revised["annual_pledge"] == Decimal("2400.00")
    assert revised["installment_amount"] == Decimal("200.00")
    assert revised["installment_source"] == "computed"
    assert revised["family_id_system"] == "parishsoft_duid"
    # $260.25 a year in 52 installments is 500.48 cents, shown as $5.00.
    assert families["duid:1005"]["installment_amount"] == Decimal("5.00")
    assert families["duid:1003"]["cannot_give"] is True
    assert families["duid:1003"]["frequency"] is None
    assert families["duid:1004"]["cannot_give"] is None
    assert families["duid:1004"]["annual_pledge"] is None
    assert [
        (
            d["first_submissions"],
            d["cumulative_families"],
            d["cumulative_annual_pledged"],
        )
        for d in archive.daily
    ] == [
        (first, cumulative, Decimal(total)) for first, cumulative, total in EXPECTED[:4]
    ]
    assert archive.metadata["active_families"] == COHORT
    assert archive.metadata["is_final"] is False
    assert archive.metadata["as_of"] == source().cutoff
    assert archive.metadata["converted_at"] == CONVERTED.replace(microsecond=0)
    assert archive.metadata["campaign_last_submission_date"] == START + timedelta(
        days=3
    )
    assert archive.ministry_rows == 3
    assert archive.notes == {}
    assert (written.name, written.families, written.submissions) == (
        "stewardship-2055-partial.xlsx",
        5,
        6,
    )


def test_ministry_rows_number_members_by_duid_then_proposed_members():
    """Slots: Members by ascending DUID, then proposed Members by key."""
    rows = archive_sheets(source())[3]
    assert [
        (
            r["member_slot"],
            r["member_id"],
            r["ministry"],
            r["ministry_code"],
            r["response"],
        )
        for r in rows
    ] == [
        (1, 20, "7", None, "leave"),
        (2, 21, "105", "105", "join"),
        (3, None, "105", "105", "join"),
    ]


def test_a_final_file_is_as_of_its_last_submission():
    """A closed campaign's file is final, named so, and as of its last answer."""
    final = source(final=True)
    written = write_source(final, check=in_process)
    archive = read_archive(written.data)
    assert archive.metadata["is_final"] is True
    assert archive.metadata["as_of"] == at(4, 14 + 6 % 5)
    assert written.name == "stewardship-2055.xlsx"


def test_retries_write_identical_bytes_and_a_new_request_a_new_checksum():
    """The re-pack pins every time, so only converted_at can change the bytes."""
    first = write_source(source(), check=in_process)
    again = write_source(source(), check=in_process)
    assert first.data == again.data and first.sha256 == again.sha256
    later = write_source(
        source(converted_at=CONVERTED + timedelta(days=1)), check=in_process
    )
    assert later.sha256 != first.sha256


def test_the_repack_ignores_openpyxls_save_times():
    """Two saves with different document times re-pack to the same bytes."""
    as_of = datetime(2054, 10, 8, 4, 0, tzinfo=UTC)
    books = [build_workbook([], {}) for _ in range(2)]
    books[0].properties.created = datetime(2001, 1, 1)
    books[1].properties.created = datetime(2002, 2, 2)
    assert save_workbook(books[0]) != save_workbook(books[1])
    data = save_workbook(books[0], as_of=as_of)
    assert data == save_workbook(books[1], as_of=as_of)
    with zipfile.ZipFile(BytesIO(data)) as package:
        names = package.namelist()
        assert names[0] == "[Content_Types].xml"
        assert names[1:] == sorted(names[1:])
        assert {info.date_time for info in package.infolist()} == {
            (2054, 10, 8, 4, 0, 0)
        }
        core = package.read("docProps/core.xml").decode()
    assert core.count("2054-10-08T04:00:00Z") == 2


def test_an_unavailable_pledge_total_is_left_unchecked_with_a_note():
    """A day whose facts have no pledge total is not a disagreement."""
    days = facts()
    days = (
        *days[:2],
        replace(days[2], pledge_available=False, pledge_total=None),
        *days[3:],
    )
    written = write_source(source(facts=days), check=in_process)
    assert written.unchecked == {"population": 0, "pledge": 1}
    notes = read_archive(written.data).notes
    assert list(notes.values()) == [
        "The pledge total of 1 day(s) was unavailable in the report's facts "
        "and was not checked against them."
    ]


def test_an_unavailable_population_is_left_unchecked_with_a_note():
    """A day whose population is unavailable is skipped, never compared."""
    days = facts()
    days = (replace(days[0], population_available=False, first_responses=0), *days[1:])
    written = write_source(source(facts=days), check=in_process)
    assert written.unchecked == {"population": 1, "pledge": 0}
    assert "population of 1 day(s)" in next(
        iter(read_archive(written.data).notes.values())
    )


def test_the_cutoff_days_population_must_be_available():
    """active_families needs the cutoff day's cohort denominator."""
    days = facts()
    days = (*days[:4], replace(days[4], population_available=False))
    with pytest.raises(FactUnavailable):
        write_source(source(facts=days), check=in_process)


@pytest.mark.parametrize(
    "days",
    [
        # Day 2's cumulative pledge disagrees.
        (*EXPECTED[:1], (2, 4, "1300.00"), *EXPECTED[2:]),
        # Day 3 counts a first response the file does not have (a Family
        # whose first answer came before it entered the cohort).
        (*EXPECTED[:2], (1, 5, "2400.00"), *EXPECTED[3:]),
        # A response after the last submission day.
        (*EXPECTED[:4], (1, 6, "2660.25")),
        # A missing fact row.
        EXPECTED[:4],
    ],
)
def test_a_fact_disagreement_fails_the_generation(days):
    """No file is offered when the derived series disagrees with the facts."""
    with pytest.raises(FactUnavailable):
        write_source(source(facts=facts(days)), check=in_process)


def test_without_the_financial_module_pledges_are_blank_and_totals_zero():
    """No financial answer anywhere: no_answer, zero totals and one note."""
    plain = tuple(
        replace(v, annual_pledge=None, frequency=None, cannot_give=None)
        for v in versions()
    )
    days = facts(tuple((f, c, "0") for f, c, _ in EXPECTED), pledge=False)
    written = write_source(
        source(versions=plain, facts=days, financial=False), check=in_process
    )
    archive = read_archive(written.data)
    assert {f["pledge_status"] for f in archive.families} == {"no_answer"}
    assert {d["cumulative_annual_pledged"] for d in archive.daily} == {Decimal(0)}
    assert list(archive.notes.values()) == [
        "The campaign collected no pledges (no financial module), so the pledge "
        "columns are blank and the daily pledge totals are zero."
    ]


def test_families_outside_the_cohort_are_counted_and_noted():
    """count.outside_cohort and its note; the reader keeps both."""
    written = write_source(source(outside_cohort=2), check=in_process)
    archive = read_archive(written.data)
    assert archive.other_counts == {"count.outside_cohort": "2"}
    assert "outside the campaign's historical cohort" in next(
        iter(archive.notes.values())
    )


def test_no_respondent_means_nothing_to_export():
    """An archive needs at least one Family."""
    with pytest.raises(ArchiveUnavailable):
        write_source(source(versions=()), check=in_process)


def test_a_read_back_refusal_offers_no_file():
    """The reader's problems are kept for the log; no file is returned."""
    problem = {"code": "family", "rule": "a rule", "sheet": "families", "cell": "A2"}

    def refuse(data, on_timeout=None):
        """A reader that refuses every file."""
        return {"valid": False, "problems": [problem], "more_problems": 0}

    with pytest.raises(ArchiveWriteRefused) as refused:
        write_source(source(), check=refuse)
    assert refused.value.problems == [problem]
    assert "rule" not in str(refused.value)


def test_metadata_names_this_system_as_the_source():
    """The provenance, converter and dedup rule this system writes."""
    values = metadata_of(write_source(source(), check=in_process).data)
    assert values["source_system"] == export.SOURCE_SYSTEM
    assert values["source_files"] == export.SOURCE_FILES
    assert values["converter"] == export.CONVERTER
    assert values["dedup_rule"] == export.DEDUP_RULE
    assert values["active_families_provenance"].endswith("through day 5")
    assert "column_mapping.01" not in values
