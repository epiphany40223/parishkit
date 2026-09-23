"""The load check prints fixed keys, counts and seconds; refusals stay generic."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import load_check
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_budget import RuntimeBudget
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.startup_interlock import StartupBusy, StartupLease

from .bootstrap_factory import bootstrap_fixture

SENTINEL_DUID = "884422"
SENTINEL_EMAIL = "private-person@example.org"
T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch):
    """Console tests must not install the process-wide stderr log handler."""
    monkeypatch.setattr(load_check, "configure_logging", lambda: None)


def passing_document(**overrides):
    """A complete passing document from the module's own assembly helpers."""
    serial = [0.1] * 4
    reports = {
        "statistics": load_check.summarize([0.2] * 20, target=2.0),
        "financial_first_page": {"status": "skipped"},
        "information_first_page": load_check.summarize([0.3] * 20, target=3.0),
    }
    document = load_check.build_document(
        population=load_check.population_counts(None, 4, 3),
        budget=RuntimeBudget(),
        form=load_check.form_section(serial, serial, samples=4, concurrency=2),
        reports=reports,
        invitation_run={"status": "not_run"},
        background={"start": {"queued": 0, "running": 0}, "end": {"queued": 1}},
    )
    document.update(overrides)
    return document


@pytest.mark.parametrize(
    "value,expected",
    [(None, 200), ("1", 1), ("1000", 1000), ("37", 37)],
)
def test_bounded_option_accepts_only_decimal_text_within_bounds(value, expected):
    assert load_check.bounded_option(value, default=200, bounds=(1, 1000)) == expected


@pytest.mark.parametrize("value", ["0", "1001", "-5", "1e3", " 7", "٣", "abc", 5])
def test_bounded_option_refuses_out_of_range_and_non_decimal_input(value):
    with pytest.raises(ConfigError):
        load_check.bounded_option(value, default=200, bounds=(1, 1000))


@pytest.mark.parametrize(
    "requested,web_threads,expected",
    [(4, 8, 4), (8, 4, 4), (100, 100, 8), (1, 1, 1), (0, 8, 1)],
)
def test_concurrency_never_exceeds_web_threads_or_the_fixed_cap(
    requested, web_threads, expected
):
    assert load_check.effective_concurrency(requested, web_threads) == expected


@pytest.mark.parametrize(
    "mode,campaign", [("production", "campaign"), ("testing", None)]
)
def test_runtime_state_refuses_production_and_missing_campaign(mode, campaign):
    with pytest.raises(ConfigError):
        load_check.admit_runtime_state(mode, campaign)
    load_check.admit_runtime_state("testing", "campaign")


def test_samples_take_largest_households_then_an_even_spread():
    """Half the sample is the biggest households; the rest spreads by DUID."""
    households = [(duid, 1) for duid in range(1, 101)]
    households[9] = (10, 7)
    households[49] = (50, 5)
    chosen = load_check.choose_samples(households, 6)
    assert chosen[:3] == [10, 50, 1]
    assert len(chosen) == len(set(chosen)) == 6
    assert chosen == load_check.choose_samples(list(reversed(households)), 6)
    # Fewer households than samples: every household once, largest first.
    assert load_check.choose_samples(households[:3], 10) == [1, 2, 3]
    assert load_check.choose_samples([], 10) == []


def test_summary_uses_nearest_rank_and_fails_on_any_refused_sample():
    timings = [index / 100 for index in range(1, 21)]
    result = load_check.summarize(timings, target=2.0)
    assert result == {
        "runs": 20,
        "failures": 0,
        "target_seconds": 2.0,
        "p50": 0.1,
        "p95": 0.19,
        "max": 0.2,
        "pass": True,
    }
    slow = load_check.summarize([0.1] * 19 + [2.5], target=2.0)
    assert slow["p95"] == 0.1 and slow["max"] == 2.5 and slow["pass"]
    assert not load_check.summarize([0.1] * 18 + [2.5, 2.6], target=2.0)["pass"]
    refused = load_check.summarize([0.1, None], target=2.0)
    assert refused["failures"] == 1 and not refused["pass"]
    empty = load_check.summarize([], target=2.0)
    assert empty["p95"] is None and not empty["pass"]


def test_verdict_requires_every_measured_section_and_ignores_the_timeline():
    document = passing_document()
    assert document["result"] == "pass"
    assert document["family_form_inputs"]["failures"] == 0
    failing = passing_document(
        invitation_run={"status": "in_progress"},
        reports={
            "statistics": load_check.summarize([0.1] * 20, target=2.0),
            "financial_first_page": load_check.summarize([3.5] * 20, target=3.0),
            "information_first_page": load_check.summarize([0.1] * 20, target=3.0),
        },
    )
    assert (
        load_check.build_document(
            population=failing["population"],
            budget=RuntimeBudget(),
            form=failing["family_form_inputs"],
            reports=failing["reports"],
            invitation_run=failing["invitation_run"],
            background=failing["background"],
        )["result"]
        == "fail"
    )
    # Nearest rank at four samples is the third value, so two slow concurrent
    # reads move p95 past the target while the serial pass stays intact.
    slow_form = load_check.form_section(
        [0.1] * 4, [0.1, 0.1, 2.2, 2.3], samples=4, concurrency=2
    )
    assert not slow_form["pass"] and slow_form["serial"]["pass"]


