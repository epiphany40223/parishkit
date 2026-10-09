"""On-request report audits record their mode, filter and snapshot (#556).

The response lists and the Talents report record closed choices only: the
system mode, each report's own filter key, a configured talent's UUID and the
ParishSoft snapshot read, never search text. Python's ``sanitize`` and the SQL
allowlist (``stewardship_safe_context_v1``) accept the same contexts; the
PostgreSQL side runs ``DOWNLOAD_AUDIT_CASES`` in
database/test_audit_context_postgresql.py.
"""

import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.audit.log_descriptions import FIELD_LABELS, VALUE_LABELS
from parishkit.stewardship.audit.log_rows import detail_labels
from parishkit.stewardship.audit.schemas import (
    REPORT_FILTERS,
    REPORT_MODES,
    ContextKind,
    Outcome,
    sanitize,
)
from parishkit.stewardship.reports.response_lists import LISTS, ListQuery
from parishkit.stewardship.reports.response_metrics import MODES
from parishkit.stewardship.reports.talent_views import TALENT_WORDS, audit_choices
from parishkit.stewardship.reports.talents import TalentQuery

ROOT = Path(__file__).resolve().parents[2]
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
    """The closed vocabulary follows the reports' own menus, no more."""
    shows = {choice.value for spec in LISTS.values() for choice in spec.choices}
    assert shows | TALENT_WORDS | {"option"} == REPORT_FILTERS
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


def test_response_list_audit_choices():
    from parishkit.stewardship.reports.response_list_views import audit_choices

    spec = LISTS["data-quality"]
    query, _ = ListQuery.parse(
        spec, MultiValueDict({"mode": ["testing"], "show": ["envelope"]})
    )
    snapshot = uuid4()
    context = audit_choices(query, snapshot)
    assert context == {
        "report_mode": "testing",
        "report_filter": "envelope",
        "snapshot_id": snapshot,
    }
    assert sanitize(ContextKind.ACTION, context)["snapshot_id"] == str(snapshot)
    # Nothing was read (Testing with no rehearsal): no snapshot is named.
    assert "snapshot_id" not in audit_choices(query, None)


def test_talent_audit_choices_never_keep_the_search_text():
    query = TalentQuery.parse({"search": "Smith", "talent": OPTION})
    context = audit_choices(query, None)
    assert context == {
        "search_used": True,
        "report_filter": "option",
        "talent_option_id": UUID(OPTION),
    }
    assert "Smith" not in str(sanitize(ContextKind.ACTION, context))
    plain = audit_choices(TalentQuery.parse({"talent": "cannot_serve"}), uuid4())
    assert plain["report_filter"] == "cannot_serve" and not plain["search_used"]
    assert "talent_option_id" not in plain and "snapshot_id" in plain


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
    }
