"""The LOCAL launch-day spike check's pure parts and its shard loop (#392 M3)."""

import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from parishkit.stewardship.local import spike
from parishkit.stewardship.local.spike import (
    OUTCOMES,
    STEPS,
    Browser,
    in_shard,
    message_tokens,
    outcome,
    run_family,
    run_shard,
    safe_document,
    summarize,
    unchanged_answers,
)

TOKEN = "Abc_def-0123456789xyz"


def test_only_the_local_origins_access_links_are_read():
    """Text and HTML links count once each; other hosts and paths never."""
    message = {
        "Text": f"Open https://localhost:8443/access/{TOKEN} today.",
        "HTML": (
            f'<a href="https://localhost:8443/access/{TOKEN}">open</a>'
            '<a href="https://parish.example.org/access/OtherHost12345">x</a>'
            '<a href="https://localhost:8443/files/NotAnAccessLink">f</a>'
            '<a href="https://localhost:8443/access/Second_token_123">2</a>'
        ),
    }
    assert message_tokens(message) == [TOKEN, "Second_token_123"]
    assert message_tokens({"Text": None, "HTML": 7}) == []


def test_shards_split_messages_stably_and_completely():
    """Every message belongs to exactly one shard, the same one every time."""
    identifiers = [f"message-{index}" for index in range(400)]
    owners = [
        [shard for shard in range(8) if in_shard(identifier, shard, 8)]
        for identifier in identifiers
    ]
    assert all(len(owner) == 1 for owner in owners)
    counts = [sum(owner == [shard] for owner in owners) for shard in range(8)]
    assert min(counts) > 25
    assert all(in_shard(identifier, 0, 1) for identifier in identifiers)


def field(name, value):
    """One form field as form_presentation serializes it."""
    return {"name": name, "value": value}


def form(**overrides):
    """A small census form with Ministries, talents and a financial section."""
    value = {
        "baseline": "baseline-id",
        "household": {
            "fields": [field("line1", "1 Main St"), field("city", "Town")],
            "mailing_same_as_home": True,
        },
        "members": [
            {"id": "1", "fields": [field("first_name", "Ann")], "request": None},
            {
                "id": "2",
                "fields": [field("first_name", "Bob")],
                "request": {"moved_household": True},
            },
        ],
        "proposed_members": [],
        "additional_enabled": True,
        "additional_information": "kept",
        "cannot_attend": False,
        "ministries": {
            "options": [{"id": 10}],
            "members": {
                "1": {"join": [10], "leave": [], "current": [4]},
                "2": {"join": [], "leave": [], "current": []},
            },
            "proposed_members": {},
        },
        "service": {
            "members": {"1": {"cannot_serve": False, "talents": {"t": True}}},
            "proposed_members": {},
        },
        "financial": {
            "answers": {
                "annual_pledge": "",
                "frequency": "monthly",
                "shares": {"a": "1"},
                "cannot_give": False,
            }
        },
    }
    value.update(overrides)
    return value


def test_the_unchanged_submission_is_what_the_page_sends():
    """Shown values, terminal requests, own choices; no pledge declares $0."""
    payload = unchanged_answers(form())
    assert payload["family"] == {
        "line1": "1 Main St",
        "city": "Town",
        "mailing_same_as_home": True,
    }
    assert payload["members"] == {
        "1": {"first_name": "Ann"},
        "2": {"moved_household": True},
    }
    # A Member with a terminal request has no Ministry or talent answer.
    assert payload["ministries"] == {
        "members": {"1": {"join": [10], "leave": []}},
        "proposed_members": {},
    }
    assert payload["service"] == {
        "members": {"1": {"cannot_serve": False, "talents": {"t": True}}},
        "proposed_members": {},
    }
    assert payload["additional_information"] == "kept"
    assert payload["financial"] == {
        "annual_pledge": "0",
        "frequency": "",
        "shares": {},
        "cannot_give": False,
    }


@pytest.mark.parametrize(
    "answer, expected",
    [
        (
            {"annual_pledge": "$1,200", "frequency": "monthly", "shares": {"a": "1"}},
            {"annual_pledge": "$1,200", "frequency": "monthly", "shares": {"a": "1"}},
        ),
        (
            {"annual_pledge": "50", "cannot_give": True, "frequency": "x"},
            {"annual_pledge": "", "frequency": "", "shares": {}, "cannot_give": True},
        ),
        (
            {"annual_pledge": "0.00", "frequency": "annual", "shares": {"a": "1"}},
            {"annual_pledge": "0.00", "frequency": "", "shares": {}},
        ),
    ],
)
def test_the_financial_answer_follows_the_pages_rules(answer, expected):
    """Frequency and shares stay only with a pledge above zero."""
    payload = unchanged_answers(form(financial={"answers": answer}))
    assert payload["financial"] == expected


