"""The LOCAL mail catcher (#476, OPS-10.05): transport rule and refusals both ways.

Every new branch is gated on ``DeploymentProfile.LOCAL``. LOCAL admits only
the mail-catcher document and sends only through ``LOCAL_SMTP_ENDPOINT``; every
other profile refuses that document and keeps the Gmail endpoint and XOAUTH2
transport byte for byte. Helper requests carry an explicit ``profile`` and the
helpers apply the same rule, so a parent cannot talk a production helper into
the local endpoint. Nothing here opens a socket.
"""

import base64
import io
import json
import ssl
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import family_delivery, readiness_delivery
from parishkit.stewardship import provider_check_worker as worker
from parishkit.stewardship.accounts.credential_errors import (
    CredentialValidationUnavailable,
)
from parishkit.stewardship.deployment import DeploymentProfile, recorded_profile
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    SmtpSession,
    deliver_family,
)
from parishkit.stewardship.family_delivery_process import (
    FamilyMailSession,
    submit_family,
)
from parishkit.stewardship.family_delivery_worker import (
    decode_request,
    decode_session_header,
)
from parishkit.stewardship.mail_catcher import (
    LOCAL_SMTP_ENDPOINT,
    MAIL_CATCHER_DOCUMENT,
    MAIL_CATCHER_TYPE,
    MailTransport,
    is_mail_catcher,
    mail_catcher_info,
    open_mail_catcher,
    workspace_transport,
)
from parishkit.stewardship.provider_checks import check_candidate
from parishkit.stewardship.readiness_delivery import DeliveryOutcome, deliver_sample
from parishkit.stewardship.readiness_delivery_process import submit_sample
from parishkit.stewardship.readiness_delivery_worker import (
    decode_request as decode_readiness_request,
)
from parishkit.stewardship.readiness_delivery_worker import request_profile

from .test_family_delivery import SETTINGS, sample
from .test_integration_candidates import account
from .test_provider_checks import payload as check_payload
from .test_readiness_delivery import SETTINGS as READINESS_SETTINGS
from .test_readiness_delivery import sample as readiness_sample

PROFILES = list(DeploymentProfile)
OTHERS = [profile for profile in PROFILES if profile is not DeploymentProfile.LOCAL]
LOCAL = DeploymentProfile.LOCAL


def forbidden(*args, **kwargs):
    """A factory or exchange that must never be reached."""
    raise AssertionError("This transport must not be used.")


class PlainSMTP:
    """A scripted Mailpit: plain SMTP that answers 250 and never sees AUTH."""

    opened = []

    def __init__(self, host, port, **kwargs):
        """Record how the connection was opened."""
        self.host, self.port, self.kwargs = host, port, kwargs
        self.recipients, self.quits = [], 0
        PlainSMTP.opened.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.quits += 1

    def ehlo(self):
        return 250, b"mailpit"

    def docmd(self, *args):
        raise AssertionError("AUTH is never sent to the mail catcher.")

    def has_extn(self, name):
        return False

    def mail(self, sender, *, options):
        return 250, b"OK"

    def rcpt(self, address):
        self.recipients.append(address)
        return 250, b"OK"

    def data(self, content):
        return 250, b"queued"

    def send_message(self, message, *, from_addr, to_addrs):
        self.recipients += to_addrs
        return {}


@pytest.fixture
def mailpit(monkeypatch):
    """Route every mail-catcher connection to PlainSMTP and forbid Gmail."""
    PlainSMTP.opened = []
    for module in (family_delivery, readiness_delivery, worker):
        monkeypatch.setattr(
            module,
            "open_mail_catcher",
            lambda: open_mail_catcher(smtp_factory=PlainSMTP),
        )
    monkeypatch.setattr(family_delivery, "_credentials", forbidden)
    monkeypatch.setattr(readiness_delivery, "_credentials", forbidden)
    return PlainSMTP


# --- The document and the endpoint --------------------------------------------


def test_mail_catcher_document_is_small_closed_and_secretless():
    """Exactly one field, the type marker; no endpoint or secret can ride along."""
    assert MAIL_CATCHER_DOCUMENT == b'{"type": "parishkit-mail-catcher"}\n'
    assert mail_catcher_info(MAIL_CATCHER_DOCUMENT) == {"type": MAIL_CATCHER_TYPE}
    assert mail_catcher_info(b' {"type":"parishkit-mail-catcher"} ') == {
        "type": MAIL_CATCHER_TYPE
    }
    assert is_mail_catcher(MAIL_CATCHER_DOCUMENT)
    assert not is_mail_catcher(account())


