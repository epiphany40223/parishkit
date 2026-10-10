"""Testing submissions (the go-live Testing Families list), served with its
production template.

Families are named "Surname, heads" (#932), so the long names are the point:
the table must still fit the page, and the Family name heading sorts it in
place. The served pages stand in for the server's ordering.
"""

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.campaigns.cleanup_preview import TESTING_FAMILY_SORTING
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import window_table

PATH = reverse("admin:go_live_families")
ROWS = [
    {"duid": 1000 + index, "name": name}
    for index, name in enumerate(
        (
            "Abbott, Christopher and Mary-Katherine",
            "Nguyen, Bartholomew, Anastasia Okonkwo-Smith and Maximilian",
            "Squyres, Tracy and Jeff",
            "",  # Not in the current ParishSoft data.
        )
    )
]


def components(context, admin):
    """The list by DUID (the default), and by Family name Z-A."""

    def page(rows, sort):
        """Render the list in ``sort``'s order."""
        table = window_table(
            PageWindow(1, 50),
            rows,
            False,
            total=(len(rows), False),
            sorting=TESTING_FAMILY_SORTING,
            sort=sort,
        )
        return table, (
            "text/html",
            render_to_string(
                "stewardship/go-live-families.html",
                context
                | {
                    "admin_chrome": admin,
                    "table": table,
                    "campaign": {"active_configuration": {"name": "Stewardship"}},
                },
            ),
        )

    first, served = page(ROWS, "duid")
    by_name = sorted(ROWS, key=lambda row: row["name"].lower(), reverse=True)
    _, descending = page(by_name, "-name")
    # The name heading's first choice is A-Z; its address after A-Z is Z-A.
    by_name_table, ascending = page(by_name[::-1], "name")
    return {
        PATH: served,
        f"{PATH}?{first.heading_query('name')}": ascending,
        f"{PATH}?{by_name_table.heading_query('name')}": descending,
    }
