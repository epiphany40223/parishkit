"""The response lists as their view renders them, from synthetic rows (#477).

Each page is the real template with the Admin chrome, shaped by the view's
own ``page_context`` from the response-lists unit test rows. Besides the
default page, the fixtures serve each page the lists' in-place controls lead
to at the exact query string those controls carry (a sort heading, the mode
switch), so the script's fetch is an ordinary page request and the
swapped-in region is the real template output. The filter form and the
search post privately (#849); the fixture server serves only GET pages, so
each POST answer is served at its own fixture path (``SEARCHED`` and the
rest) for a test's Playwright route to answer the POST with.
"""

from datetime import timedelta

from django.template.loader import render_to_string

from parishkit.stewardship.reports.response_list_views import page_context
from parishkit.stewardship.reports.response_lists import LISTS, ListQuery
from parishkit.stewardship.web.dates import using
from parishkit.stewardship.web.tables import paginate

from ..test_response_lists import CAMPAIGN, rows_of
from ..test_response_metrics import START

SUBMITTED = ListQuery().url("submitted")
DATA_QUALITY = ListQuery().url("data-quality")
# The addresses the submitted list's GET controls lead to, as the browser
# builds them: the Family heading and the Testing switch.
BY_FAMILY = SUBMITTED + "?size=50&sort=family"
TESTING = ListQuery("testing").url("submitted")
# The POST answers: the submitted list searched for "e" (=Baker, Bob and
# Evans, Eve), that search sorted by Family, an envelope-number search, a
# search that finds nothing, and data quality filtered to envelope 0.
SEARCHED = "/response-list-searched"
SEARCHED_BY_FAMILY = "/response-list-searched-by-family"
SEARCHED_ENVELOPE = "/response-list-searched-envelope"
SEARCHED_NONE = "/response-list-searched-none"
ENVELOPE_ZERO = "/response-list-envelope-zero"


def render(context, admin, key, query, values=None, *, rows=True, paused=False):
    """One list page for ``query`` with the table choices in ``values``."""
    spec = LISTS[key]
    table = paginate(
        rows_of(key, query.show, search=query.search) if rows else [],
        values or {},
        carry=query.posted(),
        sorting=spec.sorting,
    )
    shaped = page_context(
        CAMPAIGN,
        spec,
        query,
        table,
        START + timedelta(days=1),
        no_rehearsal=not rows,
        can_test=True,
        can_export=True,
        paused=paused,
    )
    return render_to_string(
        "stewardship/response-list.html", context | {"admin_chrome": admin} | shaped
    )


def components(context, admin):
    """The submitted list and the pages its controls fetch, and data quality."""
    pages = {
        SUBMITTED: ("submitted", ListQuery(), None, True, False),
        BY_FAMILY: ("submitted", ListQuery(), {"sort": "family"}, True, False),
        SEARCHED: ("submitted", ListQuery(search="e"), None, True, False),
        SEARCHED_BY_FAMILY: (
            "submitted",
            ListQuery(search="e"),
            {"sort": "family"},
            True,
            False,
        ),
        SEARCHED_ENVELOPE: ("submitted", ListQuery(search="101"), None, True, False),
        SEARCHED_NONE: ("submitted", ListQuery(search="zzz"), None, True, False),
        ENVELOPE_ZERO: (
            "data-quality",
            ListQuery(show="envelope"),
            None,
            True,
            False,
        ),
        # No Testing rehearsal: the empty Testing view.
        TESTING: ("submitted", ListQuery("testing"), None, False, False),
        DATA_QUALITY: ("data-quality", ListQuery(), None, True, False),
        "/response-list-paused": ("submitted", ListQuery(), None, True, True),
    }
    responses = {}
    with using("us_long"):
        for path, (key, query, values, rows, paused) in pages.items():
            responses[path] = (
                "text/html",
                render(context, admin, key, query, values, rows=rows, paused=paused),
            )
    return responses