@pytest.mark.parametrize(
    "value",
    [
        b"",
        b"not-json",
        b"[]",
        b"\xff",
        "parishkit-mail-catcher",
        json.dumps({"type": "parishkit-mail-catcher", "host": "smtp.example"}).encode(),
        json.dumps({"type": "service_account"}).encode(),
        b'{"type":"parishkit-mail-catcher","type":"parishkit-mail-catcher"}',
        b'{"type": "parishkit-mail-catcher"}' + b" " * 131073,
        account(),
    ],
)
def test_anything_but_the_exact_document_is_refused(value):
    """A redirected, duplicated, oversized or foreign document is not the catcher."""
    with pytest.raises(ConfigError, match="Mail-catcher credential"):
        mail_catcher_info(value)
    assert not is_mail_catcher(value)


def test_endpoint_is_a_code_constant_opened_as_plain_smtp():
    """The host and port never come from a document; the connection is plain."""
    assert LOCAL_SMTP_ENDPOINT == ("mailpit", 1025)
    PlainSMTP.opened = []
    smtp = open_mail_catcher(smtp_factory=PlainSMTP)
    assert (smtp.host, smtp.port) == LOCAL_SMTP_ENDPOINT
    assert smtp.kwargs == {"timeout": 10}


# --- The two-way rule ------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_workspace_transport_refuses_in_both_directions(profile):
    """LOCAL: catcher only. Everything else: never the catcher, Gmail as before."""
    if profile is LOCAL:
        assert workspace_transport(MAIL_CATCHER_DOCUMENT, profile) is (
            MailTransport.MAIL_CATCHER
        )
        for value in (account(), b"synthetic", b""):
            with pytest.raises(ConfigError, match="only the mail-catcher"):
                workspace_transport(value, profile)
    else:
        with pytest.raises(ConfigError, match="only in the local profile"):
            workspace_transport(MAIL_CATCHER_DOCUMENT, profile)
        # The Gmail path's own parser still decides what a service account is.
        for value in (account(), b"synthetic"):
            assert workspace_transport(value, profile) is MailTransport.GMAIL


@pytest.mark.parametrize("profile", [None, "local", "production", object()])
def test_workspace_transport_requires_an_explicit_profile(profile):
    """A caller that cannot name its profile selects no transport at all."""
    for value in (MAIL_CATCHER_DOCUMENT, account()):
        with pytest.raises(ConfigError, match="requires a deployment profile"):
            workspace_transport(value, profile)


