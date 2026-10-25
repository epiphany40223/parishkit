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


def assert_span_row(page, width, from_until, time):
    """From, Until and Send time share one row at a desktop ``width`` and
    stack at a phone's, with no sideways scroll either way (pk-local R48).

    ``from_until`` is a selector reaching the rule's From and Until through
    their data-repeat hooks' parent (the panel), and ``time`` the row's Send
    time box. Their document order (From, Until, Send time) is the tab
    order, which the layout never changes.
    """
    boxes = [
        page.locator(f'{from_until} [data-repeat="from"]'),
        page.locator(f'{from_until} [data-repeat="until"]'),
        page.locator(time),
    ]
    tops = [box.bounding_box()["y"] for box in boxes]
    lefts = [box.bounding_box()["x"] for box in boxes]
    if width >= 1280:
        assert max(tops) - min(tops) < 1, tops
        assert lefts[0] < lefts[1] < lefts[2], lefts
    else:
        assert tops[0] < tops[1] < tops[2], tops
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    # Document order is tab order: From, Until, then Send time.
    assert boxes[0].evaluate(
        "(from, [until, time]) => {"
        " const after = (a, b) => !!(a.compareDocumentPosition(b)"
        " & Node.DOCUMENT_POSITION_FOLLOWING);"
        " return after(from, until) && after(until, time); }",
        [boxes[1].element_handle(), boxes[2].element_handle()],
    )
