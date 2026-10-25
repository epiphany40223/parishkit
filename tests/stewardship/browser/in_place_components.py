"""Pages for the shared in-place mechanism's own tests (#519).

Background work and Families on the form now are served at their real
addresses, because their periodic re-reads (#1586) read those addresses and
the mechanism follows a data-in-place link only within the same page: the
plain page, the answers a test routes those re-reads to (with changed
counts, so the update is visible) and the background table's page 2.

A small test-only page exercises form[data-in-place] POSTs, which PR 1 of
#519 does not yet put on a real Admin page: it is the real Admin chrome and
assets around one region holding one form. ``POSTS`` lists the fixture
server's answers to its POSTs: a Post/Redirect/Get redirect back to the
page, refusals that re-render the page with an error summary inside the
region or (as base.html draws it) outside it, a refusal without the region,
and a redirect to another page that carries a region of the same id. Below
that region, a selection form marked data-in-place wraps a shared table
region whose sort heading must still act as a table control.
"""

from datetime import UTC, datetime
from uuid import UUID

from django import forms
from django.template import engines
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.presence import PRESENCE_SORTING
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.jobs.views import TASK_SORTING
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import window_table

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
BACKGROUND = reverse("admin:background")
PRESENCE = reverse("admin:presence")
# Routed answers to a periodic re-read of each page: more work and more
# Families than the page first showed.
BACKGROUND_LATER = "/in-place-background-later"
PRESENCE_LATER = "/in-place-presence-later"
# A Rows per page choice of 25, and the routed answer to its re-read.
BACKGROUND_25_LATER = "/in-place-background-25-later"
FORM = "/in-place-form"
# Where each fixture POST answers (status, Location or None, body). The
# conftest adds the answers whose bodies are rendered pages.
POSTS = {
    f"{FORM}/save": (303, f"{FORM}?saved=1", ""),
    f"{FORM}/slow": (303, f"{FORM}?saved=1", ""),
    f"{FORM}/elsewhere": (303, "/in-place-other", ""),
}
# POST paths the fixture server answers only after a pause, so a test can
# act while the save is still in flight.
SLOW = {f"{FORM}/slow"}
# Enough filler that the form sits well below the fold, and the page is
# tall enough to keep a scroll position below it.
FILLER = range(40)
FORM_PAGE = """{% extends 'stewardship/admin-base.html' %}
{% block title %}In-place form{% endblock %}
{% block content %}
<h1>{{ heading }}</h1>
{% for n in filler %}<p>Filler paragraph {{ n }} above the form.</p>{% endfor %}
<section id="saved" class="panel" data-in-place-region>
{% include 'stewardship/components/errors.html' with errors=region_errors %}
<p id="saved-count">Saved {{ saved }} times</p>
<form data-in-place method="post" action="/in-place-form/save#saved"
 data-in-place-message="Saved.">
<input type="hidden" name="csrfmiddlewaretoken" value="{{ csrf_token }}">
<label for="note">Note</label> <input id="note" name="note" value="{{ note }}">
<button id="save" type="submit" name="action" value="save">Save</button>
<button id="refuse" type="submit"
 formaction="/in-place-form/refuse#saved">Refuse</button>
<button id="invalid" type="submit"
 formaction="/in-place-form/invalid#saved">Invalid</button>
<button id="denied" type="submit"
 formaction="/in-place-form/denied#saved">Denied</button>
<button id="elsewhere" type="submit"
 formaction="/in-place-form/elsewhere#saved">Elsewhere</button>
<button id="plain" type="submit"
 formaction="/in-place-form/plain#saved">Plain answer</button>
<button id="slow" type="submit"
 formaction="/in-place-form/slow#saved">Slow save</button>
<button id="multipart" type="submit" formenctype="multipart/form-data">Upload</button>
</form>
<form id="outside-form" data-in-place method="post" action="/in-place-form/save#saved"
 data-in-place-message="Saved from outside.">
<input type="hidden" name="csrfmiddlewaretoken" value="{{ csrf_token }}">
</form>
<button id="outside-save" type="submit" form="outside-form">Save from outside</button>
<form data-in-place method="get" action="/in-place-form#saved">
<input type="hidden" name="saved" value="1">
<button id="show">Show saved</button>
<button id="show-post" formmethod="post"
 formaction="/in-place-form/save#saved">Save by formmethod</button>
</form>
</section>
<form id="picks-form" data-in-place method="post" action="/in-place-form/save#picks"
 data-in-place-message="Selection saved.">
<input type="hidden" name="csrfmiddlewaretoken" value="{{ csrf_token }}">
<div id="picks" data-table-region>
<table><thead><tr>
<th scope="col" data-sort-column="name"
{% if pick_sort %} aria-sort="{{ pick_sort }}"{% endif %}>
<a class="sort-link" href="/in-place-form?sort=-name#picks">Name</a></th>
</tr></thead>
<tbody><tr><td><input type="checkbox" name="pick" value="1"> Pick one</td></tr></tbody>
</table>
</div>
<button id="pick-save" type="submit">Save selection</button>
</form>
{% for n in filler %}<p>Filler paragraph {{ n }} below the form.</p>{% endfor %}
{% endblock %}
"""
# The page a refusal answers with when it is not this page again (a denial):
# the Admin chrome, and so the page's own timers, but not the form's region.
DENIED_PAGE = """{% extends 'stewardship/admin-base.html' %}
{% block title %}Denied{% endblock %}
{% block content %}<h1>Denied page</h1><p>This change is not allowed.</p>{% endblock %}
"""
# A Django choice group refused by the server (#592): Django 5.2 draws a
# RadioSelect as a fieldset described by the error, with each input marked
# invalid but not described, so the page finds the message on the fieldset.
CHOICE_PAGE = """{% extends 'stewardship/admin-base.html' %}
{% block title %}Choice group{% endblock %}
{% block content %}<h1>Choice group</h1>
<form method="post" action="/field-error-group">{{ form.kind.as_field_group }}
<button type="submit">Save</button></form>{% endblock %}
"""


