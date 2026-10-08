"""Home's My Ministries panel for a Ministry leader (#533 slice 1).

``PATH`` is a leader's Home with four Ministries: long names (a phone's
width is tested), one with no open requests and one since removed from the
campaign that still has requests. ``EMPTY`` is a leader none of whose
Ministries is in the campaign.
"""

from datetime import date
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

PATH, EMPTY = "/my-ministries", "/my-ministries-empty"
CAMPAIGN = SimpleNamespace(
    pk=UUID(int=533),
    state="active",
    active_configuration=SimpleNamespace(
        name="Stewardship 2027",
        start_date=date(2026, 10, 3),
        end_date=date(2026, 11, 30),
        timezone="America/New_York",
    ),
)


def ministry(duid, name, join, leave, in_campaign=True):
    """One Ministry as ``my_ministries`` shapes it."""
    return {
        "duid": duid,
        "name": name,
        "join": join,
        "leave": leave,
        "in_campaign": in_campaign,
    }


MINISTRIES = [
    ministry(12, "Extraordinary Ministers of Holy Communion", 0, 0),
    ministry(9, "Food pantry", 3, 1),
    ministry(31, "Youth choir", 412, 2),
    ministry(40, "Altar servers", 1, 0, in_campaign=False),
]


def components(context, admin):
    """The Homes, as the index view renders them for a leader."""
    # Home's own chrome: no breadcrumb, nothing current in the menu.
    chrome = admin | {"breadcrumbs": [], "sections": []}

    def home(mine):
        """Home with only the leader's panel and the campaign line."""
        return render_to_string(
            "stewardship/home.html",
            context
            | {"admin_chrome": chrome}
            | {
                "configuration": SimpleNamespace(mode="production"),
                "dashboard": {
                    "campaign": CAMPAIGN,
                    "refreshed_at": None,
                    "my_ministries": mine,
                },
            },
        )

    return {
        PATH: ("text/html", home({"ministries": MINISTRIES})),
        EMPTY: ("text/html", home({"ministries": []})),
    }
