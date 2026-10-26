"""Setup wizard status pages and the fresh status regions they poll (#1354).

The Test email page is served at its own address (SEND) with a Send another
test form, whose POST the fixture server answers with a real
Post/Redirect/Get redirect back to the same path (``POSTS``), where the page
shows the new test still being sent. The status fragments each test answers
its polls with are drawn by ``mail_status``, ``finishing_status`` and
``load_status`` from the same templates and view helpers the real views use.
"""

from datetime import timedelta
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import setup_mail_views as mail_views
from parishkit.stewardship.accounts import (
    setup_notification_views as notification_views,
)
from parishkit.stewardship.accounts.setup_mail_views import SetupMailForm
from parishkit.stewardship.accounts.setup_notification_views import (
    SetupNotificationForm,
)

from ..test_setup_final_steps import wizard as final_wizard

# Each test page as its panel is re-read once a test settles: accepted (Send
# another test and Continue) or of unknown delivery (the acknowledgement box).
SETTLED = {
    (channel, state): f"/setup-{channel}-settled-{state}"
    for channel in ("mail", "slack")
    for state in ("accepted", "delivery_unknown")
}
# A Test email page whose test of this revision was accepted, so Send another
# test is in its form, and its address once that form was sent.
SEND = "/setup-mail-send"
SENT = SEND + "?sent=1"
POSTS = {SEND: (303, SENT, "")}
ACCEPTED_ID = "00000000-0000-4000-8000-000000001354"
QUEUED_ID = "00000000-0000-4000-8000-000000001355"


def status_row(identity, state, created_at, *, current=True):
    """One Recent tests row, as ``setup_mail_views._items`` returns it."""
    return {
        "id": identity,
        "state": state,
        "label": str(mail_views.LABELS[state]),
        "created_at": created_at.isoformat(),
        "current": current,
    }


def mail_status(items, *, pending):
    """The Test email status fragment the page polls, for these rows."""
    return render_to_string(
        "stewardship/setup-test-status.html",
        {"items": items, "pending": pending, "empty": mail_views.EMPTY},
    )


def finishing_status(context):
    """The Finishing setup status fragment for a ``finishing_context``."""
    return render_to_string("stewardship/setup-finishing-status.html", context)


def components(context, admin, now, status_url, setup_draft):
    """The Test email page before and after Send another test, and each test
    page as its settled test shows it (SETTLED)."""
    accepted = status_row(ACCEPTED_ID, "accepted", now)

    def mail_page(items, *, pending):
        """The Test email page with these rows, its test already accepted."""
        return render_to_string(
            "stewardship/setup-mail.html",
            context
            | {
                "admin_chrome": admin,
                "draft": setup_draft,
                "wizard": final_wizard("mail_test", tests=frozenset({"mail_test"})),
                "tested": True,
                "testing_recipient": "testing@example.org",
                "pending": pending,
                "unknown": False,
                "form": SetupMailForm(
                    initial={
                        "test_token": "synthetic-preview",
                        "request_key": UUID(int=1354),
                    }
                ),
                "items": items,
                "status_url": status_url,
                "empty": mail_views.EMPTY,
            },
        )

    def settled(channel, state):
        """A test page whose one test of this revision ended in ``state``."""
        row = status_row(ACCEPTED_ID, state, now)
        if channel == "slack":
            row["label"] = str(notification_views.SLACK_LABELS[state])
        data = {
            "items": [row],
            "pending": False,
            "unknown": state == "delivery_unknown",
        }
        initial = {"test_token": "synthetic-preview", "request_key": UUID(int=1)}
        if channel == "mail":
            name, extra = "setup-mail", {"testing_recipient": "testing@example.org"}
            steps = final_wizard("mail_test", tests=_tests(state, "mail_test"))
            values = mail_views.page_status(
                SetupMailForm(initial=initial), data, mail_views.EMPTY, status_url
            )
        else:
            name, extra = "setup-notification", {"channel_id": "CFIXTURE"}
            steps = final_wizard(
                "slack_test",
                slack=True,
                tests=frozenset({"mail_test"}) | _tests(state, "slack_test"),
            )
            values = mail_views.page_status(
                SetupNotificationForm(initial=initial),
                data,
                notification_views.EMPTY,
                status_url.replace("mail-test", "slack-test"),
            )
        return render_to_string(
            f"stewardship/{name}.html",
            context
            | values
            | extra
            | {"admin_chrome": admin, "draft": setup_draft, "wizard": steps},
        )

    queued = status_row(QUEUED_ID, "queued", now + timedelta(minutes=1))
    return {
        SEND: ("text/html", mail_page([accepted], pending=False)),
        SENT: ("text/html", mail_page([queued, accepted], pending=True)),
    } | {
        path: ("text/html", settled(channel, state))
        for (channel, state), path in SETTLED.items()
    }


def _tests(state, step):
    """The accepted tests a stepper shows when this step's test ended in ``state``."""
    return frozenset({step}) if state == "accepted" else frozenset()
