"""``integration key replace`` (ADM-11 PR 10): a settings page's Save with a key.

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, over the
same isolated credential roles and installer as the settings page's tests
(test_integration_inline_keys_postgresql's ``working`` and
test_integration_slack_setup_postgresql's ``slack``). The key arrives as the
host wrapper sends it, on the line after the preamble; it is staged, checked
by the target's installer, acknowledged and switched to exactly as from the
page. Nothing printed, logged or recorded in the audit trail may carry the
key, and no output may carry its fingerprint.
"""

import base64
import json
import logging
from uuid import UUID

import pytest

from parishkit.stewardship.accounts.automation_models import (
    AutomationNotice,
    AutomationSession,
)
from parishkit.stewardship.accounts.integration_credentials import selection_key
from parishkit.stewardship.accounts.key_files import file_fingerprint
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.audit.models import AuditEvent

from . import test_admin_test_sample_cli_postgresql as sample_cli
from .automation_builders import as_staff, paired
from .test_admin_integration_keys_cli_postgresql import (  # noqa: F401
    existing_web_login,
    keys,
)
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_configuration_service_postgresql import (  # noqa: F401
    as_config_installer,
    config_role,
)
from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401
from .test_integration_inline_keys_postgresql import (  # noqa: F401
    CANDIDATE,
    PRIOR,
    _record,
    install,
    installed,
    working,
)
from .test_integration_slack_setup_postgresql import (  # noqa: F401
    TOKEN,
    apply,
    install_key,
    records,
    slack,
)
from .test_integration_views_postgresql import handoff  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_integration_key_replace"


def line(value):
    """The key line the wrapper sends after the preamble."""
    return b"pk-admin-secret/1 " + base64.b64encode(value) + b"\n"


def replace(admin, secret, *, key=CANDIDATE, answer=b"yes\n", target="parishsoft"):
    """Run ``integration key replace``; extra options follow ``target``.

    ``target`` may be a tuple: the integration then its options. Without an
    ``answer`` it passes ``--yes``.
    """
    words = target if isinstance(target, tuple) else (target,)
    version = admin.service.store.active().digest
    extra = () if answer else ("--yes",)
    return admin(
        "integration",
        "key",
        "replace",
        *words,
        "--expected-version",
        getattr(admin, "version", None) or version,
        "--secret-stdin",
        *extra,
        secret=secret,
        answer=(b"" if key is None else line(key)) + (answer or b""),
    )


def refused(document, code):
    """The error document's code is ``code``, with no result."""
    assert document["error"]["code"] == code, document
    assert "result" not in document


def trail():
    """The command events, fresh-gate events and notices by kind so far."""
    return (
        AuditEvent.objects.filter(event_type=EVENT).count(),
        AuditEvent.objects.filter(event_type="automation_fresh_gate").count(),
        AutomationNotice.objects.filter(kind="fresh_gated").count(),
        AutomationNotice.objects.filter(kind="policy_change").count(),
    )


def no_key_in_audit(*values):
    """No audit event, audit context or operational log row holds a key."""
    from parishkit.stewardship.audit.models import AuditContext, OperationalLog

    for model in (AuditEvent, AuditContext, OperationalLog):
        text = json.dumps(list(model.objects.values()), default=str)
        for value in values:
            assert value.decode() not in text, model.__name__


