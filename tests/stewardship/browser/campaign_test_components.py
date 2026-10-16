"""Preview and test email's Send this test email, in place (#519).

The page is served at its real address. The fixture server answers Send's
POST as its view does: a Post/Redirect/Get redirect back to the page, here
with a query (``SENT``) so a test can answer that page's own polls
(live-status-v1.js re-reads the address it is on) with the settled list
(``SETTLED``) without touching the redirect it follows. The settled list's
quiet follow-up re-reads the bare page, which offers Send again.

A second email's Send is refused with the view's fixed 409 error page.
"""

from datetime import timedelta
from uuid import UUID

from django.core import signing
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.campaign_mail import SALT
from parishkit.stewardship.accounts.campaign_mail_views import CampaignMailForm
from parishkit.stewardship.accounts.setup_mail_views import LABELS

PAGE = reverse("admin:campaign_mail", args=[UUID(int=5401)])
SENT = f"{PAGE}?sent=1"
SETTLED = f"{PAGE}?settled=1"
REFUSED = reverse("admin:campaign_mail", args=[UUID(int=5402)])


def components(context, admin):
    """The test page before a send, while the new test is on its way, once
    it was accepted, and the page whose Send is refused."""
    now = context["server_now"]
    page = context | {"admin_chrome": admin}
    earlier = {
        "id": UUID(int=5410),
        "label": LABELS["accepted"],
        "created_at": now - timedelta(days=1),
        "current": True,
    }

    def mail(address, *, pending=False, latest=None):
        """The test page drawn as the view draws it, with ``latest`` (a test
        state) as the newest test above the earlier accepted one."""
        items = [earlier]
        if latest:
            items = [
                {
                    "id": UUID(int=5411),
                    "label": LABELS[latest],
                    "created_at": now,
                    "current": True,
                },
                earlier,
            ]
        return render_to_string(
            "stewardship/campaign-mail.html",
            page
            | {
                "campaign": {"active_configuration": {"name": "Annual"}},
                "form": CampaignMailForm(
                    initial={"preview_token": signing.dumps({"n": 1}, salt=SALT)}
                ),
                "sample": {
                    "subject": "[TEST] Sample invitation",
                    "html": "<p>Hello Sample Family.</p>",
                    "text": "Hello Sample Family.",
                },
                "testing_recipient": "testing@example.org",
                "pending": pending,
                "unknown": False,
                "items": items,
                "page_url": address,
            },
        )

    responses = {
        PAGE: mail(PAGE, latest=None),
        # The pending page names its own address as its poll address, so a
        # test can answer its polls (see the module docstring).
        SENT: mail(SENT, pending=True, latest="queued"),
        SETTLED: mail(PAGE, latest="accepted"),
        REFUSED: mail(REFUSED),
    }
    return {path: ("text/html", html) for path, html in responses.items()}


def posts(context, admin):
    """The fixture server's answers: Send's redirect back to the page, and
    the refused email's 409 error page."""
    refused = render_to_string(
        "stewardship/error.html",
        context
        | {
            "admin_chrome": admin,
            "admin": True,
            "title": "This page is out of date",
            "error_messages": ["Reload the page and try again."],
            "home": "/admin/",
            "home_label": "Administration home",
        },
    )
    return {PAGE: (303, SENT, ""), REFUSED: (409, None, refused)}