class ChoiceForm(forms.Form):
    """One required choice drawn as radio buttons (a use_fieldset widget)."""

    kind = forms.ChoiceField(
        choices=[("a", "First"), ("b", "Second")], widget=forms.RadioSelect
    )


# The error a refused save re-renders, shaped as a view's form errors are.
NOTE_ERROR = {"field_id": "note", "message": "Enter a shorter note."}


def refused_choice():
    """The choice form as the server re-renders it after an empty choice."""
    form = ChoiceForm(data={})
    assert not form.is_valid()
    return form


def task(number):
    """One running background task, shaped as the background view shapes it."""
    return {
        "id": str(UUID(int=number)),
        "type": "source_refresh",
        "name": f"ParishSoft data refresh {number}",
        "state": "running",
        "heartbeat_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
        "progress": {
            "phase": "fetching",
            "current": 1,
            "total": 4,
            "display": Percentage(1, 4),
        },
    }


def background_page(number, active, size=50, tasks=1, state="nonterminal"):
    """The background page ``number`` of 51 tasks, with ``active`` running,
    ``size`` rows per page and ``tasks`` rows drawn, in Task state
    ``state``."""
    table = window_table(
        PageWindow(number, size),
        [task(number + index * 100) for index in range(tasks)],
        number == 1,
        total=(51, False),
        sorting=TASK_SORTING,
        sort=TASK_SORTING.default,
    )
    return table, {
        "work": {
            "counts": {"active": active, "queued": 0, "retry_wait": 0, "abandoned": 0}
        },
        "states": ("nonterminal", "all"),
        "selected_state": state,
        "table": table,
    }


def size_query(table, size):
    """The query a Rows per page choice of ``size`` reads: the navigator form's
    carried fields, the size and page 1 (ui-v1.js resets the page)."""
    fields = [*table.carried_fields, (table.size_name, size), (table.page_name, 1)]
    return "&".join(f"{name}={value}" for name, value in fields)


