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


def repeat_dates_answer(path, body, content_type):
    """``(status, fragment)`` for a POST of ``body`` to ``path``, or None
    when ``path`` is not the list's.

    Like Django, it reads the body as form fields only when the request says
    it is a form (``content_type``); any other body leaves the fields empty,
    which the view refuses. A script that posts its fields as plain text (a
    fetch of a string body) then fails here as it does on a real server.
    """
    if path != reverse("admin:schedule_repeat_dates"):
        return None
    form = content_type.split(";")[0].strip().lower()
    fields = body.decode() if form == "application/x-www-form-urlencoded" else ""
    try:
        context = repeat_dates_preview.fragment(QueryDict(fields), now=timezone.now())
    except ValueError:
        return 400, ""
    return 200, render_to_string(repeat_dates_preview.TEMPLATE, context)
