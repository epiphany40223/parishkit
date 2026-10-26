"""Target-owned provider validation with private IPC and finite helper lifetime."""

import base64
import json
import math
import subprocess
import sys
import time
from contextlib import suppress
from threading import Event, Thread

from .accounts.credential_errors import (
    CredentialOrganizationMismatch,
    CredentialValidationUnavailable,
)
from .accounts.key_files import MAX_FILE_BYTES
from .accounts.provider_context import validated_context
from .deployment import DeploymentProfile
from .local import source_base_url
from .mail_catcher import redirect_field


class ProviderCheckDrainFailure(BaseException):
    """Exit the isolated installer if the OS cannot confirm helper termination."""


class ProviderCheckOwnershipLost(BaseException):
    """Lost installer ownership is fatal, never a verdict on a supplied secret."""


def _check_owner(check):
    """Keep ownership loss outside candidate rejection and scrub private context."""
    try:
        check()
    except Exception:
        raise ProviderCheckOwnershipLost("Provider check ownership was lost.") from None


def stop_helper(process):
    """Kill and reap this exact child; never leave credential-bearing work detached.

    Shared by every short-lived helper process (provider checks, delivery,
    chart rendering, the archive check). Raises ProviderCheckDrainFailure
    when the operating system cannot confirm the child is gone.
    """
    if process.poll() is not None:
        return
    try:
        with suppress(ProcessLookupError):
            process.kill()
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        raise ProviderCheckDrainFailure("Provider check could not drain.") from None


# The original private name, still imported by the older helpers.
_stop = stop_helper


def helper_timeout_recorder(helper, *, what, seconds):
    """Build the hook that logs a helper killed at its deadline (#293).

    Called with the moment the deadline stopped the helper, just after the
    kill, it records which helper, the running task (when a worker bound one),
    the time limit and how long the helper ran. Recording never raises.
    """
    started = time.monotonic()

    def record(stopped):
        """Write one helper_timed_out entry for this helper."""
        from .audit.timeouts import record_timeout
        from .observability import Event

        record_timeout(
            Event.HELPER_TIMED_OUT,
            what=what,
            helper=helper,
            limit_seconds=seconds,
            elapsed_seconds=stopped - started,
        )

    return record


def _exchange(
    process, payload, *, deadline, check, on_timeout=None, keep_finished=False
):
    """One pipe owner completes partial writes while the caller checks its lease.

    Retrying communicate with no input can stop pumping a partially written
    stdin on supported Python versions. A single bounded call owns both pipes;
    the caller remains responsive and must join it before closing descriptors.
    When the deadline is what ends the exchange, ``on_timeout(stopped)`` runs
    right after the helper is killed, so logging never delays the kill.

    A helper that has finished always has a real result, even if the lease
    check waited past the deadline meanwhile (#318). With ``keep_finished`` (a
    mail helper, whose finished result may be "accepted by the provider") that
    result is returned even when a lease check fails after the helper
    finished: discarding it would turn a known outcome into an uncertain one.
    The caller's settlement rechecks ownership before recording anything.
    """
    stopped = []

    def timed_out():
        """Note a deadline stop (not ownership loss) for the report below."""
        if on_timeout is not None and time.monotonic() >= deadline:
            stopped.append(time.monotonic())

    completed = Event()
    result = []

    def communicate():
        """Keep helper errors private and always wake the supervising caller."""
        try:
            result.append(
                process.communicate(
                    input=payload, timeout=max(0, deadline - time.monotonic())
                )[0]
            )
        except Exception:
            # Do not let the thread exception hook print candidate-bearing
            # transport details. A missing result is always retryable, never
            # evidence that the credential itself is invalid.
            pass
        finally:
            completed.set()

    _check_owner(check)
    thread = Thread(target=communicate, name="provider-private-ipc", daemon=True)
    thread.start()
    try:
        while not completed.is_set():
            try:
                _check_owner(check)
            except ProviderCheckOwnershipLost:
                if not (keep_finished and completed.is_set()):
                    raise
            # The check may have waited: a helper that finished meanwhile has
            # a real result, whatever the deadline says now.
            if completed.is_set():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out()
                raise CredentialValidationUnavailable()
            completed.wait(min(remaining, 0.25))
        if not keep_finished:
            _check_owner(check)
        if not result:
            # communicate() itself may have given up at the deadline.
            timed_out()
            raise CredentialValidationUnavailable()
        return result[0]
    finally:
        try:
            _stop(process)
        finally:
            if stopped:
                with suppress(Exception):
                    on_timeout(stopped[0])
            thread.join(timeout=5)
            if thread.is_alive():
                raise ProviderCheckDrainFailure(
                    "Provider pipe exchange could not drain."
                )