def presence_page(count):
    """Families on the form now, with ``count`` visible sessions."""
    rows = [
        {
            "name": f"Family {index}",
            "duid": 12345 + index,
            "started_at": NOW,
            "last_activity_at": NOW,
            "presence_at": NOW,
            "section": "welcome",
        }
        for index in range(count)
    ]
    table = window_table(
        PageWindow(1, 50),
        rows,
        False,
        total=(count, False),
        sorting=PRESENCE_SORTING,
        sort="-heartbeat",
    )
    return table, {"presence": {"count": count, "as_of": NOW}, "table": table}


def components(context, admin):
    """Every page above, keyed by the exact path and query it is fetched at."""

    def html(template, extra):
        """One Admin page rendered from a template name with the chrome."""
        return (
            "text/html",
            render_to_string(template, context | {"admin_chrome": admin} | extra),
        )

    form_template = engines["django"].from_string(FORM_PAGE)
    denied_template = engines["django"].from_string(DENIED_PAGE)

    def form_page(heading, saved, note="", pick_sort="", **errors):
        """The test-only form page (or the other page sharing its region).

        ``errors`` (a list) is drawn by base.html above the content, outside
        every region; ``region_errors`` inside the form's region.
        """
        extra = errors | {
            "heading": heading,
            "saved": saved,
            "note": note,
            "pick_sort": pick_sort,
            "filler": FILLER,
        }
        return (
            "text/html",
            form_template.render(context | {"admin_chrome": admin} | extra),
        )

    first, first_context = background_page(1, 1)
    second_context = background_page(2, 1)[1]
    refreshed = background_page(1, 2)[1]
    shown, presence_context = presence_page(1)
    return {
        BACKGROUND: html("stewardship/background.html", first_context),
        f"{BACKGROUND}?{first.current_query}": html(
            "stewardship/background.html", refreshed
        ),
        BACKGROUND_LATER: html("stewardship/background.html", refreshed),
        # The table's Next leads to page 2.
        f"{BACKGROUND}?{first.next_query}": html(
            "stewardship/background.html", second_context
        ),
        f"{BACKGROUND}?{size_query(first, 25)}": html(
            "stewardship/background.html", background_page(1, 1, size=25)[1]
        ),
        BACKGROUND_25_LATER: html(
            "stewardship/background.html", background_page(1, 2, size=25)[1]
        ),
        PRESENCE: html("stewardship/presence.html", presence_context),
        PRESENCE_LATER: html("stewardship/presence.html", presence_page(2)[1]),
        f"{PRESENCE}?{shown.size_name}={shown.size_value}&{shown.sort_name}="
        f"{shown.sort}": html("stewardship/presence.html", presence_page(2)[1]),
        FORM: form_page("In-place form", 0),
        f"{FORM}?saved=1": form_page("In-place form", 1),
        # This page as an ordinary (not in-place) form's refusal draws it: the
        # summary where base.html puts it, outside every region.
        f"{FORM}?error=1": form_page("In-place form", 0, errors=[NOTE_ERROR]),
        # The selection table's sort heading leads here.
        f"{FORM}?sort=-name": form_page("In-place form", 0, pick_sort="descending"),
        "/in-place-other": form_page("Another page", 0),
        # The pages refused POSTs answer with. A 400 re-renders the form with
        # the value sent and the summary inside its region; a 200 puts the
        # summary where base.html draws it, outside every region; a denial
        # (400) is another page. The headings differ from this page's, so a
        # test can tell a region swap from a whole-page write.
        "/in-place-refused": form_page(
            "Refused page", 0, note="hello", region_errors=[NOTE_ERROR]
        ),
        "/in-place-invalid": form_page(
            "Invalid page", 0, note="hello", errors=[NOTE_ERROR]
        ),
        "/in-place-denied": (
            "text/html",
            denied_template.render(context | {"admin_chrome": admin}),
        ),
        "/field-error-group": (
            "text/html",
            engines["django"]
            .from_string(CHOICE_PAGE)
            .render(context | {"admin_chrome": admin, "form": refused_choice()}),
        ),
        # A POST's own successful answer (no redirect) without the region:
        # the sign-in page stands in for any such page.
        "/in-place-plain": (
            "text/html",
            render_to_string("stewardship/login.html", context),
        ),
    }