def test_a_form_without_optional_sections_sends_empty_ones():
    """No household, Ministries, talents or financial section: empty answers."""
    payload = unchanged_answers(
        form(
            household=None,
            ministries=None,
            service=None,
            financial=None,
            additional_enabled=False,
        )
    )
    assert payload["family"] == {} and payload["ministries"] == {}
    assert payload["service"] == {} and "financial" not in payload
    assert payload["additional_information"] == ""


def test_answers_are_named_by_status():
    """429 is the limiter, 503 unavailable, anything else unexpected refused."""
    assert outcome(200, 200) == "ok"
    assert outcome(429, 200) == "limited"
    assert outcome(503, 302) == "unavailable"
    assert outcome(500, 200) == outcome(403, 302) == "refused"


class Response:
    """A recorded answer for the fake connection."""

    def __init__(self, status, body=b"", headers=None, cookies=()):
        self.status, self.body = status, body
        self.headers = SimpleNamespace(
            get=(headers or {}).get, get_all=lambda name: list(cookies) or None
        )

    def read(self):
        return self.body


class Connection:
    """A fake keep-alive connection that answers a script of responses."""

    def __init__(self, script, sent):
        self.script, self.sent = script, sent

    def request(self, method, path, body=None, headers=None):
        self.sent.append((method, path, body, dict(headers)))
        answer = self.script.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        self.answer = answer

    def getresponse(self):
        return self.answer

    def close(self):
        pass


def browser(script):
    """A Browser over a scripted fake connection; returns (browser, sent)."""
    sent = []
    return Browser(None, connection_factory=lambda: Connection(script, sent)), sent


PORTAL = (
    b'<form><input type="hidden" name="csrfmiddlewaretoken" '
    b'value="AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"></form>'
)


def happy(form_value=None):
    """The five answers a healthy portal gives."""
    return [
        Response(
            302,
            headers={"Location": "/family/"},
            cookies=["pk_family=session; Path=/; Secure; HttpOnly"],
        ),
        Response(200, PORTAL, cookies=["pk_family_csrf=csrf; Path=/; Secure"]),
        Response(200, json.dumps({"form": form_value or form()}).encode()),
        Response(200, b"{}"),
        Response(200, json.dumps({"accepted": True}).encode()),
    ]


def test_a_family_runs_the_five_steps_with_its_cookies_and_csrf_token():
    """Each step is the page's own request; every step answers ok."""
    client, sent = browser(happy())
    results = run_family(client, TOKEN)
    assert [results[step][1] for step in STEPS] == ["ok"] * 5
    assert [(method, path) for method, path, _, _ in sent] == [
        ("GET", f"/access/{TOKEN}"),
        ("GET", "/family/"),
        ("POST", "/family/form"),
        ("POST", "/family/presence"),
        ("POST", "/family/submit"),
    ]
    # The session cookie set by the link comes back, then the CSRF one too.
    assert sent[1][3]["Cookie"] == "pk_family=session"
    assert sent[4][3]["Cookie"] == "pk_family=session; pk_family_csrf=csrf"
    token = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert sent[2][3]["X-CSRFToken"] == token and sent[2][2] == b"{}"
    assert sent[3][2] == b"section=welcome"
    submitted = json.loads(sent[4][2])
    assert submitted == {
        "baseline": "baseline-id",
        "answers": unchanged_answers(form()),
    }
    assert all(headers["Host"] == "localhost:8443" for *_, headers in sent)


def test_review_required_submits_again_with_the_refreshed_form():
    """A 409 with a refreshed form is answered once more, as the page does."""
    refreshed = form(baseline="fresh-baseline")
    script = happy()
    script[4:] = [
        Response(
            409, json.dumps({"error": "review_required", "form": refreshed}).encode()
        ),
        Response(200, json.dumps({"accepted": True}).encode()),
    ]
    client, sent = browser(script)
    results = run_family(client, TOKEN)
    assert results["submit"][1] == "ok"
    assert json.loads(sent[5][2])["baseline"] == "fresh-baseline"


