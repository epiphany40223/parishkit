"""``integration backup-key challenge/preview/confirm`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The public
key and the proof code arrive as the host wrapper sends them, on the line
after the preamble. The commands run the Backup encryption key page's own
checks, proof, review and confirmation, and the configuration installer
applies the change with its ``backup_key_replaced`` security event, as for
the page. No private key, code or pasted text may appear in any output, log
or audit row.
"""

import base64
import json
import logging
from uuid import UUID, uuid4

import pytest
from django.core import signing

from parishkit.stewardship import backup_sealing
from parishkit.stewardship.accounts import backup_key
from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditContext, AuditEvent, OperationalLog

from .automation_builders import as_staff, paired
from .campaign_builders import change
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner
from .test_backup_key_postgresql import configured, keypair, private_key_text
from .test_backup_postgresql import recorded

pytestmark = pytest.mark.django_db(transaction=True)
EVENT = "admin_cmd_integration_backup_key_confirm"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on (no mail)."""
    return runner(auth_service, monkeypatch)


def line(tag, value):
    """The tagged line the wrapper sends after the preamble."""
    return tag + b" " + base64.b64encode(value) + b"\n"


def challenge(admin, secret, public):
    """Run ``integration backup-key challenge`` with ``public`` as its line."""
    body = b"" if public is None else line(b"pk-admin-public-key/1", public.encode())
    return admin(
        "integration",
        "backup-key",
        "challenge",
        "--public-key-stdin",
        secret=secret,
        answer=body,
    )


def preview(admin, secret, intent, code):
    """Run ``integration backup-key preview`` with ``code`` as its line."""
    body = b"" if code is None else line(b"pk-admin-proof/1", code.encode())
    return admin(
        "integration",
        "backup-key",
        "preview",
        "--intent",
        intent,
        "--code-stdin",
        secret=secret,
        answer=body,
    )


def confirm(admin, secret, token, *, answer=None):
    """Run ``integration backup-key confirm``; ``--yes`` unless answered."""
    extra = () if answer is not None else ("--yes",)
    return admin(
        "integration",
        "backup-key",
        "confirm",
        "--token",
        token,
        *extra,
        secret=secret,
        answer=answer or b"",
    )


def proved(admin, secret, private, public):
    """Challenge, open the proof with ``private`` and preview: the review."""
    code, document, _ = challenge(admin, secret, public)
    assert code == 0, document
    result = document["result"]
    answer = backup_sealing.open_proof(result["challenge"], private)
    code, review, _ = preview(
        admin, secret, result["intent"], backup_sealing.display_code(answer).lower()
    )
    assert code == 0, review
    return result, review["result"]


def refused(document, code):
    """The error document's code is ``code``; return its field names."""
    assert document["error"]["code"] == code, document
    assert "result" not in document
    return [problem["field"] for problem in document["error"].get("fields", [])]


def trail():
    """The command events, fresh-gate events and notices by kind so far."""
    return (
        AuditEvent.objects.filter(event_type=EVENT).count(),
        AuditEvent.objects.filter(event_type="automation_fresh_gate").count(),
        AutomationNotice.objects.filter(kind="fresh_gated").count(),
        AutomationNotice.objects.filter(kind="policy_change").count(),
    )


def nowhere(texts, *values):
    """None of ``values`` is in the outputs, the logs or the audit tables."""
    stored = [
        json.dumps(list(model.objects.values()), default=str)
        for model in (AuditEvent, AuditContext, OperationalLog)
    ]
    for value in values:
        for text in [*texts, *stored]:
            assert value not in text