def test_a_key_is_staged_installed_and_switched_with_its_trail(keys, caplog):  # noqa: F811
    """The page's own staging, then its installer, acknowledgement and
    switch; the command's event, the fresh gate and the policy notice; no
    key anywhere and no fingerprint in the output."""
    caplog.set_level(logging.DEBUG)
    _, secret, row = paired(keys.service)
    code, document, errors = replace(keys, secret)
    assert code == 0, document
    result = document["result"]
    staged = SecretReplacementRequest.objects.get()
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(staged.pk)
    )
    assert result["target"] == "parishsoft" and result["name"] == "ParishSoft"
    assert result["setup"] is False and result["changes"] == []
    assert result["key_request_id"] == str(staged.pk)
    assert result["request"]["request_id"] == str(selection.pk)
    assert result["version"] == selection.base.digest
    # The prompt named the integration, never the key.
    assert "This replaces the ParishSoft key" in errors
    # Staged as the session's Administrator, with the automation sign-in
    # instant standing in for the page's fresh sign-in, and naming the
    # session the guards require live (#1763).
    session = AutomationSession.objects.get(pk=row.pk)
    assert staged.requested_by_id == session.principal_id
    assert staged.reauthenticated_at == session.authenticated_at
    assert staged.automation_session_id == row.pk
    assert staged.expected_fingerprint == file_fingerprint(PRIOR)
    assert trail() == (1, 1, 1, 1)
    event = AuditEvent.objects.get(event_type=EVENT)
    assert event.subject_id == row.pk
    assert selection.patch[0]["values"] == {
        "credential_fingerprint": file_fingerprint(CANDIDATE)
    }
    # The installer, then the consumer, then the selection: as for the page.
    assert installed(keys.working).state == "applied"
    assert install(keys.working, selection.pk).state == "applied"
    values = _record(keys.working)["values"]
    assert values["credential_fingerprint"] == file_fingerprint(CANDIDATE)
    no_key_in_audit(CANDIDATE)
    assert CANDIDATE.decode() not in caplog.text
    assert file_fingerprint(CANDIDATE) not in caplog.text


def test_a_declined_prompt_stages_nothing(keys):  # noqa: F811
    """No answer, or not yes: exit 4, nothing staged or recorded."""
    _, secret, _ = paired(keys.service)
    for answer in (b"no\n", b"\n"):
        code, document, _ = replace(keys, secret, answer=answer)
        assert code == 4
        refused(document, "confirmation_required")
    assert not SecretReplacementRequest.objects.exists()
    assert not ConfigurationChangeRequest.objects.exists()
    assert trail() == (0, 0, 0, 0)


def test_settings_are_saved_with_the_key(keys):  # noqa: F811
    """--changes saves the page's settings with the key it is checked with."""
    _, secret, _ = paired(keys.service)
    changes = json.dumps({"settings": {"organization_id": "54321"}})
    code, document, errors = replace(
        keys, secret, target=("parishsoft", "--changes", changes)
    )
    assert code == 0, document
    assert document["result"]["changes"] == [
        {"field": "organization_id", "before": "12345", "after": "54321"}
    ]
    assert "It also changes the ParishSoft settings: organization_id." in errors
    assert "54321" not in errors
    staged = SecretReplacementRequest.objects.get()
    selection = ConfigurationChangeRequest.objects.get(
        request_key=selection_key(staged.pk)
    )
    assert selection.patch[0]["values"]["settings"]["organization_id"] == "54321"


def test_refused_settings_and_keys_are_invalid_at_their_fields(keys, monkeypatch):  # noqa: F811
    """The page's form errors at settings.<name>; an empty, NUL-holding or
    too long key at ``key``, never quoting it; nothing is staged."""
    _, secret, _ = paired(keys.service)
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.integration_views.loaded_organization",
        lambda: 12345,
    )
    changes = json.dumps({"settings": {"organization_id": "54321"}})
    code, document, _ = replace(
        keys, secret, target=("parishsoft", "--changes", changes)
    )
    assert code == 1
    refused(document, "invalid")
    [problem] = document["error"]["fields"]
    assert problem["field"] == "settings.organization_id"
    for value, code_word in (
        (b"   \n", "required"),
        (b"bad\x00key", "invalid"),
        (b"k" * (128 * 1024 + 1), "invalid"),
    ):
        code, document, _ = replace(keys, secret, key=value)
        assert code == 1, document
        refused(document, "invalid")
        [problem] = document["error"]["fields"]
        assert problem["field"] == "key" and problem["code"] == code_word
        assert "bad" not in problem["message"]
    assert not SecretReplacementRequest.objects.exists()
    assert trail() == (0, 0, 0, 0)


def test_a_malformed_key_line_is_a_usage_error(keys):  # noqa: F811
    """Not the wrapper's line: exit 2, nothing read further or staged."""
    _, secret, _ = paired(keys.service)
    code, document, _ = keys(
        "integration",
        "key",
        "replace",
        "parishsoft",
        "--expected-version",
        keys.service.store.active().digest,
        "--secret-stdin",
        "--yes",
        secret=secret,
        answer=CANDIDATE + b"\n",
    )
    assert code == 2
    refused(document, "usage")
    assert not SecretReplacementRequest.objects.exists()


