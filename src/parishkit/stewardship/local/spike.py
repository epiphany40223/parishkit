"""The LOCAL launch-day spike check (#392 M3).

On launch day the initial email lands in about a thousand inboxes over tens
of minutes, and Families click while the send is still going. Each click
goes through Caddy's TLS and a gunicorn slot, then:

1. ``access``: ``GET /access/<token>``, the per-source access bucket in
   Valkey, the token lookup, a new Family session and the redirect;
2. ``portal``: ``GET /family/``, the portal shell and its CSRF token;
3. ``form``: ``POST /family/form``, issuing the form baseline under the
   work-order lock;
4. ``presence``: ``POST /family/presence``, one heartbeat;
5. ``submit``: ``POST /family/submit``, the submission write path (the
   answers, the receipt, the audit and a receipt email for mail dispatch).

``pk-stewardship load-check`` measures none of this: it runs read-only reads
inside the web container, on the validation and Production hosts. This check
writes real submissions, so it runs only on the LOCAL deployment, against
seeded data. ``tools/stewardship-local.sh spike`` starts it in several
one-off containers on the ``ingress`` network, each its own source address
as Families on different networks are, so the per-source limiter applies to
each as it does in Production.

Each container (``run_shard``, the ``local-spike`` command) reads the Family
links from Mailpit's API as the messages arrive. The containers agree on one
count by arrival order: the first ``--families`` distinct Families, each run
once by the container that owns its link token. Each Family starts after a
short random delay and runs the five steps with its own cookies, exactly as
the page and its script make the requests. The
submission is the Family's form returned unchanged (``unchanged_answers``),
built from the form's own JSON as the page script builds it. Requests go
only to the fixed LOCAL endpoints below: Caddy by its Compose name with the
TLS server name ``localhost``, verified against Caddy's local root
certificate, and Mailpit. No other host can be named.

The shard prints one JSON document of fixed keys, counts and seconds: never
a token, URL, cookie, address or name. The offline report
(``local-spike-report``, ``summarize``) merges the shards and checks them
against the architecture targets (pages under 2 s and the submission under
3 s at p95), with no limiter refusal, unavailable answer or error, every
shard reporting and as many Families run as were asked for.
"""

import hashlib
import heapq
import http.client
import json
import math
import random
import re
import socket
import ssl
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlencode

from parishkit.config import ConfigError

# The only endpoints the driver talks to: Caddy, by its Compose service
# name on the ingress network, presenting the LOCAL site name; and Mailpit.
CADDY = ("caddy", 8443)
SERVER_NAME = "localhost"
ORIGIN = "https://localhost:8443"
MAILPIT = ("mailpit", 8025)
# Mailpit admits only these Host names (MP_ALLOWED_HOSTS in
# runtime_topology._mailpit), which guard its UI against DNS rebinding; the
# shards reach it by its service name, so they send an admitted Host.
MAILPIT_HOST = "localhost"
# A Family's personal link in a LOCAL email.
LINK = re.compile(re.escape(ORIGIN) + r"/access/([A-Za-z0-9_\-]{8,512})")
CSRF = re.compile(r'name="csrfmiddlewaretoken" value="([A-Za-z0-9]{16,128})"')
STEPS = ("access", "portal", "form", "presence", "submit")
# What one step's answer was. ``limited`` is a 429 (the per-source bucket),
# ``unavailable`` a 503, ``refused`` any other unexpected answer, ``error``
# no answer (a refused connection or a protocol failure) and ``timeout`` no
# answer within REQUEST_SECONDS. A step after a failed one is ``not_run``.
OUTCOMES = ("ok", "limited", "unavailable", "refused", "error", "timeout", "not_run")
# Architecture reference targets (load_check uses the same): ordinary pages
# under 2 s at p95; a submission is a write, so it gets the report target.
TARGETS = {
    "access": 2.0,
    "portal": 2.0,
    "form": 2.0,
    "presence": 2.0,
    "submit": 3.0,
}
REQUEST_SECONDS = 30
# How often a shard asks Mailpit for new messages, and how many it lists at
# once (Mailpit's own page limit is larger).
POLL_SECONDS = 2
PAGE = 500
# Bounds for the operator's options.
FAMILIES_BOUNDS = (1, 5000)
SHARDS_BOUNDS = (1, 32)
CONCURRENCY_BOUNDS = (1, 32)
WAIT_BOUNDS = (10, 4 * 3600)
# A Family opens its email a random 0 to THINK_SECONDS after it arrives.
THINK_SECONDS = 20


