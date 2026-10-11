"""Synthetic campaign archive workbooks for the #1018 tests.

Every value here is invented: no real Family, pledge or archive is used in
CI. ``campaign`` derives the families, daily and Ministry sheets and the
metadata from a list of submissions the way the one-time converter did
(last submission by submitted_at, then row_id; each day's latest financial
answer), so a test starts from a consistent file and changes one thing.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from parishkit.stewardship.archive.workbook import (
    FINANCIAL_FORMS,
    SCHEMA,
    SCHEMA_VERSION,
    build_workbook,
    financial_answer,
    save_workbook,
)

ZONE = "America/New_York"
START = date(2054, 10, 4)
ACTIVE = 40


def at(day, hour=14, minute=0, second=0):
    """A UTC instant on a campaign day (14:00 UTC is 10:00 in New York)."""
    moment = datetime.combine(START, datetime.min.time(), tzinfo=UTC)
    return moment + timedelta(days=day - 1, hours=hour, minutes=minute, seconds=second)


def submission(key, form, when, annual=None, frequency=None, cannot_give=False):
    """One source submission; ``key`` None is an unattributed row."""
    return {
        "family_key": key,
        "form": form,
        "submitted_at": when,
        "annual_pledge": None if annual is None else Decimal(annual),
        "frequency": frequency,
        "cannot_give": None if form == "ministry" else cannot_give,
    }


def default_submissions():
    """A small campaign touching every rule: revisions, two forms, a tie."""
    return [
        submission("duid:1001", "combined", at(1), "1200", "monthly"),
        submission("duid:1002", "combined", at(1, 15), "0"),
        submission("duid:1003", "combined", at(2), None, None, True),
        submission(None, "combined", at(2, 16), "75", "annual"),
        submission("duid:1004", "combined", at(3), None),
        # A revision on day 4 replaces 1001's pledge; it never adds.
        submission("duid:1001", "combined", at(4), "2400.5", "monthly"),
        submission("duid:1005", "combined", at(4, 18), "260.25", "weekly"),
        # Two submissions in the same second: row_id settles the tie.
        submission("duid:1006", "combined", at(5), "500", "annual"),
        submission("duid:1006", "combined", at(5), "10000", "annual"),
        submission("duid:1007", "ministry", at(6)),
    ]


def campaign(submissions=None, *, active=ACTIVE):
    """(metadata rows, sheets) of a consistent archive of these submissions."""
    submissions = default_submissions() if submissions is None else submissions
    zone = ZoneInfo(ZONE)
    ordered = sorted(enumerate(submissions), key=lambda p: (p[1]["submitted_at"], p[0]))
    rows = []
    for row_id, (_, source) in enumerate(ordered, start=1):
        local = source["submitted_at"].astimezone(zone).date()
        rows.append(
            {
                "row_id": row_id,
                **source,
                "local_date": local,
                "campaign_day": (local - START).days + 1,
                "attributed": source["family_key"] is not None,
                "is_last_for_family": False,
            }
        )
    by_family = defaultdict(list)
    for row in rows:
        if row["attributed"]:
            by_family[row["family_key"]].append(row)
    families, ministry = [], []
    for key, own in sorted(by_family.items()):
        for form in {r["form"] for r in own}:
            [r for r in own if r["form"] == form][-1]["is_last_for_family"] = True
        financial = [r for r in own if r["form"] in FINANCIAL_FORMS]
        status, annual, frequency, cannot = financial_answer(
            financial[-1] if financial else None
        )
        first, last = own[0], own[-1]
        families.append(
            {
                "family_key": key,
                "family_id": int(key.split(":")[1]),
                "family_id_system": "parishsoft_duid",
                "submission_count": len(own),
                "first_submitted_at": first["submitted_at"],
                "first_local_date": first["local_date"],
                "first_campaign_day": first["campaign_day"],
                "last_submitted_at": last["submitted_at"],
                "last_local_date": last["local_date"],
                "last_campaign_day": last["campaign_day"],
                "pledge_status": status,
                "annual_pledge": annual,
                "frequency": frequency,
                "frequency_detail": frequency,
                "installment_amount": None if annual is None else Decimal("1.00"),
                "installment_source": None if annual is None else "computed",
                "cannot_give": cannot,
            }
        )
        ministry.append(
            {
                "family_key": key,
                "member_slot": 1,
                "member_id": 7000 + len(ministry),
                "ministry": "101 Invented Choir",
                "ministry_code": "101",
                "response": "participate",
            }
        )
    attributed = [r for r in rows if r["attributed"]]
    last_date = max(r["local_date"] for r in attributed)
    daily = _daily(attributed, (last_date - START).days + 1, active)
    metadata = [
        ("schema", SCHEMA, None),
        ("schema_version", SCHEMA_VERSION, None),
        ("campaign_name", "Stewardship 2055", None),
        ("campaign_year", 2055, None),
        ("is_final", True, None),
        ("as_of", max(r["submitted_at"] for r in attributed), None),
        ("timezone", ZONE, None),
        ("campaign_start_date", START, None),
        ("campaign_last_submission_date", last_date, None),
        ("active_families", active, None),
        ("active_families_provenance", "Invented test cohort", None),
        ("source_system", "Invented test system", None),
        ("source_files", "none (synthetic)", None),
        ("converted_at", at(30), None),
        ("converter", "tests/stewardship/archive_factory.py", None),
        ("dedup_rule", "Last submission by submitted_at, then row_id", None),
        ("column_mapping.01", "Pledge -> annual_pledge", None),
        ("note.01", "Synthetic data for tests", None),
        ("count.rows_read", len(rows), None),
        ("count.families", len(families), None),
        ("count.unattributed_rows", len(rows) - len(attributed), None),
        ("count.ministry_response_rows", len(ministry), None),
        ("count.active_now", 41, None),
    ]
    sheets = {
        "families": families,
        "submissions": [{k: v for k, v in r.items()} for r in rows],
        "daily": daily,
        "ministry_responses": ministry,
    }
    return metadata, sheets


def _daily(attributed, days, active):
    """The day-by-day series, each Family's latest financial answer per day."""
    seen, latest, out = set(), {}, []
    position = 0
    for day in range(1, days + 1):
        first = 0
        while (
            position < len(attributed) and attributed[position]["campaign_day"] <= day
        ):
            row = attributed[position]
            if row["family_key"] not in seen:
                seen.add(row["family_key"])
                first += 1
            if row["form"] in FINANCIAL_FORMS:
                latest[row["family_key"]] = row["annual_pledge"] or Decimal(0)
            position += 1
        out.append(
            {
                "campaign_day": day,
                "local_date": START + timedelta(days=day - 1),
                "first_submissions": first,
                "cumulative_families": len(seen),
                "cumulative_pct_of_active": round(len(seen) / active, 4),
                "cumulative_pledging_families": sum(v > 0 for v in latest.values()),
                "cumulative_annual_pledged": sum(latest.values(), Decimal(0)),
                "source": "derived",
            }
        )
    return out


def workbook_bytes(metadata=None, sheets=None, adjust=None):
    """A saved archive; ``adjust(book)`` may change the openpyxl workbook first."""
    if metadata is None or sheets is None:
        default_metadata, default_sheets = campaign()
        metadata = default_metadata if metadata is None else metadata
        sheets = default_sheets if sheets is None else sheets
    book = build_workbook(metadata, sheets)
    if adjust is not None:
        adjust(book)
    return save_workbook(book)


def set_metadata(metadata, key, value):
    """The metadata rows with one key's value replaced (or added)."""
    rows = [(k, value if k == key else v, n) for k, v, n in metadata]
    if key not in {k for k, _, _ in metadata}:
        rows.append((key, value, None))
    return rows
