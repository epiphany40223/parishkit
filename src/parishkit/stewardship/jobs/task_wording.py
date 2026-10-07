"""Plain-language wording for background task pages.

Task rows carry developer phase names (``fetching``, ``staging`` ...), an
attempt counter and, for ParishSoft refreshes, a request kind. Staff reading
the task page need what those mean: which kind of refresh this is, what it is
doing now, that its counts are records *checked* (every refresh places every
record into a complete new copy), how many of those records actually
changed once a refresh finishes, and why a run started over. This module
turns bounded metadata into that wording; it never reads worker payloads,
source records or exceptions.
"""

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from parishkit.stewardship.web.presentation import number

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


# Each source collection in a changed-records summary, singular and plural,
# in the order the summary lists them.
CHANGE_NAMES = {
    "family": ("%(count)s Family", "%(count)s Families"),
    "member": ("%(count)s Member", "%(count)s Members"),
    "contact": ("%(count)s contact", "%(count)s contacts"),
    "address": ("%(count)s address", "%(count)s addresses"),
    "ministry": ("%(count)s Ministry", "%(count)s Ministries"),
    "roster": ("%(count)s Ministry roster entry", "%(count)s Ministry roster entries"),
    "fund": ("%(count)s fund", "%(count)s funds"),
    "pledge": ("%(count)s pledge", "%(count)s pledges"),
    "contribution": ("%(count)s contribution", "%(count)s contributions"),
}


def _counts(value):
    """Whether ``value`` maps collection names to nonnegative integers."""
    return type(value) is dict and all(
        type(count) is int and count >= 0 for count in value.values()
    )


def refresh_result(task_id):
    """How many records a finished refresh checked and changed, in words.

    Reads the counts and the changed-record counts (#242) that the run's
    promoted snapshot recorded (one indexed query, columns the web role may
    read). A quick update recorded as unchanged (#630) carries the current
    snapshot's counts and no changes, so it says none changed. Returns None
    when this run promoted nothing. A snapshot promoted before changes were
    recorded still says how many records were checked.
    """
    from parishkit.stewardship.source.snapshot_models import SourceSnapshot

    row = (
        SourceSnapshot.objects.filter(
            task_id=task_id, state__in=("promoted", "unchanged")
        )
        .values_list("counts", "cursor")
        .first()
    )
    return None if row is None else result_text(*row)


def result_text(counts, cursor):
    """Word a promoted snapshot's recorded counts, or None if they are unusable.

    Changes that name a collection this wording does not know (a collection
    added later without a name here) fall back to the checked count alone
    rather than hiding the result.
    """
    if not _counts(counts):
        return None
    changes = cursor.get("changes") if type(cursor) is dict else None
    if not _counts(changes) or not set(changes) <= set(CHANGE_NAMES):
        changes = None
    return refresh_summary(sum(counts.values()), changes)


def refresh_summary(checked, changes):
    """Say how many records a refresh checked and, when known, changed."""
    if changes is None:
        return ngettext(
            "Checked %(checked)s record from ParishSoft.",
            "Checked %(checked)s records from ParishSoft.",
            checked,
        ) % {"checked": number(checked)}
    changed = sum(changes.values())
    parts = [
        ngettext(*CHANGE_NAMES[kind], changes[kind]) % {"count": number(changes[kind])}
        for kind in CHANGE_NAMES
        if changes.get(kind)
    ]
    text = ngettext(
        "Checked %(checked)s record from ParishSoft; %(changed)s changed",
        "Checked %(checked)s records from ParishSoft; %(changed)s changed",
        checked,
    ) % {"checked": number(checked), "changed": number(changed)}
    return f"{text} ({', '.join(parts)})." if parts else f"{text}."


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
