"""Weekly mail keeps one recipient, closed private transport and truthful outcomes."""

import base64
import json
import subprocess
import sys
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.digest_delivery import DigestDeliveryMail, deliver_digest
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryMail,
    FamilyDeliveryResult,
)
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.family_delivery_process import (
    submit_digest,
    submit_family,
    submit_weekly,
)
from parishkit.stewardship.jobs.digest_content import (
    DigestTemplate,
    render_digest_envelope,
)
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail, deliver_weekly
from parishkit.stewardship.weekly_delivery_worker import decode_request

from .test_digest_delivery import sample as daily_sample
from .test_family_delivery import SETTINGS, delivery
from .test_family_delivery import sample as family_sample
from .test_weekly_digest_content import render


def sample():
    """Use actual compiler output and entirely synthetic addresses/identity."""
    content = render()
    return WeeklyDeliveryMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        ("admin@example.org",),
        content.subject,
        content.html,
        content.text,
    )


def request(mail=None):
    """No live credentials, database settings or provider secrets are needed."""
    return json.dumps(
        {
            "candidate": base64.b64encode(b"synthetic").decode(),
            "settings": SETTINGS,
            "mail": (mail or sample()).payload(),
        }
    ).encode()


def test_real_compiler_decoder_and_mime_have_no_attachment_or_other_recipients():
    """Message identity survives retries and private values stay out of repr."""
    mail = sample()
    candidate, settings, decoded = decode_request(request(mail))
    assert candidate == b"synthetic" and settings == SETTINGS and decoded == mail
    mime = decoded.message()
    assert mime["To"] == "admin@example.org"
    assert mime["Cc"] is mime["Bcc"] is None
    assert mime["Message-ID"] == mail.message()["Message-ID"]
    assert mime["Reply-To"] == SETTINGS["reply_to"]
    assert not list(mime.iter_attachments())
    assert {part.get_content_type() for part in mime.walk()} == {
        "multipart/alternative",
        "text/plain",
        "text/html",
    }
    assert "Please call us." in mime.get_body(preferencelist=("plain",)).get_content()
    assert "Please call us." not in repr(mail)


@pytest.mark.parametrize("testing", [False, True])
def test_weekly_template_keeps_compiled_private_text_and_routes_exactly_one_admin(
    testing,
):
    """Public authored substitutions never interpret Family-supplied report text."""
    campaign = uuid4()
    content = render()
    value = render_digest_envelope(
        identity=DeliveryIdentity(
            scope_id=campaign,
            campaign_id=campaign,
            semantic_key=uuid4(),
            mode="testing" if testing else "production",
            routing="testing_override" if testing else "production",
            purpose="weekly_digest",
        ),
        configuration_id=uuid4(),
        template_id=None,
        template=DigestTemplate(
            "{{ campaign_name }} — information", "<p>Welcome.</p>", "Welcome."
        ),
        content=content,
        values={"campaign_name": "Annual"},
        sender=SETTINGS["sender"],
        reply_to=SETTINGS["reply_to"],
        recipient="admin@example.org",
        testing_recipient="test@example.org" if testing else None,
    )
    assert value.intended_recipients == ("admin@example.org",)
    assert value.routed_recipients == (
        ("test@example.org",) if testing else ("admin@example.org",)
    )
    assert content.html in value.html and content.text in value.text
    assert value.subject.startswith("[TEST]") is testing
    if testing:
        assert "instead of Administrator admin@example.org" in value.text
    replace(
        sample(),
        subject=value.subject,
        html=value.html,
        text=value.text,
        recipients=value.routed_recipients,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"semantic_key": "bad"},
        {"sender": "bad\naddress"},
        {"reply_to": "bad\naddress"},
        {"recipients": ()},
        {"recipients": ("one@example.org", "two@example.org")},
        {"subject": ""},
        {"subject": "x" * 255},
        {"subject": "bad\nheader"},
        {"subject": "\ud800"},
        {"html": '<img src="cid:chart">'},
        {"text": ""},
        {"text": None},
        {"text": "\ud800"},
        {"html": "<script>private()</script>"},
    ],
)
def test_unsafe_mail_fails_before_provider_access(changes):
    """Every private adapter validates directly constructed objects too."""
    with pytest.raises(ValueError):
        replace(sample(), **changes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "daily-digest-mail-v1"),
        ("path", "/private/secret"),
        ("attachments", []),
        ("chart", "abc"),
        ("recipients", "admin@example.org"),
        ("text", None),
        ("semantic_key", "00000000000000000000000000000001"),
    ],
)
def test_closed_payload_rejects_extra_and_noncanonical_values(field, value):
    """A MIME/attachment/path or another purpose cannot enter through JSON."""
    with pytest.raises(ValueError):
        WeeklyDeliveryMail.from_payload(sample().payload() | {field: value})