@pytest.mark.parametrize(
    "position, answer, step, expected",
    [
        (0, Response(429), "access", "limited"),
        (0, Response(302, headers={"Location": "/elsewhere"}), "access", "refused"),
        (1, Response(200, b"no token here"), "portal", "refused"),
        (2, Response(503), "form", "unavailable"),
        (2, Response(200, b"not json"), "form", "refused"),
        (3, TimeoutError(), "presence", "timeout"),
        (4, ConnectionResetError(), "submit", "error"),
        (4, Response(200, b'{"accepted": false}'), "submit", "refused"),
    ],
)
def test_a_failed_step_ends_the_familys_run(position, answer, step, expected):
    """The failed step is named; later steps are not run."""
    script = happy()
    script[position] = answer
    client, _ = browser(script)
    results = run_family(client, TOKEN)
    assert results[step][1] == expected
    assert all(later not in results for later in STEPS[STEPS.index(step) + 1 :])


def test_a_closed_keep_alive_connection_is_reopened_once():
    """The server may close an idle connection between a Family's steps."""
    import http.client

    script = happy()
    script.insert(1, http.client.RemoteDisconnected("closed"))
    client, sent = browser(script)
    assert run_family(client, TOKEN)["portal"][1] == "ok"
    assert [path for _, path, _, _ in sent].count("/family/") == 2


def test_the_document_holds_only_fixed_words_counts_and_seconds():
    """A token, URL or other text anywhere stops the print."""
    steps = spike.empty_steps()
    spike.record(steps, {"access": (0.5, "ok"), "portal": (None, "limited")})
    document = {
        "check": "spike",
        "shard": 0,
        "shards": 1,
        "families": 1,
        "messages": 1,
        "started_at": "2026-10-08T01:02:03Z",
        "finished_at": "2026-10-08T01:02:04Z",
        "steps": steps,
    }
    assert safe_document(document) is document
    assert steps["access"]["seconds"] == [0.5]
    assert steps["form"]["outcomes"]["not_run"] == 1
    for bad in (
        {**document, "token": TOKEN},
        {**document, "check": TOKEN},
        {**document, "families": object()},
    ):
        with pytest.raises(ValueError):
            safe_document(bad)


def shard_document(seconds, outcomes=None):
    """One shard's document with the same timings for every step."""
    steps = spike.empty_steps()
    for step in STEPS:
        steps[step]["seconds"] = list(seconds)
        steps[step]["outcomes"]["ok"] = len(seconds)
        steps[step]["outcomes"].update((outcomes or {}).get(step, {}))
    return {"families": len(seconds), "messages": len(seconds), "steps": steps}


def test_the_report_passes_only_within_targets_and_without_refusals():
    """p95 over a target, any limited or failed step, or no Family fails."""
    passed = summarize([shard_document([0.1] * 19 + [1.5]), shard_document([0.2])])
    assert passed["passed"] and passed["families"] == 21
    assert passed["steps"]["submit"]["p95"] == 0.2
    slow = summarize([shard_document([2.5] * 20)])
    assert not slow["passed"]
    assert "access: p95 2.50 s over 2 s" in slow["failures"]
    # The submission's target is 3 s, so 2.5 s passes for that step.
    assert not any(failure.startswith("submit") for failure in slow["failures"])
    limited = summarize([shard_document([0.1], {"access": {"limited": 2}})])
    assert limited["failures"] == ["access: 2 limited"]
    assert summarize([shard_document([])])["failures"][0] == "no Family ran"
    text = spike.render(passed)
    assert text.endswith("PASS\n") and "submit" in text


