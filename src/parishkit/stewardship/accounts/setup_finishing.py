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

from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.domain import Percentage

from . import stages

TARGET_LABELS = {
    "parishsoft": _("Install the ParishSoft credential"),
    "google_workspace": _("Install the Google Workspace credential"),
    "slack": _("Install the Slack credential"),
}

# What each credential request state means to the Admin, in one short line,
# so the box's reserved forms (#1903) stay small and the row is no taller
# than it needs to be (#2145). While it waits for the server operator the
# box's status already says so, and its only detail is the live count of
# acknowledged services (setup-finishing-step.html); the operator's own
# steps are in the runtime guide, not on the page.
CREDENTIAL_TEXT = {
    "missing": _("Waiting for the installer."),
    "staged": _("Queued to install."),
    "testing": _("Checking with the provider."),
    "installing": _("Installing on the server."),
    # Plain "", never _(""): gettext of an empty string is the catalog header.
    "operator": "",
    "acknowledged": _("Installed and acknowledged."),
}

# Why a credential was not installed (#456 L2), keyed by its final state, or
# by its cleanup reason while the installer is still removing it. A provider
# rejection ("failed") is said per provider by ``rejected``.
CREDENTIAL_FAILURES = {
    "expired": _(
        "The credential was not installed in time, so it was removed. Cancel "
        "this setup and start a new one. If it happens again, ask the server "
        "operator to check that the credential installers are running."
    ),
    "cancelled": _(
        "The credential installation was cancelled. Cancel this setup and "
        "start a new one."
    ),
}
FALLBACK_FAILURE = _(
    "The credential was not installed. Cancel this setup and start a new one."
)

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

