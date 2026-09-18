"""Bounded, atomic Valkey admission and ephemeral keyed abuse accounting.

No key contains an address, identity, candidate, token or URL. Callers must
account each unsuccessful request exactly once, including pre-verification
rejections. PostgreSQL incident delivery is supplied by the owning service.
"""

import hashlib
import hmac
import math
import re
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from threading import Lock
from time import monotonic
from uuid import uuid4

from django.db import DatabaseError, connection
from redis.exceptions import RedisError

from parishkit.stewardship.authentication_policy import AuthenticationLimits

WINDOW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local result = 0
for i, key in ipairs(KEYS) do
    local limit = tonumber(ARGV[(i-1)*2+2])
    local span = tonumber(ARGV[(i-1)*2+3])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now-span)
    if ARGV[1] ~= '' then
        redis.call('ZADD', key, now, ARGV[1])
        redis.call('EXPIRE', key, span)
        redis.call('ZREMRANGEBYRANK', key, 0, -(limit*2+10))
    end
    local count = redis.call('ZCARD', key)
    if count >= limit then
        local delay = math.min(3600, 30 * 2^math.min(7, count-limit))
        result = math.max(result, delay)
    end
end
return result
"""

BUCKET = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local rate, capacity = tonumber(ARGV[1]), tonumber(ARGV[2])
local old = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = capacity
if old[1] then
    tokens = math.min(capacity, tonumber(old[1])
        + math.max(0, now-tonumber(old[2]))*rate)
end
local wait = 0
if tokens >= 1 then tokens = tokens-1 else wait = math.ceil((1-tokens)/rate) end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
redis.call('EXPIRE', KEYS[1], math.max(120, math.ceil(capacity/rate)))
return wait
"""

AGGREGATE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
for i=1,4 do
    redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now-300)
    if ARGV[i] ~= '' then redis.call('ZADD', KEYS[i], now, ARGV[i]) end
    redis.call('EXPIRE', KEYS[i], 300)
    redis.call('ZREMRANGEBYRANK', KEYS[i], 0, -1001)
end
local counts = {}
for i=1,4 do counts[i] = redis.call('ZCARD', KEYS[i]) end
local meets = counts[1] >= tonumber(ARGV[7]) and (counts[2] >= tonumber(ARGV[5])
    or (ARGV[6] == 'admin' and counts[3] >= tonumber(ARGV[8])))
local severity = 0
if meets then
    redis.call('SET', KEYS[6], '1', 'EX', 300)
    local window = math.floor(now/300)
    local prior = redis.call('HMGET', KEYS[5], 'window', 'streak')
    if not prior[1] or tonumber(prior[1]) ~= window then
        local streak = 1
        if prior[1] and tonumber(prior[1]) == window-1 then
            streak = tonumber(prior[2])+1
        end
        redis.call('HSET', KEYS[5], 'window', window, 'streak', streak)
        redis.call('EXPIRE', KEYS[5], 900)
        severity = 1
        if streak >= 3 then severity = 2 end
    end