class Clock:
    """A clock the shard loop advances one second per pause."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def pause(self, seconds):
        self.now += 1.0


def shard_run(shard, shards, families, fetch, message, clock, ran, wait=600):
    """One shard over fake Mailpit answers; record the tokens it ran."""

    def family(browser, token):
        ran.append(token)
        return {step: (0.1, "ok") for step in STEPS}

    return run_shard(
        shard=shard,
        shards=shards,
        families=families,
        concurrency=2,
        since=None,
        wait_seconds=wait,
        context=None,
        fetch=fetch,
        message=message,
        family=family,
        browser=lambda context: None,
        rng=random.Random(4),
        clock=clock,
        pause=clock.pause,
    )


def test_shards_share_one_count_and_each_family_runs_once():
    """The first N Families by arrival, split by token; duplicates run once.

    Each Family has an invitation and a reminder (two messages, one token),
    and some arrive later. Together the three shards run exactly the first
    eight distinct Families, each on one shard, and every shard finishes
    without waiting out its limit.
    """
    tokens = [f"{TOKEN}{index:03d}" for index in range(12)]
    # Invitation m0..m11, then reminders r0..r11 for the same Families.
    arrivals = [(f"m{i:02d}", i) for i in range(12)] + [
        (f"r{i:02d}", 100 + i) for i in range(12)
    ]
    bodies = {f"m{i:02d}": tokens[i] for i in range(12)}
    bodies |= {f"r{i:02d}": tokens[i] for i in range(12)}
    totals, everyone = {}, []
    for shard in range(3):
        clock, ran, reads = Clock(), [], []

        def fetch(since, clock=clock):
            # Half the invitations arrive at once, the rest a little later.
            ready = arrivals[:6] if clock.now < 5 else arrivals
            return list(ready)

        def message(identifier, reads=reads):
            reads.append(identifier)
            link = f"{spike.ORIGIN}/access/{bodies[identifier]}"
            return {"Text": link, "HTML": f'<a href="{link}">x</a>'}

        document = shard_run(shard, 3, 8, fetch, message, clock, ran)
        assert not document["deadline_reached"]
        assert len(reads) == len(set(reads))  # each message read once
        totals[shard] = document["families"]
        everyone += ran
    assert sorted(everyone) == sorted(tokens[:8])
    assert sum(totals.values()) == 8


def test_a_shard_records_its_deadline_and_the_report_fails_short_runs(capsys):
    """Too few messages: the shard waits out its limit, logs it, and the
    report fails for fewer Families than asked."""
    clock, ran = Clock(), []
    link = f"{spike.ORIGIN}/access/{TOKEN}"
    owner = next(shard for shard in range(4) if in_shard(TOKEN, shard, 4))
    document = shard_run(
        owner,
        4,
        10,
        lambda since: [("m0", 0)],
        lambda identifier: {"Text": link},
        clock,
        ran,
        wait=30,
    )
    assert document["deadline_reached"] and document["families"] == 1
    assert document["wait_seconds"] == 30 and document["elapsed_seconds"] >= 30
    assert "TIMEOUT: shard" in capsys.readouterr().err
    summary = summarize([document], {"families": 10})
    assert not summary["passed"]
    assert "only 1 of 10 Families ran (1 shards reached --wait)" in summary["failures"]


def test_mailpit_listing_stops_at_the_first_older_message(monkeypatch):
    """Newest first: paging ends at the first message older than ``since``."""
    from datetime import UTC, datetime

    pages = [
        {
            "total": 4,
            "messages": [
                {"ID": "c", "Created": "2026-10-08T01:00:03Z"},
                {"ID": "b", "Created": "2026-10-08T01:00:02Z"},
            ],
        },
        {
            "total": 4,
            "messages": [
                {"ID": "a", "Created": "2026-10-08T00:59:00Z"},
                {"ID": "z", "Created": "2026-10-08T00:58:00Z"},
            ],
        },
    ]
    asked = []

    def get(path):
        asked.append(path)
        return pages[len(asked) - 1]

    monkeypatch.setattr(spike, "mailpit_get", get)
    since = datetime(2026, 10, 8, 1, 0, tzinfo=UTC)
    assert [item[0] for item in spike.new_messages(since)] == ["b", "c"]
    assert len(asked) == 2


def test_mailpit_requests_send_a_host_it_admits(monkeypatch):
    """Mailpit refuses Host mailpit:8025 (MP_ALLOWED_HOSTS); send localhost."""
    sent = []

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port) == spike.MAILPIT

        def request(self, method, path, headers):
            sent.append(headers)

        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda: b"{}")

        def close(self):
            pass

    monkeypatch.setattr(spike.http.client, "HTTPConnection", Connection)
    assert spike.mailpit_get("/api/v1/messages") == {}
    # The rendered LOCAL topology's Mailpit admits exactly these names.
    golden = json.loads(
        (Path(__file__).parent / "fixtures" / "local-compose.golden.json").read_text()
    )
    environment = golden["services"]["mailpit"]["environment"]
    allowed = environment["MP_ALLOWED_HOSTS"].split(",")
    assert sent[0]["Host"] == spike.MAILPIT_HOST and spike.MAILPIT_HOST in allowed


def test_a_post_is_never_sent_twice_on_a_dropped_connection():
    """Only a GET is retried; a submission the server may have taken is not."""
    import http.client

    script = happy()
    script.insert(4, http.client.RemoteDisconnected("closed"))
    client, sent = browser(script)
    results = run_family(client, TOKEN)
    assert results["submit"][1] == "error"
    assert [path for _, path, _, _ in sent].count("/family/submit") == 1


def test_a_request_timeout_is_logged_with_its_limit(capsys):
    """What timed out, the limit and the elapsed time; never the token."""
    script = happy()
    script[2] = TimeoutError()
    client, _ = browser(script)
    assert run_family(client, TOKEN)["form"][1] == "timeout"
    err = capsys.readouterr().err
    assert "TIMEOUT: the form request after" in err and "(limit 30s)" in err
    assert TOKEN not in err


def test_the_report_counts_failed_and_unreadable_shards(tmp_path, capsys):
    """One bad shard does not hide the others, but the run fails."""
    base = {
        "check": "spike",
        "shard": 0,
        "shards": 2,
        "deadline_reached": False,
        "wait_seconds": 60,
        "elapsed_seconds": 5.0,
        "started_at": "2026-10-08T01:02:03Z",
        "finished_at": "2026-10-08T01:02:04Z",
    }
    (tmp_path / "shard-0.json").write_text(
        json.dumps({**base, **shard_document([0.1])})
    )
    (tmp_path / "shard-1.json").write_text("")
    (tmp_path / "meta.json").write_text(
        json.dumps({"families": 1, "sources": 2, "failed_shards": 1})
    )
    assert spike.execute_spike_report(tmp_path) == 1
    summary = json.loads((tmp_path / "summary.json").read_text())
    # The failed container and its empty document are one shard, not two.
    assert summary["failed_shards"] == 1 and summary["families"] == 1
    assert "1 shards failed or left no readable document" in summary["failures"]
    # A shard that wrote no file at all is counted from the expected sources.
    (tmp_path / "shard-1.json").unlink()
    (tmp_path / "meta.json").write_text(json.dumps({"families": 1, "sources": 2}))
    assert spike.execute_spike_report(tmp_path) == 1
    assert json.loads((tmp_path / "summary.json").read_text())["failed_shards"] == 1


def args(**values):
    """Command-line values as the CLI passes them."""
    defaults = {
        "profile": "local",
        "shard": "0",
        "shards": "2",
        "families": "100",
        "concurrency": "4",
        "since": "2026-10-08T01:00:00Z",
        "wait_seconds": "600",
        "ca_file": None,
    }
    return SimpleNamespace(**{**defaults, **values})


@pytest.mark.parametrize(
    "values, message",
    [
        ({"profile": "production"}, "only with --profile local"),
        ({"profile": None}, "only with --profile local"),
        ({"shards": "0"}, "--shards must be from 1 to 32"),
        ({"shard": "2"}, "--shard must be from 0 to 1"),
        ({"families": "many"}, "--families must be a whole number"),
        ({"concurrency": "99"}, "--concurrency must be from 1 to 32"),
        ({"wait_seconds": "1"}, "--wait-seconds must be from 10"),
        ({"since": "yesterday"}, "--since must be an ISO 8601 UTC instant"),
        ({"since": "2026-10-08T01:00:00"}, "--since must be an ISO 8601 UTC instant"),
        ({}, "--ca-file must name Caddy's local root certificate"),
    ],
)
def test_the_shard_command_refuses_bad_options(values, message, capsys):
    """Refusals exit 2 with a fixed message, before any request."""
    assert spike.execute_spike(args(**values)) == 2
    assert message in capsys.readouterr().err


def test_the_cli_routes_both_commands(monkeypatch):
    """local-spike takes its options; the report requires --input."""
    from parishkit.stewardship import cli

    seen = []
    monkeypatch.setattr(spike, "execute_spike", lambda a: seen.append(a) or 0)
    monkeypatch.setattr(spike, "execute_spike_report", lambda d: seen.append(d) or 0)
    assert (
        cli.main(
            [
                "local-spike",
                "--profile",
                "local",
                "--shard",
                "0",
                "--shards",
                "1",
                "--families",
                "5",
                "--concurrency",
                "1",
                "--since",
                "2026-10-08T01:00:00Z",
                "--wait-seconds",
                "60",
                "--ca-file",
                "/tmp/root.crt",
            ]
        )
        == 0
    )
    assert seen[0].ca_file == "/tmp/root.crt" and seen[0].shards == "1"
    assert cli.main(["local-spike-report", "--input", "/tmp/run"]) == 0
    assert seen[1] == "/tmp/run"
    with pytest.raises(SystemExit):
        cli.main(["local-spike-report"])
    with pytest.raises(SystemExit):
        cli.main(["local-spike", "--samples", "3"])


def test_the_report_command_reads_the_shards(tmp_path, capsys):
    """Exit 0 on a pass, 1 on a failure, 2 without shard documents."""
    assert spike.execute_spike_report(tmp_path) == 2
    base = {
        "check": "spike",
        "shard": 0,
        "shards": 1,
        "started_at": "2026-10-08T01:02:03Z",
        "finished_at": "2026-10-08T01:02:04Z",
    }
    (tmp_path / "shard-0.json").write_text(
        json.dumps({**base, **shard_document([0.1, 0.2])})
    )
    (tmp_path / "meta.json").write_text(json.dumps({"during_send": True}))
    assert spike.execute_spike_report(tmp_path) == 0
    out = capsys.readouterr().out
    assert "beside a bulk Family send" in out and out.endswith("PASS\n")
    assert json.loads((tmp_path / "summary.json").read_text())["passed"] is True
    (tmp_path / "shard-1.json").write_text(
        json.dumps({**base, **shard_document([0.1], {"form": {"unavailable": 1}})})
    )
    assert spike.execute_spike_report(tmp_path) == 1


def test_every_outcome_word_is_counted():
    """The step table counts every outcome, so none is silently dropped."""
    assert set(spike.empty_steps()["access"]["outcomes"]) == set(OUTCOMES)


def test_new_deadlocks_fail_the_run_and_are_shown():
    """The run record's deadlock counter must not grow during the run."""
    meta = {"families": 1, "sources": 1, "deadlocks_before": 4, "deadlocks_after": 4}
    clean = summarize([shard_document([0.1])], meta)
    assert clean["passed"] and clean["deadlocks"] == 0
    assert "Database deadlocks during the run: 0" in spike.render(clean)
    grown = summarize([shard_document([0.1])], meta | {"deadlocks_after": 6})
    assert "2 database deadlocks during the run" in grown["failures"]
    # Statistics reset mid-run (a negative delta) is not a failure.
    reset = summarize([shard_document([0.1])], meta | {"deadlocks_after": 0})
    assert reset["passed"] and reset["deadlocks"] == -4