# --- The recorded profile ------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_recorded_profile_reads_the_assembled_setting(settings, profile):
    """Runtime assembly records the profile; mail parents read exactly that."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = profile.value
    assert recorded_profile() is profile


@pytest.mark.parametrize("value", [None, "", "staging", 7])
def test_recorded_profile_fails_closed_on_a_missing_or_unknown_setting(
    settings, monkeypatch, value
):
    """No profile means no mail: a ConfigError, never a default."""
    if value is None:
        monkeypatch.delattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE")
    else:
        settings.STEWARDSHIP_DEPLOYMENT_PROFILE = value
    with pytest.raises(ConfigError, match="profile is unavailable"):
        recorded_profile()


def test_test_settings_name_their_profile():
    """The fast-test settings module records TEST, so parents can label requests."""
    assert recorded_profile() is DeploymentProfile.TEST


# --- The Family session (and so digest, weekly, operational and security mail) --


def test_local_session_sends_through_the_mail_catcher_without_a_token(mailpit):
    """LOCAL: plain connection to the constant, EHLO, no token, no AUTH, reuse."""
    session = SmtpSession(
        MAIL_CATCHER_DOCUMENT,
        SETTINGS,
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    first = session.deliver(sample())
    second = session.deliver(sample())
    session.close()
    assert first.status is second.status is FamilyDeliveryStatus.ACCEPTED
    assert len(mailpit.opened) == 1
    smtp = mailpit.opened[0]
    assert (smtp.host, smtp.port) == LOCAL_SMTP_ENDPOINT
    assert smtp.recipients == list(sample().recipients) * 2
    assert first.stats["token_refreshed"] is False
    assert "connect_ms" in first.stats and "auth_ms" not in first.stats
    assert first.stats["conn_reused"] is False and second.stats["conn_reused"] is True
    assert smtp.quits == 1


def test_local_one_shot_delivery_uses_the_mail_catcher(mailpit):
    """The one-message entry point selects the same transport."""
    result = deliver_family(
        MAIL_CATCHER_DOCUMENT,
        SETTINGS,
        sample(),
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    assert len(mailpit.opened) == 1


def test_local_refuses_a_service_account_before_any_connection(mailpit):
    """LOCAL with a real-looking Google key: SYSTEMIC, nothing contacted."""
    session = SmtpSession(
        account(),
        SETTINGS,
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    result = session.deliver(sample())
    assert result == FamilyDeliveryResult(
        FamilyDeliveryStatus.SYSTEMIC, 2, stats=result.stats
    )
    assert mailpit.opened == []
    assert "conn_seq" not in result.stats and result.stats["conn_end"] == "token_failed"


@pytest.mark.parametrize("profile", OTHERS)
def test_other_profiles_refuse_the_mail_catcher_before_any_connection(mailpit, profile):
    """Production (and development, test) with the catcher document: SYSTEMIC."""
    result = deliver_family(
        MAIL_CATCHER_DOCUMENT,
        SETTINGS,
        sample(),
        profile=profile,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    assert result.status is FamilyDeliveryStatus.SYSTEMIC
    assert mailpit.opened == []


class GmailSSL:
    """A scripted Gmail: records the TLS connection and the XOAUTH2 AUTH."""

    opened = []

    def __init__(self, host, port, **kwargs):
        self.host, self.port, self.kwargs = host, port, kwargs
        self.auth = []
        GmailSSL.opened.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def ehlo(self):
        return 250, b"gmail"

    def docmd(self, name, value):
        self.auth.append((name, value))
        return 235, b"accepted"

    def has_extn(self, name):
        return False

    def mail(self, sender, *, options):
        return 250, b""

    def rcpt(self, address):
        return 250, b""

    def data(self, content):
        return 250, b""

    def send_message(self, message, *, from_addr, to_addrs):
        return {}


@pytest.mark.parametrize("profile", OTHERS)
def test_gmail_transport_is_unchanged_outside_local(monkeypatch, profile):
    """Every non-LOCAL profile still opens smtp.gmail.com:465 over TLS and AUTHs."""
    GmailSSL.opened = []
    monkeypatch.setattr(family_delivery, "open_mail_catcher", forbidden)
    monkeypatch.setattr(
        family_delivery,
        "_credentials",
        lambda *args: SimpleNamespace(token="synthetic-token"),
    )
    result = deliver_family(
        b"synthetic-key",
        SETTINGS,
        sample(),
        profile=profile,
        smtp_factory=GmailSSL,
        session_factory=nullcontext,
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    smtp = GmailSSL.opened[0]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 465)
    assert smtp.kwargs["timeout"] == 10
    assert isinstance(smtp.kwargs["context"], ssl.SSLContext)
    assert len(smtp.auth) == 1 and smtp.auth[0][0] == "AUTH"
    assert smtp.auth[0][1].startswith("XOAUTH2 ")
    assert result.stats["token_refreshed"] is True and "auth_ms" in result.stats


# --- Readiness samples ---------------------------------------------------------


def test_local_readiness_sample_goes_to_the_mail_catcher(mailpit):
    """The sample path: plain connection, EHLO, no token, no AUTH, accepted."""
    outcome = deliver_sample(
        MAIL_CATCHER_DOCUMENT,
        READINESS_SETTINGS,
        readiness_sample(),
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    assert outcome is DeliveryOutcome.ACCEPTED
    smtp = mailpit.opened[0]
    assert (smtp.host, smtp.port) == LOCAL_SMTP_ENDPOINT
    assert smtp.recipients == [READINESS_SETTINGS["recipient"]]


@pytest.mark.parametrize(
    "profile,value",
    [(LOCAL, account()), (LOCAL, b"synthetic")]
    + [(profile, MAIL_CATCHER_DOCUMENT) for profile in OTHERS],
)
def test_readiness_mismatch_is_definitively_not_sent(mailpit, profile, value):
    """A document that does not match the profile launches no connection."""
    outcome = deliver_sample(
        value,
        READINESS_SETTINGS,
        readiness_sample(),
        profile=profile,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    assert outcome is DeliveryOutcome.NOT_SENT
    assert mailpit.opened == []


@pytest.mark.parametrize("profile", OTHERS)
def test_readiness_gmail_transport_is_unchanged_outside_local(monkeypatch, profile):
    """The sample still authenticates to smtp.gmail.com:465 with XOAUTH2."""
    GmailSSL.opened = []
    monkeypatch.setattr(readiness_delivery, "open_mail_catcher", forbidden)
    monkeypatch.setattr(
        readiness_delivery,
        "_credentials",
        lambda *args: SimpleNamespace(token="synthetic-token"),
    )
    outcome = deliver_sample(
        b"synthetic-key",
        READINESS_SETTINGS,
        readiness_sample(),
        profile=profile,
        smtp_factory=GmailSSL,
        session_factory=nullcontext,
    )
    assert outcome is DeliveryOutcome.ACCEPTED
    smtp = GmailSSL.opened[0]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 465)
    assert smtp.auth[0][0] == "AUTH" and smtp.auth[0][1].startswith("XOAUTH2 ")


# --- The helpers: an explicit profile in every request --------------------------------


def family_request(profile, value=b"synthetic-key"):
    """One Family helper request as the parent writes it."""
    return {
        "candidate": base64.b64encode(value).decode(),
        "settings": SETTINGS,
        "mail": sample().payload(),
        "profile": profile,
    }


@pytest.mark.parametrize("profile", PROFILES)
def test_helper_decoders_return_the_named_profile(profile):
    """Family, session-header and readiness decoders all carry the profile."""
    raw = json.dumps(family_request(profile.value)).encode()
    assert decode_request(raw)[3] is profile
    header = json.dumps(
        {
            "candidate": base64.b64encode(b"synthetic-key").decode(),
            "settings": SETTINGS,
            "profile": profile.value,
        }
    ).encode()
    assert decode_session_header(header)[2] is profile
    readiness = json.dumps(
        {
            "settings": READINESS_SETTINGS,
            "candidate": base64.b64encode(b"synthetic-private").decode(),
            "mail": readiness_sample().payload(),
            "profile": profile.value,
        }
    ).encode()
    assert decode_readiness_request(readiness)[3] is profile


@pytest.mark.parametrize("profile", [None, "", "LOCAL", "staging", 1, ["local"]])
def test_helper_decoders_refuse_a_missing_or_unknown_profile(profile):
    """Without a known profile name a request is malformed and nothing is sent."""
    request = family_request("local")
    if profile is None:
        del request["profile"]
    else:
        request["profile"] = profile
    with pytest.raises(ValueError):
        decode_request(json.dumps(request).encode())
    if profile is not None:
        with pytest.raises(ValueError, match="profile"):
            request_profile(profile)


@pytest.mark.parametrize("profile", OTHERS)
def test_helper_refuses_the_local_endpoint_for_a_non_local_request(
    monkeypatch, profile
):
    """Even a parent that passes the catcher document gets SYSTEMIC outside LOCAL.

    The whole helper path: decode, then deliver with the decoded profile. The
    only transports that exist are patched to fail loudly, proving neither
    is reached.
    """
    monkeypatch.setattr(family_delivery, "open_mail_catcher", forbidden)
    monkeypatch.setattr(family_delivery.smtplib, "SMTP_SSL", forbidden)
    monkeypatch.setattr(family_delivery, "_credentials", forbidden)
    raw = json.dumps(family_request(profile.value, MAIL_CATCHER_DOCUMENT)).encode()
    candidate, settings, mail, decoded = decode_request(raw)
    result = deliver_family(candidate, settings, mail, profile=decoded)
    assert result.status is FamilyDeliveryStatus.SYSTEMIC


def test_real_family_helper_refuses_the_mail_catcher_outside_local():
    """The installed helper process itself applies the rule with no environment."""
    raw = json.dumps(family_request("production", MAIL_CATCHER_DOCUMENT)).encode()
    result = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.stewardship.family_delivery_worker"],
        input=raw,
        env={},
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0 and result.stderr == b""
    assert json.loads(result.stdout)["status"] == "systemic"


# --- The parents: the recorded profile labels every request ----------------------


def test_parent_labels_each_request_with_the_recorded_profile(settings, monkeypatch):
    """The one-shot parent sends its recorded profile; the helper decodes it."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "local"
    seen = []

    def exchange(payload, **kwargs):
        seen.append(decode_request(payload)[3])
        return kwargs["decode"](
            json.dumps(
                FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 2).payload()
            ).encode()
        )

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", exchange
    )
    result = submit_family(
        MAIL_CATCHER_DOCUMENT, SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED and seen == [LOCAL]


def test_parent_without_a_recorded_profile_sends_nothing(settings, monkeypatch):
    """No profile: SYSTEMIC (Family) and NOT_SENT (readiness), no helper launched."""
    monkeypatch.delattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE")
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", forbidden
    )
    monkeypatch.setattr(
        "parishkit.stewardship.readiness_delivery_process._submit_private", forbidden
    )
    result = submit_family(
        b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result == FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, 2)
    outcome = submit_sample(
        b"synthetic",
        READINESS_SETTINGS,
        readiness_sample(),
        seconds=5,
        check=lambda: None,
    )
    assert outcome is DeliveryOutcome.NOT_SENT


