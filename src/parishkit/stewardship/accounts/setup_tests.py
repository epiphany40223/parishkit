"""A setup test's own inputs, signed for its page and checked again on send.

Test email and Test Slack each prove one staged credential with the public
settings sealed with it, and nothing else (#1578): the email test the Google
Workspace key with the outgoing mail and Testing settings, the Slack test
the bot token with the channel. A test page signs that binding (never the
whole Finish setup preview, which needs the parish data load and every step), and
the send repeats it, so a test can be sent before the later steps exist and
an accepted one stays accepted while its own inputs are unchanged. The SQL
live checks and readiness guard enforce the same binding.
"""

import hashlib
import json
from dataclasses import dataclass, field

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import load_preview

from .setup_drafts import DraftView, view_draft
from .setup_secret_models import SetupSealedCredential
from .setup_wizard import TESTS, staged_scope

TEST_SALT = "stewardship-initial-setup-test-v1"
# The saved steps each test reads besides its credential: the email test
# names the parish and its From name (#142).
SECTIONS = {"mail_test": ("parish", "mail", "testing"), "slack_test": ("slack",)}


@dataclass(frozen=True)
class SetupTestInputs:
    """One test's admitted draft and the current staged credential it proves."""

    draft: DraftView = field(repr=False)
    credential: SetupSealedCredential = field(repr=False)

    def binding(self):
        """The signed evidence: this attempt and the exact staged credential."""
        return {
            "attempt": str(self.draft.status.attempt_id),
            "credential": str(self.credential.pk),
            "version": self.credential.version,
            "fingerprint": self.credential.fingerprint,
        }

    @property
    def digest(self):
        """What the test proves, stored as the delivery's ``candidate_digest``.

        The binding plus the public settings sealed with the credential;
        before #1578 the column held the whole Review candidate's digest.
        """
        value = self.binding() | {"settings": dict(self.credential.settings)}
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def staged_credential(draft, key):
    """The current staged credential the test step ``key`` would use, or None.

    None when it is missing, scrubbed or sealed for settings changed since
    (the step that stages it is then not done). Only non-secret receipt
    columns are read.
    """
    expected = staged_scope(draft.sections)[TESTS[key]]
    credential = (
        SetupSealedCredential.objects.only("id", "version", "fingerprint", "settings")
        .filter(attempt_id=draft.status.attempt_id, target=TESTS[key], scrubbed_at=None)
        .first()
    )
    if not expected or credential is None or credential.settings != expected:
        return None
    return credential


def prepare_test(request, service, key):
    """Admit the original owner's collecting draft and the test's own inputs.

    A missing step or a stale credential is a ConfigError, which the page
    explains as "complete the earlier setup steps first" with the stepper
    naming the step that fixes it. Slack off is a ValueError: Test Slack
    does not apply then.
    """
    with work_transaction():
        draft = view_draft(request, service)
        if draft is None or draft.status.state != "collecting":
            raise LookupError("An original collecting setup draft is required.")
        if key == "slack_test" and not draft.sections.get("slack", {}).get("enabled"):
            raise ValueError("Slack is disabled for this setup.")
        credential = staged_credential(draft, key)
        if credential is None or not draft.sections.keys() >= set(SECTIONS[key]):
            raise ConfigError("Complete the earlier setup steps first.")
        return SetupTestInputs(draft, credential)


def verify_test(request, service, key, token):
    """A signature never replaces fresh session, draft and credential checks."""
    if type(token) is not str or len(token) > 4096:
        raise ValueError("Invalid setup test.")
    binding = load_preview(token, salt=TEST_SALT, link=request.path)
    current = prepare_test(request, service, key)
    if current.binding() != binding:
        raise StaleRecordError("Setup changed; open the test page again.")
    return current