def test_a_backlog_run_lists_mailpit_once():
    """Without a send in progress, the mail is already there: list it once."""
    clock, ran, listings = Clock(), [], []
    link = f"{spike.ORIGIN}/access/{TOKEN}"

    def fetch(since):
        listings.append(clock.now)
        return []

    run_shard(
        shard=0,
        shards=1,
        families=3,
        concurrency=1,
        since=None,
        wait_seconds=20,
        context=None,
        fetch=fetch,
        message=lambda identifier: {"Text": link},
        family=lambda browser, token: ran.append(token) or {},
        browser=lambda context: None,
        clock=clock,
        pause=clock.pause,
        follow=False,
    )
    assert len(listings) == 1 and ran == []
    # Nothing more can arrive, so the shard stops at once, short of its
    # count, rather than waiting out --wait; the report fails the shortfall.
    assert clock.now < 5


def test_families_still_queued_at_the_deadline_never_start(capsys):
    """--wait bounds the run: queued Families are cancelled, not run late."""
    import threading

    clock, started, release = Clock(), [], threading.Event()
    tokens = [f"{TOKEN}{index:03d}" for index in range(6)]

    def family(browser, token):
        started.append(token)
        release.wait(5)
        return {step: (0.1, "ok") for step in STEPS}

    def pause(seconds):
        clock.now += 1.0
        if clock.now >= 30:
            release.set()

    document = run_shard(
        shard=0,
        shards=1,
        families=10,
        concurrency=1,
        since=None,
        wait_seconds=30,
        context=None,
        fetch=lambda since: [(f"m{i}", i) for i in range(6)],
        message=lambda identifier: {
            "Text": f"{spike.ORIGIN}/access/{tokens[int(identifier[1:])]}"
        },
        family=family,
        browser=lambda context: None,
        rng=random.Random(1),
        clock=clock,
        pause=pause,
    )
    assert document["deadline_reached"]
    # One thread: the first Family ran; the others were cancelled in the queue.
    assert len(started) == 1 and document["families"] == 1
