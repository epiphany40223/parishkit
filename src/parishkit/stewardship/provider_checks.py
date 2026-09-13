"""Target-owned provider validation with private IPC and finite helper lifetime."""

import base64
import json
import math
import subprocess
import sys
import time
from contextlib import suppress
from threading import Event, Thread

from .accounts.credential_errors import CredentialValidationUnavailable
from .accounts.key_files import MAX_FILE_BYTES
from .accounts.provider_context import validated_context


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


def _stop(process):
    """Kill and reap this exact child; never leave credential-bearing work detached."""
    if process.poll() is not None:
        return
    try:
        with suppress(ProcessLookupError):
            process.kill()
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        raise ProviderCheckDrainFailure("Provider check could not drain.") from None


def _exchange(process, payload, *, deadline, check):
    """One pipe owner completes partial writes while the caller checks its lease.

    Retrying communicate with no input can stop pumping a partially written
    stdin on supported Python versions. A single bounded call owns both pipes;
    the caller remains responsive and must join it before closing descriptors.
    """
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
            _check_owner(check)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CredentialValidationUnavailable()
            completed.wait(min(remaining, 0.25))
        _check_owner(check)
        if not result:
            raise CredentialValidationUnavailable()
        return result[0]
    finally:
        try:
            _stop(process)
        finally:
            thread.join(timeout=5)
            if thread.is_alive():
                raise ProviderCheckDrainFailure(
                    "Provider pipe exchange could not drain."
                )


def check_candidate(target, settings, value, *, seconds, check):
    """Run only the compiled helper; no secret arguments, environment or temp files.

    This internal function has no authorization of its own. The request validator
    below admits the actual target login, closes SQL and reserves a deadline first.
    """
    settings = validated_context(target, settings)
    if (
        type(value) is not bytes
        or not 0 < len(value) <= MAX_FILE_BYTES
        or type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or not 0 < seconds <= 30
        or not callable(check)
    ):
        raise ValueError("Invalid provider check invocation.")
    payload = json.dumps(
        {
            "target": target,
            "settings": settings,
            "candidate": base64.b64encode(value).decode("ascii"),
        }
    ).encode()
    process = None
    _check_owner(check)
    deadline = time.monotonic() + seconds
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
            output = _exchange(process, payload, deadline=deadline, check=check)
        except OSError:
            raise CredentialValidationUnavailable() from None
        if process.returncode != 0 or output not in {b"valid\n", b"invalid\n"}:
            raise CredentialValidationUnavailable()
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


def request_validator(target, *, check):
    """Bind an isolated runtime to immutable intake scope, not current YAML guesses."""
    if target not in {"parishsoft", "google_workspace", "slack"} or not callable(check):
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
                target, settings, value, seconds=seconds, check=check
            )
        except CredentialValidationUnavailable:
            raise
        except Exception:
            # Local admission, invocation and connection failures are not a
            # provider verdict on the candidate. Fatal ownership/drain signals
            # inherit BaseException and must still terminate this installer.
            raise CredentialValidationUnavailable() from None

    return validate
