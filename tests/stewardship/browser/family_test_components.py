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
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.campaign_family_test_views import (
    FamilyTestConfirmForm,
    FamilyTestForm,
)

# Check is answered by a route, so this page's POST never reaches the server.
CHECK = reverse("admin:campaign_mail_families", args=[UUID(int=704)])
# Send's redirect lands on SENT, a stand-in for the session's requested
# count (the static fixture server cannot remember one), at the same path.
SEND = reverse("admin:campaign_mail_families", args=[UUID(int=705)])
SENT = SEND + "?sent=1"
# A Send the server refuses with its error page, which has no region of
# this page.
DENIED = reverse("admin:campaign_mail_families", args=[UUID(int=706)])
# A page whose Family test is still on its way, so its recent tests watch
# themselves (#1586); a route answers its reads with PENDING_SETTLED.
PENDING = reverse("admin:campaign_mail_families", args=[UUID(int=707)])
PENDING_SETTLED = "/family-test-settled"
# Routed answers to CHECK, fetched by GET from these addresses.
REVIEW = "/family-test-review"
REFUSED = "/family-test-refused"
FAMILIES = [{"name": "Sample Family", "duid": 1234, "label": "Eligible"}]


def family_tests_page(context, admin, **values):
    """The real Send to chosen Families page, with ``values`` over a sample.

    The sample is one ready Family under an annual campaign, so a test sets
    only what its state changes.
    """
    campaign = SimpleNamespace(
        pk=UUID(int=619),
        delivery_paused=False,
        active_configuration=SimpleNamespace(
            name="Annual campaign",
            starts_at=context["deadline"],
            ends_at=context["absolute_deadline"],
        ),
    )
    sample = {
        "admin_chrome": admin,
        "campaign": campaign,
        "subject": "Welcome",
        "testing_recipient": "testing@example.org",
        "epoch_ready": True,
        "available": 3,
        "families": [{"name": "Sample Family", "duid": 1234, "label": "Ready"}],
        "confirm": True,
        "sendable": True,
    }
    return render_to_string(
        "stewardship/campaign-mail-families.html", context | sample | values
    )


def denied_page(context, admin):
    """The server's error page for a refused action: no region of any page."""
    return render_to_string(
        "stewardship/error.html",
        context
        | {
            "admin_chrome": admin,
            "admin": True,
            "title": "Not allowed",
            "error_messages": ["This action is not allowed."],
            "home": "/admin/",
            "home_label": "Administration home",
        },
    )


def _page(context, admin, address, **values):
    """The page at ``address``, empty unless ``values`` say otherwise.

    It is tall enough (a long recent-tests list) that the page scrolls, so a
    test can wheel down to the review and see it is not sent to the top.
    """
    now = context["server_now"]
    items = [
        {
            "id": UUID(int=7000 + index),
            "duid": 2000 + index,
            "label": "Sent",
            "created_at": now - timedelta(hours=index),
        }
        for index in range(1, 25)
    ]
    return family_tests_page(
        context,
        admin,
        **(
            {
                "form": FamilyTestForm(),
                "families": [],
                "confirm": None,
                "sendable": False,
                "items": items,
                "refresh": False,
                "refresh_url": address,
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
        DENIED: _review(context, admin, DENIED),
        PENDING: _page(context, admin, PENDING, refresh=True),
        PENDING_SETTLED: _page(context, admin, PENDING),
    }
    return {path: ("text/html", body) for path, body in pages.items()}


def posts(context, admin):
    """The fixture server's answers to Send: SEND redirects back to the page
    with its requested notice, and DENIED is refused with the error page."""
    return {
        SEND: (303, SENT, ""),
        DENIED: (403, None, denied_page(context, admin)),
    }
