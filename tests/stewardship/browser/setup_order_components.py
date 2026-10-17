"""The setup wizard's final steps in their order (#1476).

Test email and Test Slack come after Load parish data and before Review, so
Review is the last step before Finish setup and shows the tests' results.
These pages are drawn from the real templates and view helpers:

- ``FAILED``: Test email whose newest test of this revision was not sent,
  with the notice linking the steps that fix it.
- ``FIX``: the Outgoing email settings step that notice links to, at its
  real address. Its Save and continue answers (``POSTS``) as the real view
  does once the fix is saved: a redirect to the next step not done yet,
  which is Test email (wizard rule 8), never Review.
- ``RETEST``: Test email at its real address after the fix: the failed test
  is of an earlier revision, so the notice is gone and Send test email is
  the primary action again.
- ``REVIEW_ACCEPTED`` and ``REVIEW_UNTESTED``: Review with the email test
  accepted (Continue to Finish setup), and opened before it was (a link to
  Test email, no Continue).
- ``SLACK_FAILED``: Test Slack whose newest test was not sent, with Turn off
  Slack notifications above the test form and in the notice (#1499). Both
  post the Slack notifications step's form (``SLACK_OFF``), answered as the
  real view answers once Slack is saved off: a redirect to the next step not
  done, Test email (``RETEST``, drawn with Slack off), never Review.
- ``REDIRECTS``: GET answers that are redirects. Test Slack's real address
  answers as the real view does while Slack is off: a redirect to the next
  step needing work, Test email (#1505).
- ``SLACK_CONNECTION``: the Slack connection step with a token saved and
  Slack on, offering the same button above its form.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import setup_mail_views as mail_views
from parishkit.stewardship.accounts import setup_notification_views as slack_views
from parishkit.stewardship.accounts.setup_credential_views import (
    SetupCredentialForm,
)
from parishkit.stewardship.accounts.setup_forms import FORMS, initial_values
from parishkit.stewardship.accounts.setup_mail_views import SetupMailForm
from parishkit.stewardship.accounts.setup_preview_views import _readiness
from parishkit.stewardship.accounts.setup_wizard import BY_KEY

from ..test_setup_final_steps import wizard as final_wizard
from ..test_setup_forms import VALUES
from .setup_live_components import status_row

FAILED = "/setup-mail-failed"
FIX = BY_KEY["mail"].url
RETEST = BY_KEY["mail_test"].url
REVIEW_ACCEPTED = "/setup-review-accepted"
REVIEW_UNTESTED = "/setup-review-untested"
SLACK_FAILED = "/setup-slack-failed"
SLACK_CONNECTION = "/setup-slack-connection"
SLACK_OFF = BY_KEY["slack"].url
POSTS = {FIX: (303, RETEST, ""), SLACK_OFF: (303, RETEST, "")}
REDIRECTS = {BY_KEY["slack_test"].url: RETEST}
FAILED_ID = "00000000-0000-4000-8000-000000001476"


def components(context, admin, now, status_url, setup_draft):
    """The failed test, its fix, the retest and both Reviews (module doc)."""

    def page(template, values):
        """One setup page with the shared Admin chrome and setup draft."""
        return (
            "text/html",
            render_to_string(
                f"stewardship/{template}.html",
                context | {"admin_chrome": admin, "draft": setup_draft} | values,
            ),
        )

    def mail_test(row):
        """Test email with one test, its status read from the real helper."""
        data = {"items": [row], "pending": False, "unknown": False}
        form = SetupMailForm(
            initial={"preview_token": "synthetic-preview", "request_key": UUID(int=2)}
        )
        return page(
            "setup-mail",
            mail_views.page_status(form, data, mail_views.EMPTY, status_url)
            | {
                "wizard": final_wizard("mail_test"),
                "testing_recipient": "testing@example.org",
            },
        )

    def review(tests):
        """Review with these accepted tests, as the real view draws it."""
        readiness = _readiness(
            SimpleNamespace(sections={"slack": {"enabled": False}}), tests
        )
        return page(
            "setup-preview",
            {
                "wizard": final_wizard("preview", tests=tests),
                "parish": {
                    "name": "Sample Parish",
                    "website": "https://parish.example.org/",
                    "phone": "+12125551234",
                },
                "testing_recipient": "testing@example.org",
                "candidate_digest": "a" * 64,
                "document": "{}",
                "readiness": readiness,
                "tests_accepted": all(item["accepted"] for item in readiness),
            },
        )

    def slack_test(row):
        """Test Slack with one test, its status read from the real helper."""
        data = {"items": [row], "pending": False, "unknown": False}
        form = slack_views.SetupNotificationForm(
            initial={"preview_token": "synthetic-preview", "request_key": UUID(int=3)}
        )
        return page(
            "setup-notification",
            mail_views.page_status(
                form,
                data,
                slack_views.EMPTY,
                status_url.replace("mail-test", "slack-test"),
                slack_views.FIX,
            )
            | {
                "wizard": final_wizard(
                    "slack_test", slack=True, tests=frozenset({"mail_test"})
                ),
                "channel_id": "CFIXTURE",
            },
        )

    not_sent = status_row(FAILED_ID, "not_sent", now)
    return {
        FAILED: mail_test(not_sent),
        FIX: page(
            "setup-step",
            {
                "wizard": final_wizard("mail"),
                "form": FORMS["mail"](initial=initial_values("mail", VALUES["mail"])),
                "step": "mail",
                "step_label": BY_KEY["mail"].label,
            },
        ),
        RETEST: mail_test(not_sent | {"current": False}),
        REVIEW_ACCEPTED: review(frozenset({"mail_test"})),
        REVIEW_UNTESTED: review(frozenset()),
        SLACK_FAILED: slack_test(not_sent),
        SLACK_CONNECTION: page(
            "setup-credential",
            {
                "wizard": final_wizard("slack_credential", slack=True),
                "draft": setup_draft
                | {"sections": {"slack": {"enabled": True, "channel_id": "CFIXTURE"}}},
                "form": SetupCredentialForm("slack", saved=True),
                "target": "slack",
                "label": "Slack",
                "step_label": BY_KEY["slack_credential"].label,
                "saved": True,
                "current": True,
            },
        ),
    }
