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