class SpikeRefused(ConfigError):
    """The spike check refused to start; the message names no value."""


# ---------------------------------------------------------------------------
# Pure parts: links, answers, outcomes and the report.


def message_tokens(message):
    """The distinct access-link tokens in one Mailpit message, in order."""
    tokens = []
    for part in (message.get("Text"), message.get("HTML")):
        if isinstance(part, str):
            for token in LINK.findall(part):
                if token not in tokens:
                    tokens.append(token)
    return tokens


def in_shard(token, shard, shards):
    """Whether this shard owns a Family: a stable split by its link token.

    Every shard computes the same answer for a token, so each Family is run
    by exactly one shard however many emails carry its link.
    """
    digest = hashlib.sha256(str(token).encode()).digest()
    return int.from_bytes(digest[:8], "big") % shards == shard


def _cents(value):
    """Whole cents of a pledge as the page parses it, or None when not a number."""
    match = re.fullmatch(r"\$?\s*([0-9][0-9,]*)(?:\.([0-9]{1,2}))?", str(value).strip())
    if not match:
        return None
    whole = int(match[1].replace(",", ""))
    return whole * 100 + int((match[2] or "0").ljust(2, "0"))


def unchanged_answers(form):
    """The submission the page script sends for an unedited form.

    Mirrors ``accept`` and the Review page's submit handler in
    ``family-v1.js``: every field keeps the value the form showed, a Member
    with a terminal request (moved or deceased) is sent as that request,
    Ministry and talent choices are the form's own, and the financial answer
    is cleared of its frequency and shares unless the pledge is above zero.
    A form with a financial section but no pledge answer declares $0, as the
    page requires an answer before it submits.
    """
    household = form.get("household")
    family = {}
    if household:
        family = {field["name"]: field["value"] for field in household["fields"]}
        family["mailing_same_as_home"] = household["mailing_same_as_home"]
    members, requests = {}, {}
    for member in form.get("members", []):
        members[member["id"]] = {
            field["name"]: field["value"] for field in member["fields"]
        }
        if member.get("request"):
            requests[member["id"]] = member["request"]
    proposed = {
        member["id"]: {field["name"]: field["value"] for field in member["fields"]}
        for member in form.get("proposed_members", [])
    }
    payload = {
        "family": family,
        "members": {key: requests.get(key, value) for key, value in members.items()},
        "proposed_members": proposed,
        "additional_information": form.get("additional_information", "")
        if form.get("additional_enabled")
        else "",
        "cannot_attend": bool(form.get("cannot_attend")),
    }
    ministries = form.get("ministries")
    if ministries:
        eligible = [
            member["id"]
            for member in form.get("members", [])
            if member["id"] not in requests and member["id"] in ministries["members"]
        ]
        payload["ministries"] = {
            "members": {
                key: {
                    "join": list(ministries["members"][key].get("join", [])),
                    "leave": list(ministries["members"][key].get("leave", [])),
                }
                for key in eligible
            },
            "proposed_members": {
                key: {
                    "join": list(
                        ministries["proposed_members"].get(key, {}).get("join", [])
                    )
                }
                for key in proposed
            },
        }
    else:
        payload["ministries"] = {}
    service = form.get("service")
    if service and ministries:
        defaults = {"cannot_serve": False, "talents": {}}
        payload["service"] = {
            group: {
                key: {
                    "cannot_serve": bool(
                        service.get(group, {}).get(key, defaults)["cannot_serve"]
                    ),
                    "talents": dict(
                        service.get(group, {}).get(key, defaults)["talents"]
                    ),
                }
                for key in payload["ministries"][group]
            }
            for group in ("members", "proposed_members")
        }
    else:
        payload["service"] = {}
    financial = form.get("financial")
    if financial:
        answer = dict(financial["answers"])
        if answer.get("cannot_give"):
            answer = {
                "annual_pledge": "",
                "frequency": "",
                "shares": {},
                "cannot_give": True,
            }
        else:
            if answer.get("annual_pledge") in (None, ""):
                answer["annual_pledge"] = "0"
            cents = _cents(answer["annual_pledge"])
            if cents is None or cents <= 0:
                answer = {**answer, "frequency": "", "shares": {}}
        payload["financial"] = answer
    return payload


