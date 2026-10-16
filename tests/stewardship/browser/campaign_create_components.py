"""Create the campaign's confirmation dialog (#142, #1356), as fixture pages.

The browser checks need no database. Every page is the real
``campaign-create.html`` (or its confirmation fragment):

- ``PAGE``: the empty creation form, posting to ``PAGE``. A test answers
  its Review with ``REVIEW`` (the real confirmation fragment) or
  ``REFUSED`` (the page drawn again with the name's error, status 400).
- Apply posts to ``PAGE``, which the fixture server redirects to
  ``APPLIED``: the page naming its change, already applied, so the dialog
  stops following at once and redraws the page's region from that address:
  the campaign created, its status and the offer to load its ParishSoft data.

``ONWARD`` is the confirmation the view now draws (#1404): it names the
next Campaign setup step, the stepper's Share options fixture page
(``stepper_components.PATH``), which the dialog opens once Apply is applied.

``POSTS`` lists the fixture server's answer to the page's Apply POST.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import setup_help
from parishkit.stewardship.accounts.admin_editing import (
    receipt_status,
    summary_errors,
)
from parishkit.stewardship.accounts.campaign_forms import CampaignForm

from .stepper_components import PATH as STEPPER

PAGE = "/campaign-create-confirm"
REVIEW = "/campaign-create-confirm-review"
REFUSED = "/campaign-create-confirm-refused"
ONWARD = "/campaign-create-confirm-onward"
REQUEST = UUID("13560000-0000-4000-8000-000000001356")
APPLIED = f"{PAGE}?request={REQUEST}"
# The values a test types, which the confirmation must never repeat.
VALUES = {
    "name": "Annual stewardship",
    "start_date": "2054-10-01",
    "end_date": "2054-10-31",
}
POSTS = {PAGE: (303, APPLIED, "")}


def _form(data=None):
    """The creation form as the view builds it, bound to ``data`` if given."""
    form = setup_help.apply(
        CampaignForm(
            data,
            initial={
                "timezone": "America/New_York",
                "census": True,
                "additional_information": True,
                "base_digest": "a" * 64,
            },
            previous={},
            ministries=[("4", "Community outreach"), ("5", "Choir")],
            funds=[("9", "Offertory")],
        ),
        setup_help.ADMIN_CAMPAIGN,
        replace=True,
    )
    if data is not None:
        form.is_valid()
    return form


def components(context, admin):
    """Every page above, keyed by the exact path and query it is fetched at."""

    def page(form, receipt=None, *, created=False):
        """Create the campaign around ``form``, or the status of ``receipt``."""
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-create.html",
                context
                | {
                    "admin_chrome": admin,
                    "configuration": {
                        "mode": "testing",
                        "testing_recipient": "testing@example.org",
                    },
                    "form": form,
                    "created": created,
                    "page_url": PAGE,
                    "errors": summary_errors(form) if form.is_bound else [],
                    "refresh_key": "13560000-0000-4000-8000-00000000beef"
                    if receipt
                    else None,
                }
                | receipt_status(receipt),
            ),
        )

    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    sent = {
        "timezone": "America/New_York",
        "census": "on",
        "additional_information": "on",
        "base_digest": "a" * 64,
        **VALUES,
        "name": "",
    }
    return {
        PAGE: page(_form()),
        REVIEW: (
            "text/html",
            render_to_string(
                "stewardship/campaign-create-review.html",
                {"review": {"content": 7, "preview": "synthetic-signed-intent"}},
            ),
        ),
        ONWARD: (
            "text/html",
            render_to_string(
                "stewardship/campaign-create-review.html",
                {
                    "review": {
                        "content": 7,
                        "preview": "synthetic-signed-intent",
                        "next_url": STEPPER,
                    }
                },
            ),
        ),
        REFUSED: page(_form(sent)),
        APPLIED: page(_form(), applied, created=True),
    }
