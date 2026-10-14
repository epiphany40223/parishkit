"""``integration key status`` and Finish switching (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The key is
saved, installed and acknowledged as the settings page and its installer do
(test_integration_inline_keys_postgresql's ``working``), then another change
lands first so its own switch fails and Finish switching is needed. No
output may carry the key or a fingerprint.
"""

from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.db.backends.signals import connection_created

from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.accounts.key_files import file_fingerprint
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent

from . import test_admin_test_sample_cli_postgresql as sample_cli
from .automation_builders import as_staff, paired
from .role_grants import grant_runtime
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_integration_actions_cli_postgresql import staged
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
    failed_switch,
    install,
    working,
)
from .test_integration_views_postgresql import handoff, hidden, post  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_integration_key_finish_switching"


@contextmanager
def existing_web_login(service, **_options):
    """The web login the key fixture's ``isolated_roles`` already created.

    ``runner`` creates its own exact ``pk_stewardship_web`` role, which the
    page's key fixture already owns (and drops, with everything granted to
    it, when the test ends), so the commands run as that role instead, with
    the web's full runtime grants, as ``task_login`` gives its own.
    """
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.runtime_grants import (
        admit_columns,
        runtime_functions,
        runtime_grants,
    )

    assert service == ServiceRole.WEB
    tables, columns = runtime_grants(service)

    def restrict(sender, connection, **kwargs):
        """Keep every new connection on the web login."""
        with connection.cursor() as cursor:
            cursor.execute('SET SESSION AUTHORIZATION "pk_stewardship_web"')

    with connection.cursor() as cursor:
        grant_runtime(
            cursor, "pk_stewardship_web", tables, columns, runtime_functions(service)
        )
        cursor.execute('SET SESSION AUTHORIZATION "pk_stewardship_web"')
    connection_created.connect(restrict, weak=False)
    try:
        admit_columns(connection, tables, columns)
        yield
    finally:
        connection_created.disconnect(restrict)
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@pytest.fixture
def keys(working, monkeypatch):  # noqa: F811
    """The command line over the same service as the signed-in page."""
    monkeypatch.setattr(sample_cli, "task_login", existing_web_login)
    run = runner(working["service"], monkeypatch)
    outputs = []

    def admin(*argv, secret, answer=b""):
        """Run one command, keeping its output for the no-secret check."""
        code, document, errors = run(*argv, secret=secret, answer=answer)
        outputs.append(repr(document) + errors)
        return code, document, errors

    admin.service = working["service"]
    admin.working = working
    yield admin
    # Nothing printed, on standard output or error, names a key or one of
    # its fingerprints.
    secrets = [
        CANDIDATE.decode(),
        PRIOR.decode(),
        file_fingerprint(CANDIDATE),
        file_fingerprint(PRIOR),
    ]
    for output in outputs:
        for secret in secrets:
            assert secret not in output


def status(admin, secret, *extra):
    """Run ``integration key status parishsoft``."""
    return admin("integration", "key", "status", "parishsoft", *extra, secret=secret)


def preview(admin, secret, target="parishsoft"):
    """Run ``integration key finish-switching-preview``."""
    return admin(
        "integration", "key", "finish-switching-preview", target, secret=secret
    )