def outcome(status, expected):
    """Name one answer: ``ok`` when it is the expected status."""
    if status == expected:
        return "ok"
    return {429: "limited", 503: "unavailable"}.get(status, "refused")


def nearest_rank(values, quantile):
    """The nearest-rank quantile of ``values`` (None when empty)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def empty_steps():
    """Per-step timings and outcome counts, all zero."""
    return {
        step: {"seconds": [], "outcomes": dict.fromkeys(OUTCOMES, 0)} for step in STEPS
    }


def record(steps, results):
    """Add one Family's step results ({step: (seconds, outcome)}) to ``steps``."""
    for step in STEPS:
        seconds, result = results.get(step, (None, "not_run"))
        steps[step]["outcomes"][result] += 1
        if result == "ok":
            steps[step]["seconds"].append(round(seconds, 4))


def safe_document(document):
    """Refuse to print anything but the fixed vocabulary, counts and seconds.

    A stray string could be a token, a URL or a name, so the document's
    keys must be known and its only strings the step and outcome words.
    """
    keys = {
        "check",
        "shard",
        "shards",
        "families",
        "messages",
        "deadline_reached",
        "wait_seconds",
        "elapsed_seconds",
        "started_at",
        "finished_at",
        "steps",
        "seconds",
        "outcomes",
        *STEPS,
        *OUTCOMES,
    }

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in keys:
                    raise ValueError("Unexpected key in the spike document.")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            if value not in {"spike", *STEPS, *OUTCOMES} and not re.fullmatch(
                r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value
            ):
                raise ValueError("Unexpected text in the spike document.")
        elif value is not None and type(value) not in {bool, int, float}:
            raise ValueError("Unexpected value in the spike document.")

    walk(document)
    return document


def summarize(shards, meta=None, *, unreadable=0):
    """Merge shard documents into the report and its pass/fail verdict.

    The run passes when every shard reported (none failed or left an
    unreadable document), as many Families ran as were asked for
    (``meta["families"]``, or at least one when the run record lacks it),
    every step of every Family answered as expected (nothing limited,
    unavailable, refused, failed or timed out), and each step's p95 is
    within its target. Every shard reads the same messages, so the message
    count is the largest one, not the sum.
    """
    meta = meta or {}
    steps = empty_steps()
    families = messages = 0
    deadlines = 0
    for document in shards:
        families += document["families"]
        messages = max(messages, document["messages"])
        deadlines += bool(document.get("deadline_reached"))
        for step in STEPS:
            steps[step]["seconds"] += document["steps"][step]["seconds"]
            for name, count in document["steps"][step]["outcomes"].items():
                steps[step]["outcomes"][name] += count
    requested = meta.get("families")
    # A failed container usually leaves an unreadable or missing document
    # too; count each shard once, as the larger of the two views.
    expected = meta.get("sources")
    missing = expected - len(shards) if type(expected) is int else unreadable
    failed = max(meta.get("failed_shards") or 0, missing, unreadable)
    before, after = meta.get("deadlocks_before"), meta.get("deadlocks_after")
    deadlocks = after - before if type(before) is int and type(after) is int else None
    result = {
        "families": families,
        "requested": requested,
        "messages": messages,
        "shards": len(shards),
        "failed_shards": failed,
        "deadlines": deadlines,
        "deadlocks": deadlocks,
    }
    failures = []
    if families == 0:
        failures.append("no Family ran")
    elif type(requested) is int and families < requested:
        failures.append(
            f"only {families} of {requested} Families ran"
            + (f" ({deadlines} shards reached --wait)" if deadlines else "")
        )
    if failed:
        failures.append(f"{failed} shards failed or left no readable document")
    if deadlocks is not None and deadlocks > 0:
        failures.append(f"{deadlocks} database deadlocks during the run")
    table = {}
    for step in STEPS:
        timings = steps[step]["seconds"]
        p95 = nearest_rank(timings, 0.95)
        bad = {
            name: count
            for name, count in steps[step]["outcomes"].items()
            if name != "ok" and count
        }
        table[step] = {
            "ok": steps[step]["outcomes"]["ok"],
            "p50": nearest_rank(timings, 0.5),
            "p95": p95,
            "max": max(timings) if timings else None,
            "target": TARGETS[step],
            "other": bad,
        }
        if bad:
            failures.append(f"{step}: " + ", ".join(f"{n} {k}" for k, n in bad.items()))
        if p95 is not None and p95 > TARGETS[step]:
            failures.append(f"{step}: p95 {p95:.2f} s over {TARGETS[step]:g} s")
    result["steps"] = table
    result["meta"] = meta
    result["failures"] = failures
    result["passed"] = not failures
    return result