def check_candidate(
    target, settings, value, *, seconds, check, profile, base_url=None, redirect=None
):
    """Run only the compiled helper; no secret arguments, environment or temp files.

    This internal function has no authorization of its own. The request validator
    below admits the actual target login, closes SQL and reserves a deadline first.
    ``profile`` and ``base_url`` (the deployment's ParishSoft choice) travel in
    the request, so the environment-free helper reaches that one base URL and
    admits the local fake only for a LOCAL deployment. A LOCAL deployment's
    mail ``redirect`` (#1399) is added only when it is set, so every other
    request is exactly as before; the helper needs it before a real
    Workspace key may reach Google from LOCAL. Returns whether the
    provider accepted the key; a ParishSoft key for another organization
    raises ``CredentialOrganizationMismatch`` instead of returning False, so
    the installer can record which refusal it was (#1495).
    """
    settings = validated_context(target, settings)
    if (
        type(value) is not bytes
        or not 0 < len(value) <= MAX_FILE_BYTES
        or type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or not 0 < seconds <= 30
        or not callable(check)
        or not isinstance(profile, DeploymentProfile)
        or (redirect is not None and profile is not DeploymentProfile.LOCAL)
    ):
        raise ValueError("Invalid provider check invocation.")
    payload = json.dumps(
        {
            "target": target,
            "settings": settings,
            "candidate": base64.b64encode(value).decode("ascii"),
            "profile": profile.value,
            "base_url": source_base_url(profile, base_url),
        }
        | redirect_field(redirect)
    ).encode()
    process = None
    _check_owner(check)
    deadline = time.monotonic() + seconds
    on_timeout = helper_timeout_recorder(
        "provider_check_worker", what="provider_check", seconds=seconds
    )
    try:
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "parishkit.stewardship.provider_check_worker",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env={},
            )
            output = _exchange(
                process,
                payload,
                deadline=deadline,
                check=check,
                on_timeout=on_timeout,
            )
        except OSError:
            raise CredentialValidationUnavailable() from None
        # A key for another ParishSoft organization ("mismatch", #1492) is
        # refused for good, as an invalid key is: retrying cannot change
        # which organization it sees. Only "unavailable" (or no answer) waits
        # and tries again.
        if process.returncode != 0 or output not in {
            b"valid\n",
            b"invalid\n",
            b"mismatch\n",
        }:
            raise CredentialValidationUnavailable()
        if output == b"mismatch\n":
            raise CredentialOrganizationMismatch()
        return output == b"valid\n"
    except ProviderCheckDrainFailure:
        # Drainage already failed fatally. Closing a buffered pipe still owned
        # by a live exchange thread can block on its lock indefinitely.
        process = None
        raise
    finally:
        if process is not None:
            _stop(process)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def request_validator(target, *, check, profile, base_url=None, redirect=None):
    """Bind an isolated runtime to immutable intake scope, not current YAML guesses.

    ``base_url`` is the deployment's ``parishsoft_base_url`` (None is the
    profile's default) and ``redirect`` its ``mail_redirect`` (#1399).
    """
    if (
        target not in {"parishsoft", "google_workspace", "slack"}
        or not callable(check)
        or not isinstance(profile, DeploymentProfile)
    ):
        raise ValueError("Unsupported provider validation target.")

    def validate(identifier, value):
        """Read narrow target evidence and close SQL sockets before external IO."""
        from django.db import connections

        from .accounts.credential_database import admit_installer_database
        from .accounts.provider_models import ProviderValidationContext
        from .accounts.secret_requests import _now

        _check_owner(check)
        try:
            try:
                admit_installer_database(target)
                context = (
                    ProviderValidationContext.objects.select_related("request")
                    .filter(
                        request_id=identifier, target=target, request__state="testing"
                    )
                    .first()
                )
                if context is None:
                    raise CredentialValidationUnavailable()
                # Reserve forced drainage inside the immutable request's TTL.
                seconds = min(
                    30, (context.request.expires_at - _now()).total_seconds() - 10
                )
                if seconds <= 0:
                    raise CredentialValidationUnavailable()
                settings = validated_context(target, context.settings)
            finally:
                connections.close_all()
            return check_candidate(
                target,
                settings,
                value,
                seconds=seconds,
                check=check,
                profile=profile,
                base_url=base_url,
                redirect=redirect,
            )
        except (CredentialValidationUnavailable, CredentialOrganizationMismatch):
            # A mismatch is the provider's verdict on the key, not a local
            # failure: it must reach the installer as itself (#1495).
            raise
        except Exception:
            # Local admission, invocation and connection failures are not a
            # provider verdict on the candidate. Fatal ownership/drain signals
            # inherit BaseException and must still terminate this installer.
            raise CredentialValidationUnavailable() from None

    return validate