def test_a_pending_key_change_and_a_stale_version_are_refused(keys):  # noqa: F811
    """A second key while the first is checked is not_available; another
    version is stale_version; neither stages anything."""
    _, secret, _ = paired(keys.service)
    code, document, _ = replace(keys, secret, answer=None)
    assert code == 0, document
    code, document, _ = replace(keys, secret, answer=None)
    assert code == 1
    refused(document, "not_available")
    assert SecretReplacementRequest.objects.count() == 1
    keys.version = "f" * 64
    code, document, _ = replace(keys, secret, answer=None)
    assert code == 1
    refused(document, "stale_version")
    assert trail() == (1, 1, 1, 1)


def test_refused_sessions_read_no_key(keys, monkeypatch):  # noqa: F811
    """Staff is denied before the key line is read (no line at all is still
    denied, unprompted); a read-only session is refused; an ended session
    is exit 5; nothing is staged."""
    _, reader, _ = paired(keys.service, scope="read-only")
    code, document, _ = replace(keys, reader)
    assert code == 1
    refused(document, "denied")
    _, ended, _ = paired(keys.service)
    revoke(keys.service, ended)
    code, document, _ = replace(keys, ended)
    assert code == 5
    refused(document, "session_ended")
    _, staff, _ = paired(keys.service)
    as_staff(monkeypatch)
    code, document, errors = replace(keys, staff, key=None, answer=b"")
    assert code == 1
    refused(document, "denied")
    assert "This " not in errors and "Type yes" not in errors
    assert not SecretReplacementRequest.objects.exists()
    assert trail() == (0, 0, 0, 0)


@pytest.fixture
def slack_admin(slack, monkeypatch):  # noqa: F811
    """The command line over the Slack page's service and installer."""
    monkeypatch.setattr(sample_cli, "task_login", existing_web_login)
    run = runner(slack["service"], monkeypatch)
    outputs = []

    def admin(*argv, secret, answer=b""):
        """Run one command, keeping its output for the no-key check."""
        code, document, errors = run(*argv, secret=secret, answer=answer)
        outputs.append(repr(document) + errors)
        return code, document, errors

    admin.service = slack["service"]
    yield admin
    for output in outputs:
        assert TOKEN.decode() not in output
        assert file_fingerprint(TOKEN) not in output


def test_slack_is_set_up_by_its_first_key(slack, slack_admin):  # noqa: F811
    """Slack not set up: the key and its channel add it, as on the page."""
    _, secret, _ = paired(slack["service"])
    code, document, _ = replace(
        slack_admin, secret, key=TOKEN, answer=None, target=("slack",)
    )
    # The channel is required to set Slack up, as on the page.
    assert code == 1
    refused(document, "invalid")
    assert [problem["field"] for problem in document["error"]["fields"]] == [
        "settings.channel_id"
    ]
    changes = json.dumps({"settings": {"channel_id": "C0123ABC"}})
    code, document, errors = replace(
        slack_admin,
        secret,
        key=TOKEN,
        answer=b"yes\n",
        target=("slack", "--changes", changes),
    )
    assert code == 0, document
    result = document["result"]
    assert result["setup"] is True
    assert "This sets up Slack notifications with the key given." in errors
    staged = SecretReplacementRequest.objects.get(pk=UUID(result["key_request_id"]))
    install_key(slack, staged)
    assert apply(slack, result["request"]["request_id"]).state == "applied"
    record = records(slack)["slack"]["values"]
    assert record["settings"]["channel_id"] == "C0123ABC"
    assert record["credential_fingerprint"] == file_fingerprint(TOKEN)
    no_key_in_audit(TOKEN)


def test_a_slack_token_in_another_format_is_invalid_before_the_prompt(
    slack,  # noqa: F811
    slack_admin,
):
    """The page's own format check, before anything is asked or staged."""
    _, secret, _ = paired(slack["service"])
    changes = json.dumps({"settings": {"channel_id": "C0123ABC"}})
    code, document, errors = replace(
        slack_admin,
        secret,
        key=b"xoxb-with a space",
        target=("slack", "--changes", changes),
    )
    assert code == 1
    refused(document, "invalid")
    assert document["error"]["fields"][0]["field"] == "key"
    assert "Type yes" not in errors
    assert not SecretReplacementRequest.objects.exists()