# What the Start the load step, the configuration step and the final load
# step say in each running state (#1916), each one short line (#2145). The
# queued Start the load box keeps the shared sentence every queued job
# says (``stages.QUEUED``, UX conventions "Stage boxes").
START_TEXT = {
    "waiting": _("Starts after the configuration."),
    "active": stages.QUEUED,
    "done": _("The load started."),
}
CONFIGURATION_TEXT = {
    "waiting": _("Starts after the credentials are acknowledged."),
    "active": _("Preparing the configuration."),
    "done": _("Configuration prepared."),
}
SOURCE_TEXT = {
    "waiting": _("Starts after the configuration."),
    # The final load is queued and no worker has taken it yet.
    "queued": _("Waiting to start."),
    "active": _("Loading parish data."),
    "done": _("Parish data loaded."),
}
# The final load's states in which it ended without loading anything.
SOURCE_ENDED = frozenset({"failed", "cancelled", "abandoned"})
SOURCE_FAILED = _(
    "The final load did not finish. Ask the operator to check the service logs "
    "for the failed final parish data load, then cancel this setup before "
    "starting again."
)
# Stand-ins for the counts of a reserved form (#1903) that the step cannot
# show yet: an acknowledgement count of one digit (a deployment has a
# handful of credential consumers) and a final load as large as a big
# parish's. Both are assumptions: with ten or more credential consumers, or
# a final load of a million or more records, the shown form can be longer
# than its reserve, and that box grows once, when it first appears. No step
# shows its background task's reference: an IT administrator finishing
# setup has no use for the raw identifier, and the operator finds the one
# final load of the attempt in the service logs (#1969).
SAMPLE_COUNT = {"done": 8, "total": 8}
SAMPLE_PROGRESS = Percentage(888_888, 999_999)
# Every form each step can take while the page still updates itself, as
# (state, text, extras): a failure is left out, since it stops the updates
# and brings its notice above the boxes, and a box reserved for a long
# failure would stand tall the whole time setup runs. Setup complete
# waits until the page shows its result instead of the boxes. "The load
# did not start." is a failure's text too, so it needs no reserve.
CREDENTIAL_FORMS = (
    ("waiting", CREDENTIAL_TEXT["missing"], {}),
    *(("active", CREDENTIAL_TEXT[s], {}) for s in ("staged", "testing", "installing")),
    ("operator", CREDENTIAL_TEXT["operator"], {"count": SAMPLE_COUNT}),
    ("done", CREDENTIAL_TEXT["acknowledged"], {}),
)
RUNNING_FORMS = {
    "start": tuple((state, words, {}) for state, words in START_TEXT.items()),
    "configuration": tuple(
        (state, words, {}) for state, words in CONFIGURATION_TEXT.items()
    ),
    "source": (
        ("waiting", SOURCE_TEXT["waiting"], {}),
        ("waiting", SOURCE_TEXT["queued"], {}),
        (
            "active",
            SOURCE_TEXT["active"],
            {"progress": SAMPLE_PROGRESS},
        ),
        ("done", SOURCE_TEXT["done"], {}),
    ),
    "completed": (),
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
    reason = item["request__cleanup_reason"] if state == "cleanup_pending" else state
    return _row(target, label, "failed", failure_text(target, reason))


def failure_text(target, reason):
    """Say why ``target``'s credential was not installed and what to do next.

    A provider rejection repeats what the integration's settings page says
    about a rejected key (``integration_credentials.FAILED``), so both pages
    name the same things to check; the provider's own error is never shown,
    because it can contain the key. Setup cannot take a corrected key once it
    is confirmed, so the shared advice's "try again" is spelled out as a new
    setup attempt.
    """
    if reason == "failed":
        from .integration_credentials import FAILED

        if target in FAILED:
            return format_lazy(
                "{} {}",
                FAILED[target],
                _("To try again, cancel this setup and start a new one."),
            )
    return CREDENTIAL_FAILURES.get(reason, FALLBACK_FAILURE)


def _start(source, completed):
    """The shared Start the load step ahead of the final load (#1916).

    In progress, with the waiting sentence every Start the load box says
    (``stages.QUEUED``), from the moment the final load is queued until a
    worker picks it up; Done from then on. A final load that ended before
    any worker took it fails here, rather than on the load's own step,
    which never began.
    """
    label = stages.START[1]
    if completed or (source and stages.picked_up(source.get("phase"))):
        return _row("start", label, "done", START_TEXT["done"])
    if source is None:
        return _row("start", label, "waiting", START_TEXT["waiting"])
    if source["state"] in SOURCE_ENDED:
        return _row("start", label, "failed", SOURCE_FAILED)
    return _row("start", label, "active", START_TEXT["active"])


def finishing(status, *, completed=False):
    """Return the step rows, the overall state and any failure explanation.

    ``status`` is ``finalization_status`` output; ``completed`` says the
    system is configured. The overall state is "completed", "failed",
    "operator" (waiting on the server operator) or "working". A credential's
    installation request records the original sign-in instant, of any age
    (#1547).
    """
    installs = {item["target"]: item for item in status["credentials"]}
    targets = list(dict.fromkeys([*status["targets"], *installs]))
    rows = [_credential(target, installs.get(target)) for target in targets]
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
    else:
        if status["prepared"] or completed:
            state = "done"
        elif credentials_done:
            state = "active"
        else:
            state = "waiting"
        rows.append(_row("configuration", label, state, CONFIGURATION_TEXT[state]))

    source = status["source"]
    rows.append(_start(source, completed))
    # Whether a worker has taken the final load: until then its own step has
    # not begun, and the Start the load step above says why.
    picked = bool(source) and stages.picked_up(source.get("phase"))
    label = _("Final parish data load")
    if completed or (source and source["state"] == "succeeded"):
        rows.append(_row("source", label, "done", SOURCE_TEXT["done"]))
    elif picked and source["state"] in SOURCE_ENDED:
        rows.append(_row("source", label, "failed", SOURCE_FAILED))
    elif picked:
        rows.append(_row("source", label, "active", SOURCE_TEXT["active"]))
        rows[-1]["progress"] = source["progress"]
    elif source and source["state"] in SOURCE_ENDED:
        rows.append(_row("source", label, "waiting", _("The load did not start.")))
    elif source:
        rows.append(_row("source", label, "waiting", SOURCE_TEXT["queued"]))
    else:
        rows.append(_row("source", label, "waiting", SOURCE_TEXT["waiting"]))

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

    for row in rows:
        row["reserve"] = reserve(row)
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
        "elapsed": elapsed,
        "signature": signature(rows, overall),
    }


def reserve(row):
    """Every form ``row``'s box can take while setup runs (#1903).

    The box draws each as an invisible ghost in its one grid cell
    (components/stage-boxes.html), so it is as tall as its longest running
    form from the first poll and never grows or shrinks as the step moves
    on. The form the row shows is reserved too, with the stand-in counts:
    a shown final load with fewer records than the stand-in would
    otherwise shrink the box it was reserved in.
    """
    forms = RUNNING_FORMS.get(row["key"], CREDENTIAL_FORMS)
    return [
        _row(row["key"], row["label"], state, words) | extras
        for state, words, extras in forms
    ]


def signature(rows, overall):
    """A short digest of what the page shows; polling reloads when it changes."""
    shown = [
        [row["key"], row["state"], row.get("count"), str(row.get("progress", ""))]
        for row in rows
    ]
    payload = json.dumps([shown, overall], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