# The longest the setup wizard's organization lookup may run (#1554),
# ParishSoft's organization list and every Family check together.
LOOKUP_SECONDS = 120
# The largest answer the lookup helper may write: 10,000 organizations of
# a 200-character name each, with room for the JSON around them.
MAX_LOOKUP_OUTPUT = 4 * 1024 * 1024


def _lookup_answer(answer):
    """The decoded lookup answer once checked for its exact shape, else None."""
    from parishkit.parishsoft_source import MAXIMUM_ORGANIZATIONS

    if type(answer) is not dict or answer.get("outcome") not in {
        "ok",
        "refused",
        "unavailable",
        "unreadable",
    }:
        return None
    if answer["outcome"] != "ok":
        return answer if set(answer) == {"outcome"} else None
    organizations = answer.get("organizations")
    elapsed = answer.get("elapsed")
    if (
        set(answer) != {"outcome", "organizations", "unchecked", "elapsed"}
        or type(organizations) is not list
        or len(organizations) > MAXIMUM_ORGANIZATIONS
        or type(answer["unchecked"]) is not int
        or not 0 <= answer["unchecked"] <= len(organizations)
        or (elapsed is not None and type(elapsed) not in (int, float))
    ):
        return None
    for organization in organizations:
        if (
            type(organization) is not dict
            or set(organization) != {"id", "name", "families"}
            or type(organization["id"]) is not int
            or not 1 <= organization["id"] < 2**31
            or (
                organization["name"] is not None
                and (
                    type(organization["name"]) is not str
                    or not 1 <= len(organization["name"]) <= 200
                )
            )
            or organization["families"] not in (True, False, None)
        ):
            return None
    if len({organization["id"] for organization in organizations}) != len(
        organizations
    ):
        return None
    return answer


