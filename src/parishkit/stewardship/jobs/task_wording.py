"""Plain-language wording for background task pages.

Task rows carry developer phase names (``fetching``, ``staging`` ...), an
attempt counter and, for ParishSoft refreshes, a request kind. Staff reading
the task page need what those mean: which kind of refresh this is, what it is
doing now, that its counts are records *checked* (every refresh places every
record into a complete new copy), and why a run started over. This module
turns the bounded metadata the page already reads into that wording; it
never reads worker payloads or exceptions.
"""

from django.utils.translation import gettext_lazy as _

from .models import NONTERMINAL_STATES, TaskRunEvent

REFRESH = "source_refresh"

# A refresh request's kind, as stored on its immutable request row.
REFRESH_LABELS = {
    "full": _("Full refresh"),
    "delta": _("15-minute update"),
}

# What a ParishSoft refresh is doing in each phase it reports.
REFRESH_PHASES = {
    "fetching": _("Downloading from ParishSoft"),
    "staging": _("Saving the downloaded records"),
    "validating": _("Checking the new data"),
    "promoting": _("Making the new data current"),
}

# Other tasks' phases, in words; unknown phases fall back to the name itself.
PHASES = {
    "unspecified": _("Working"),
    "starting": _("Starting"),
    "preparing": _("Preparing"),
    "fetching": _("Downloading"),
    "validating": _("Checking"),
    "staging": _("Saving"),
    "promoting": _("Making the result current"),
    "reconciling": _("Updating related records"),
    "rendering": _("Preparing content"),
    "delivering": _("Sending"),
    "verifying": _("Verifying"),
    "compacting": _("Cleaning up old data"),
    "draining": _("Finishing up"),
}

# Why the newest restart happened, keyed by the event that caused it.
RETRY_REASONS = {
    "lease_expired": _(
        "An earlier attempt stopped unexpectedly (for example, the server "
        "restarted), so this work started again automatically."
    ),
    "recovery_retry": _(
        "An earlier attempt stopped unexpectedly (for example, the server "
        "restarted), so this work started again automatically."
    ),
    "retryable_failure": _(
        "An earlier attempt ran into a temporary problem, so this work is "
        "trying again automatically."
    ),
}


def refresh_kinds(root_ids):
    """Map each refresh task root to its kind in one query."""
    from parishkit.stewardship.source.refresh_models import SourceRefreshRequest

    return {
        str(root): kind
        for root, kind in SourceRefreshRequest.objects.filter(
            task_root_id__in=list(root_ids)
        ).values_list("task_root_id", "kind")
    }


def refresh_label(task, kinds):
    """The refresh kind in words, or None for other tasks and unknown kinds."""
    if task["type"] != REFRESH:
        return None
    return REFRESH_LABELS.get(kinds.get(task["root_id"]))


def phase_words(task_type, phase):
    """What a task is doing now, in words, for its current phase."""
    if task_type == REFRESH and phase in REFRESH_PHASES:
        return REFRESH_PHASES[phase]
    return PHASES.get(phase, phase.replace("_", " ").capitalize())


def retry_reason(task):
    """Why a still-pending run restarted, or None.

    Only a run that is still queued, running or waiting to retry says it
    "started again" or "is trying again": once it has finished, that present
    tense would mislead, and the history table still records every attempt.
    Only restart-causing events are read (one query, newest first), so the
    answer does not depend on which page of history the Admin is viewing.
    """
    # A run waiting to retry still carries its failed attempt's number, so it
    # is read even on attempt 1; a first attempt that is queued or running
    # has never restarted and needs no query.
    if task["state"] not in NONTERMINAL_STATES or (
        task["state"] != "retry_wait" and task["attempt"] <= 1
    ):
        return None
    action = (
        TaskRunEvent.objects.filter(run_id=task["id"], action__in=tuple(RETRY_REASONS))
        .order_by("-version")
        .values_list("action", flat=True)
        .first()
    )
    return RETRY_REASONS.get(action)