def occurrence(offset, state="succeeded", target="family:a"):
    return {
        "target": target,
        "created_at": T0 + timedelta(seconds=offset),
        "due_at": T0 + timedelta(minutes=5),
        "state": state,
    }


def message(created, finished, state="delivered"):
    return {
        "created_at": T0 + timedelta(seconds=created),
        "finished_at": None if finished is None else T0 + timedelta(seconds=finished),
        "state": state,
    }


def test_timeline_reports_not_run_without_occurrences():
    assert load_check.invitation_timeline([], [message(1, 2)]) == {"status": "not_run"}


def test_timeline_aggregates_a_completed_run():
    occurrences = [occurrence(0), occurrence(30, target="family:b")]
    messages = [message(60, 400), message(120, 520)]
    result = load_check.invitation_timeline(occurrences, messages)
    assert result["status"] == "complete"
    assert result["families"] == 2
    assert result["earliest_due_at"] == (T0 + timedelta(minutes=5)).isoformat()
    assert result["planning"] == {
        "first": T0.isoformat(),
        "last": (T0 + timedelta(seconds=30)).isoformat(),
        "seconds": 30.0,
        "count": 2,
        "per_minute": 4.0,
    }
    assert result["preparation"]["seconds"] == 60.0
    assert result["preparation"]["per_minute"] == 2.0
    assert result["dispatch"]["count"] == 2 and result["dispatch"]["seconds"] == 120.0
    assert result["due_to_last_terminal_seconds"] == 220.0
    assert result["occurrence_states"] == {"succeeded": 2}
    assert result["message_states"] == {"delivered": 2}


def test_timeline_marks_unfinished_runs_and_leaves_single_rates_undefined():
    occurrences = [occurrence(0, state="running")]
    messages = [message(10, None, state="submitting")]
    result = load_check.invitation_timeline(occurrences, messages)
    assert result["status"] == "in_progress"
    assert result["dispatch"] is None
    assert result["due_to_last_terminal_seconds"] is None
    assert result["preparation"]["per_minute"] is None
    assert result["occurrence_states"] == {"running": 1}
    single = load_check.invitation_timeline([occurrence(0)], [message(5, 9)])
    assert single["status"] == "complete"
    assert single["dispatch"] == {
        "first": (T0 + timedelta(seconds=9)).isoformat(),
        "last": (T0 + timedelta(seconds=9)).isoformat(),
        "seconds": 0.0,
        "count": 1,
        "per_minute": None,
    }


def test_threads_keep_item_order_release_each_connection_and_surface_errors():
    released = []
    results = load_check.run_threads(
        list(range(10)), 3, lambda item: item * 2, release=lambda: released.append(1)
    )
    assert results == [item * 2 for item in range(10)]
    assert len(released) == 3
    assert load_check.run_threads([], 3, lambda item: item, release=released.pop) == []

    def fail(item):
        raise KeyError(item)

    with pytest.raises(KeyError):
        load_check.run_threads([1, 2], 4, fail, release=lambda: None)


def test_safe_document_admits_only_the_fixed_vocabulary():
    load_check.safe_document(passing_document())
    load_check.safe_document(
        load_check.invitation_timeline(
            [occurrence(0, state="pending")], [message(1, None, state="pending")]
        )
    )
    for bad in (
        {"population": {"family_duid": 1}},
        {"result": SENTINEL_EMAIL},
        {"population": [SENTINEL_DUID]},
        {"invitation_run": {"status": object()}},
    ):
        with pytest.raises(ValueError):
            load_check.safe_document(bad)


def test_cli_prints_the_document_and_exit_code_follows_the_verdict(monkeypatch, capsys):
    document = passing_document()
    monkeypatch.setattr(load_check, "load_deployment", lambda path: object())
    monkeypatch.setattr(
        load_check, "load_check_command", lambda config, **options: document
    )
    assert main(["load-check", "--config", "private-input.yaml"]) == 0
    result = capsys.readouterr()
    assert json.loads(result.out) == document
    assert result.err == ""
    document["result"] = "fail"
    assert main(["load-check", "--config", "private-input.yaml"]) == 1


