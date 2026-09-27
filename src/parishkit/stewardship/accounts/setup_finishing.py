"""Plain-language "Finishing setup" steps from the finalization status facts.

After the Admin confirms setup, background owners finish it: each provider
credential's installer stages, tests and installs its file; the server operator
recreates the consumers and acknowledges the new credentials; the config
installer prepares the configuration; the final parish data load runs; and the
completion transaction configures the system. This module turns the safe
status facts that ``finalization_status`` reads into one row per step, so the
page can say what is happening, what is waiting on a person, and what failed.
It is pure, so every state mapping is unit tested without a database.
"""

import hashlib
import json
from datetime import timedelta

from django.utils.translation import gettext_lazy as _

# The installer copies the original sign-in instant into each credential
# request, and SQL admits that request only within five minutes of it.
FRESH_SIGN_IN = timedelta(minutes=5)

TARGET_LABELS = {
    "parishsoft": _("Install the ParishSoft credential"),
    "google_workspace": _("Install the Google Workspace credential"),
    "slack": _("Install the Slack credential"),
}

# What each credential request state means to the Admin.
CREDENTIAL_TEXT = {
    "missing": _("Waiting for the credential installer to start."),
    "staged": _("Queued for installation."),
    "testing": _("Checking the credential with the provider."),
    "installing": _("Installing the credential on the server."),
    "operator": _(
        "Waiting for the server operator to acknowledge the new credentials. "
        "This step is done on the server; see the deployment runbook."
    ),
    "acknowledged": _("Installed and acknowledged by the server."),
    "rolled_back": _("The installation was rolled back."),
}

CHECKPOINT_FAILURES = {
    "stale_base": _(
        "The configuration changed after you confirmed setup, so it was not "
        "applied. Cancel this setup and start a new one."
    ),
    "invalid_candidate": _(
        "The configuration could not be validated, so it was not applied. "
        "Cancel this setup and ask the operator to check the service logs."
    ),
    "actor_unauthorized": _(
        "Your Administrator access could not be confirmed, so the configuration "
        "was not applied. Cancel this setup and start again."
    ),
}

STATUS_LABELS = {
    "done": _("Done"),
    "active": _("In progress"),
    "waiting": _("Waiting"),
    "operator": _("Waiting for the server operator"),
    "failed": _("Failed"),
}


def _row(key, label, state, text):
    """One displayed step: its state drives the badge and the page summary."""
    return {
        "key": key,
        "label": label,
        "state": state,
        "status": STATUS_LABELS[state],
        "text": text,
    }


def _credential(target, item):
    """Map one credential request to a step row.

    For initial setup the request stays ``awaiting_ack`` after every consumer
    acknowledged it, until setup commits; the acknowledgement counts tell the
    operator's step apart from the finished one.
    """
    label = TARGET_LABELS.get(target, target)
    if item is None:
        return _row(target, label, "waiting", CREDENTIAL_TEXT["missing"])
    state = item["request__state"]
    if state in {"staged", "testing", "installing"}:
        return _row(target, label, "active", CREDENTIAL_TEXT[state])
    if state == "awaiting_ack":
        if item["consumers"] and item["acknowledged"] >= item["consumers"]:
            return _row(target, label, "done", CREDENTIAL_TEXT["acknowledged"])
        row = _row(target, label, "operator", CREDENTIAL_TEXT["operator"])
        row["count"] = {"done": item["acknowledged"], "total": item["consumers"]}
        return row
    if state == "applied" or (
        state == "cleanup_pending" and item["request__cleanup_reason"] == "applied"
    ):
        return _row(target, label, "done", CREDENTIAL_TEXT["acknowledged"])
    return _row(target, label, "failed", CREDENTIAL_TEXT["rolled_back"])


def finishing(status, *, completed=False):
    """Return the step rows, the overall state and any failure explanation.

    ``status`` is ``finalization_status`` output; ``completed`` says the
    system is configured. The overall state is "completed", "failed",
    "operator" (waiting on the server operator) or "working". ``reauthenticate``
    is set when a credential cannot start because the original sign-in is older
    than the five minutes its installation request requires; signing in again
    in the same session refreshes that instant and keeps the setup.
    """
    installs = {item["target"]: item for item in status["credentials"]}
    targets = list(dict.fromkeys([*status["targets"], *installs]))
    rows = [_credential(target, installs.get(target)) for target in targets]
    stale_sign_in = status["server_now"] - status["authenticated_at"] > FRESH_SIGN_IN
    reauthenticate = (
        not completed
        and status["attempt"].state == "frozen"
        and stale_sign_in
        and any(target not in installs for target in targets)
    )
    credentials_done = all(row["state"] == "done" for row in rows)

    checkpoint = status["checkpoint"]
    label = _("Apply the configuration")
    if checkpoint == "failed":
        rows.append(
            _row(
                "configuration",
                label,
                "failed",
                CHECKPOINT_FAILURES.get(
                    status["failure_code"], _("The configuration was not applied.")
                ),
            )
        )
    elif status["prepared"] or completed:
        rows.append(
            _row("configuration", label, "done", _("The configuration is prepared."))
        )
    elif credentials_done:
        rows.append(
            _row(
                "configuration",
                label,
                "active",
                _("Preparing the new configuration."),
            )
        )
    else:
        rows.append(
            _row(
                "configuration",
                label,
                "waiting",
                _("Starts after every credential is installed and acknowledged."),
            )
        )

    source = status["source"]
    label = _("Final parish data load")
    if completed or (source and source["state"] == "succeeded"):
        rows.append(_row("source", label, "done", _("Parish data is loaded.")))
    elif source and source["state"] in {"failed", "cancelled", "abandoned"}:
        rows.append(
            _row(
                "source",
                label,
                "failed",
                _(
                    "The final load did not finish. Ask the operator to check the "
                    "service logs for the task reference below, then cancel this "
                    "setup before starting again."
                ),
            )
        )
    elif source:
        rows.append(
            _row("source", label, "active", _("Loading the parish data again."))
        )
        rows[-1]["progress"] = source["progress"]
        rows[-1]["task"] = source["id"]
    else:
        rows.append(
            _row(
                "source",
                label,
                "waiting",
                _("Starts after the configuration is prepared."),
            )
        )
    if source and source["state"] in {"failed", "cancelled", "abandoned"}:
        rows[-1]["task"] = source["id"]

    rows.append(
        _row(
            "completed",
            _("Setup complete"),
            "done" if completed else "waiting",
            _("The system is configured.")
            if completed
            else _("Finishes after the final load."),
        )
    )

    if completed:
        overall = "completed"
    elif status["attempt"].state == "expired" or any(
        row["state"] == "failed" for row in rows
    ):
        overall = "failed"
    elif any(row["state"] == "operator" for row in rows):
        overall = "operator"
    else:
        overall = "working"
    elapsed = max(
        0, int((status["server_now"] - status["confirmed_at"]).total_seconds())
    )
    return {
        "steps": rows,
        "overall": overall,
        "reauthenticate": reauthenticate,
        "elapsed": elapsed,
        "elapsed_minutes": elapsed // 60,
        "signature": signature(rows, overall, reauthenticate),
    }


def signature(rows, overall, reauthenticate):
    """A short digest of what the page shows; polling reloads when it changes."""
    shown = [
        [row["key"], row["state"], row.get("count"), str(row.get("progress", ""))]
        for row in rows
    ]
    payload = json.dumps([shown, overall, reauthenticate], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
