"""The setup wizard's tests and final steps in their order (#1476, #1578).

Test email follows Google Workspace connection and Test Slack the Slack
notifications step; Finish setup follows Load parish data (no Review page,
#2145) and checks the tests' results itself. These pages are drawn from
the real templates and view helpers:

- ``FAILED``: Test email whose newest test of this revision was not sent,
  with the notice linking the steps that fix it.
- ``FIX``: the Outgoing email settings step that notice links to, at its
  real address. Its Save and continue answers (``POSTS``) as the real view
  does once the fix is saved with the staged key still current for it: a
  redirect to the next step not done yet, which is Test email (wizard rule
  8), never Finish setup.
- ``RETEST``: Test email at its real address after the fix: the failed test
  is of earlier settings, so the notice is gone and Send test email is the
  primary action again.
- ``FINISH_READY`` and ``FINISH_UNREADY``: Finish setup with the email test
  accepted (its Check readiness and finish setup button), and opened before
  it was (the readiness refusal, a link to Test email and the button
  unavailable).
- ``SLACK_FAILED``: Test Slack whose newest test was not sent, with Turn off
  Slack notifications above the test form and in the notice (#1499). Both
  post the Slack notifications step's form (``SLACK_OFF``), answered as the
  real view answers once Slack is saved off: a redirect to the next step not
  done, here Test email (``RETEST``, drawn with Slack off), never Finish
  setup.
- ``REDIRECTS``: GET answers that are redirects. Test Slack's real address
  answers as the real view does while Slack is off: a redirect to the next
  step needing work, Test email (#1505).
- ``SLACK_STEP``: the one Slack notifications step (#1578) with Slack on and
  a token saved for its channel, offering the same button above its form.
"""

from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import setup_mail_views as mail_views
from parishkit.stewardship.accounts import setup_notification_views as slack_views
from parishkit.stewardship.accounts.setup_confirmation_views import (
    SetupConfirmationForm,
)
from parishkit.stewardship.accounts.setup_forms import FORMS, initial_values
from parishkit.stewardship.accounts.setup_mail_views import SetupMailForm
from parishkit.stewardship.accounts.setup_slack_step import SetupSlackStepForm
from parishkit.stewardship.accounts.setup_wizard import BY_KEY

from ..test_setup_final_steps import wizard as final_wizard
from ..test_setup_forms import VALUES
from .setup_live_components import status_row

FAILED = "/setup-mail-failed"
FIX = BY_KEY["mail"].url
RETEST = BY_KEY["mail_test"].url
FINISH_READY = "/setup-finish-ready"
FINISH_UNREADY = "/setup-finish-unready"
SLACK_FAILED = "/setup-slack-failed"
SLACK_STEP = "/setup-slack-step"
SLACK_OFF = BY_KEY["slack"].url
POSTS = {FIX: (303, RETEST, ""), SLACK_OFF: (303, RETEST, "")}
REDIRECTS = {BY_KEY["slack_test"].url: RETEST}
FAILED_ID = "00000000-0000-4000-8000-000000001476"
SLACK = {"enabled": True, "channel_id": "CFIXTURE"}


def components(context, admin, now, status_url, setup_draft):
    """The failed test, its fix, the retest and both Finish setups (module doc)."""

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
            initial={"test_token": "synthetic-preview", "request_key": UUID(int=2)}
        )
        return page(
            "setup-mail",
            mail_views.page_status(form, data, mail_views.EMPTY, status_url)
            | {
                "wizard": final_wizard("mail_test"),
                "testing_recipient": "testing@example.org",
            },
        )

    def finish(tests, problem=None):
        """Finish setup with these accepted tests, as the real view draws it:
        ``problem`` is the readiness refusal ``ready_inputs`` gives."""
        return page(
            "setup-confirmation",
            {
                "wizard": final_wizard("finish", tests=tests),
                "form": SetupConfirmationForm(initial={"preview_token": "synthetic"}),
                "readiness_problem": problem,
                "candidate_digest": "a" * 64,
            },
        )

    def slack_test(row):
        """Test Slack with one test, its status read from the real helper."""
        data = {"items": [row], "pending": False, "unknown": False}
        form = slack_views.SetupNotificationForm(
            initial={"test_token": "synthetic-preview", "request_key": UUID(int=3)}
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
        FINISH_READY: finish(frozenset({"mail_test"})),
        FINISH_UNREADY: finish(
            frozenset(), "A successful test of the current settings is required."
        ),
        SLACK_FAILED: slack_test(not_sent),
        SLACK_STEP: page(
            "setup-step",
            {
                "wizard": final_wizard("slack", slack=True),
                "draft": setup_draft | {"sections": {"slack": SLACK}},
                "form": SetupSlackStepForm(
                    initial=initial_values("slack", SLACK), saved="CFIXTURE"
                ),
                "step": "slack",
                "step_label": BY_KEY["slack"].label,
                "slack_on": True,
                "slack_saved": True,
                "slack_saved_channel": "CFIXTURE",
            },
        ),
    }
