"""The header's Find a Family box on an Admin page (#561, NAV-19).

``PATH`` is an Admin page whose chrome offers the box. Its search posts to
``FIND``, which the fixture server does not serve: each test answers it
with a Playwright route from ``ANSWERS``, the real results template
rendered for synthetic matches, so the test can see what was posted and
how often.
"""

from uuid import UUID

from django.template import engines
from django.template.loader import render_to_string

PATH = "/find-family"
FIND = "/find-family/find"
DIRECTORY = "/find-family/directory"
CAMPAIGN = UUID(int=561)
PAGE = """{% extends 'stewardship/admin-base.html' %}
{% block title %}Find a Family{% endblock %}
{% block content %}<h1>Home</h1><p><a href="/home">Elsewhere</a></p>{% endblock %}"""


def _answer(count, total):
    """The results fragment listing ``count`` of ``total`` matching Families."""
    rows = [
        {
            "family_id": str(UUID(int=index + 1)),
            "display_name": f"Example {index + 1}, Anna and Ben",
            "family_duid": 100 + index,
            "envelope": str(500 + index),
        }
        for index in range(count)
    ]
    return render_to_string(
        "stewardship/find-family-results.html",
        {
            "rows": rows,
            "total": total,
            "more": total > count,
            "search": "examp",
            "campaign_id": CAMPAIGN,
            "directory_url": DIRECTORY,
            "csrf_token": "a" * 64,
        },
    )


# The answer for each search text the tests type; anything else matches none.
ANSWERS = {"examp": _answer(8, 12), "example 3": _answer(1, 1)}
NONE = _answer(0, 0)


# A long parish name beside the box and both header pills (#561 review):
# the header must still fit a tablet-width window.
LONG = "/find-family-long"
LONG_NAME = "The Catholic Community of the Most Holy Epiphany of Our Lord"
# The "See all" button's POST lands here: the fixture server answers it
# with a stand-in directory page.
POSTS = {
    DIRECTORY: (
        200,
        None,
        "<!doctype html><title>Active parishioner family directory</title>"
        "<h1>Active parishioner family directory</h1>",
    )
}


def components(context, admin):
    """The Admin page with the box in its header, and one with a long name."""
    chrome = admin | {"find_family": {"url": FIND}}
    page = engines["django"].from_string(PAGE)
    return {
        PATH: ("text/html", page.render(context | {"admin_chrome": chrome})),
        LONG: (
            "text/html",
            page.render(
                context
                | {"admin_chrome": chrome | {"parish_name": LONG_NAME, "admin": True}}
            ),
        ),
    }