def render(summary):
    """The report as text."""
    lines = [
        f"Launch-day spike: {summary['families']} of "
        f"{summary['requested'] or summary['families']} Families over "
        f"{summary['shards']} sources ({summary['messages']} messages read)",
    ]
    meta = summary["meta"]
    if meta.get("during_send"):
        lines.append("Run beside a bulk Family send.")
    if summary.get("deadlocks") is not None:
        lines.append(f"Database deadlocks during the run: {summary['deadlocks']}")
    lines.append(f"{'step':<10}{'ok':>7}{'p50':>9}{'p95':>9}{'max':>9}{'target':>9}")

    def seconds(value):
        return "-" if value is None else f"{value:.3f}"

    for step, row in summary["steps"].items():
        lines.append(
            f"{step:<10}{row['ok']:>7}{seconds(row['p50']):>9}"
            f"{seconds(row['p95']):>9}{seconds(row['max']):>9}{row['target']:>9.1f}"
        )
    if summary["passed"]:
        lines.append("PASS")
    else:
        lines += ["FAIL:", *(f"  {failure}" for failure in summary["failures"])]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The HTTP client: one Family's browser.


class _CaddyConnection(http.client.HTTPSConnection):
    """HTTPS to Caddy by its service name, presenting the LOCAL site name."""

    def connect(self):
        """Connect to ``caddy`` but verify and send SNI for ``localhost``."""
        raw = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=SERVER_NAME)


class Browser:
    """One Family's cookies and connection, as its browser tab keeps them."""

    def __init__(self, context, *, connection_factory=None):
        self.context = context
        self.cookies = {}
        self.factory = connection_factory or (
            lambda: _CaddyConnection(*CADDY, timeout=REQUEST_SECONDS, context=context)
        )
        self.connection = None
        self.last_status = self.last_body = None

    def close(self):
        """Close the connection, if one is open."""
        if self.connection is not None:
            with suppress(OSError):
                self.connection.close()
            self.connection = None

    def request(self, method, path, *, body=None, headers=None):
        """Send one request; return (seconds, status, headers, body bytes).

        The connection is kept alive between a Family's steps. A GET is sent
        again once on a fresh connection if the server had closed the idle
        one; a POST never is, since the server may have acted on it (a
        submission must never be sent twice). Cookies are kept by name.
        """
        headers = {
            "Host": SERVER_NAME + ":" + str(CADDY[1]),
            "User-Agent": "parishkit-local-spike",
            **(headers or {}),
        }
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        started = monotonic()
        for attempt in range(2):
            if self.connection is None:
                self.connection = self.factory()
            try:
                self.connection.request(method, path, body=body, headers=headers)
                response = self.connection.getresponse()
                data = response.read()
                break
            except (http.client.RemoteDisconnected, BrokenPipeError):
                self.close()
                if attempt or method != "GET":
                    raise
        for value in response.headers.get_all("Set-Cookie") or ():
            cookie = SimpleCookie()
            with suppress(Exception):
                cookie.load(value)
            for name, morsel in cookie.items():
                self.cookies[name] = morsel.value
        self.last_status, self.last_body = response.status, data
        return monotonic() - started, response.status, response.headers, data


