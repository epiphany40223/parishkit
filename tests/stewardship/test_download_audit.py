"""On-request report audits record their mode, filter and snapshot (#556).

A response list also records its sort token (``report_sort``, #851).

The response lists and the Limitations report record closed choices only: the
system mode, each report's own filter key, a configured talent's UUID (kept
for rows recorded before talents were retired, #970) and the ParishSoft
snapshot read, never search text. Python's ``sanitize`` and the SQL
allowlist (``stewardship_safe_context_v1``) accept the same contexts; the
PostgreSQL side runs ``DOWNLOAD_AUDIT_CASES`` in
database/test_audit_context_postgresql.py.
"""

import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.audit.log_descriptions import (
    FIELD_LABELS,
    VALUE_LABELS,
    field_value,
)
from parishkit.stewardship.audit.log_rows import detail_labels
from parishkit.stewardship.audit.schemas import (
    REPORT_FILTERS,
    REPORT_MODES,
    REPORT_SORT_COLUMNS,
    REPORT_SORTS,
    ContextKind,
    Outcome,
    sanitize,
)
from parishkit.stewardship.reports.limitation_views import audit_choices
from parishkit.stewardship.reports.limitations import SHOW_WORDS, LimitationsQuery
from parishkit.stewardship.reports.response_metrics import MODES

ROOT = Path(__file__).resolve().parents[2]
# The removed response lists' Show choices (#933), which earlier audit
# entries still hold, so the vocabulary keeps them.
LIST_SHOWS = frozenset(
    {
        "all",
        "progressed",
        "opened",
        "followed",
        "unfollowed",
        "mailing-name",
        "envelope",
    }
)
SNAPSHOT = "00000000-0000-0000-0000-000000000001"
OPTION = "aaaaaaaa-0000-0000-0000-000000000002"

# (JSON context, accepted) pairs that Python and SQL must decide alike.
DOWNLOAD_AUDIT_CASES = [
    ({"report_mode": "production", "report_filter": "all", "count": 3}, True),
    ({"report_mode": "testing", "report_filter": "mailing-name"}, True),
    ({"report_filter": "option", "talent_option_id": OPTION}, True),
    ({"report_filter": "cannot_attend", "search_used": True}, True),
    ({"snapshot_id": SNAPSHOT, "outcome": "succeeded"}, True),
    ({"report_mode": "live"}, False),
    ({"report_mode": True}, False),
    ({"report_filter": "Smith"}, False),
    ({"report_filter": "ALL"}, False),
    ({"report_filter": 1}, False),
    ({"snapshot_id": "not-a-uuid"}, False),
    ({"talent_option_id": OPTION.upper()}, False),
    ({"search": "Smith"}, False),
    ({"report_search": "Smith"}, False),
    # A response list's sort token (#851): a column key, or "-" and one.
    ({"report_filter": "all", "report_sort": "-submitted"}, True),
    ({"report_sort": "family"}, True),
    ({"report_sort": "name"}, False),
    ({"report_sort": "--family"}, False),
    ({"report_sort": "Smith"}, False),
    ({"report_sort": 1}, False),
    # An export's report kind and file format (#1398): closed words only.
    ({"report": "family_directory", "format": "pdf", "outcome": "started"}, True),
    ({"report": "campaign_archive", "format": "xlsx"}, True),
    ({"report": "anything"}, False),
    ({"report": "Participation"}, False),
    ({"report": 1}, False),
    ({"format": "docx"}, False),
    ({"format": "CSV"}, False),
]


def _python(context):
    """Context as a caller passes it: UUIDs for ids, the Outcome enum."""
    shaped = dict(context)
    for key, value in context.items():
        if key == "outcome":
            shaped[key] = Outcome(value)
        elif key.endswith("_id") and type(value) is str and value == value.lower():
            shaped[key] = UUID(value)
    return shaped


@pytest.mark.parametrize("context, valid", DOWNLOAD_AUDIT_CASES)
def test_sanitize_accepts_only_closed_report_choices(context, valid):
    if valid:
        assert sanitize(ContextKind.ACTION, _python(context)) == context
    else:
        with pytest.raises(ValueError):
            sanitize(ContextKind.ACTION, _python(context))


def test_report_filters_are_exactly_the_reports_choices():
    """The closed vocabulary follows the reports' own menus, no more.

    The removed response lists' Show values (#933) stay, with Submitted's
    earlier retired ones (#860), since earlier events recorded them. So does
    the Limitations report's ``option`` (a talent filter, retired with #970).
    """
    shows = LIST_SHOWS | {"invited", "uninvited"}
    assert shows | SHOW_WORDS | {"option"} == REPORT_FILTERS
    assert set(MODES) == REPORT_MODES


def test_sql_allowlist_names_the_same_words():
    """The baseline SQL lists the Python vocabularies, so they cannot drift."""
    text = (ROOT / "src/parishkit/stewardship/schema/functions.sql").read_text()

    def words(key):
        """The quoted words of one key's NOT IN list in the allowlist."""
        branch = re.search(
            rf"ELSIF key='{key}' THEN\s+IF .*? NOT IN \((.*?)\) THEN", text, re.S
        )
        return set(re.findall(r"'([^']*)'", branch[1]))

    assert words("report_filter") == REPORT_FILTERS
    assert words("report_mode") == REPORT_MODES
    assert words("report_sort") == REPORT_SORTS


