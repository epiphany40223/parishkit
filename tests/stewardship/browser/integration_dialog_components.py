"""Integration settings reviewed and applied in the review dialog (#750).

Every page is the real settings template; every answer is the real review
fragment (``integration-review.html``) or the real page drawn again.

Slack notifications, set up, with its optional new-key field: ``SLACK`` is
the page at version ``"a" * 64``, posting to itself; a test answers its Save
with ``SLACK_REVIEW`` (the compact review of a new channel ID) or
``SLACK_REFUSED`` (the page drawn again with the channel ID's error, status
400), and its Remove Slack with ``SLACK_REMOVE`` (the confirmation). Its
Apply is redirected by the fixture server (``POSTS``) to ``APPLIED``, the
change's status page, Applied; the editor's region is then redrawn from
``SLACK_SETTLED``, the new channel ID at version ``"b" * 64``.
``SLACK_REMOVING`` is the same page whose region is redrawn from
``SLACK_REMOVED`` instead, Slack not set up.

A pasted key (#1568): ``SLACK_KEY_REVIEW`` is the key's confirmation, which
a test answers the Review (posted without the key) with; its Apply, the
page's own form with the key, is answered with ``SLACK_PENDING``, the page
whose status line says the key is being installed and polls
``SLACK_STATUS``. A test answers that poll with ``SLACK_STATUS_PENDING``,
then ``SLACK_STATUS_DONE``, whose follow-up re-reads ``SLACK`` in place,
answered with ``SLACK_KEYED`` (Key updated). ``SLACK_UNSELECTED`` is the page
whose key's switch did not finish: its Finish switching Review is answered
with ``SLACK_FINISH``, and its Apply is redirected (``POSTS``) to
``APPLIED``; the editor and status line are then redrawn from
``SLACK_SETTLED``. ``PARISHSOFT_KEY`` is ParishSoft's page with its key
field, answered with ``PARISHSOFT_KEY_REVIEW``.

ParishSoft with a schedule saved from Basic (#1122): ``SCHEDULE`` posts to
itself; a test answers its Save with ``SCHEDULE_REVIEW`` (the full review of
a schedule change) and the editor's live check with
``refresh_schedule_components.check_answer``. Its region is redrawn from
``SCHEDULE_SETTLED``, the new schedule at version ``"b" * 64``.

The Backup encryption key (#750): ``BACKUP_KEY`` is its page at the paste
step, posting to itself, with ``OLD_FINGERPRINT`` in use. A test answers its
Continue with ``BACKUP_KEY_PROOF`` (the challenge step) or
``BACKUP_KEY_REFUSED`` (the paste refused, status 400), and the code's
Review with ``BACKUP_KEY_WRONG`` (the challenge again with the code's error,
status 400) or ``BACKUP_KEY_REVIEW`` (the compact review). Its Apply is
redirected (``POSTS``) to ``APPLIED``; a test then answers the redraw's read
of ``BACKUP_KEY`` with ``BACKUP_KEY_SETTLED``, ``NEW_FINGERPRINT`` in use.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.backup_key import KeyStatus
from parishkit.stewardship.accounts.integration_credentials import CredentialSummary
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
SLACK_REMOVE = "/integration-dialog-remove"
SLACK_SETTLED = "/integration-dialog-settled"
SLACK_REMOVING = "/integration-dialog-removing"
SLACK_REMOVED = "/integration-dialog-removed"
SCHEDULE = "/integration-dialog-schedule"
SCHEDULE_REVIEW = "/integration-dialog-schedule-review"
SCHEDULE_SETTLED = "/integration-dialog-schedule-settled"
APPLIED = "/integration-dialog-applied"
SLACK_KEY_REVIEW = "/integration-dialog-key-review"
SLACK_PENDING = "/integration-dialog-pending"
SLACK_STATUS = "/integration-dialog-status"
SLACK_STATUS_PENDING = "/integration-dialog-status-pending"
SLACK_STATUS_DONE = "/integration-dialog-status-done"
SLACK_KEYED = "/integration-dialog-keyed"
SLACK_UNSELECTED = "/integration-dialog-unselected"
SLACK_FINISH = "/integration-dialog-finish"
PARISHSOFT_KEY = "/integration-dialog-parishsoft-key"
PARISHSOFT_KEY_REVIEW = "/integration-dialog-parishsoft-key-review"
BACKUP_KEY = "/backup-key-dialog"
BACKUP_KEY_PROOF = "/backup-key-dialog-proof"
BACKUP_KEY_REFUSED = "/backup-key-dialog-refused"
BACKUP_KEY_WRONG = "/backup-key-dialog-wrong"
BACKUP_KEY_REVIEW = "/backup-key-dialog-review"
BACKUP_KEY_SETTLED = "/backup-key-dialog-settled"
OLD_FINGERPRINT = "0123456789abcdef"
NEW_FINGERPRINT = "fedcba9876543210"
POSTS = {
    BACKUP_KEY: (303, APPLIED, ""),
    SLACK: (303, APPLIED, ""),
    SLACK_REMOVING: (303, APPLIED, ""),
    SCHEDULE: (303, APPLIED, ""),
    SLACK_UNSELECTED: (303, APPLIED, ""),
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

    def slack(
        channel, digest, *, path, refresh, data=None, configured=True, summary=None
    ):
        """Slack's page at ``digest`` showing ``channel``, or bound to ``data``,
        with ``summary`` as its key change's status line (pending: no key
        field, the editor saying the page updates by itself)."""
        form = IntegrationForm(
            "slack",
            data,
            initial=None if data else {"base_digest": digest, "channel_id": channel},
        )
        if data:
            form.is_valid()
        pending = summary is not None and summary.kind == "pending"
        return page(
            "slack",
            "Slack notifications",
            form,
            refresh,
            page_url=path,
            configured=configured,
            removable=configured,
            summary=summary,
            pending=pending,
            status_url=SLACK_STATUS,
            credential=None
            if pending
            else InlineCredentialForm(
                "slack", initial={"intent": "synthetic-intent"}, required=not configured
            ),
        )

    def schedule(stored, digest, *, path=SCHEDULE, key=False):
        """ParishSoft's page with ``stored`` as its schedule, at ``digest``,
        with its new-key field when ``key``."""
        form = IntegrationForm(
            "parishsoft", initial=stored | {"base_digest": digest}, stored=stored
        )
        return page(
            "parishsoft",
            "ParishSoft",
            form,
            SCHEDULE_SETTLED,
            page_url=path,
            complete_gate=False,
            refresh_key=uuid4(),
            credential=InlineCredentialForm(
                "parishsoft", initial={"intent": "synthetic-intent"}
            )
            if key
            else None,
            **_schedule(form.schedule, now),
        )

    def status(summary):
        """The passive status line the page polls, with ``summary``."""
        return (
            "text/html",
            render_to_string(
                "stewardship/integration-status.html",
                {
                    "target": "slack",
                    "summary": summary,
                    "pending": summary.kind == "pending",
                    "follow_url": SLACK,
                    "page_url": SLACK,
                },
            ),
        )

    installing = CredentialSummary(
        "pending", now, "Checking the new key with Slack…", REQUEST
    )
    updated = CredentialSummary("updated", now, "Key updated.", REQUEST)
    unselected = CredentialSummary(
        "unselected",
        now,
        "A new key is installed, but switching to it did not finish.",
        REQUEST,
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

    def backup_key(fingerprint, **values):
        """The Backup encryption key page with ``fingerprint`` in use, at the
        step ``values`` draw (the paste step without a challenge)."""
        return (
            "text/html",
            render_to_string(
                "stewardship/backup-key.html",
                context
                | {
                    "admin_chrome": admin,
                    "label": "Backup encryption key",
                    "page_url": BACKUP_KEY,
                    "current": KeyStatus("configured", fingerprint),
                    "errors": [],
                }
                | values,
            ),
        )

    # The challenge step, as the server draws it after Continue.
    proof = {
        "new_fingerprint": NEW_FINGERPRINT,
        "challenge": "PKBKP1:" + "A" * 80,
        "intent": "synthetic-intent",
    }
    wrong = "That code does not match."
    refused = "This is not a backup public key."
    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    return {
        BACKUP_KEY: backup_key(OLD_FINGERPRINT),
        BACKUP_KEY_PROOF: backup_key(OLD_FINGERPRINT, **proof),
        BACKUP_KEY_REFUSED: backup_key(
            OLD_FINGERPRINT,
            key_error=refused,
            errors=[{"message": refused, "field_id": "backup-key-public"}],
        ),
        BACKUP_KEY_WRONG: backup_key(
            OLD_FINGERPRINT,
            **proof,
            code_error=wrong,
            errors=[{"message": wrong, "field_id": "backup-key-code"}],
        ),
        BACKUP_KEY_REVIEW: review(
            kind="backup_key",
            target="backup_key",
            label="Backup encryption key",
            changes=[
                {
                    "label": "Key fingerprint",
                    "before": OLD_FINGERPRINT,
                    "after": NEW_FINGERPRINT,
                }
            ],
        ),
        BACKUP_KEY_SETTLED: backup_key(NEW_FINGERPRINT),
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
        SLACK_KEY_REVIEW: review(
            kind="key", target="slack", label="Slack notifications", changes=[]
        ),
        SLACK_PENDING: slack(
            CHANNEL, "b" * 64, path=SLACK, refresh=SLACK, summary=installing
        ),
        SLACK_STATUS: status(installing),
        SLACK_STATUS_PENDING: status(installing),
        SLACK_STATUS_DONE: status(updated),
        SLACK_KEYED: slack(
            CHANNEL, "c" * 64, path=SLACK, refresh=SLACK, summary=updated
        ),
        SLACK_UNSELECTED: slack(
            CHANNEL,
            "a" * 64,
            path=SLACK_UNSELECTED,
            refresh=SLACK_SETTLED,
            summary=unselected,
        ),
        SLACK_FINISH: review(
            kind="finish",
            target="slack",
            label="Slack notifications",
            changes=[],
            request_id=REQUEST,
        ),
        PARISHSOFT_KEY: schedule(BASIC, "a" * 64, path=PARISHSOFT_KEY, key=True),
        PARISHSOFT_KEY_REVIEW: review(
            kind="key",
            target="parishsoft",
            label="ParishSoft",
            changes=[
                {
                    "label": "Expected ParishSoft organization ID",
                    "before": "12345",
                    "after": "54321",
                }
            ],
        ),
        APPLIED: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context | {"admin_chrome": admin, "receipt": applied},
            ),
        ),
    }
