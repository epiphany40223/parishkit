"""The engagement backfill command (#477): pure grouping and its console entry."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship import engagement_backfill
from parishkit.stewardship.cli import main

SENTINEL = "private-config-value"
NOW = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch):
    """Console tests must not install the process-wide stderr log handler."""
    monkeypatch.setattr(engagement_backfill, "configure_logging", lambda: None)


def test_backfill_observations_merge_per_family_and_keep_only_current_families():
    first, second, stranger = sorted((uuid4(), uuid4(), uuid4()), key=str)
    earlier = NOW - timedelta(hours=2)
    observations = engagement_backfill.backfill_observations(
        [(first, earlier, NOW), (stranger, earlier, NOW)],
        [(first, NOW, NOW), (second, earlier, earlier)],
        {first, second},
    )
    assert observations == [
        {"family_id": first, "seen_at": NOW, "link_at": earlier, "form_at": NOW},
        {"family_id": second, "seen_at": earlier, "form_at": earlier},
    ]
    assert engagement_backfill.BATCH_FAMILIES == 50


def test_cli_prints_the_backfill_document(monkeypatch, capsys):
    document = {"check": "engagement_backfill", "result": "backfilled"}
    monkeypatch.setattr(engagement_backfill, "load_deployment", lambda path: object())
    monkeypatch.setattr(
        engagement_backfill, "engagement_backfill_command", lambda config: document
    )
    assert main(["engagement-backfill", "--config", SENTINEL]) == 0
    assert json.loads(capsys.readouterr().out) == document


@pytest.mark.parametrize(
    "error,line",
    [
        (None, "engagement backfill refused"),
        (engagement_backfill.BackfillRefused(SENTINEL), "engagement backfill refused"),
        (ValueError(SENTINEL), "unexpected error"),
    ],
)
def test_cli_failures_print_one_fixed_line(monkeypatch, capsys, error, line):
    """Missing configuration, refusals and faults never echo private detail."""
    monkeypatch.setattr(engagement_backfill, "load_deployment", lambda path: object())
    monkeypatch.setattr(engagement_backfill, "emit_failure", lambda err, *, event: None)

    def fail(config):
        raise error

    monkeypatch.setattr(engagement_backfill, "engagement_backfill_command", fail)
    argv = ["engagement-backfill"] + ([] if error is None else ["--config", SENTINEL])
    assert main(argv) == 2
    output = capsys.readouterr()
    assert output.out == "" and line in output.err
    assert output.err.count("ERROR:") == 1 and SENTINEL not in output.err


def test_cli_rejects_options_the_backfill_does_not_take(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["engagement-backfill", "--config", "c", "--samples", SENTINEL])
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "options not supported by engagement-backfill: --samples" in output.err
    assert SENTINEL not in output.err
