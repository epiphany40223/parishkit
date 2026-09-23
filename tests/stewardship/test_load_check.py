"""The load check prints fixed keys, counts and seconds; refusals stay generic."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import load_check
from parishkit.stewardship.cli import main
from parishkit.stewardship.database_provisioning import role_limit
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_budget import RuntimeBudget
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.startup_interlock import StartupBusy, StartupLease

from .bootstrap_factory import bootstrap_fixture

SENTINEL_DUID = "884422"
SENTINEL_EMAIL = "private-person@example.org"
T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
FAR = monotonic() + 10**6


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
        headroom=22,
        form=load_check.form_section(
            serial, serial, samples=4, concurrency=2, threads=2
        ),
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
    "budget",
    [
        RuntimeBudget(),
        RuntimeBudget(rollout_overlap=1),
        RuntimeBudget(web_processes=1, web_threads=8, rollout_overlap=1),
        RuntimeBudget(
            rollout_overlap=3, auxiliary_connections=18, database_connections=200
        ),
    ],
)
def test_headroom_is_the_web_role_limit_minus_the_serving_generation(budget):
    """The same provisioning formula bounds the login and the check's readers."""
    limit = role_limit(SimpleNamespace(runtime_budget=budget), ServiceRole.WEB)
    serving = budget.web_processes * budget.replicas * (budget.web_threads + 3)
    assert limit == serving * budget.rollout_overlap
    assert load_check.web_headroom(budget) == limit - serving
    assert load_check.web_headroom(RuntimeBudget(rollout_overlap=1)) == 0
    assert load_check.web_headroom(RuntimeBudget()) == 22


@pytest.mark.parametrize(
    "requested,web_threads,headroom,expected",
    [(4, 8, 22, 4), (8, 4, 22, 4), (100, 100, 100, 8), (8, 8, 3, 3), (0, 8, 22, 1)],
)
def test_concurrency_never_exceeds_threads_headroom_or_the_fixed_cap(
    requested, web_threads, headroom, expected
):
    assert (
        load_check.effective_concurrency(requested, web_threads, headroom) == expected
    )


@pytest.mark.parametrize(
    "mode,campaign", [("production", "campaign"), ("testing", None)]
)
def test_runtime_state_refuses_production_and_missing_campaign(mode, campaign):
    with pytest.raises(load_check.LoadCheckRefused):
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


@pytest.mark.parametrize(
    "values,quantile,expected",
    [
        ([0.1, 2.5], 0.95, 2.5),
        ([0.1, 2.5], 0.5, 0.1),
        ([0.1, 0.1, 0.1, 2.2], 0.95, 2.2),
        ([0.1, 0.1, 2.2, 2.3], 0.5, 0.1),
        (list(range(1, 21)), 0.95, 19),
        (list(range(1, 8)), 0.95, 7),
        (list(range(1, 8)), 0.5, 4),
        ([3.0], 0.95, 3.0),
    ],
)
def test_nearest_rank_is_the_ceiling_rank_clamped_to_the_sample(
    values, quantile, expected
):
    assert load_check.nearest_rank(values, quantile) == expected


