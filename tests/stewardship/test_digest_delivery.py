"""Compiled chart mail is bounded, private and uses unchanged SMTP certainty."""

import base64
import json
import subprocess
import sys
from dataclasses import replace
from functools import cache
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from parishkit.stewardship.digest_delivery import DigestDeliveryMail, deliver_digest
from parishkit.stewardship.digest_delivery_worker import decode_request
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryMail,
    FamilyDeliveryResult,
    deliver_family,
)
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryStatus as Status,
)
from parishkit.stewardship.family_delivery_process import submit_digest, submit_family
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.web.digest_content import (
    CHART_ALT,
    CHART_ID,
    MAX_BODY_BYTES,
    MAX_CHART_BYTES,
)

from .test_daily_digest_content import document, render
from .test_family_delivery import SETTINGS, delivery
from .test_family_delivery import sample as family_sample


@cache
def chart():
    """Small synthetic PNG with the compiled renderer's exact dimensions."""
    output = BytesIO()
    Image.new("RGB", (1440, 840)).save(output, format="PNG")
    return output.getvalue()


def sample():
    """A single Admin envelope with no real credential or persisted report data."""
    return DigestDeliveryMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        ("admin@example.org",),
        "Campaign report",
        f'<p>Report</p><img src="cid:{CHART_ID}" alt="{CHART_ALT}" width="720">',
        "Report",
        chart(),
    )


def request(mail=None):
    """Mirror the parent's closed JSON without reading any credential file."""
    return json.dumps(
        {
            "candidate": base64.b64encode(b"synthetic").decode(),
            "settings": SETTINGS,
            "mail": (mail or sample()).payload(),
        }
    ).encode()


def test_private_decoder_accepts_maximum_valid_escaped_text():
    """JSON's six-byte control escapes must fit the validated plain-body budget."""
    mail = replace(sample(), text="\x01" * MAX_BODY_BYTES)
    _, _, decoded = decode_request(request(mail))
    assert decoded == mail


def test_compiled_report_roundtrips_through_actual_private_decoder_and_mime():
    """The adapter accepts real chart/table compiler output, not only a tiny fixture."""
    compiled = render(document())
    mail = replace(
        sample(),
        subject=compiled.subject,
        html=compiled.html,
        text=compiled.text,
        chart=compiled.chart.data,
    )
    candidate, settings, decoded = decode_request(request(mail))
    assert candidate == b"synthetic" and settings == SETTINGS and decoded == mail
    mime = decoded.message()
    assert mime["To"] == "admin@example.org"
    assert mime["Cc"] is mime["Bcc"] is None
    assert mime["Reply-To"] == SETTINGS["reply_to"]
    assert mime["Message-ID"] == mail.message()["Message-ID"]
    images = [part for part in mime.walk() if part.get_content_type() == "image/png"]
    assert (
        len(images) == 1 and images[0].get_payload(decode=True) == compiled.chart.data
    )
    assert images[0]["Content-ID"] == f"<{CHART_ID}>"
    assert "Report" not in repr(mail) and "admin@example.org" not in repr(mail)


@pytest.mark.parametrize(
    "changes",
    [
        {"semantic_key": "not-uuid"},
        {"sender": "bad\naddress"},
        {"reply_to": "bad\naddress"},
        {"recipients": ()},
        {"recipients": ("a@example.org", "b@example.org")},
        {"subject": ""},
        {"subject": "x" * 255},
        {"subject": "bad\nheader"},
        {"html": ""},
        {"text": ""},
        {"text": None},
        {"text": "\x00"},
        {"text": "\ud800"},
        {"chart": b""},
        {"chart": "path.png"},
        {"chart": b"x" * (MAX_CHART_BYTES + 1)},
        {"chart": b"not an image"},
    ],
)
def test_malformed_mail_rejected_before_provider_io(changes):
    with pytest.raises(ValueError):
        replace(sample(), **changes)


@pytest.mark.parametrize(
    "defect",
    [
        "script",
        "external",
        "missing",
        "duplicate",
        "width",
        "alt",
        "event",
        "anchor_cid",
        "style",
        "comment",
        "active_url",
    ],
)
def test_digest_html_cannot_add_authored_images_or_executable_markup(defect):
    mail = sample()
    html = mail.html
    if defect == "script":
        html += "<script>alert(1)</script>"
    elif defect == "external":
        html = html.replace(f"cid:{CHART_ID}", "https://other/image")
    elif defect == "missing":
        html = "<p>No image</p>"
    elif defect == "duplicate":
        html += html
    elif defect == "width":
        html = html.replace('width="720"', 'width="2000"')
    elif defect == "alt":
        html = html.replace(CHART_ALT, "Different")
    elif defect == "event":
        html = html.replace("<img ", '<img onerror="alert(1)" ')
    elif defect == "anchor_cid":
        html += f'<a href="cid:{CHART_ID}" rel="noopener noreferrer">Link</a>'
    elif defect == "style":
        html += '<p style="display:none">Hidden</p>'
    elif defect == "comment":
        html += "<!--private-->"
    else:
        html += '<a href="javascript:alert(1)" rel="noopener noreferrer">Bad</a>'
    with pytest.raises(ValueError, match="compiled digest"):
        replace(mail, html=html)


@pytest.mark.parametrize("format,size", [("JPEG", (1440, 840)), ("PNG", (1, 1))])
def test_only_the_compiled_chart_format_and_dimensions_are_admitted(format, size):
    output = BytesIO()
    Image.new("RGB", size).save(output, format=format)
    with pytest.raises(ValueError):
        replace(sample(), chart=output.getvalue())