def test_cli_passes_admitted_options_and_refuses_unbounded_ones(monkeypatch, capsys):
    seen = []

    def command(config, **options):
        seen.append(options)
        return passing_document()

    monkeypatch.setattr(load_check, "load_deployment", lambda path: object())
    monkeypatch.setattr(load_check, "load_check_command", command)
    assert main(["load-check", "--config", "c", "--samples", "9"]) == 0
    assert main(["load-check", "--config", "c", "--concurrency", "8"]) == 0
    assert seen == [
        {"samples": 9, "concurrency": 4},
        {"samples": 200, "concurrency": 8},
    ]
    capsys.readouterr()
    assert main(["load-check", "--config", "c", "--concurrency", "9"]) == 2
    assert main(["load-check", "--config", "c", "--samples", "1001"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and len(seen) == 2
    assert output.err.count("ERROR: load check refused") == 2


def test_cli_refuses_missing_config_without_touching_the_deployment(
    monkeypatch, capsys
):
    called = []
    monkeypatch.setattr(load_check, "load_deployment", lambda path: called.append(1))
    assert main(["load-check"]) == 2
    output = capsys.readouterr()
    assert called == [] and output.out == ""
    assert "ERROR: load check refused" in output.err


def test_cli_never_prints_private_failure_text_or_sentinel_values(monkeypatch, capsys):
    def fail(path):
        raise ValueError("private-error-value " + SENTINEL_EMAIL)

    monkeypatch.setattr(load_check, "load_deployment", fail)
    assert main(["load-check", "--config", "private-input"]) == 2
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
    # A document carrying anything outside the vocabulary is never printed.
    monkeypatch.setattr(load_check, "load_deployment", lambda path: object())
    leaked = passing_document()
    leaked["population"]["families"] = SENTINEL_DUID
    monkeypatch.setattr(
        load_check, "load_check_command", lambda config, **options: leaked
    )
    assert main(["load-check", "--config", "private-input"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and SENTINEL_DUID not in output.err


def test_cli_reports_offline_maintenance_generically(monkeypatch, capsys):
    def busy(path):
        raise StartupBusy("private path")

    monkeypatch.setattr(load_check, "load_deployment", busy)
    assert main(["load-check", "--config", "private-input"]) == 2
    output = capsys.readouterr()
    assert "offline maintenance" in output.err and "private" not in output.err


def test_command_admits_web_only_under_the_shared_lease_and_closes_sql(
    tmp_path, monkeypatch
):
    """Only an admitted web profile measures, never during offline maintenance."""
    config, _ = bootstrap_fixture(tmp_path)
    config = replace(config, service_role=ServiceRole.WEB)
    events = []
    monkeypatch.setattr(
        "parishkit.stewardship.service_boundaries.admit_online_service",
        lambda value: value.service_role,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.admit_lifecycle_mounts",
        lambda _: events.append("mounts"),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.operator_commands.configure_operator_database",
        lambda _: events.append("database"),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_grants.admit_runtime_database",
        lambda _: events.append("authority"),
    )
    monkeypatch.setattr(
        "django.db.connections.close_all", lambda: events.append("sql_closed")
    )

    def measure(budget, *, samples, concurrency):
        """The lease is held while measuring, exactly like the health check."""
        with (
            pytest.raises(StartupBusy),
            StartupLease(RuntimeLayout(config).interlock, offline=True),
        ):
            pass
        events.append(("measure", budget, samples, concurrency))
        return {"result": "pass"}

    monkeypatch.setattr(load_check, "measure", measure)
    with pytest.raises(ConfigError, match="web profile"):
        load_check.load_check_command(
            replace(config, service_role=ServiceRole.WORKER), samples=1, concurrency=1
        )
    assert events == []
    with (
        StartupLease(RuntimeLayout(config).interlock, offline=True),
        pytest.raises(StartupBusy),
    ):
        load_check.load_check_command(config, samples=1, concurrency=1)
    assert events == ["mounts"]
    result = load_check.load_check_command(config, samples=5, concurrency=2)
    assert result == {"result": "pass"}
    assert events[2:] == [
        "database",
        "authority",
        ("measure", config.runtime_budget, 5, 2),
        "sql_closed",
    ]
    with StartupLease(RuntimeLayout(config).interlock, offline=True):
        pass


def test_timed_counts_unavailable_reads_as_failures_only(monkeypatch):
    """Refused or timed-out reads are counted; anything else is a real error."""

    class Unavailable(Exception):
        pass

    monkeypatch.setattr(load_check, "_failure_types", lambda: (Unavailable,))

    def refuse():
        raise Unavailable()

    assert load_check.timed(refuse) is None
    assert load_check.timed(lambda: None) >= 0

    def crash():
        raise KeyError("bug")

    with pytest.raises(KeyError):
        load_check.timed(crash)


def test_population_reports_reference_figures_and_tolerates_no_statistics():
    active = SimpleNamespace(families=3, active_members=5, deliverable_email=2)
    statistics = SimpleNamespace(active=active)
    assert load_check.population_counts(statistics, 4, 3) == {
        "family_campaign_rows": 4,
        "portal_eligible_families": 3,
        "active_families": 3,
        "active_members": 5,
        "deliverable_email": 2,
        "reference_families": 5000,
        "reference_members": 10000,
    }
    empty = load_check.population_counts(SimpleNamespace(active=None), 0, 0)
    assert empty["active_families"] is None and empty["family_campaign_rows"] == 0
