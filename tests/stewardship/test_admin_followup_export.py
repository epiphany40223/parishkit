"""``followup export``'s own pieces: its options and the page's file name (#463).

The page's CSV, XLSX and PDF, the admission and the event are the page's
own, proven against the page in
``database/test_admin_followup_export_cli_postgresql.py``; these tests cover
the command's parser, the shared choice check and the file name.
"""

from datetime import UTC, datetime

import pytest
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship import admin_cli
from parishkit.stewardship.reports import ministry_followup, ministry_followup_exports


def parse(*argv):
    """``followup export`` parsed by pk-admin's own parser."""
    return admin_cli.build_parser().parse_args(
        ["followup", "export", *argv, "--config", "web.yaml", "--session-stdin"]
    )


def test_the_command_offers_exactly_the_pages_choices():
    """The parser's formats and filters are the page's own, so the two
    cannot drift: the parser is built before Django, so it lists them."""
    entry = admin_cli.BY_NAME["followup export"]
    assert entry.streams and entry.scope == "full" and not entry.changes_state
    args = parse()
    assert (
        args.ministry,
        args.action,
        args.state,
        args.outcome,
        args.sort,
        args.format,
        args.timezone,
    ) == (None, None, None, None, None, "xlsx", "UTC")
    assert set(admin_cli.FOLLOWUP_EXPORT_STATES) == {
        "any",
        "unresolved",
        *ministry_followup.STATES,
    }
    for fmt in ministry_followup_exports.FORMATS:
        assert parse("--format", fmt).format == fmt
    for state in admin_cli.FOLLOWUP_EXPORT_STATES:
        assert parse("--state", state).state == state
    for refused in (
        ("--format", "jsonl"),
        ("--state", "not_in_parishsoft"),
        ("--search", "Smith"),
        ("--page", "2"),
        ("--size", "50"),
    ):
        with pytest.raises(admin_cli.UsageError):
            parse(*refused)


def chosen(**values):
    """The page's choice check of ``values`` as the command posts them."""
    return ministry_followup_exports._choices(
        MultiValueDict({key: [value] for key, value in values.items()})
    )


def test_the_choice_check_is_the_pages():
    """Every option the parser takes passes the page's own check; a Ministry
    DUID or time zone the page refuses is a ``ValueError`` (``invalid``)."""
    query, fmt, zone = chosen(
        ministry="9", action="join", state="any", format="csv", timezone="UTC"
    )
    assert (query.ministry, query.action, query.state, fmt, zone.key) == (
        "9",
        "join",
        "any",
        "csv",
        "UTC",
    )
    assert query.page == 1 and query.search == ""
    for state in admin_cli.FOLLOWUP_EXPORT_STATES:
        chosen(state=state, format="xlsx", timezone="America/New_York")
    for outcome in admin_cli.FOLLOWUP_OUTCOMES:
        chosen(outcome=outcome, format="pdf", timezone="UTC")
    for refused in (
        {"ministry": "09", "format": "csv", "timezone": "UTC"},
        {"format": "csv", "timezone": "Mars/Olympus"},
        {"state": "not_in_parishsoft", "format": "csv", "timezone": "UTC"},
    ):
        with pytest.raises(ValueError):
            chosen(**refused)


def test_the_file_name_is_the_pages():
    """Stamped with the UTC instant, as the page's download was named."""
    moment = datetime(2054, 10, 3, 14, 5, 6, tzinfo=UTC)
    assert (
        ministry_followup_exports.export_file_name("csv", moment)
        == "stewardship-ministry-requests-20541003-140506Z.csv"
    )