def test_limitations_audit_choices_never_keep_the_search_text():
    query = LimitationsQuery.parse({"search": "Smith", "show": "cannot_attend"})
    context = audit_choices(query, None)
    assert context == {"search_used": True, "report_filter": "cannot_attend"}
    assert "Smith" not in str(sanitize(ContextKind.ACTION, context))
    plain = audit_choices(LimitationsQuery.parse({"show": "cannot_serve"}), uuid4())
    assert plain["report_filter"] == "cannot_serve" and not plain["search_used"]
    assert "talent_option_id" not in plain and "snapshot_id" in plain


def test_talent_filter_rows_from_before_970_still_read():
    """Rows that recorded a talent filter stay valid and worded (#970)."""
    context = {"report_filter": "option", "talent_option_id": UUID(OPTION)}
    assert sanitize(ContextKind.ACTION, context) == {
        "report_filter": "option",
        "talent_option_id": OPTION,
    }
    rows = detail_labels([("report_filter", "option"), ("talent_option_id", OPTION)])
    assert [(str(key), str(value)) for key, value in rows] == [
        ("Filter chosen", "One talent"),
        ("Talent chosen (its settings id)", OPTION),
    ]


def test_system_logs_show_the_choices_in_words():
    """Field names and closed values read as the pages word them."""
    assert set(VALUE_LABELS["report_filter"]) == REPORT_FILTERS
    assert set(VALUE_LABELS["report_mode"]) == REPORT_MODES
    rows = detail_labels(
        [
            ("report_filter", "mailing-name"),
            ("report_mode", "testing"),
            ("search_used", "True"),
            ("snapshot_id", SNAPSHOT),
            ("count", "2"),
        ]
    )
    assert [(str(key), str(value)) for key, value in rows] == [
        ("Filter chosen", "Blank mailing name"),
        ("Responses shown", "Testing"),
        ("Search box used", "True"),
        ("ParishSoft data read (its snapshot id)", SNAPSHOT),
        ("Count", "2"),
    ]
    assert set(FIELD_LABELS) <= {
        "report_mode",
        "report_filter",
        "talent_option_id",
        "snapshot_id",
        "search_used",
        "report_sort",
        # The active parishioner family directory's choices (#933).
        "directory_response",
        "directory_data_check",
        "directory_response_columns",
        "directory_sort",
        # A report view the open page made by itself (#1620).
        "automatic",
    }


def test_automatic_report_views_read_as_an_automatic_refresh():
    """An automatic view's marker is a boolean action key, shown in words.

    The open Participation or Campaign comparison page re-reads itself to
    bring newer figures in; its audited view records ``automatic: true``
    (#1620), which System logs words as an automatic refresh.
    """
    context = {"outcome": Outcome.STARTED, "automatic": True}
    assert sanitize(ContextKind.ACTION, context) == {
        "outcome": "started",
        "automatic": True,
    }
    for value in ("true", 1, None):
        with pytest.raises(ValueError):
            sanitize(ContextKind.ACTION, {"automatic": value})
    with pytest.raises(ValueError):
        sanitize(ContextKind.REQUEST, {"automatic": True})
    rows = detail_labels([("automatic", "True"), ("outcome", "succeeded")])
    assert [(str(key), str(value)) for key, value in rows] == [
        ("How it was opened", "Automatic refresh (the open page updated itself)"),
        ("Outcome", "succeeded"),
    ]


def test_directory_sorts_read_in_words():
    """System logs word each directory sort by the page's own heading (#933)."""
    from parishkit.stewardship.audit.schemas import DIRECTORY_SORTS
    from parishkit.stewardship.reports.directories import RESPONSE_COLUMNS

    assert set(VALUE_LABELS["directory_sort"]) == DIRECTORY_SORTS
    assert str(field_value("directory_sort", "name")) == "Family (ascending)"
    assert str(field_value("directory_sort", "name_desc")) == "Family (descending)"
    assert str(field_value("directory_sort", "duid")) == "Family DUID (ascending)"
    assert str(field_value("directory_sort", "duid_desc")) == "Family DUID (descending)"
    for key, column in RESPONSE_COLUMNS.items():
        ascending = str(field_value("directory_sort", key))
        assert ascending == f"{column.heading} (ascending)"
        assert (
            str(field_value("directory_sort", f"{key}_desc"))
            == f"{column.heading} (descending)"
        )
    rows = detail_labels([("directory_sort", "submitted_desc")])
    assert [(str(key), str(value)) for key, value in rows] == [
        ("Sorted by", "First submitted (descending)")
    ]


def test_response_list_sorts_still_read():
    """Every recorded response list sort token reads in words (#851, #933).

    The lists were removed, but their earlier entries keep their tokens: a
    column key ascending, ``-key`` descending, each worded by the column's
    heading in System logs.
    """
    assert set(VALUE_LABELS["report_sort"]) == REPORT_SORTS
    for key in REPORT_SORT_COLUMNS:
        heading = str(field_value("report_sort", key)).removesuffix(" (ascending)")
        assert heading != key
        assert str(field_value("report_sort", f"-{key}")) == f"{heading} (descending)"
    rows = detail_labels([("report_sort", "-duid")])
    assert [(str(key), str(value)) for key, value in rows] == [
        ("Sorted by", "Family DUID (descending)")
    ]