end
return {severity, math.floor(now/300), counts[1], counts[2], counts[3], counts[4]}
"""


class LimiterUnavailable(RuntimeError):
    """Guessable credentials must not be evaluated while storage is unavailable."""


@dataclass(frozen=True)
class Counter:
    """One closed-vocabulary sliding-window policy with an already-keyed subject."""

    name: str
    fingerprint: str
    limit: int
    seconds: int

    def __post_init__(self):
        """Do not let a caller put a raw identity or unbounded policy into Valkey."""
        if (
            type(self.name) is not str
            or (
                self.name
                not in {"admin_start", "admin_callback", "family_ip", "family_pair"}
                and re.fullmatch(r"admin_identity_[1-9][0-9]{0,18}", self.name) is None
            )
            or type(self.fingerprint) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.fingerprint) is None
            or type(self.limit) is not int
            or not 1 <= self.limit <= 10000
            or type(self.seconds) is not int
            or not 1 <= self.seconds <= 86400
        ):
            raise ValueError("Invalid authentication counter policy.")


class LocalBuckets:
    """Equivalent token bucket for opaque links only, with a bounded memory cap."""

    def __init__(self, *, capacity=10_000, clock=monotonic):
        self.capacity, self.clock = capacity, clock
        self.entries, self.lock = OrderedDict(), Lock()

    def consume(self, fingerprint, *, rate=2, burst=30):
        """Do not evict active clients to let arbitrary new addresses evade caps."""
        with self.lock:
            now = self.clock()
            lifetime = max(120, burst / rate)
            while (
                self.entries and next(iter(self.entries.values()))[1] <= now - lifetime
            ):
                self.entries.popitem(last=False)
            if fingerprint not in self.entries and len(self.entries) >= self.capacity:
                return 60
            tokens, instant = self.entries.pop(fingerprint, (burst, now))
            tokens = min(burst, tokens + max(0, now - instant) * rate)
            delay = 0 if tokens >= 1 else math.ceil((1 - tokens) / rate)
            self.entries[fingerprint] = (tokens - 1 if not delay else tokens, now)
            return delay


class Limiter:
    """One service-scoped store; outage never silently weakens guessable routes.

    The incident callback accepts the optional observed_after SQL timestamp.
    For limiter_available it returns True only after fenced recovery succeeds
    (or nothing remains pending); False retains the local retry state. Other
    incident kinds return None. Missing positive proof never clears that state.
    """

    def __init__(
        self, client, key, *, incident, namespace="stewardship:auth:v1", limits=None
    ):
        self.limits = AuthenticationLimits() if limits is None else limits
        if not isinstance(self.limits, AuthenticationLimits):
            raise TypeError("Typed authentication limits are required.")
        if type(key) is not bytes or len(key) < 32:
            raise ValueError(
                "An independent limiter key of at least 32 bytes is required."
            )
        if (
            type(namespace) is not str
            or re.fullmatch(r"[a-zA-Z0-9:_-]{1,96}", namespace) is None
        ):
            raise ValueError("Invalid authentication store namespace.")
        self.client, self.key, self.incident = client, key, incident
        self.namespace = namespace
        self.window_script = client.register_script(WINDOW)
        self.bucket_script = client.register_script(BUCKET)
        self.aggregate_script = client.register_script(AGGREGATE)
        self.fallback = LocalBuckets()
        self.outage_lock = Lock()
        self.outage_generation = 0
        self._outage = False
        self.health_lock = Lock()
        self.health_checked_at = None

    @property
    def outage(self):
        """Read the retry flag under the same lock that owns its generation."""
        with self.outage_lock:
            return self._outage

    @outage.setter
    def outage(self, value):
        """Every new local failure invalidates older successful observations."""
        if type(value) is not bool:
            raise TypeError("Outage state requires a boolean.")
        with self.outage_lock:
            if value:
                self.outage_generation += 1
            self._outage = value

    def _outage_state(self):
        """Capture an atomic generation/flag pair before external observation."""
        with self.outage_lock:
            return self.outage_generation, self._outage

    def _recover_outage(self, generation, recovered):
        """A stale success cannot erase a later thread's need to retry recovery."""
        with self.outage_lock:
            if recovered is not True:
                self.outage_generation += 1
                self._outage = True
            elif generation == self.outage_generation:
                self._outage = False

    def check_health(self, *, force=False, skip_busy=False):
        """Observe on first use and at most every 30 seconds in each process.

        The durable baseline survives this process. OPS health/scheduler callers
        can force an observation without generating a synthetic login attempt.
        An unavailable probe follows the same fail-closed path as failed counters.
        Call outside application transactions: the health observation commits its
        independent baseline. Contending threads skip the probe, not admission.
        Only unattended probes use skip_busy to also skip a sibling's SQL health
        lock. False is not evidence of a new accepted sample: skipped/throttled
        observations return it too, without changing durable proof or recovery.
        A skipped SQL-lock attempt still resets this process's probe throttle.
        """
        from .limiter_health import observe_store

        if connection.in_atomic_block:
            raise LimiterUnavailable("Authentication requires independent admission.")
        if not self.health_lock.acquire(blocking=False):
            return False
        try:
            if (
                force
                or self.health_checked_at is None
                or monotonic() - self.health_checked_at >= 30
            ):
                generation, _ = self._outage_state()
                try:
                    # Preserve the ordinary admission probe's calling contract.
                    options = {"skip_busy": True} if skip_busy else {}
                    result = observe_store(
                        self.client, self.namespace, self.limits, **options
                    )
                finally:
                    # Failed INFO probes are throttled too; ordinary scripts
                    # still detect outages on every admission attempt.
                    self.health_checked_at = monotonic()
                if result is None:
                    return False
                # observe_store commits timestamp-fenced availability while it
                # still holds the authentication lock. Do not resolve again here.
                self._recover_outage(generation, result.available)
                return result.lost
        finally:
            self.health_lock.release()
        return False

    def observe_health(self):
        """Force an unattended probe and retain failures without synthetic traffic."""
        try:
            return self.check_health(force=True, skip_busy=True)
        except (RedisError, DatabaseError):
            raise self._unavailable_error() from None

    def _unavailable_error(self):
        """Share request/periodic failure intent without retaining private errors."""
        self.outage = True
        with suppress(LimiterUnavailable):
            self._notify("limiter_unavailable", 2, 0, (0, 0, 0, 0))
        return LimiterUnavailable("Authentication is temporarily unavailable.")

    def _notify(self, *args, **kwargs):
        """An unavailable durable incident store is a retryable auth outage."""
        try:
            return self.incident(*args, **kwargs)
        except DatabaseError:
            self.outage = True
            raise LimiterUnavailable(
                "Authentication is temporarily unavailable."
            ) from None

    def fingerprint(self, kind, value):
        """Domain-separated short-lived fingerprints are not reversible identifiers."""
        if isinstance(value, (IPv4Address, IPv6Address)):
            value = str(value)
        return hmac.new(
            self.key,
            kind.encode("ascii") + b"\x00" + value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _call(self, script, **kwargs):
        """Durable, deduplicated health notification precedes generic unavailability."""
        try:
            self.check_health()
            observed_after = None
            generation, pending_outage = self._outage_state()
            if pending_outage:
                # Only outage recovery needs this extra SQL observation fence.
                # Network I/O still occurs outside any application transaction.
                with connection.cursor() as cursor:
                    cursor.execute("SELECT statement_timestamp()")
                    observed_after = cursor.fetchone()[0]
            result = script(**kwargs)
        except (RedisError, DatabaseError):
            raise self._unavailable_error() from None
        if self.outage and observed_after is not None:
            recovered = self._notify(
                "limiter_available",
                0,
                0,
                (0, 0, 0, 0),
                observed_after=observed_after,
            )
            self._recover_outage(generation, recovered)
        return result

    def bucket(self, kind, source):
        """Admit before allocating session/state or computing a link-token digest."""
        if connection.in_atomic_block:
            raise LimiterUnavailable("Authentication requires independent admission.")
        if kind not in {"admin", "access"}:
            raise ValueError("Unknown authentication route class.")
        fingerprint = self.fingerprint("ip", source)
        rate, burst = (
            (self.limits.admin_per_minute / 60, self.limits.admin_burst)
            if kind == "admin"
            else (self.limits.access_per_minute / 60, self.limits.access_burst)
        )
        try:
            return self._call(
                self.bucket_script,
                keys=[f"{self.namespace}:bucket:{kind}:{fingerprint}"],
                args=[rate, burst],
            )
        except LimiterUnavailable:
            if kind != "access":
                raise
            return self.fallback.consume(fingerprint, rate=rate, burst=burst)

    def counters(self, counters, *, failure=False):
        """Check or record applicable windows atomically without plaintext inputs."""
        if (
            type(counters) not in {list, tuple}
            or not 1 <= len(counters) <= 5
            or any(not isinstance(counter, Counter) for counter in counters)
            or type(failure) is not bool
        ):
            raise ValueError("Authentication counters require a bounded typed batch.")
        keys, args = [], [uuid4().hex if failure else ""]
        for counter in counters:
            keys.append(f"{self.namespace}:window:{counter.name}:{counter.fingerprint}")
            args.extend((counter.limit, counter.seconds))
        return self._call(self.window_script, keys=keys, args=args)

    def clear(self, counter):
        """Success clears only a verified identity or exact source/candidate pair."""
        try:
            self.client.delete(
                f"{self.namespace}:window:{counter.name}:{counter.fingerprint}"
            )
        except RedisError:
            self.outage = True
            # This reset follows a committed login. A failed incident write must
            # not turn that success into a 503 that discards its session cookie.
            with suppress(LimiterUnavailable):
                self._notify("limiter_unavailable", 2, 0, (0, 0, 0, 0))

    def elevated(self, kind):
        """Elevated controls expire with the last observed distributed burst."""
        if kind not in {"family", "admin"}:
            raise ValueError("Unknown authentication failure class.")
        try:
            return bool(
                self.client.exists(f"{self.namespace}:aggregate:{kind}:elevated")
            )
        except RedisError:
            self.outage = True
            self._notify("limiter_unavailable", 2, 0, (0, 0, 0, 0))
            raise LimiterUnavailable(
                "Authentication is temporarily unavailable."
            ) from None

    def failed(self, kind, source, *, identity="", candidate=""):
        """Call once per request; diversity of candidates never gates detection."""
        if kind not in {"admin", "family"}:
            raise ValueError("Unknown authentication failure class.")
        if any(
            type(value) is not str
            or (value and re.fullmatch(r"[0-9a-f]{64}", value) is None)
            for value in (identity, candidate)
        ):
            raise ValueError("Authentication telemetry requires keyed fingerprints.")
        result = self._call(
            self.aggregate_script,
            keys=[
                f"{self.namespace}:aggregate:{kind}:{part}"
                for part in (
                    "attempts",
                    "sources",
                    "identities",
                    "candidates",
                    "alert",
                    "elevated",
                )
            ],
            args=[
                uuid4().hex,
                self.fingerprint("ip", source),
                identity,
                candidate,
                self.limits.admin_sources
                if kind == "admin"
                else self.limits.family_sources,
                kind,
                self.limits.aggregate_attempts,
                self.limits.admin_identities,
            ],
        )
        severity, window, *counts = result
        if severity:
            self._notify(f"{kind}_abuse", severity, window, tuple(counts))
        return bool(severity)