def test_summary_uses_nearest_rank_and_fails_on_unavailable_or_unreached_reads():
    timings = [index / 100 for index in range(1, 21)]
    result = load_check.summarize(timings, target=2.0)
    assert result == {
        "runs": 20,
        "failures": 0,
        "skipped": 0,
        "not_run": 0,
        "target_seconds": 2.0,
        "p50": 0.1,
        "p95": 0.19,
        "max": 0.2,
        "pass": True,
    }
    # Two samples: the slow one is the p95, so it fails.
    two = load_check.summarize([0.1, 2.5], target=2.0)
    assert two["p95"] == 2.5 and not two["pass"]
    # Four samples with exactly one slow read: the fourth value is the p95.
    four = load_check.summarize([0.1, 0.1, 0.1, 2.2], target=2.0)
    assert four["p95"] == 2.2 and not four["pass"]
    # Twenty samples tolerate one slow read at p95 but not two.
    slow = load_check.summarize([0.1] * 19 + [2.5], target=2.0)
    assert slow["p95"] == 0.1 and slow["max"] == 2.5 and slow["pass"]
    assert not load_check.summarize([0.1] * 18 + [2.5, 2.6], target=2.0)["pass"]
    # A sample count that is not a multiple of twenty still ranks correctly.
    seven = load_check.summarize([0.1] * 6 + [1.9], target=2.0)
    assert seven["p95"] == 1.9 and seven["pass"]
    refused = load_check.summarize([0.1, None], target=2.0)
    assert refused["failures"] == 1 and not refused["pass"]
    skipped = load_check.summarize([0.1, load_check.SKIPPED], target=2.0)
    assert skipped["skipped"] == 1 and skipped["runs"] == 2 and skipped["pass"]
    unreached = load_check.summarize([0.1, load_check.NOT_RUN], target=2.0)
    assert unreached == unreached | {"runs": 1, "not_run": 1, "pass": False}
    empty = load_check.summarize([], target=2.0)
    assert empty["p95"] is None and not empty["pass"]


