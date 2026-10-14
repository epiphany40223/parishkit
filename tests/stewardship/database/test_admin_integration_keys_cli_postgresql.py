"""``integration key status`` and Finish switching (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The key is
saved, installed and acknowledged as the settings page and its installer do
(test_integration_inline_keys_postgresql's ``working``), then another change
lands first so its own switch fails and Finish switching is needed. No
output field may carry the key or a fingerprint; ``preview.token`` is the
page's own signed (not encrypted) binding, which embeds the reviewed patch
with the new key's fingerprint, as the page's hidden field does, so it is
decoded and checked on its own.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core import signing
from django.db import connection
from django.db.backends.signals import connection_created

from parishkit.stewardship.accounts import integration_selection
from parishkit.stewardship.accounts import integration_selection_views as selection
from parishkit.stewardship.accounts.admin_editing import (
    editable_configuration,
    sign_preview,
)
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

    These grants are a superset of production's web login: ``isolated_roles``
    also grants the role what the credential tests need (for example the
    setup install table and the fresh-principal function). The commands'
    own queries read only what the web login holds, so this is not an
    exact-grant check; the page's code runs on the same role either way.
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
    outputs, tokens = [], []

    def admin(*argv, secret, answer=b""):
        """Run one command, keeping its output for the no-secret check.

        A review's ``preview.token`` is decoded (it is signed, not
        encrypted) and kept apart: it embeds the page's patch, fingerprint
        included, by design; everything else printed is checked as is.
        """
        code, document, errors = run(*argv, secret=secret, answer=answer)
        result = document.get("result") or {}
        printed = repr(document)
        if "preview" in result:
            token = result["preview"]["token"]
            tokens.append(
                signing.loads(token, salt=selection.SALT + result["request_id"])
            )
            printed = printed.replace(token, "<token>")
        outputs.append(printed + errors)
        return code, document, errors

    admin.service = working["service"]
    admin.working = working
    admin.tokens = tokens
    yield admin
    # Nothing printed, on standard output or error, names a key or one of
    # its fingerprints, outside the page's own signed token.
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
    """Run ``integration key finish-switching``; ``--yes`` unless answered
    (always for ``--token -``, whose standard input is the token)."""
    extra = ("--yes",) if answer is None or token == "-" else ()
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

    # The token is the page's binding: its patch selects the new key by
    # fingerprint, as the page's hidden field does; never the key itself.
    [payload] = keys.tokens
    patch = repr(payload["patch"])
    assert file_fingerprint(CANDIDATE) in patch and CANDIDATE.decode() not in patch
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
    # Read from standard input, as the guide recommends, so the token stays
    # out of the argument list.
    token = hidden(page, "preview")
    code, confirmed, _ = finish(keys, secret, "-", answer=token.encode())
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


def staged_by(actor_id, target="parishsoft"):
    """Stage a synthetic key change (no key) for ``actor_id``; return its id."""
    from datetime import timedelta

    from django.utils import timezone

    from parishkit.stewardship.accounts.secret_requests import stage_secret_request

    request_id = uuid4()
    stage_secret_request(
        request_id=request_id,
        target=target,
        staging_reference=uuid4(),
        actor_id=actor_id,
        reauthenticated_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint="a" * 64,
        correlation_id=uuid4(),
    )
    return request_id


def test_another_administrators_key_change_is_not_available(keys):
    """Status reads only one's own key changes, latest or named."""
    _, secret, _ = paired(keys.service)
    other = staged_by(uuid4())
    for extra in ((), ("--request-id", str(other))):
        code, document, _ = status(keys, secret, *extra)
        assert code == 1
        refused(document, "not_available")
        assert str(other) not in repr(document)


def test_another_administrators_token_is_denied(keys):
    """A token signed for someone else, with the page's own salt."""
    failed_switch(keys.working)
    _, secret, _ = paired(keys.service)
    code, document, _ = preview(keys, secret)
    result = document["result"]
    other = sign_preview(
        actor=SimpleNamespace(identity=uuid4()),
        configuration=editable_configuration(keys.service),
        patch=keys.tokens[0]["patch"],
        salt=selection.SALT + result["request_id"],
    )
    code, document, _ = finish(keys, secret, other)
    assert code == 1
    refused(document, "denied")
    assert trail() == (0, 0, 0, 0)


def test_an_expired_token_is_stale(keys, monkeypatch):
    """Past the page's fifteen minutes, review again."""
    from parishkit.stewardship.web import refusals

    failed_switch(keys.working)
    _, secret, _ = paired(keys.service)
    code, document, _ = preview(keys, secret)
    monkeypatch.setattr(refusals, "PREVIEW_MAX_AGE", -1)
    code, document, _ = finish(keys, secret, document["result"]["preview"]["token"])
    assert code == 1
    refused(document, "stale_version")
    assert trail() == (0, 0, 0, 0)


def test_a_token_for_an_earlier_key_change_is_invalid(keys):
    """A newer key change started since the review: the token's salt names
    the earlier one, so it no longer confirms."""
    failed_switch(keys.working)
    _, secret, row = paired(keys.service)
    code, document, _ = preview(keys, secret)
    token = document["result"]["preview"]["token"]
    staged(row)
    code, document, _ = finish(keys, secret, token)
    assert code == 1
    refused(document, "invalid")
    assert trail() == (0, 0, 0, 0)


def test_a_key_no_longer_current_is_stale_on_review(keys, monkeypatch):
    """The page's receipt proof refuses a key that is no longer current."""
    failed_switch(keys.working)

    def stale(*_args):
        """The page's refusal of a receipt that is no longer current."""
        raise integration_selection.StaleCredentialReceipt("Not current.")

    monkeypatch.setattr(selection, "current_receipt", stale)
    _, secret, _ = paired(keys.service)
    code, document, _ = preview(keys, secret)
    assert code == 1
    refused(document, "stale_version")


def test_a_loaded_organization_found_on_confirm_is_invalid(keys, monkeypatch):
    """Reviewed before the first load, confirmed after it: the confirmation
    repeats the page's check under the lock and refuses at
    ``organization_id`` (the page answers a 503 refusal page instead)."""
    failed_switch(keys.working)
    _, secret, _ = paired(keys.service)
    code, document, _ = preview(keys, secret)
    assert code == 0, document
    monkeypatch.setattr(integration_selection, "loaded_organization", lambda: 12345)
    code, document, _ = finish(keys, secret, document["result"]["preview"]["token"])
    assert code == 1
    refused(document, "invalid")
    [problem] = document["error"]["fields"]
    assert problem["field"] == "organization_id"
    assert trail()[0] == 0