def test_existing_daily_and_family_boundaries_are_not_widened():
    """Each purpose retains its own shape, type and transport admission."""
    weekly, daily, family = sample(), daily_sample(), family_sample()
    for cls, mail in (
        (WeeklyDeliveryMail, daily),
        (WeeklyDeliveryMail, family),
        (DigestDeliveryMail, weekly),
        (FamilyDeliveryMail, weekly),
    ):
        with pytest.raises(ValueError):
            cls.from_payload(mail.payload())
    for adapter, mail in ((deliver_weekly, daily), (deliver_digest, weekly)):
        with pytest.raises(ValueError):
            adapter(b"synthetic", SETTINGS, mail)
    for adapter, mail in (
        (submit_weekly, daily),
        (submit_weekly, family),
        (submit_digest, weekly),
        (submit_family, weekly),
    ):
        with pytest.raises(ValueError):
            adapter(b"synthetic", SETTINGS, mail, seconds=5, check=lambda: None)


@pytest.mark.parametrize(
    "failure,status",
    [
        (None, Status.ACCEPTED),
        ("auth", Status.UNAVAILABLE),
        ("mail", Status.UNAVAILABLE),
        ("rcpt0", Status.UNAVAILABLE),
        ("data", Status.UNKNOWN),
    ],
)
def test_weekly_reuses_the_exact_smtp_acceptance_boundary(monkeypatch, failure, status):
    """A lost DATA acknowledgement never becomes permission to resend blindly."""
    result, seen = delivery(
        monkeypatch, mail=sample(), failure=failure, adapter=deliver_weekly
    )
    assert result.status is status and result.recipient_count == 1
    assert seen.count("data") == (1 if failure in {None, "data"} else 0)


@pytest.mark.parametrize("status", list(Status))
def test_private_result_is_bound_to_one_recipient(monkeypatch, status):
    """Use the actual decoder and shared pipe owner, with no provider I/O."""
    expected = FamilyDeliveryResult(status, 1)

    def exchange(payload, **options):
        """Only the compiled weekly helper is selected; bytes remain on stdin."""
        assert options["helper"] == "weekly_delivery_worker"
        assert decode_request(payload)[2].recipients == ("admin@example.org",)
        return options["decode"](json.dumps(expected.payload()).encode())

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", exchange
    )
    assert (
        submit_weekly(b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None)
        == expected
    )


@pytest.mark.parametrize("reply", [b"bad", b"{}", b"null", b"\xff"])
def test_invalid_acknowledgement_remains_unknown(monkeypatch, reply):
    """Malformed output cannot fabricate acceptance or safe nonsubmission."""
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private",
        lambda payload, **options: options["decode"](reply),
    )
    result = submit_weekly(
        b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result.status is Status.UNKNOWN


def test_actual_private_process_uses_fixed_weekly_entrypoint(monkeypatch):
    """Retain empty environment, closed inherited descriptors and silent stderr."""
    from unittest.mock import Mock

    from parishkit.stewardship import readiness_delivery_process as parent

    from .test_provider_checks import Process

    expected = FamilyDeliveryResult(Status.ACCEPTED, 1)
    process = Process(json.dumps(expected.payload()).encode(), 0)
    factory = Mock(return_value=process)
    monkeypatch.setattr(parent.subprocess, "Popen", factory)
    assert (
        submit_weekly(b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None)
        == expected
    )
    assert factory.call_args.args == (
        [sys.executable, "-I", "-m", "parishkit.stewardship.weekly_delivery_worker"],
    )
    assert factory.call_args.kwargs == dict(
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env={},
    )
    assert (
        len(process.inputs) == 1
        and decode_request(process.inputs[0])[0] == b"synthetic"
    )
    assert process.stdin.closed and process.stdout.closed


def test_real_helper_rejects_synthetic_credentials_without_network():
    """The actual isolated process does not read Django settings or a real secret."""
    result = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.stewardship.weekly_delivery_worker"],
        input=request(),
        env={},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == b""
    assert (
        json.loads(result.stdout) == FamilyDeliveryResult(Status.SYSTEMIC, 1).payload()
    )


def test_weekly_private_helper_has_no_orm_or_report_compiler_imports():
    """The private helper must not import reporting models through side effects."""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from parishkit.stewardship.weekly_delivery_worker import decode_request; "
            "import sys; "
            "assert not any(name == prefix or name.startswith(prefix + '.') "
            "for name in sys.modules for prefix in "
            "('django.db', 'matplotlib', 'parishkit.stewardship.reports'))",
        ],
        env={},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_larger_weekly_budget_preserves_valid_json_escape_expansion():
    """The private envelope accepts the full admitted plain-text byte limit."""
    from parishkit.stewardship.web.weekly_digest_content import MAX_WEEKLY_BODY_BYTES

    mail = replace(sample(), text="\x01" * MAX_WEEKLY_BODY_BYTES)
    _, _, decoded = decode_request(request(mail))
    assert decoded == mail


def test_weekly_settings_mismatch_is_rejected_before_delivery():
    """An otherwise valid report may not select another sender or reply address."""
    mail = replace(sample(), sender="other@example.org")
    with pytest.raises(ValueError):
        deliver_weekly(b"synthetic", SETTINGS, mail)
    with pytest.raises(ValueError):
        decode_request(request(mail))