def test_verdict_requires_every_measured_section_and_ignores_the_timeline():
    document = passing_document()
    assert document["result"] == "pass"
    assert document["family_form_inputs"]["failures"] == 0
    assert document["family_form_inputs"]["threads"] == 2
    assert document["runtime_budget"]["web_connection_headroom"] == 22
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
            headroom=22,
            form=failing["family_form_inputs"],
            reports=failing["reports"],
            invitation_run=failing["invitation_run"],
            background=failing["background"],
        )["result"]
        == "fail"
    )
    # One slow concurrent read out of four is the p95 at four samples, so the
    # concurrent phase fails while the serial phase still passes.
    slow_form = load_check.form_section(
        [0.1] * 4, [0.1, 0.1, 0.1, 2.2], samples=4, concurrency=2, threads=2
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


def test_state_vocabularies_match_the_owning_models():
    """A new schedule or delivery state must reach the output allow-list."""
    from parishkit.stewardship.campaigns.schedule_models import OCCURRENCE_STATES
    from parishkit.stewardship.jobs.delivery_states import DeliveryState

    assert frozenset(OCCURRENCE_STATES) == load_check.OCCURRENCE_STATES
    assert {state.value for state in DeliveryState} == load_check.MESSAGE_STATES
    assert load_check.TERMINAL_OCCURRENCE_STATES < load_check.OCCURRENCE_STATES
    assert load_check.TERMINAL_MESSAGE_STATES < load_check.MESSAGE_STATES
    assert load_check.OCCURRENCE_STATES | load_check.MESSAGE_STATES <= (
        load_check.ALLOWED_KEYS
    )


def test_threads_keep_item_order_count_starts_release_connections_and_raise():
    released = []
    results, threads = load_check.run_threads(
        list(range(10)),
        3,
        lambda item: item * 2,
        release=lambda: released.append(1),
        stop=load_check.Stopper(FAR),
    )
    assert results == [item * 2 for item in range(10)]
    assert threads == 3 and len(released) == 3
    # Fewer items than workers: only as many threads as items start.
    results, threads = load_check.run_threads(
        [1], 4, lambda item: item, release=released.pop, stop=load_check.Stopper(FAR)
    )
    assert (results, threads) == ([1], 1)
    assert load_check.run_threads(
        [], 3, lambda item: item, release=released.pop, stop=load_check.Stopper(FAR)
    ) == ([], 0)

    def fail(item):
        raise KeyError(item)

    with pytest.raises(KeyError):
        load_check.run_threads(
            [1, 2], 4, fail, release=lambda: None, stop=load_check.Stopper(FAR)
        )


def test_phases_stop_after_repeated_failures_or_at_the_deadline():
    """Unreached samples are reported as not run, never silently dropped."""
    outcomes = iter([None, 0.1, None, None, None, None, 0.2, 0.3])
    results = load_check.phase(
        range(8), lambda _: next(outcomes), load_check.Stopper(FAR)
    )
    assert results == [None, 0.1, None, None, None, None, "not_run", "not_run"]
    expired = load_check.Stopper(monotonic() - 1)
    assert load_check.phase([1, 2], lambda _: 0.1, expired) == ["not_run"] * 2
    results, threads = load_check.run_threads(
        [1, 2, 3], 2, lambda _: 0.1, release=lambda: None, stop=expired
    )
    assert results == ["not_run"] * 3 and threads == 2
    failing = load_check.Stopper(FAR)
    results, _ = load_check.run_threads(
        list(range(12)), 2, lambda _: None, release=lambda: None, stop=failing
    )
    assert results.count(None) >= 5 and "not_run" in results
    summary = load_check.summarize(results, target=2.0)
    assert summary["not_run"] >= 1 and not summary["pass"]


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
    assert output.err.count("ERROR: invalid --samples or --concurrency") == 2
    assert "load check refused" not in output.err


def test_cli_refuses_missing_config_without_touching_the_deployment(
    monkeypatch, capsys
):
    called = []
    monkeypatch.setattr(load_check, "load_deployment", lambda path: called.append(1))
    assert main(["load-check"]) == 2
    output = capsys.readouterr()
    assert called == [] and output.out == ""
    assert "ERROR: load check refused" in output.err


@pytest.mark.parametrize(
    "error,line",
    [
        (ValueError("private-error-value"), "stopped by an unexpected error"),
        (load_check.LoadCheckRefused("private portal detail"), "load check refused"),
        (PermissionError("private admission detail"), "load check refused"),
        (load_check.SourceChanged("private generation"), "source changed during"),
        (load_check.PortalClosed("private portal"), "portal closed during"),
        (load_check.CampaignUnavailable("private"), "became unavailable during"),
        (load_check.NoConnectionHeadroom("private"), "no spare web database"),
        (load_check.NoEligibleFamilies("private"), "no portal-eligible Families"),
        (load_check.InvalidOptions("private"), "invalid --samples or --concurrency"),
        (StartupBusy("private path"), "offline maintenance"),
    ],
)
def test_cli_classifies_each_outcome_with_its_own_generic_line(
    monkeypatch, capsys, error, line
):
    """Refusal, drift, maintenance and a real error each get one fixed line."""
    events = []
    monkeypatch.setattr(load_check, "load_deployment", lambda path: object())
    monkeypatch.setattr(
        load_check, "emit_failure", lambda err, *, event: events.append(event)
    )
    monkeypatch.setattr(
        load_check, "emit", lambda event, **kwargs: events.append(event)
    )

    def fail(config, **options):
        raise error

    monkeypatch.setattr(load_check, "load_check_command", fail)
    assert main(["load-check", "--config", "private-input"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and line in output.err
    assert output.err.count("ERROR:") == 1
    assert "private" not in output.err
    expected = {
        "stopped by an unexpected error": [load_check.Event.TASK_FAILED],
        "load check refused": [load_check.Event.STARTUP_REJECTED],
        "source changed during": [load_check.Event.FACT_DRIFT],
        "portal closed during": [load_check.Event.STARTUP_REJECTED],
        "became unavailable during": [load_check.Event.STARTUP_REJECTED],
        "no spare web database": [load_check.Event.STARTUP_REJECTED],
        "no portal-eligible Families": [load_check.Event.STARTUP_REJECTED],
        "invalid --samples or --concurrency": [load_check.Event.STARTUP_REJECTED],
        "offline maintenance": [],
    }
    assert events == expected[line]


def test_cli_never_prints_a_document_outside_the_vocabulary(monkeypatch, capsys):
    monkeypatch.setattr(load_check, "load_deployment", lambda path: object())
    leaked = passing_document()
    leaked["population"]["families"] = SENTINEL_DUID
    monkeypatch.setattr(
        load_check, "load_check_command", lambda config, **options: leaked
    )
    assert main(["load-check", "--config", "private-input"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and SENTINEL_DUID not in output.err
    assert "unexpected error" in output.err


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

    def measure(budget, store, *, samples, concurrency):
        """The lease is held while measuring, exactly like the health check."""
        with (
            pytest.raises(StartupBusy),
            StartupLease(RuntimeLayout(config).interlock, offline=True),
        ):
            pass
        events.append(("measure", budget, store.root, samples, concurrency))
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
        ("measure", config.runtime_budget, config.paths["authority"], 5, 2),
        "sql_closed",
    ]
    with StartupLease(RuntimeLayout(config).interlock, offline=True):
        pass


def test_timed_counts_unavailable_reads_and_skips_declined_ones(monkeypatch):
    """Refused or timed-out reads are counted; anything else is a real error."""

    class Unavailable(Exception):
        pass

    monkeypatch.setattr(load_check, "_failure_types", lambda: (Unavailable,))

    def refuse():
        raise Unavailable()

    assert load_check.timed(refuse) is None
    assert load_check.timed(lambda: None) >= 0
    assert load_check.timed(lambda: load_check.SKIPPED) is load_check.SKIPPED

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


def test_source_drift_is_refused_not_published(monkeypatch):
    """A promotion between capture and re-read voids the run."""
    source = load_check.Source("snapshot", 3)
    monkeypatch.setattr(load_check, "current_source", lambda: source)
    load_check.require_same_source(source)
    monkeypatch.setattr(
        load_check, "current_source", lambda: load_check.Source("snapshot", 4)
    )
    with pytest.raises(load_check.SourceChanged):
        load_check.require_same_source(source)

    def compacted():
        raise load_check.LoadCheckRefused("compacted")

    monkeypatch.setattr(load_check, "current_source", compacted)
    with pytest.raises(load_check.SourceChanged):
        load_check.require_same_source(source)


def test_summary_half_rule_tolerates_skips_only_while_half_is_measured():
    """Skipped Families never fail a phase by themselves, unless too few remain."""
    skipped = load_check.SKIPPED
    assert load_check.summarize([0.1, skipped], target=2.0)["pass"]
    assert load_check.summarize([0.1, 0.1, skipped], target=2.0)["pass"]
    assert not load_check.summarize([0.1, skipped, skipped], target=2.0)["pass"]
    mostly = load_check.summarize([0.1] + [skipped] * 3, target=2.0)
    assert mostly["skipped"] == 3 and mostly["runs"] == 4 and not mostly["pass"]
    assert not load_check.summarize([skipped, skipped], target=2.0)["pass"]


def test_form_read_distinguishes_a_closed_portal_from_one_lost_family(monkeypatch):
    """A closed portal ends the run; one ineligible Family is only skipped."""
    from contextlib import nullcontext

    from parishkit.stewardship.responses import source_inputs

    reads = []
    monkeypatch.setattr(load_check, "_guard", lambda campaign_id: nullcontext())
    monkeypatch.setattr(
        source_inputs, "load_census_inputs", lambda *args, **kwargs: reads.append(args)
    )
    scope = SimpleNamespace(campaign=SimpleNamespace(pk="campaign"))
    source = load_check.Source("snapshot", 1)
    monkeypatch.setattr(load_check, "portal_open", lambda store, campaign_id: True)
    monkeypatch.setattr(load_check, "family_admitted", lambda campaign_id, duid: True)
    assert load_check.time_form_inputs("store", scope, source, {}, 7) >= 0
    assert reads == [("snapshot", 7)]
    monkeypatch.setattr(load_check, "family_admitted", lambda campaign_id, duid: False)
    assert (
        load_check.time_form_inputs("store", scope, source, {}, 7) is load_check.SKIPPED
    )
    monkeypatch.setattr(load_check, "portal_open", lambda store, campaign_id: False)
    with pytest.raises(load_check.PortalClosed):
        load_check.time_form_inputs("store", scope, source, {}, 7)
    # The closure propagates through both phases rather than becoming a skip.
    with pytest.raises(load_check.PortalClosed):
        load_check.phase(
            [7],
            lambda duid: load_check.time_form_inputs("store", scope, source, {}, duid),
            load_check.Stopper(FAR),
        )
    with pytest.raises(load_check.PortalClosed):
        load_check.run_threads(
            [7, 8],
            2,
            lambda duid: load_check.time_form_inputs("store", scope, source, {}, duid),
            release=lambda: None,
            stop=load_check.Stopper(FAR),
        )
    assert len(reads) == 1


def test_unavailable_admission_guard_is_a_refusal_not_an_error(monkeypatch):
    """A purging campaign or purge-lock timeout refuses the check before timing."""
    from contextlib import nullcontext

    from parishkit.stewardship.campaigns.read_guards import ReadUnavailable

    class Unavailable:
        def __enter__(self):
            raise ReadUnavailable("private campaign detail")

        def __exit__(self, *error):
            return False

    monkeypatch.setattr(load_check, "bounded_read", nullcontext)
    monkeypatch.setattr(load_check, "current_campaign_id", lambda: "campaign")
    monkeypatch.setattr(load_check, "background_counts", dict)
    monkeypatch.setattr(load_check, "_guard", lambda campaign_id: Unavailable())
    with pytest.raises(load_check.LoadCheckRefused):
        load_check.measure(RuntimeBudget(), "store", samples=1, concurrency=1)


def admission_fakes(monkeypatch, households):
    """Fake every read before sampling so measure() can be driven without SQL."""
    from contextlib import nullcontext

    monkeypatch.setattr(load_check, "bounded_read", nullcontext)
    monkeypatch.setattr(load_check, "current_campaign_id", lambda: "campaign")
    monkeypatch.setattr(load_check, "background_counts", dict)
    monkeypatch.setattr(load_check, "open_scope", lambda store, cid: "scope")
    monkeypatch.setattr(load_check, "current_source", lambda: load_check.Source("s", 1))
    monkeypatch.setattr(load_check, "family_campaign_rows", lambda cid: 5)
    monkeypatch.setattr(load_check, "household_sizes", lambda cid, sid: households)
    monkeypatch.setattr(load_check, "form_input_arguments", lambda scope: {})
    monkeypatch.setattr(load_check, "_close_connection", lambda: None)
    monkeypatch.setattr(
        load_check, "time_form_inputs", lambda store, scope, source, args, duid: 0.1
    )


class Raising:
    """A guard whose entry fails the way a lost or timed-out campaign read does."""

    def __init__(self, error):
        self.error = error

    def __enter__(self):
        raise self.error

    def __exit__(self, *error):
        return False


def test_zero_eligible_families_is_a_refusal_not_an_empty_failure(monkeypatch):
    from contextlib import nullcontext

    admission_fakes(monkeypatch, [])
    monkeypatch.setattr(load_check, "_guard", lambda campaign_id: nullcontext())
    with pytest.raises(load_check.NoEligibleFamilies):
        load_check.measure(RuntimeBudget(), "store", samples=3, concurrency=1)


@pytest.mark.parametrize("kind", ["read_unavailable", "database"])
def test_admission_guard_errors_refuse_and_later_guard_errors_ask_for_a_rerun(
    monkeypatch, kind
):
    """Before sampling a lost read is a refusal; after it, a rerun request."""
    from contextlib import nullcontext

    from django.db import OperationalError

    from parishkit.stewardship.campaigns.read_guards import ReadUnavailable

    error = (
        ReadUnavailable("private") if kind == "read_unavailable" else OperationalError()
    )
    admission_fakes(monkeypatch, [(1, 2), (2, 1)])
    monkeypatch.setattr(load_check, "_guard", lambda campaign_id: Raising(error))
    with pytest.raises(load_check.LoadCheckRefused):
        load_check.measure(RuntimeBudget(), "store", samples=2, concurrency=1)
    guards = iter([nullcontext(), Raising(error)])
    monkeypatch.setattr(load_check, "_guard", lambda campaign_id: next(guards))
    with pytest.raises(load_check.CampaignUnavailable):
        load_check.measure(RuntimeBudget(), "store", samples=2, concurrency=1)