def test_readiness_parent_labels_its_request(settings, monkeypatch):
    """The readiness parent's payload names the recorded profile too."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "local"
    seen = []

    def exchange(payload, **kwargs):
        seen.append(decode_readiness_request(payload)[3])
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(
        "parishkit.stewardship.readiness_delivery_process._submit_private", exchange
    )
    outcome = submit_sample(
        MAIL_CATCHER_DOCUMENT,
        READINESS_SETTINGS,
        readiness_sample(),
        seconds=5,
        check=lambda: None,
    )
    assert outcome is DeliveryOutcome.ACCEPTED and seen == [LOCAL]


class HeaderProcess:
    """A fake batched helper that keeps its header and answers one message."""

    def __init__(self):
        self.header = None
        self.returncode = None
        self.stdin = io.BytesIO()
        result = FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 2)
        self.stdout = io.BytesIO(
            b"started 1\n"
            + json.dumps({"seq": 1, "result": result.wire_payload()}).encode()
            + b"\n"
        )
        # Keep what the parent wrote even after it closes the pipe.
        self.stdin.close = self._close_stdin

    def _close_stdin(self):
        self.header = self.stdin.getvalue().split(b"\n")[0]

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_batched_session_header_names_the_profile(settings):
    """The batched helper's first line carries the parent's profile."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "local"
    process = HeaderProcess()
    session = FamilyMailSession(spawn=lambda: process)
    try:
        result = submit_family(
            MAIL_CATCHER_DOCUMENT,
            SETTINGS,
            sample(),
            seconds=5,
            check=lambda: None,
            session=session,
        )
    finally:
        session.close()
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    candidate, decoded_settings, profile = decode_session_header(process.header)
    assert candidate == MAIL_CATCHER_DOCUMENT
    assert decoded_settings == SETTINGS and profile is LOCAL


