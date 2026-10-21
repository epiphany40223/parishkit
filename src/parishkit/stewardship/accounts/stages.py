"""Stage boxes: one box per major stage of a long piece of work, in order.

The shared component (``components/stage-boxes.html``, ``.stage-boxes`` in
ui-v1.css) draws a row of boxes, left to right and wrapping on a phone,
each named with its stage and its state in words (UX conventions, "Stage
boxes"). Loading parish data and Finishing setup (the setup wizard),
Load ParishSoft data (the Campaign setup stepper, #1404 PR 5) and the Go
live page's Prepare step (#1585 slice 3, go_live_prepare) draw it.
This module words each box's state the same way for every one of them.
"""

from django.utils.translation import gettext_lazy as _

# Each box's state in words, which the box always shows: the mark and the
# colour only reinforce it.
STATUS = {
    "done": _("Done"),
    "active": _("In progress"),
    "waiting": _("Not started"),
    "failed": _("Failed"),
}


def rows(stages, index, current="active"):
    """One box per ``(key, label)`` of ``stages``, worded for the template.

    The stages before ``index`` are done, the one at ``index`` is
    ``current`` ("active", "waiting" or "failed") and later ones are
    waiting. An ``index`` of ``len(stages)`` marks every stage done, and
    -1 (the work has not reached its first stage) marks every one waiting.
    """
    boxes = []
    for position, (key, label) in enumerate(stages):
        if position < index:
            state = "done"
        elif position == index:
            state = current
        else:
            state = "waiting"
        boxes.append(
            {"key": key, "label": label, "state": state, "status": STATUS[state]}
        )
    return boxes


# The first box of work that waits in the background queue before a worker
# takes it (UX conventions "Stage boxes", #1916): In progress, with the
# working spinner, from the moment the request is accepted, and Done once a
# worker has picked the job up. Without it every box stayed Not started for
# as long as the job waited, which looked as if nothing was happening.
START = ("start", _("Start the load"))
# What the Start box's detail line says while the job waits. Every page with
# a Start box, and setup's Loading parish data summary, says it this way.
QUEUED = _(
    "Waiting for the background service to pick up the job. This can take a "
    "minute or two."
)


def picked_up(phase):
    """Whether a worker has picked up the task now at ``phase``.

    A task is created at phase "unspecified", and a worker's claim sets it
    to "starting" (jobs/storage.py ``change_run``); nothing sets it back. So
    the task's own phase, which every status read already has, tells a job
    still waiting in the queue from one a worker has started, whatever its
    state (queued, running, waiting to retry, or ended).
    """
    return phase not in (None, "", "unspecified")


def queued_index(keys, phase):
    """``rows``' index for work whose first stage is START, at ``phase``.

    ``keys`` names the stages, START's first. Not picked up yet: START. A
    phase of its own stage: that stage. Picked up but not yet in any stage
    (the worker is "starting"): the stage after START, so Start the load is
    Done and the first real stage In progress as soon as the worker starts.
    """
    if phase in keys:
        return keys.index(phase)
    return 1 if picked_up(phase) else 0
