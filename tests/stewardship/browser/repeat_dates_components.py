"""The repeat rules' live date list in the browser tests' fixture server (#2092).

The page script posts a rule's dates to ``admin:schedule_repeat_dates``;
the fixture server answers as the view does, through the view's own
``repeat_dates_preview.fragment`` and template, so the browser tests see the
list the server draws.
"""

from django.http import QueryDict
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from parishkit.stewardship.accounts import repeat_dates_preview


def repeat_dates_answer(path, body):
    """``(status, fragment)`` for a POST of ``body`` to ``path``, or None
    when ``path`` is not the list's."""
    if path != reverse("admin:schedule_repeat_dates"):
        return None
    try:
        context = repeat_dates_preview.fragment(
            QueryDict(body.decode()), now=timezone.now()
        )
    except ValueError:
        return 400, ""
    return 200, render_to_string(repeat_dates_preview.TEMPLATE, context)