def finish(admin, secret, token, *, answer=None, target="parishsoft"):
    """Run ``integration key finish-switching``; ``--yes`` unless answered."""
    extra = () if answer is not None else ("--yes",)
    return admin(
        "integration",
        "key",
        "finish-switching",
        target,
        "--token",
        token,
        *extra,
        secret=secret,
        answer=answer or b"",
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


def test_a_failed_switch_is_shown_reviewed_and_finished(keys):
    """The status page's offer, the page's review of the switch, a prompt
    naming only the setting, and the request with its trail; a repeat
    returns the same request and records nothing new."""
    row = failed_switch(keys.working)
    _, secret, _ = paired(keys.service)
    code, document, _ = status(keys, secret)
    assert code == 0, document
    assert document["result"] == {
        "target": "parishsoft",
        "request_id": str(row.pk),
        "state": "applied",
        "pending": False,
        "selectable": True,
    }
    count = ConfigurationChangeRequest.objects.count()
    code, document, _ = preview(keys, secret)
    assert code == 0, document
    result = document["result"]
    assert result["request_id"] == str(row.pk) and result["name"] == "ParishSoft"
    assert result["version"] == keys.service.store.active().digest
    assert result["changes"] == [
        {"field": "organization_id", "before": "12345", "after": "54321"}
    ]
    assert ConfigurationChangeRequest.objects.count() == count
    assert trail() == (0, 0, 0, 0)

    token = result["preview"]["token"]
    code, document, errors = finish(keys, secret, token, answer=b"no\n")
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert "organization_id" in errors and "54321" not in errors
    assert ConfigurationChangeRequest.objects.count() == count
    code, confirmed, _ = finish(keys, secret, token, answer=b"yes\n")
    assert code == 0 and confirmed["result"]["created"], confirmed
    assert trail() == (1, 1, 1, 1)
    event = AuditEvent.objects.get(event_type=EVENT)
    assert event.subject_id is not None
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    assert install(keys.working, request_id).state == "applied"
    values = _record(keys.working)["values"]
    assert values["credential_fingerprint"] == file_fingerprint(CANDIDATE)
    assert values["settings"]["organization_id"] == "54321"

    code, again, _ = finish(keys, secret, token)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == str(request_id)
    assert trail() == (1, 1, 1, 1)
    # Switched: no longer offered, and nothing is left to review.
    code, document, _ = status(keys, secret)
    assert code == 0 and document["result"]["selectable"] is False
    code, document, _ = preview(keys, secret)
    assert code == 1
    refused(document, "not_available")


def test_a_token_from_the_page_is_confirmed_by_the_command(keys):
    """Tokens are bound to the Administrator, not the channel."""
    row = failed_switch(keys.working)
    _, secret, _ = paired(keys.service)
    page = keys.working["browser"].get(f"/admin/system/key-changes/{row.pk}/selection/")
    code, confirmed, _ = finish(keys, secret, hidden(page, "preview"))
    assert code == 0 and confirmed["result"]["created"], confirmed
    assert trail() == (1, 1, 1, 1)


def test_an_altered_or_other_pages_token_is_invalid(keys):
    """An altered token, or one an integration settings page signed."""
    from django.core import signing

    from parishkit.stewardship.accounts.integration_views import SALT

    failed_switch(keys.working)
    _, secret, _ = paired(keys.service)
    code, document, _ = preview(keys, secret)
    token = document["result"]["preview"]["token"]
    # Signed for the ParishSoft settings page, not for Finish switching.
    other = signing.dumps({"patch": [{"values": {}}]}, salt=SALT + "parishsoft")
    for bad in (token[:-2] + "xx", other):
        code, document, _ = finish(keys, secret, bad)
        assert code == 1
        refused(document, "invalid")
    assert trail() == (0, 0, 0, 0)


def test_nothing_to_switch_is_not_available(keys):
    """No key change waits for a switch: no status of one's own, no review,
    and a key change still running is pending, with no offer."""
    _, secret, row = paired(keys.service)
    for code, document, _ in (status(keys, secret), preview(keys, secret)):
        assert code == 1
        refused(document, "not_available")
    request_id = staged(row)
    code, document, _ = status(keys, secret)
    assert code == 0, document
    assert document["result"]["request_id"] == str(request_id)
    assert document["result"]["state"] == "staged"
    assert document["result"]["pending"] and not document["result"]["selectable"]
    code, document, _ = preview(keys, secret)
    assert code == 1
    refused(document, "not_available")
    # Another integration's, or an unknown, key change is the page's 404.
    for target, wanted in (("slack", request_id), ("parishsoft", uuid4())):
        code, document, _ = keys(
            "integration",
            "key",
            "status",
            target,
            "--request-id",
            str(wanted),
            secret=secret,
        )
        assert code == 1
        refused(document, "not_available")


def test_a_changed_loaded_organization_is_invalid(keys, monkeypatch):
    """The page refuses a switch that would change the loaded organization."""
    from parishkit.stewardship.accounts import integration_selection

    failed_switch(keys.working)
    monkeypatch.setattr(integration_selection, "loaded_organization", lambda: 12345)
    _, secret, _ = paired(keys.service)
    code, document, _ = preview(keys, secret)
    assert code == 1
    refused(document, "invalid")
    [problem] = document["error"]["fields"]
    assert problem["field"] == "organization_id"
    assert "organization ID can't change" in problem["message"]


def test_refused_sessions(keys, monkeypatch):
    """A read-only session reads the status but cannot review or switch;
    Staff is denied; an ended session is exit 5; nothing is recorded."""
    failed_switch(keys.working)
    _, secret, _ = paired(keys.service)
    code, document, _ = preview(keys, secret)
    token = document["result"]["preview"]["token"]
    _, reader, _ = paired(keys.service, scope="read-only")
    code, document, _ = status(keys, reader)
    assert code == 0 and document["result"]["selectable"]
    for code, document, _ in (preview(keys, reader), finish(keys, reader, token)):
        assert code == 1
        refused(document, "denied")
    _, ended, _ = paired(keys.service)
    revoke(keys.service, ended)
    for code, document, _ in (
        status(keys, ended),
        preview(keys, ended),
        finish(keys, ended, token),
    ):
        assert code == 5
        refused(document, "session_ended")
    _, staff, _ = paired(keys.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        status(keys, staff),
        preview(keys, staff),
        finish(keys, staff, token),
    ):
        assert code == 1
        refused(document, "denied")
    assert trail()[0] == 0
