"""Sign out while the Administrator owns a setup attempt (#2272).

Two real setup pages drawn with the owned attempt in the Admin chrome, so the
menu's Sign out (and the page's own) name the confirmation dialog:
``COLLECTING`` is the setup overview of a collecting attempt, and ``FINISHING``
the Finishing setup page of a confirmed one.
"""

from uuid import UUID

from django.template.loader import render_to_string

ATTEMPT = UUID(int=2272)
COLLECTING = "/setup-signout-collecting"
FINISHING = "/setup-signout-finishing"


def components(context, admin, setup_draft, finishing):
    """Both pages, with the real menu in its setup form."""
    chrome = admin | {
        "setup_pending": True,
        "setup_attempt": ATTEMPT,
        "setup_url": "/admin/setup",
        "sections": [],
    }
    page = context | {"admin_chrome": chrome}
    return {
        COLLECTING: (
            "text/html",
            render_to_string(
                "stewardship/setup.html",
                page | {"draft": setup_draft, "wizard": None, "expired": False},
            ),
        ),
        FINISHING: (
            "text/html",
            render_to_string("stewardship/setup-cancel.html", page | finishing),
        ),
    }