@pytest.mark.parametrize(
    "field,value",
    [
        ("path", "/private/secret"),
        ("attachments", []),
        ("schema", "other"),
        ("recipients", "admin@example.org"),
        ("chart", "not-base64!"),
        ("chart", None),
        ("semantic_key", "00000000000000000000000000000001"),
    ],
)
def test_closed_payload_rejects_unknown_fields_and_noncanonical_values(field, value):
    with pytest.raises(ValueError):
        DigestDeliveryMail.from_payload(sample().payload() | {field: value})


def test_family_and_digest_entry_points_are_not_interchangeable():
    """Family's no-image content restriction and decoder remain intact."""
    digest, family = sample(), family_sample()
    with pytest.raises(ValueError):
        FamilyDeliveryMail.from_payload(digest.payload())
    with pytest.raises(ValueError):
        DigestDeliveryMail.from_payload(family.payload())
    with pytest.raises(ValueError):
        replace(family, html=digest.html)
    for adapter, mail in ((deliver_digest, family), (deliver_family, digest)):
        with pytest.raises(ValueError):
            adapter(b"synthetic", SETTINGS, mail)
    for adapter, mail in ((submit_digest, family), (submit_family, digest)):
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
def test_digest_reuses_smtp_acceptance_boundary(monkeypatch, failure, status):
    result, seen = delivery(
        monkeypatch,
        mail=sample(),
        failure=failure,
        adapter=deliver_digest,
    )
    assert result.status is status and result.recipient_count == 1
    assert seen.count("data") == (1 if failure in {None, "data"} else 0)


@pytest.mark.parametrize(
    "code,status", [(450, Status.TRANSIENT), (550, Status.PERMANENT)]
)
def test_single_refused_admin_never_submits_data(monkeypatch, code, status):
    result, seen = delivery(
        monkeypatch,
        mail=sample(),
        replies={"rcpt0": code},
        adapter=deliver_digest,
    )
    assert result.status is status and "data" not in seen
    assert (result.transient if code == 450 else result.permanent) == (0,)


@pytest.mark.parametrize("status", list(Status))
def test_private_transport_binds_result_to_single_admin(monkeypatch, status):
    expected = FamilyDeliveryResult(status, 1)

    def exchange(payload, **options):
        """The shared pipe owner sees only the compiled digest helper name."""
        assert options["helper"] == "digest_delivery_worker"
        assert decode_request(payload)[2].recipients == ("admin@example.org",)
        return options["decode"](json.dumps(expected.payload()).encode())

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", exchange
    )
    result = submit_digest(
        b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result == expected


@pytest.mark.parametrize("reply", [b"bad", b"{}", b"null", b"\xff"])
def test_invalid_private_acknowledgement_never_authorizes_duplicate(monkeypatch, reply):
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private",
        lambda payload, **options: options["decode"](reply),
    )
    result = submit_digest(
        b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result.status is Status.UNKNOWN


@pytest.mark.parametrize(
    "outcome,status",
    [
        (DeliveryOutcome.NOT_SENT, Status.UNAVAILABLE),
        (DeliveryOutcome.UNKNOWN, Status.UNKNOWN),
    ],
)
def test_launch_failure_and_lost_acknowledgement_are_distinct(
    monkeypatch, outcome, status
):
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private",
        lambda *args, **options: outcome,
    )
    result = submit_digest(
        b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result.status is status


def test_private_helper_imports_without_orm_or_report_renderer():
    """Stateless Django validators are allowed, ORM and report compilation are not."""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from parishkit.stewardship.digest_delivery_worker import decode_request; "
            "import sys; "
            "assert not any(name == prefix or name.startswith(prefix + '.') "
            "for name in sys.modules "
            "for prefix in ('django.db', 'matplotlib', "
            "'parishkit.stewardship.reports'))",
        ],
        env={},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_real_digest_helper_rejects_synthetic_credentials_without_network():
    """Exercise the installed isolated entry point with deliberately invalid JSON."""
    result = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.stewardship.digest_delivery_worker"],
        input=request(),
        env={},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == b""
    assert (
        json.loads(result.stdout) == FamilyDeliveryResult(Status.SYSTEMIC, 1).payload()
    )


def test_actual_shared_pipe_launch_has_fixed_digest_entrypoint(monkeypatch):
    """Private bytes remain stdin-only even with the chart's larger input budget."""
    from unittest.mock import Mock

    from parishkit.stewardship import readiness_delivery_process as parent

    from .test_provider_checks import Process

    expected = FamilyDeliveryResult(Status.ACCEPTED, 1)
    process = Process(json.dumps(expected.payload()).encode(), 0)
    factory = Mock(return_value=process)
    monkeypatch.setattr(parent.subprocess, "Popen", factory)
    result = submit_digest(
        b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result == expected
    assert factory.call_args.args == (
        [sys.executable, "-I", "-m", "parishkit.stewardship.digest_delivery_worker"],
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


def test_image_bomb_failure_is_sanitized_without_warnings(monkeypatch):
    """Malformed image metadata cannot escape into helper diagnostics."""
    from parishkit.stewardship.web.digest_content import validate_digest_body

    mail = sample()

    def refuse(*args, **kwargs):
        raise Image.DecompressionBombError("private metadata")

    monkeypatch.setattr(Image, "open", refuse)
    with pytest.raises(ValueError, match="^Invalid compiled digest content.$"):
        validate_digest_body(mail.html, mail.text, mail.chart)