def test_a_key_is_proved_reviewed_confirmed_and_announced(admin, caplog):
    """The page's three steps: the challenge names the key in use and the
    new one; a wrong code is refused at ``code``; the review shows both
    fingerprints; the declined prompt changes nothing; the confirmation
    records the request with its trail; the installer applies it and every
    Administrator is told. No code, private key or pasted text anywhere."""
    caplog.set_level(logging.DEBUG)
    _, secret, _ = paired(admin.service)
    recorded(recipient_fingerprint="d" * 16)
    private, public = keypair()
    fingerprint = backup_sealing.parse_public_key(public).fingerprint
    outputs = []

    code, document, errors = challenge(admin, secret, public)
    outputs += [json.dumps(document), errors]
    assert code == 0, document
    result = document["result"]
    assert result["version"] == admin.service.store.active().digest
    assert result["current"]["source"] == "installed"
    assert result["current"]["fingerprint"] == "d" * 16
    assert result["current"]["backup_at"] is not None
    assert result["fingerprint"] == fingerprint
    assert result["challenge"].startswith("PKBKP1:")
    proof = backup_sealing.open_proof(result["challenge"], private)
    # The intent hides the pasted key, as the page's hidden field does.
    assert public not in json.dumps(
        signing.loads(result["intent"], salt=backup_key.PROOF_SALT)
    )

    code, document, errors = preview(admin, secret, result["intent"], "0000-0000-0000")
    outputs += [json.dumps(document), errors]
    assert code == 1 and refused(document, "invalid") == ["code"]
    assert "does not match" in document["error"]["fields"][0]["message"]
    # The same intent can be answered again; the code is read leniently.
    typed = backup_sealing.display_code(proof).lower()
    code, document, errors = preview(admin, secret, result["intent"], f" {typed}\n")
    outputs += [json.dumps(document), errors]
    assert code == 0, document
    review = document["result"]
    assert review["changes"] == [
        {"field": "fingerprint", "before": "d" * 16, "after": fingerprint}
    ]
    assert not ConfigurationChangeRequest.objects.exists()
    assert trail() == (0, 0, 0, 0)

    token = review["preview"]["token"]
    for answer in (b"\n", b"no\n"):
        code, document, errors = confirm(admin, secret, token, answer=answer)
        outputs += [json.dumps(document), errors]
        assert code == 4 and refused(document, "confirmation_required") == []
        assert f"fingerprint {'d' * 16} becomes {fingerprint}" in errors
    assert not ConfigurationChangeRequest.objects.exists()
    code, document, errors = confirm(admin, secret, token, answer=b"yes\n")
    outputs += [json.dumps(document), errors]
    assert code == 0 and document["result"]["created"], document
    assert trail() == (1, 1, 1, 1)
    request_id = UUID(document["result"]["request"]["request_id"])
    request = ConfigurationChangeRequest.objects.get()
    assert request.pk == request_id
    assert AuditEvent.objects.filter(
        event_type="config_request_staged", subject_id=request.pk
    ).exists()
    install_request(admin.service.store, request_id=request_id, correlation_id=uuid4())
    assert configured(admin.service.store) == [{"public_key": public}]
    [event] = PolicySecurityEvent.objects.filter(kind="backup_key_replaced")
    assert (event.before_roles, event.after_roles) == (["d" * 16], [fingerprint])
    # A repeat returns the original request and records nothing new.
    code, again, _ = confirm(admin, secret, token)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["request"]["request_id"] == str(request_id)
    assert trail() == (1, 1, 1, 1)
    secret_values = (
        base64.b64encode(bytes(private)).decode(),
        proof,
        backup_sealing.display_code(proof),
        typed,
    )
    nowhere(outputs, *secret_values)
    log = "\n".join(record.getMessage() for record in caplog.records)
    nowhere([log], *secret_values, public)


def test_challenge_refusals_never_echo_the_paste(admin):
    """Not a key, a private key and the key already in use are refused at
    ``public_key`` in the page's words, never quoting the paste."""
    _, secret, _ = paired(admin.service)
    flagged = private_key_text(top_bit=True)
    for text, words in (
        ("{not a key}", "not a backup public key"),
        (flagged, "PRIVATE key"),
    ):
        code, document, errors = challenge(admin, secret, text)
        assert code == 1 and refused(document, "invalid") == ["public_key"]
        assert words in document["error"]["fields"][0]["message"]
        nowhere([json.dumps(document), errors], text)
    private, public = keypair()
    _, review = proved(admin, secret, private, public)
    _, document, _ = confirm(admin, secret, review["preview"]["token"])
    install_request(
        admin.service.store,
        request_id=UUID(document["result"]["request"]["request_id"]),
        correlation_id=uuid4(),
    )
    code, document, _ = challenge(admin, secret, public)
    assert code == 1 and refused(document, "invalid") == ["public_key"]
    assert "already the backup" in document["error"]["fields"][0]["message"]
    leaked = base64.b64encode(bytes(private)).decode()
    code, document, errors = challenge(admin, secret, leaked)
    assert code == 1 and refused(document, "invalid") == ["public_key"]
    nowhere([json.dumps(document), errors], leaked)
    code, document, _ = challenge(admin, secret, "k" * 201)
    assert code == 1 and refused(document, "invalid") == ["public_key"]


