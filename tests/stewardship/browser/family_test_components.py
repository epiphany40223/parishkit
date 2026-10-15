"""Send to chosen Families at its real addresses, for in-place Check and Send.

#519 PR 7c: Check these Families and Send these Family tests post to the
page itself. Check's answer is the page again (200 with the review, 400
with a refused list), which a test answers from a route with one of the
pages here. Send's answer is a Post/Redirect/Get redirect back to the page,
which a route cannot fulfil on every engine, so the fixture server answers
it (``posts``). Each page has its own address, so one test's POST answer
never stands in for another's.
"""

from datetime import timedelta
from uuid import UUID

from django.urls import reverse

from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.campaign_family_test_views import (
    FamilyTestConfirmForm,
    FamilyTestForm,
)

from .step_up_components import render

# Check is answered by a route, so this page's POST never reaches the server.
CHECK = reverse("admin:campaign_mail_families", args=[UUID(int=704)])
# Send's redirect lands on SENT, a stand-in for the session's requested
# count (the static fixture server cannot remember one), at the same path.
SEND = reverse("admin:campaign_mail_families", args=[UUID(int=705)])
SENT = SEND + "?sent=1"
# A Send whose sign-in went stale between the review and Send.
STALE = reverse("admin:campaign_mail_families", args=[UUID(int=706)])
# Routed answers to CHECK, fetched by GET from these addresses.
REVIEW = "/family-test-review"
REVIEW_STALE = "/family-test-review-stale"
REVIEW_LOCAL = "/family-test-review-local"
REFUSED = "/family-test-refused"
FAMILIES = [{"name": "Sample Family", "duid": 1234, "label": "Eligible"}]


def _page(context, admin, address, *, local=False, **values):
    """The page at ``address``, empty unless ``values`` say otherwise.

    It is tall enough (a long recent-tests list) that the page scrolls, so a
    test can wheel down to the review and see it is not sent to the top.
    """
    now = context["server_now"]
    items = [
        {
            "duid": 2000 + index,
            "label": "Sent",
            "created_at": now - timedelta(hours=index),
        }
        for index in range(1, 25)
    ]
    return render(
        "family-tests",
        context,
        admin,
        local,
        **(
            {
                "form": FamilyTestForm(),
                "families": [],
                "confirm": None,
                "sendable": False,
                "fresh": True,
                "signed_in_minutes": 1,
                "items": items,
                "refresh": False,
                "refresh_url": address,
                "next": address,
            }
            | values
        ),
    )


def _review(context, admin, address, **values):
    """``address`` showing the review of FAMILIES, ready to send."""
    confirm = FamilyTestConfirmForm(initial={"preview": "signed-review"})
    return _page(
        context,
        admin,
        address,
        form=FamilyTestForm({"families": "1234"}),
        families=FAMILIES,
        confirm=confirm,
        sendable=True,
        **values,
    )


def components(context, admin):
    """Each page as first shown, and the answers the tests route to it."""
    refused = FamilyTestForm({"families": "not-a-duid"})
    refused.is_valid()
    pages = {
        CHECK: _page(context, admin, CHECK),
        REVIEW: _review(context, admin, CHECK),
        REVIEW_STALE: _review(context, admin, CHECK, fresh=False, signed_in_minutes=7),
        REVIEW_LOCAL: _review(
            context, admin, CHECK, local=True, fresh=False, signed_in_minutes=7
        ),
        REFUSED: _page(
            context,
            admin,
            CHECK,
            form=refused,
            # The summary the view draws for a refused list.
            errors=summary_errors(refused),
        ),
        SEND: _review(context, admin, SEND),
        SENT: _page(context, admin, SEND, sent=1, available=2),
        STALE: _review(context, admin, STALE),
    }
    return {path: ("text/html", body) for path, body in pages.items()}


def posts(context, admin):
    """The fixture server's answers to Send: SEND redirects back to the page
    with its requested notice, and STALE is refused with the step-up page."""
    return {
        SEND: (303, SENT, ""),
        STALE: (403, None, render("error", context, admin, next=STALE)),
    }