def _ask_helper(request, *, check, validate):
    """Send one lookup-style request to the isolated helper; its checked answer.

    Shared by the organization lookup (#1554) and the parish data check
    (#2201): one run of ``provider_check_worker`` with an empty
    environment, a deadline a little past ``LOOKUP_SECONDS`` (so the
    helper's own answer arrives before the deadline stops it) and the
    caller's ownership ``check`` run while it waits. ``validate`` checks the
    decoded answer's exact shape; a helper that answers nothing usable, or
    that the limit stops, is ``unavailable``.
    """
    payload = json.dumps(request).encode()
    process = None
    _check_owner(check)
    deadline = time.monotonic() + LOOKUP_SECONDS + 5
    on_timeout = helper_timeout_recorder(
        "provider_check_worker", what="provider_check", seconds=LOOKUP_SECONDS + 5
    )
    try:
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "parishkit.stewardship.provider_check_worker",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env={},
            )
            output = _exchange(
                process,
                payload,
                deadline=deadline,
                check=check,
                on_timeout=on_timeout,
            )
        except (OSError, CredentialValidationUnavailable):
            return {"outcome": "unavailable"}
        answer = None
        if process.returncode == 0 and len(output) <= MAX_LOOKUP_OUTPUT:
            try:
                answer = validate(json.loads(output.decode("utf-8")))
            except (ValueError, UnicodeDecodeError, RecursionError):
                answer = None
        return answer or {"outcome": "unavailable"}
    except ProviderCheckDrainFailure:
        # As in check_candidate: never close a pipe a live exchange owns.
        process = None
        raise
    finally:
        if process is not None:
            _stop(process)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def _request(operation, value, *, profile, base_url, **extra):
    """The JSON request a lookup-style helper run reads from its stdin."""
    if type(value) is not bytes or not 0 < len(value) <= MAX_FILE_BYTES:
        raise ValueError("Invalid organization lookup invocation.")
    if not isinstance(profile, DeploymentProfile):
        raise ValueError("Invalid organization lookup invocation.")
    return {
        "operation": operation,
        "candidate": base64.b64encode(value).decode("ascii"),
        "profile": profile.value,
        "base_url": source_base_url(profile, base_url),
        "seconds": LOOKUP_SECONDS,
        **extra,
    }


def lookup_organizations(value, *, check, profile, base_url=None):
    """Ask ParishSoft which organizations a staged key reaches (#1554).

    Runs only the isolated helper, as ``check_candidate`` does, with the
    request's profile and base URL and one overall limit of
    ``LOOKUP_SECONDS``. Returns the helper's checked answer
    (``provider_check_worker.lookup_organizations``); a helper that
    answers nothing usable, or that the limit stops, is ``unavailable``.
    ``check`` is the caller's ownership check, run while it waits.
    """
    if not callable(check):
        raise ValueError("Invalid organization lookup invocation.")
    request = _request("organizations", value, profile=profile, base_url=base_url)
    return _ask_helper(request, check=check, validate=_lookup_answer)


# The outcomes a parish data check answers with (#2201).
CHECK_OUTCOMES = frozenset(
    {"ok", "refused", "not_reached", "unreadable", "unavailable"}
)


def _check_answer(answer):
    """A parish data check's answer once checked for its exact shape, else None."""
    if type(answer) is not dict or answer.get("outcome") not in CHECK_OUTCOMES:
        return None
    if answer["outcome"] != "ok":
        return answer if set(answer) == {"outcome"} else None
    name = answer.get("name")
    if (
        set(answer) != {"outcome", "name", "families", "members"}
        or (name is not None and (type(name) is not str or not 1 <= len(name) <= 200))
        or any(
            type(answer[key]) is not int or not 0 <= answer[key] < 2**31
            for key in ("families", "members")
        )
    ):
        return None
    return answer


def check_source(value, *, organization, check, profile, base_url=None):
    """The setup wizard's quick parish data check (#2201).

    Runs the isolated helper as the organization lookup does, under the
    same limit, for the staged key ``value`` and its chosen
    ``organization``. Returns the helper's checked answer
    (``provider_check_worker.check_source``) or ``unavailable``.
    """
    if (
        not callable(check)
        or type(organization) is not int
        or not 1 <= organization < 2**31
    ):
        raise ValueError("Invalid parish data check invocation.")
    request = _request(
        "source_check",
        value,
        profile=profile,
        base_url=base_url,
        organization=organization,
    )
    return _ask_helper(request, check=check, validate=_check_answer)