def test_intent_and_token_refusals(admin, monkeypatch):
    """An altered intent is ``invalid`` at ``intent``; an expired one, or one
    made before the settings changed, is ``stale_version``; an altered
    token is ``invalid`` before any prompt; another Administrator's intent
    or token is ``denied``."""
    _, secret, _ = paired(admin.service)
    private, public = keypair()
    code, document, _ = challenge(admin, secret, public)
    intent = document["result"]["intent"]
    answer = backup_sealing.open_proof(document["result"]["challenge"], private)
    code, document, _ = preview(admin, secret, intent + "x", answer)
    assert code == 1 and refused(document, "invalid") == ["intent"]
    monkeypatch.setattr(backup_key, "PROOF_SECONDS", -1)
    code, document, _ = preview(admin, secret, intent, answer)
    assert code == 1 and refused(document, "stale_version") == []
    monkeypatch.setattr(backup_key, "PROOF_SECONDS", 900)
    code, document, errors = confirm(admin, secret, "altered", answer=b"yes\n")
    assert code == 1 and refused(document, "invalid") == []
    assert "Type yes" not in errors
    # Another Administrator's intent is denied at preview, and another
    # Administrator's token at confirm before any prompt; neither records
    # anything.
    forged = signing.loads(intent, salt=backup_key.PROOF_SALT)
    forged["actor"] = str(uuid4())
    code, document, _ = preview(
        admin, secret, signing.dumps(forged, salt=backup_key.PROOF_SALT), answer
    )
    assert code == 1 and refused(document, "denied") == []
    code, document, _ = preview(admin, secret, intent, answer)
    token = document["result"]["preview"]["token"]
    forged = signing.loads(token, salt=backup_key.SALT)
    forged["actor"] = str(uuid4())
    code, document, errors = confirm(
        admin,
        secret,
        signing.dumps(forged, salt=backup_key.SALT),
        answer=b"yes\n",
    )
    assert code == 1 and refused(document, "denied") == []
    assert "Type yes" not in errors
    assert not ConfigurationChangeRequest.objects.exists() and trail()[0] == 0
    store = admin.service.store
    version = store.active()
    record = version.document()["sections"]["parish"][0]
    change(
        store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": record["id"],
                "values": {"name": "Changed Parish"},
            }
        ],
    )
    code, document, _ = preview(admin, secret, intent, answer)
    assert code == 1 and refused(document, "stale_version") == []
    # A token reviewed before the settings changed is stale before any prompt.
    code, document, errors = confirm(admin, secret, token, answer=b"yes\n")
    assert code == 1 and refused(document, "stale_version") == []
    assert "Type yes" not in errors
    # Only the parish-name change above was ever requested.
    assert ConfigurationChangeRequest.objects.count() == 1


def test_refused_sessions(admin, monkeypatch):
    """A read-only session is denied; an ended one is exit 5; Staff is
    denied before any line is read or prompt shown."""
    _, secret, _ = paired(admin.service)
    private, public = keypair()
    result, review = proved(admin, secret, private, public)
    token = review["preview"]["token"]
    answer = backup_sealing.open_proof(result["challenge"], private)
    _, reader, _ = paired(admin.service, scope="read-only")
    for code, document, _ in (
        challenge(admin, reader, public),
        preview(admin, reader, result["intent"], answer),
        confirm(admin, reader, token),
    ):
        assert code == 1 and refused(document, "denied") == []
    revoke(admin.service, secret)
    for code, document, _ in (
        challenge(admin, secret, public),
        preview(admin, secret, result["intent"], answer),
        confirm(admin, secret, token),
    ):
        assert code == 5 and refused(document, "session_ended") == []
    _, staff, _ = paired(admin.service)
    as_staff(monkeypatch)
    # No line at all: Staff is denied before the command would read one.
    for code, document, errors in (
        challenge(admin, staff, None),
        preview(admin, staff, result["intent"], None),
        confirm(admin, staff, token, answer=b"yes\n"),
    ):
        assert code == 1 and refused(document, "denied") == []
        assert "Type yes" not in errors
    assert not ConfigurationChangeRequest.objects.exists() and trail()[0] == 0