# --- The installer's provider check --------------------------------------------------


def workspace_settings():
    """The Workspace check context used by the provider-check tests."""
    return json.loads(check_payload("google_workspace"))["settings"]


def test_local_provider_check_reaches_the_mail_catcher_only(mailpit, monkeypatch):
    """A LOCAL check of the catcher document EHLOs Mailpit; Google is never asked."""
    monkeypatch.setattr(worker, "workspace_candidate", forbidden)
    session = SimpleNamespace(profile=LOCAL, request=forbidden)
    assert worker._workspace(MAIL_CATCHER_DOCUMENT, workspace_settings(), session)
    assert (mailpit.opened[0].host, mailpit.opened[0].port) == LOCAL_SMTP_ENDPOINT
    assert mailpit.opened[0].recipients == []


def test_local_provider_check_is_unavailable_when_mailpit_does_not_greet(
    mailpit, monkeypatch
):
    """A non-250 greeting is an outage to retry, not a rejected credential."""
    monkeypatch.setattr(PlainSMTP, "ehlo", lambda self: (421, b"busy"))
    session = SimpleNamespace(profile=LOCAL, request=forbidden)
    with pytest.raises(CredentialValidationUnavailable):
        worker._workspace(MAIL_CATCHER_DOCUMENT, workspace_settings(), session)


def test_local_provider_check_rejects_a_service_account(mailpit, monkeypatch):
    """LOCAL: a Google key is invalid before any token exchange or connection."""
    monkeypatch.setattr(worker, "workspace_candidate", forbidden)
    session = SimpleNamespace(profile=LOCAL, request=forbidden)
    assert worker._workspace(account(), workspace_settings(), session) is False
    assert mailpit.opened == []