def _json_headers(csrf):
    """The headers the page script's ``send`` uses."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-CSRFToken": csrf,
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/family/",
    }


def log_timeout(what, limit, elapsed):
    """Log a timeout to the shard's stderr: what, the limit and the elapsed time.

    Never a token, URL or address; the operator script keeps the lines.
    """
    print(
        f"TIMEOUT: {what} after {elapsed:.1f}s (limit {limit}s)",
        file=sys.stderr,
        flush=True,
    )


class _Unexpected(Exception):
    """A step answered, but not as the page expects (recorded as refused)."""


def run_family(browser, token):
    """Run the five steps for one Family; return {step: (seconds, outcome)}.

    A step that does not answer as expected ends the Family's run; later
    steps are left out (``not_run``). An answer with the expected status but
    the wrong content (no redirect to the portal, no CSRF token, a body that
    is not the expected JSON) is ``refused``. A form that needs review once
    (409, the data moved underneath) is submitted again with the refreshed
    form, as the page does; the submit time is both requests together.
    """
    results, spent = {}, {}

    def send(name, expected, method, path, **options):
        """One request for step ``name``; return its body when it is expected."""
        began = monotonic()
        try:
            seconds, status, headers, body = browser.request(method, path, **options)
        except TimeoutError:
            results[name] = (None, "timeout")
            log_timeout(f"the {name} request", REQUEST_SECONDS, monotonic() - began)
            raise _Unexpected from None
        except (OSError, http.client.HTTPException):
            results[name] = (None, "error")
            raise _Unexpected from None
        spent[name] = spent.get(name, 0.0) + seconds
        results[name] = (spent[name], outcome(status, expected))
        if status != expected:
            raise _Unexpected
        return headers, body

    def check(name, valid):
        """Mark step ``name`` refused unless its answer's content was valid."""
        if not valid:
            results[name] = (results[name][0], "refused")
            raise _Unexpected

    current = "access"
    try:
        headers, _ = send("access", 302, "GET", f"/access/{token}")
        check("access", headers.get("Location") in {"/family/", ORIGIN + "/family/"})
        current = "portal"
        _, body = send("portal", 200, "GET", "/family/")
        match = CSRF.search(body.decode("utf-8", "replace"))
        check("portal", match is not None)
        csrf = match[1]
        current = "form"
        _, body = send(
            "form", 200, "POST", "/family/form", body=b"{}", headers=_json_headers(csrf)
        )
        form = json.loads(body)["form"]
        current = "presence"
        send(
            "presence",
            200,
            "POST",
            "/family/presence",
            body=urlencode({"section": "welcome"}).encode(),
            headers={
                **_json_headers(csrf),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        current = "submit"
        for attempt in range(2):
            answer = json.dumps(
                {"baseline": form["baseline"], "answers": unchanged_answers(form)}
            ).encode()
            try:
                _, body = send(
                    "submit",
                    200,
                    "POST",
                    "/family/submit",
                    body=answer,
                    headers=_json_headers(csrf),
                )
            except _Unexpected:
                # Review required: the refreshed form comes with the 409.
                if attempt or results["submit"][1] != "refused":
                    raise
                if browser.last_status != 409:
                    raise
                form = json.loads(browser.last_body)["form"]
                continue
            check("submit", json.loads(body).get("accepted") is True)
            break
    except _Unexpected:
        pass
    except (ValueError, KeyError, TypeError, AttributeError):
        # Not the JSON the page reads: the step answered, but not usably.
        if current in results:
            results[current] = (results[current][0], "refused")
    finally:
        browser.close()
    return results


# ---------------------------------------------------------------------------
# One shard: Mailpit polling, pacing and the thread pool.


def mailpit_get(path):
    """GET one Mailpit API path and parse its JSON answer."""
    connection = http.client.HTTPConnection(*MAILPIT, timeout=REQUEST_SECONDS)
    try:
        connection.request(
            "GET", path, headers={"Accept": "application/json", "Host": MAILPIT_HOST}
        )
        response = connection.getresponse()
        data = response.read()
        if response.status != 200:
            raise OSError("Mailpit did not answer.")
        return json.loads(data)
    finally:
        connection.close()


def new_messages(since):
    """The (ID, created) of every message created at or after ``since``.

    Mailpit lists newest first, so paging stops at the first older message.
    The result is oldest first: the order the messages arrived.
    """
    found, start = [], 0
    while True:
        page = mailpit_get(f"/api/v1/messages?start={start}&limit={PAGE}")
        messages = page.get("messages") or []
        for message in messages:
            created = _instant(message.get("Created"))
            if created is None or created < since:
                return sorted(found, key=lambda item: (item[1], item[0]))
            found.append((str(message.get("ID")), created))
        start += len(messages)
        if not messages or start >= page.get("total", 0):
            return sorted(found, key=lambda item: (item[1], item[0]))


def _instant(value):
    """A Mailpit RFC 3339 time as an aware datetime, or None."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _now_text():
    """Now, in UTC, to the second."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def bounded(value, bounds, name):
    """An option's whole-number value within its bounds."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SpikeRefused(f"--{name} must be a whole number.") from None
    if not bounds[0] <= number <= bounds[1]:
        raise SpikeRefused(f"--{name} must be from {bounds[0]} to {bounds[1]}.")
    return number


def run_shard(
    *,
    shard,
    shards,
    families,
    concurrency,
    since,
    wait_seconds,
    context,
    fetch=new_messages,
    message=lambda identifier: mailpit_get(f"/api/v1/message/{identifier}"),
    family=run_family,
    browser=Browser,
    rng=None,
    clock=monotonic,
    pause=sleep,
    follow=True,
):
    """Run this shard's Families among the first ``families``; return its document.

    Every shard sees the same Mailpit, so they agree on one count without
    talking to each other: the distinct Families (by link token) in the
    order their mail arrived, the first ``families`` of them taken, and each
    owned by one shard (``in_shard`` on the token). A Family that got two
    emails (an invitation and a reminder) is one Family and runs once, on
    the shard that owns its token. Each message is read once.

    A Family starts a random 0 to THINK_SECONDS after its mail was seen, on
    at most ``concurrency`` threads. The shard finishes when the first
    ``families`` Families exist and it has started every one it owns, or at
    ``wait_seconds``. A deadline is logged with what, the limit and the
    elapsed time, and recorded in the document; Families still queued then
    are cancelled, so ``--wait`` bounds the run, and the report fails it for
    having fewer Families than asked.

    ``follow`` is false for a run over mail already in Mailpit (no send in
    progress): Mailpit is listed once instead of every POLL_SECONDS.
    """
    rng = rng or random.Random()
    steps, lock = empty_steps(), threading.Lock()
    read, order, known, scheduled, pending = set(), [], set(), set(), []
    messages, listed = 0, False
    ran = [0]
    began_at = clock()
    deadline = began_at + wait_seconds

    def one(token):
        result = family(browser(context), token)
        with lock:
            ran[0] += 1
            record(steps, result)

    def done():
        """Every owned Family started, among the first ``families`` there are.

        A backlog run (``follow`` false) has listed everything there is, so
        it is done once that much is started, even short of ``families``;
        the report then fails the shortfall without waiting out ``--wait``.
        """
        mine = [token for token in order[:families] if in_shard(token, shard, shards)]
        enough = len(order) >= families or (listed and not follow)
        return enough and not pending and scheduled >= set(mine)

    begun = _now_text()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        next_poll = clock()
        while not done() and clock() < deadline:
            if clock() >= next_poll and (follow or not listed):
                listed = True
                for identifier, _created in fetch(since):
                    if identifier in read:
                        continue
                    read.add(identifier)
                    messages += 1
                    for token in message_tokens(message(identifier)):
                        if token not in known:
                            known.add(token)
                            order.append(token)
                for token in order[:families]:
                    if in_shard(token, shard, shards) and token not in scheduled:
                        scheduled.add(token)
                        heapq.heappush(
                            pending, (clock() + rng.uniform(0, THINK_SECONDS), token)
                        )
                next_poll = clock() + POLL_SECONDS
            while pending and pending[0][0] <= clock():
                _, token = heapq.heappop(pending)
                futures.append(pool.submit(one, token))
            pause(0.05)
        reached = not done()
        if reached:
            log_timeout(
                f"shard {shard}'s wait for its Families",
                wait_seconds,
                clock() - began_at,
            )
            # Families not yet on a thread never start after the deadline.
            for future in futures:
                future.cancel()
        for future in futures:
            if not future.cancelled():
                future.result()
    return safe_document(
        {
            "check": "spike",
            "shard": shard,
            "shards": shards,
            "families": ran[0],
            "messages": messages,
            "deadline_reached": reached,
            "wait_seconds": wait_seconds,
            "elapsed_seconds": round(clock() - began_at, 1),
            "started_at": begun,
            "finished_at": _now_text(),
            "steps": steps,
        }
    )


def _context(ca_file):
    """A TLS context that trusts only Caddy's local root certificate."""
    context = ssl.create_default_context(cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def execute_spike(args):
    """Console entry for one shard (``pk-stewardship local-spike``).

    Refuses unless ``--profile local`` is given; the operator script runs it
    only in the LOCAL deployment's containers, and the endpoints are fixed.
    Prints the shard document; exits 0 when it ran, 2 when refused.
    """
    try:
        if args.profile != "local":
            raise SpikeRefused("The spike check runs only with --profile local.")
        shards = bounded(args.shards, SHARDS_BOUNDS, "shards")
        shard = bounded(args.shard, (0, shards - 1), "shard")
        families = bounded(args.families, FAMILIES_BOUNDS, "families")
        concurrency = bounded(args.concurrency, CONCURRENCY_BOUNDS, "concurrency")
        wait_seconds = bounded(args.wait_seconds, WAIT_BOUNDS, "wait-seconds")
        since = _instant(args.since)
        if since is None or since.tzinfo is None:
            raise SpikeRefused("--since must be an ISO 8601 UTC instant.")
        if not args.ca_file or not Path(args.ca_file).is_file():
            raise SpikeRefused("--ca-file must name Caddy's local root certificate.")
        context = _context(args.ca_file)
    except (SpikeRefused, ssl.SSLError, OSError) as error:
        message = str(error) if isinstance(error, SpikeRefused) else "TLS setup failed."
        print(f"ERROR: {message}", file=sys.stderr)
        return 2
    document = run_shard(
        shard=shard,
        shards=shards,
        families=families,
        concurrency=concurrency,
        since=since,
        wait_seconds=wait_seconds,
        context=context,
        # The operator script passes the run's start beside a send, and the
        # epoch for mail already delivered, which needs listing only once.
        follow=since >= datetime.now(UTC) - timedelta(hours=1),
    )
    print(json.dumps(document, sort_keys=True))
    return 0


def execute_spike_report(directory):
    """Console entry (``local-spike-report --input DIR``): merge and judge.

    Reads ``shard-*.json`` and the optional ``meta.json`` the operator script
    wrote. A shard document that cannot be read (its container failed) is
    counted, not fatal: the others are still reported, and the run fails.
    Exits 0 when the run passed, 1 when it did not, 2 when no shard document
    could be read.
    """
    root = Path(directory)
    shards, unreadable = [], 0
    for path in sorted(root.glob("shard-*.json")):
        try:
            shards.append(safe_document(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError, KeyError):
            unreadable += 1
    if not shards:
        print(
            "ERROR: the run directory needs readable shard-*.json files",
            file=sys.stderr,
        )
        return 2
    meta = {}
    with suppress(OSError, ValueError):
        meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    summary = summarize(
        shards, meta if isinstance(meta, dict) else {}, unreadable=unreadable
    )
    sys.stdout.write(render(summary))
    with suppress(OSError):
        (root / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if summary["passed"] else 1
