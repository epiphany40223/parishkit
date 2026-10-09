"""Families the form cannot open (#774), served with its production template.

The list is rendered through the view's own sorting at its real address, at
the navigator's Next address and at the Member DUID heading's sort address,
so the in-place GET refreshes land on served pages.
"""

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.source_form_views import SORTING
from parishkit.stewardship.web.tables import paginate

PATH = reverse("admin:source_form")
ROWS = 30


def components(context, admin):
    """The list, its second page, and the list sorted by Family, Z-A."""
    rows = [
        {
            "family_duid": 1000 + index,
            "family_name": f"Family {index:02d}",
            "member_duid": 5000 + index,
            "field": "Birth date",
        }
        for index in range(ROWS)
    ]

    def page(paging):
        """Render the list with ``paging``'s table choices."""
        table = paginate(rows, paging, sorting=SORTING)
        return table, (
            "text/html",
            render_to_string(
                "stewardship/source-form.html",
                context
                | {
                    "admin_chrome": admin,
                    "table": table,
                    "rows": rows,
                    "families": ROWS,
                    "eligible": 1200,
                },
            ),
        )

    first, served = page({"size": "25"})
    _, second = page({"size": "25", "page": "2"})
    _, reversed_ = page({"size": "25", "sort": "-family"})
    return {
        PATH: served,
        f"{PATH}?{first.next_query}": second,
        # The Family heading's own address: Z-A, since A-Z is the default.
        f"{PATH}?{first.heading_query('family')}": reversed_,
    }
