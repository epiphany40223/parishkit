"""Integration settings reviewed and applied in the review dialog (#750).

Every page is the real settings template; every answer is the real review
fragment (``integration-review.html``) or the real page drawn again.

Slack notifications, set up, with its optional new-key field: ``SLACK`` is
the page at version ``"a" * 64``, posting to itself; a test answers its Save
with ``SLACK_REVIEW`` (the compact review of a new channel ID),
``SLACK_REFUSED`` (the page drawn again with the channel ID's error, status
400) or ``SLACK_STEP_UP`` (a stale sign-in's refusal, 403), and its Remove
Slack with ``SLACK_REMOVE`` (the confirmation). Its Apply is redirected by
the fixture server (``POSTS``) to ``APPLIED``, the change's status page,
Applied; the editor's region is then redrawn from ``SLACK_SETTLED``, the new
channel ID at version ``"b" * 64``. ``SLACK_REMOVING`` is the same page
whose region is redrawn from ``SLACK_REMOVED`` instead, Slack not set up.

ParishSoft with a schedule saved from Basic (#1122): ``SCHEDULE`` posts to
itself; a test answers its Save with ``SCHEDULE_REVIEW`` (the full review of
a schedule change) and the editor's live check with
``refresh_schedule_components.check_answer``. Its region is redrawn from
``SCHEDULE_SETTLED``, the new schedule at version ``"b" * 64``.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.integration_forms import (
    InlineCredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.source.refresh_rules import stored_settings

from .refresh_schedule_components import BASIC, _schedule

REQUEST = UUID("75000000-0000-4000-8000-000000000751")
SLACK = "/integration-dialog"
SLACK_REVIEW = "/integration-dialog-review"
SLACK_REFUSED = "/integration-dialog-refused"
SLACK_STEP_UP = "/integration-dialog-step-up"
SLACK_REMOVE = "/integration-dialog-remove"
SLACK_SETTLED = "/integration-dialog-settled"
SLACK_REMOVING = "/integration-dialog-removing"
SLACK_REMOVED = "/integration-dialog-removed"
SCHEDULE = "/integration-dialog-schedule"
SCHEDULE_REVIEW = "/integration-dialog-schedule-review"
SCHEDULE_SETTLED = "/integration-dialog-schedule-settled"
APPLIED = "/integration-dialog-applied"
POSTS = {
    SLACK: (303, APPLIED, ""),
    SLACK_REMOVING: (303, APPLIED, ""),
    SCHEDULE: (303, APPLIED, ""),
}
CHANNEL = "C0123456789"
NEW_CHANNEL = "C0987654321"
# The schedule the review's Apply saves: Basic's "Every 4 hours".
EVERY_FOUR = (
    {"organization_id": "12345"}
    | stored_settings(
        {
            "rules": [{"kind": "full", "every": 240, "from": "00:00", "to": "20:00"}],
            "skips": [],
            "skip_around_family_emails": True,
        }
    )
    | {"refresh_schedule_mode": "basic"}
)


def components(context, admin, now):
    """Every page and answer above, keyed by the path it is fetched at."""

    def page(target, label, form, refresh, **values):
        """The settings page of ``target`` with ``form``, posting to itself
        (``values["page_url"]``) and redrawn from ``refresh``."""
        return (
            "text/html",
            render_to_string(
                "stewardship/integration-settings.html",
                context
                | {
                    "admin_chrome": admin,
                    "target": target,
                    "label": label,
                    "summary": None,
                    "configured": True,
                    "complete_gate": True,
                    "form": form,
                    "refresh_url": refresh,
                    "errors": [],
                }
                | values,
            ),
        )

    def slack(channel, digest, *, path, refresh, data=None, configured=True):
        """Slack's page at ``digest`` showing ``channel``, or bound to ``data``."""
        form = IntegrationForm(
            "slack",
            data,
            initial=None if data else {"base_digest": digest, "channel_id": channel},
        )
        if data:
            form.is_valid()
        return page(
            "slack",
            "Slack notifications",
            form,
            refresh,
            page_url=path,
            configured=configured,
            removable=configured,
            credential=InlineCredentialForm(
                "slack", initial={"intent": "synthetic-intent"}, required=not configured
            ),
        )

    def schedule(stored, digest):
        """ParishSoft's page with ``stored`` as its schedule, at ``digest``."""
        form = IntegrationForm(
            "parishsoft", initial=stored | {"base_digest": digest}, stored=stored
        )
        return page(
            "parishsoft",
            "ParishSoft",
            form,
            SCHEDULE_SETTLED,
            page_url=SCHEDULE,
            complete_gate=False,
            refresh_key=uuid4(),
            **_schedule(form.schedule, now),
        )

    def review(**values):
        """The review dialog's answer, from the real fragment."""
        return (
            "text/html",
            render_to_string(
                "stewardship/integration-review.html",
                {"preview": "synthetic-signed-intent"} | values,
            ),
        )

    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    return {
        SLACK: slack(CHANNEL, "a" * 64, path=SLACK, refresh=SLACK_SETTLED),
        SLACK_REVIEW: review(
            kind="compact",
            target="slack",
            label="Slack notifications",
            changes=[
                {"label": "Slack channel ID", "before": CHANNEL, "after": NEW_CHANNEL}
            ],
        ),
        SLACK_REFUSED: slack(
            None,
            None,
            path=SLACK,
            refresh=SLACK_SETTLED,
            data={"base_digest": "a" * 64, "channel_id": "not-a-channel"},
        ),
        SLACK_STEP_UP: (
            "text/html",
            render_to_string(
                "stewardship/integration-review.html",
                {"refused": True, "target": "slack"},
            ),
        ),
        SLACK_REMOVE: review(kind="remove", target="slack"),
        SLACK_SETTLED: slack(NEW_CHANNEL, "b" * 64, path=SLACK, refresh=SLACK_SETTLED),
        SLACK_REMOVING: slack(
            CHANNEL, "a" * 64, path=SLACK_REMOVING, refresh=SLACK_REMOVED
        ),
        SLACK_REMOVED: slack(
            "", "b" * 64, path=SLACK_REMOVING, refresh=SLACK_REMOVED, configured=False
        ),
        SCHEDULE: schedule(BASIC, "a" * 64),
        SCHEDULE_REVIEW: review(
            kind="schedule",
            target="parishsoft",
            label="ParishSoft",
            changes=[],
            schedule_changes=[
                "Full refreshes added: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00.",
                "Full refreshes removed: 02:00.",
            ],
        ),
        SCHEDULE_SETTLED: schedule(EVERY_FOUR, "b" * 64),
        APPLIED: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context | {"admin_chrome": admin, "receipt": applied},
            ),
        ),
    }
