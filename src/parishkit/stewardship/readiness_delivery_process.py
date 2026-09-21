"""Private pipe transport with one finite, non-retrying readiness submission."""

import base64
import json
import math
import subprocess
import sys
import time

from .accounts.credential_errors import CredentialValidationUnavailable
from .accounts.key_files import MAX_FILE_BYTES
from .accounts.provider_context import validated_context
from .provider_checks import ProviderCheckDrainFailure, _check_owner, _exchange, _stop
from .readiness_delivery import DeliveryOutcome
from .readiness_delivery_worker import MAX_INPUT
from .readiness_mail import ReadinessMail


def submit_sample(value, settings, mail, *, seconds, check):
    """The owning Task must persist submitting state before entering this boundary.

    There is no retry or provider-status lookup in this transport. Any missing,
    malformed or late result after helper launch is conservatively unknown.
    Ownership loss and failure to drain remain fatal to the current Task worker;
    they cannot be converted into a successful or safely cancelled receipt.
    """
    settings = validated_context("google_workspace", settings)
    if (
        type(value) is not bytes
        or not 0 < len(value) <= MAX_FILE_BYTES
        or type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or not 0 < seconds <= 30
        or not callable(check)
        or not isinstance(mail, ReadinessMail)
        or any(
            getattr(mail, key) != settings[key]
            for key in ("sender", "reply_to", "recipient")
        )
    ):
        raise ValueError("Invalid readiness submission invocation.")
    payload = json.dumps(
        {
            "settings": settings,
            "candidate": base64.b64encode(value).decode("ascii"),
            "mail": mail.payload(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    if len(payload) > MAX_INPUT:
        raise ValueError("Readiness submission exceeds the private transport bound.")
    return _submit_private(
        payload, helper="readiness_delivery_worker", seconds=seconds, check=check
    )


def _submit_private(payload, *, helper, seconds, check, decode=None):
    """Share finite transport, but admit only compiled delivery-helper entry points."""
    # Each compiled report adapter has its own bounded input budget. Existing
    # readiness and Family helpers keep their original limits and payload types.
    limit = MAX_INPUT
    if helper == "digest_delivery_worker":
        from .family_delivery_process import MAX_DIGEST_INPUT

        limit = MAX_DIGEST_INPUT
    elif helper == "weekly_delivery_worker":
        from .family_delivery_process import MAX_WEEKLY_INPUT

        limit = MAX_WEEKLY_INPUT
    if (
        helper
        not in {
            "readiness_delivery_worker",
            "readiness_notification_worker",
            "family_delivery_worker",
            "digest_delivery_worker",
            "weekly_delivery_worker",
            "operational_mail_worker",
            "operational_slack_worker",
            "security_mail_worker",
        }
        or type(payload) is not bytes
        or not 0 < len(payload) <= limit
        or type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or not 0 < seconds <= 30
        or not callable(check)
        or (decode is not None and not callable(decode))
        or (
            helper
            in {
                "family_delivery_worker",
                "digest_delivery_worker",
                "weekly_delivery_worker",
                "operational_mail_worker",
                "security_mail_worker",
            }
        )
        != (decode is not None)
    ):
        raise ValueError("Invalid private delivery invocation.")
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
                    "parishkit.stewardship." + helper,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env={},
            )
        except OSError:
            return DeliveryOutcome.NOT_SENT
        try:
            output = _exchange(process, payload, deadline=deadline, check=check)
        except (OSError, CredentialValidationUnavailable):
            return DeliveryOutcome.UNKNOWN
        if process.returncode != 0:
            return DeliveryOutcome.UNKNOWN
        if decode is not None:
            return decode(output)
        return {
            outcome.value.encode("ascii") + b"\n": outcome
            for outcome in DeliveryOutcome
        }.get(output, DeliveryOutcome.UNKNOWN)
    except ProviderCheckDrainFailure:
        # A failed pipe owner may still hold its buffered-stream lock. Do not
        # block forever closing it; the fatal exception exits this worker.
        process = None
        raise
    finally:
        if process is not None:
            _stop(process)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