@pytest.mark.parametrize("profile", OTHERS)
def test_other_provider_checks_reject_the_mail_catcher(mailpit, monkeypatch, profile):
    """Outside LOCAL the catcher document is invalid before any connection."""
    monkeypatch.setattr(worker, "workspace_candidate", forbidden)
    session = SimpleNamespace(profile=profile, request=forbidden)
    assert worker._workspace(MAIL_CATCHER_DOCUMENT, workspace_settings(), session) is (
        False
    )
    assert mailpit.opened == []


def test_provider_check_without_a_profile_is_a_defect_not_a_verdict(mailpit):
    """A session with no profile cannot judge a Workspace or Slack credential.

    Only the mail deliverers' token exchanges use a profile-free session, and
    they never call these checks; classify always binds the request's profile.
    """
    session = SimpleNamespace(profile=None, request=forbidden)
    for value in (MAIL_CATCHER_DOCUMENT, account()):
        with pytest.raises(ValueError, match="requires a deployment profile"):
            worker._workspace(value, workspace_settings(), session)
    with pytest.raises(ValueError, match="requires a deployment profile"):
        worker._slack(b"xoxb-synthetic", {"channel_id": "C123"}, session)
    assert mailpit.opened == []


def check_request(profile, value):
    """One real provider-check request, as the parent writes it."""
    return check_payload(
        "google_workspace",
        candidate=base64.b64encode(value).decode(),
        profile=profile.value,
    )


def test_local_check_request_reaches_the_mail_catcher_end_to_end(mailpit, monkeypatch):
    """Decode, classify, CheckSession(LOCAL): catcher valid, Google never asked."""
    monkeypatch.setattr(worker, "workspace_candidate", forbidden)
    assert worker.check_request(check_request(LOCAL, MAIL_CATCHER_DOCUMENT)) == "valid"
    assert (mailpit.opened[0].host, mailpit.opened[0].port) == LOCAL_SMTP_ENDPOINT
    assert worker.check_request(check_request(LOCAL, account())) == "invalid"
    assert len(mailpit.opened) == 1


@pytest.mark.parametrize("profile", OTHERS)
def test_other_check_requests_reject_the_mail_catcher_end_to_end(
    mailpit, monkeypatch, profile
):
    """Through the real request path a non-local check refuses the catcher."""
    monkeypatch.setattr(worker, "workspace_candidate", forbidden)
    assert worker.check_request(check_request(profile, MAIL_CATCHER_DOCUMENT)) == (
        "invalid"
    )
    assert mailpit.opened == []


@pytest.mark.parametrize(
    "profile,value",
    [(DeploymentProfile.PRODUCTION, MAIL_CATCHER_DOCUMENT), (LOCAL, account())],
)
def test_installer_parent_and_real_helper_refuse_a_mismatch(profile, value):
    """The installer's parent spawns the real helper; the mismatch is a verdict.

    Neither case needs a network: the catcher is refused outside LOCAL and a
    Google key is refused in LOCAL before any endpoint is contacted.
    """
    assert (
        check_candidate(
            "google_workspace",
            workspace_settings(),
            value,
            seconds=20,
            check=lambda: None,
            profile=profile,
        )
        is False
    )


def test_local_slack_check_is_invalid_before_any_network_call():
    """LOCAL has no Slack: the token is refused without contacting Slack."""
    session = SimpleNamespace(profile=LOCAL, request=forbidden)
    assert worker._slack(b"xoxb-synthetic", {"channel_id": "C123"}, session) is False
    assert (
        worker.check_request(
            check_payload(
                "slack",
                candidate=base64.b64encode(b"xoxb-synthetic").decode(),
                profile="local",
            )
        )
        == "invalid"
    )


@pytest.mark.parametrize("profile", OTHERS)
def test_other_slack_checks_still_contact_slack(profile):
    """Every other profile keeps the existing Slack authentication check."""
    seen = []

    def request(method, url, **kwargs):
        seen.append((method, url))
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    session = SimpleNamespace(profile=profile, request=request)
    assert worker._slack(b"xoxb-synthetic", {"channel_id": "C123"}, session) is True
    assert seen == [("POST", "https://slack.com/api/auth.test")]
