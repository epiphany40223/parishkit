"""The forced mail redirect (#1399): every LOCAL envelope goes to one address.

``mail_redirect`` is a LOCAL-only deployment setting. When it is set, every
SMTP submission (Family, digest, weekly, operational, security, readiness
and ``smoke --send-to``) replaces each envelope recipient with it, and the
recipient headers with it. LOCAL without one may submit only to the mail
catcher, and a redirect anywhere else is refused. With no redirect every
other profile's requests, envelopes and messages are exactly as before.
"""

import base64
import email
import json
import subprocess
import sys
from dataclasses import replace
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import family_delivery, readiness_delivery, smoke
from parishkit.stewardship.deployment import (
    LOCAL_PUBLIC_ORIGIN,
    MAIL_REDIRECT_VARIABLE,
    DeploymentProfile,
    ServiceRole,
    load_deployment,
    recorded_mail_redirect,
)
from parishkit.stewardship.deployment_documents import deployment_document
from parishkit.stewardship.digest_delivery import deliver_digest
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
    MAIL_CATCHER_DOCUMENT,
    MailTransport,
    envelope,
    redirected_message,
    request_redirect,
)
from parishkit.stewardship.operational_delivery import deliver_operational_mail
from parishkit.stewardship.readiness_delivery import DeliveryOutcome, deliver_sample
from parishkit.stewardship.readiness_delivery_process import submit_sample
from parishkit.stewardship.readiness_delivery_worker import (
    decode_request as decode_readiness_request,
)
from parishkit.stewardship.runtime_database import profile_settings
from parishkit.stewardship.security_delivery import deliver_security_mail
from parishkit.stewardship.weekly_delivery import deliver_weekly

from .test_digest_delivery import sample as digest_sample
from .test_family_delivery import SETTINGS, sample
from .test_mail_catcher import (
    GmailSSL,
    HeaderProcess,
    PlainSMTP,
    family_request,
    forbidden,
    mailpit,  # noqa: F401 - the fixture, used by name below
)
from .test_operational_delivery import mail as operational_sample
from .test_readiness_delivery import SETTINGS as READINESS_SETTINGS
from .test_readiness_delivery import sample as readiness_sample
from .test_security_delivery import mail as security_sample
from .test_smoke import consumer
from .test_weekly_delivery import sample as weekly_sample

PROFILES = list(DeploymentProfile)
LOCAL = DeploymentProfile.LOCAL
PRODUCTION = DeploymentProfile.PRODUCTION
OTHERS = [profile for profile in PROFILES if profile is not LOCAL]
REDIRECT = "developer@example.test"


def environ(profile, value=None):
    """A loader environment for one profile, naming a redirect when given."""
    origin = {
        PRODUCTION: "https://parish.example",
        LOCAL: LOCAL_PUBLIC_ORIGIN,
    }.get(profile, "http://localhost:8000")
    values = {
        "PARISHKIT_STEWARDSHIP_PROFILE": profile.value,
        "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": origin,
    }
    if value is not None:
        values[MAIL_REDIRECT_VARIABLE] = value
    return values


def headers(content):
    """The recipient headers of serialized DATA, as one string."""
    message = email.message_from_bytes(content)
    return " ".join(str(message.get_all(name, [])) for name in ("To", "Cc", "Bcc"))


class RecordingSMTP(PlainSMTP):
    """The scripted Mailpit, also keeping each DATA and sent message."""

    def __init__(self, host, port, **kwargs):
        super().__init__(host, port, **kwargs)
        self.contents, self.messages = [], []

    def data(self, content):
        self.contents.append(content)
        return 250, b"queued"

    def send_message(self, message, *, from_addr, to_addrs):
        self.messages.append(message)
        return super().send_message(message, from_addr=from_addr, to_addrs=to_addrs)


class RecordingGmail(GmailSSL):
    """The scripted Gmail, also keeping RCPT addresses, DATA and messages."""

    def __init__(self, host, port, **kwargs):
        super().__init__(host, port, **kwargs)
        self.recipients, self.contents, self.messages = [], [], []

    def rcpt(self, address):
        self.recipients.append(address)
        return 250, b""

    def data(self, content):
        self.contents.append(content)
        return 250, b""

    def send_message(self, message, *, from_addr, to_addrs):
        self.messages.append(message)
        self.recipients += to_addrs
        return {}


@pytest.fixture
def recording(mailpit, monkeypatch):  # noqa: F811 - the imported fixture
    """Mailpit connections open RecordingSMTP; Gmail and tokens are forbidden."""
    from parishkit.stewardship.mail_catcher import open_mail_catcher

    for module in (family_delivery, readiness_delivery):
        monkeypatch.setattr(
            module,
            "open_mail_catcher",
            lambda: open_mail_catcher(smtp_factory=RecordingSMTP),
        )
    # PlainSMTP keeps every opened connection, these included.
    return mailpit


# --- The deployment setting ------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_unset_redirect_renders_and_records_nothing(profile):
    """No profile renders or records the setting unless it is set; empty is unset."""
    for value in (None, ""):
        configuration = load_deployment(environ=environ(profile, value))
        assert configuration.mail_redirect is None
        assert "mail_redirect" not in deployment_document(configuration)["deployment"]
        assert "STEWARDSHIP_MAIL_REDIRECT" not in profile_settings(configuration)


def test_local_records_the_redirect_as_a_switch():
    """LOCAL renders the redirect in service documents, never the provisioning
    record, records it in the settings and reloads it from its document."""
    configuration = load_deployment(environ=environ(LOCAL, REDIRECT))
    assert configuration.mail_redirect == REDIRECT
    document = deployment_document(configuration)
    assert document["deployment"]["mail_redirect"] == REDIRECT
    assert (
        "mail_redirect"
        not in deployment_document(configuration, switches=False)["deployment"]
    )
    assert profile_settings(configuration)["STEWARDSHIP_MAIL_REDIRECT"] == REDIRECT
    assert load_deployment(document=document, environ={}).mail_redirect == REDIRECT


@pytest.mark.parametrize("profile", OTHERS)
def test_loader_refuses_a_redirect_outside_local(profile):
    """A real deployment's mail is never redirected: the setting is refused."""
    with pytest.raises(ConfigError, match="only in the local profile"):
        load_deployment(environ=environ(profile, REDIRECT))


@pytest.mark.parametrize(
    "value",
    [
        "a@example.test, b@example.test",
        "Developer <a@example.test>",
        "a@example.test\r\nBcc: b@example.test",
        " a@example.test",
        "not-an-address",
    ],
)
def test_loader_refuses_anything_but_one_bare_address(value):
    """The redirect is one bare mailbox, never a list, a name or a header."""
    with pytest.raises(ConfigError, match="one email address"):
        load_deployment(environ=environ(LOCAL, value))


def test_recorded_redirect_reads_the_settings_and_fails_closed(settings):
    """Unset is None; LOCAL reads it; any other recorded profile is refused."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "local"
    assert recorded_mail_redirect() is None
    settings.STEWARDSHIP_MAIL_REDIRECT = REDIRECT
    assert recorded_mail_redirect() == REDIRECT
    settings.STEWARDSHIP_MAIL_REDIRECT = "two@example.test, three@example.test"
    with pytest.raises(ConfigError):
        recorded_mail_redirect()
    settings.STEWARDSHIP_MAIL_REDIRECT = REDIRECT
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "production"
    with pytest.raises(ConfigError):
        recorded_mail_redirect()


# --- The rule ----------------------------------------------------------------------


@pytest.mark.parametrize("profile", OTHERS)
@pytest.mark.parametrize("transport", list(MailTransport))
def test_other_profiles_route_unchanged_and_refuse_a_redirect(profile, transport):
    """Without a redirect the envelope is the mail's own; with one, refused."""
    recipients = ("a@example.org", "b@example.org")
    assert (
        envelope(recipients, profile=profile, transport=transport, redirect=None)
        == recipients
    )
    with pytest.raises(ConfigError):
        envelope(recipients, profile=profile, transport=transport, redirect=REDIRECT)


@pytest.mark.parametrize("transport", list(MailTransport))
def test_local_redirect_replaces_every_recipient_index_for_index(transport):
    """Each recipient becomes the redirect, so outcomes keep their positions."""
    recipients = ("a@example.org", "b@example.org", "c@example.org")
    assert (
        envelope(recipients, profile=LOCAL, transport=transport, redirect=REDIRECT)
        == (REDIRECT,) * 3
    )


def test_local_without_a_redirect_reaches_only_the_mail_catcher():
    """LOCAL may send to the routed recipients only through Mailpit."""
    recipients = ("a@example.org",)
    assert (
        envelope(
            recipients,
            profile=LOCAL,
            transport=MailTransport.MAIL_CATCHER,
            redirect=None,
        )
        == recipients
    )
    with pytest.raises(ConfigError, match="only with a mail redirect"):
        envelope(
            recipients, profile=LOCAL, transport=MailTransport.GMAIL, redirect=None
        )


@pytest.mark.parametrize(
    "profile, transport",
    [("local", MailTransport.MAIL_CATCHER), (LOCAL, "mail_catcher"), (None, None)],
)
def test_rule_requires_a_typed_profile_and_transport(profile, transport):
    """A caller that cannot name both cannot send."""
    with pytest.raises(ConfigError):
        envelope(
            ("a@example.org",), profile=profile, transport=transport, redirect=None
        )


def test_headers_are_rewritten_only_with_a_redirect():
    """Without a redirect the message is the same object, untouched."""
    message = EmailMessage()
    message["To"] = "a@example.org, b@example.org"
    message["Cc"] = "c@example.org"
    before = message.as_bytes()
    assert redirected_message(message, None) is message
    assert message.as_bytes() == before
    redirected = redirected_message(message, REDIRECT)
    assert redirected.get_all("To") == [REDIRECT] and redirected["Cc"] is None


def test_helper_field_is_admitted_only_beside_local():
    """Absent is None; LOCAL takes one bare address; every other is refused."""
    assert request_redirect({}, PRODUCTION) is None
    assert request_redirect({"mail_redirect": REDIRECT}, LOCAL) == REDIRECT
    for profile in OTHERS:
        with pytest.raises(ValueError):
            request_redirect({"mail_redirect": REDIRECT}, profile)
    for value in ("a@example.test, b@example.test", 5, None):
        with pytest.raises(ValueError):
            request_redirect({"mail_redirect": value}, LOCAL)


# --- Every sending path, LOCAL with a redirect ---------------------------------------

PATHS = {
    "family": (deliver_family, sample),
    "digest": (deliver_digest, digest_sample),
    "weekly": (deliver_weekly, weekly_sample),
    "operational": (deliver_operational_mail, operational_sample),
    "security": (deliver_security_mail, security_sample),
}


@pytest.mark.parametrize("name", PATHS)
def test_every_typed_mail_goes_only_to_the_redirect(recording, name):
    """Family (either mode), digest, weekly, operational and security mail:
    one RCPT to the redirect, DATA addressed to it alone, every index accepted."""
    deliver, factory = PATHS[name]
    mail = factory()
    result = deliver(
        MAIL_CATCHER_DOCUMENT,
        SETTINGS,
        mail,
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
        redirect=REDIRECT,
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    assert result.recipient_count == len(mail.recipients)
    (smtp,) = recording.opened
    assert smtp.recipients == [REDIRECT]
    (content,) = smtp.contents
    assert email.message_from_bytes(content).get_all("To") == [REDIRECT]
    assert all(address not in headers(content) for address in mail.recipients)


@pytest.mark.parametrize("code", [550, 451])
def test_a_refused_redirect_is_systemic_not_per_recipient(recording, monkeypatch, code):
    """A permanent or temporary refusal of the redirect address marks no real
    address refused: the two-recipient Family is SYSTEMIC, DATA never sent."""
    monkeypatch.setattr(RecordingSMTP, "rcpt", refusing(code))
    result = deliver_family(
        MAIL_CATCHER_DOCUMENT,
        SETTINGS,
        sample(),
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
        redirect=REDIRECT,
    )
    assert result == FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, 2)
    assert (result.permanent, result.transient) == ((), ())
    (smtp,) = recording.opened
    assert smtp.recipients == [REDIRECT] and smtp.contents == []


@pytest.mark.parametrize(
    "code, status, permanent, transient",
    [
        (550, FamilyDeliveryStatus.PERMANENT, (0, 1), ()),
        (451, FamilyDeliveryStatus.TRANSIENT, (), (0, 1)),
    ],
)
def test_without_a_redirect_refusals_stay_per_recipient(
    recording, monkeypatch, code, status, permanent, transient
):
    """Unchanged without a redirect: each refused address keeps its index."""
    monkeypatch.setattr(RecordingSMTP, "rcpt", refusing(code))
    result = deliver_family(
        MAIL_CATCHER_DOCUMENT,
        SETTINGS,
        sample(),
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    assert result == FamilyDeliveryResult(status, 2, permanent, transient)
    (smtp,) = recording.opened
    assert smtp.recipients == list(sample().recipients) and smtp.contents == []


def refusing(code):
    """An RCPT that records the address and refuses it with ``code``."""

    def rcpt(self, address):
        self.recipients.append(address)
        return code, b"refused"

    return rcpt


def test_batched_session_redirects_every_message(recording):
    """The batched Family helper's session applies the redirect to each message."""
    session = SmtpSession(
        MAIL_CATCHER_DOCUMENT,
        SETTINGS,
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
        redirect=REDIRECT,
    )
    try:
        results = [session.deliver(sample()) for _ in range(3)]
    finally:
        session.close()
    assert {result.status for result in results} == {FamilyDeliveryStatus.ACCEPTED}
    (smtp,) = recording.opened
    assert smtp.recipients == [REDIRECT] * 3


def test_readiness_sample_goes_only_to_the_redirect(recording):
    """Readiness, setup test and campaign samples share deliver_sample."""
    mail = readiness_sample()
    outcome = deliver_sample(
        MAIL_CATCHER_DOCUMENT,
        READINESS_SETTINGS,
        mail,
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
        redirect=REDIRECT,
    )
    assert outcome is DeliveryOutcome.ACCEPTED
    (smtp,) = recording.opened
    assert smtp.recipients == [REDIRECT]
    (message,) = smtp.messages
    assert message.get_all("To") == [REDIRECT] and mail.recipient not in str(
        message["To"]
    )


def test_local_without_a_redirect_still_delivers_to_mailpit_as_before(recording):
    """The mail catcher alone needs no redirect: Mailpit sees the routing."""
    mail = sample()
    result = deliver_family(
        MAIL_CATCHER_DOCUMENT,
        SETTINGS,
        mail,
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    (smtp,) = recording.opened
    assert smtp.recipients == list(mail.recipients)


# --- Fail closed ----------------------------------------------------------------------


def gmail_admitted_in_local(monkeypatch):
    """Simulate a LOCAL transport beyond the mail catcher (a real key).

    It skips ``workspace_transport``'s own redirect rule (#1399), so these
    tests show that ``envelope`` alone still refuses or redirects.
    """
    for module in (family_delivery, readiness_delivery):
        monkeypatch.setattr(
            module,
            "workspace_transport",
            lambda value, profile, *, redirect: MailTransport.GMAIL,
        )


@pytest.mark.parametrize("name", PATHS)
def test_local_gmail_without_a_redirect_is_systemic_before_any_contact(
    mailpit,  # noqa: F811 - the imported fixture
    monkeypatch,
    name,
):
    """No token, no connection: definitively unsent, a configuration fault."""
    gmail_admitted_in_local(monkeypatch)
    deliver, factory = PATHS[name]
    mail = factory()
    result = deliver(
        b"synthetic",
        SETTINGS,
        mail,
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    assert result == FamilyDeliveryResult(
        FamilyDeliveryStatus.SYSTEMIC, len(mail.recipients)
    )
    assert mailpit.opened == []


def test_local_readiness_gmail_without_a_redirect_is_not_sent(
    mailpit,  # noqa: F811 - the imported fixture
    monkeypatch,
):
    """A sample is definitively not sent, with nothing contacted."""
    gmail_admitted_in_local(monkeypatch)
    outcome = deliver_sample(
        b"synthetic",
        READINESS_SETTINGS,
        readiness_sample(),
        profile=LOCAL,
        smtp_factory=forbidden,
        session_factory=forbidden,
    )
    assert outcome is DeliveryOutcome.NOT_SENT
    assert mailpit.opened == []


def test_local_gmail_with_a_redirect_reaches_only_the_redirect(monkeypatch):
    """The real-mailbox path, once admitted, can address only the redirect."""
    gmail_admitted_in_local(monkeypatch)
    GmailSSL.opened = []
    token = SimpleNamespace(token="synthetic-token", expiry=None)
    monkeypatch.setattr(family_delivery, "_credentials", lambda *args: token)
    monkeypatch.setattr(readiness_delivery, "_credentials", lambda *args: token)
    mail = sample()
    result = deliver_family(
        b"synthetic",
        SETTINGS,
        mail,
        profile=LOCAL,
        smtp_factory=RecordingGmail,
        session_factory=NullSession,
        redirect=REDIRECT,
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    outcome = deliver_sample(
        b"synthetic",
        READINESS_SETTINGS,
        readiness_sample(),
        profile=LOCAL,
        smtp_factory=RecordingGmail,
        session_factory=NullSession,
        redirect=REDIRECT,
    )
    assert outcome is DeliveryOutcome.ACCEPTED
    family, readiness = GmailSSL.opened
    assert family.recipients == [REDIRECT] and readiness.recipients == [REDIRECT]
    assert email.message_from_bytes(family.contents[0]).get_all("To") == [REDIRECT]
    assert readiness.messages[0].get_all("To") == [REDIRECT]


class NullSession:
    """A token-exchange session that is never used (the token is patched)."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.parametrize("profile", OTHERS)
def test_a_redirect_outside_local_never_sends(
    mailpit,  # noqa: F811 - the imported fixture
    profile,
):
    """Even handed to the transport directly, a non-LOCAL redirect sends nothing."""
    result = deliver_family(
        b"synthetic",
        SETTINGS,
        sample(),
        profile=profile,
        smtp_factory=forbidden,
        session_factory=forbidden,
        redirect=REDIRECT,
    )
    assert result == FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, 2)
    outcome = deliver_sample(
        b"synthetic",
        READINESS_SETTINGS,
        readiness_sample(),
        profile=profile,
        smtp_factory=forbidden,
        session_factory=forbidden,
        redirect=REDIRECT,
    )
    assert outcome is DeliveryOutcome.NOT_SENT
    assert mailpit.opened == []


def test_production_gmail_envelope_and_message_are_unchanged(monkeypatch):
    """Production RCPTs every routed address in order, with the original To."""
    GmailSSL.opened = []
    token = SimpleNamespace(token="synthetic-token", expiry=None)
    monkeypatch.setattr(family_delivery, "_credentials", lambda *args: token)
    mail = sample()
    result = deliver_family(
        account(),
        SETTINGS,
        mail,
        profile=PRODUCTION,
        smtp_factory=RecordingGmail,
        session_factory=NullSession,
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    (smtp,) = GmailSSL.opened
    assert smtp.recipients == list(mail.recipients)
    sent = email.message_from_bytes(smtp.contents[0])
    assert sent["To"] == mail.message()["To"]


def account():
    """A well-formed synthetic service account (the Gmail path's document)."""
    from .test_integration_candidates import account as candidate

    return candidate()


# --- The helper requests --------------------------------------------------------------


@pytest.mark.parametrize("profile", OTHERS)
def test_helpers_refuse_a_redirect_beside_any_other_profile(profile):
    """Family (and every decode_envelope helper), session and readiness."""
    raw = family_request(profile.value) | {"mail_redirect": REDIRECT}
    with pytest.raises(ValueError):
        decode_request(json.dumps(raw).encode())
    header = {
        "candidate": raw["candidate"],
        "settings": SETTINGS,
        "profile": profile.value,
        "mail_redirect": REDIRECT,
    }
    with pytest.raises(ValueError):
        decode_session_header(json.dumps(header).encode())
    readiness = {
        "settings": READINESS_SETTINGS,
        "candidate": raw["candidate"],
        "mail": readiness_sample().payload(),
        "profile": profile.value,
        "mail_redirect": REDIRECT,
    }
    with pytest.raises(ValueError):
        decode_readiness_request(json.dumps(readiness).encode())


def test_local_helpers_decode_the_redirect():
    """A LOCAL request returns the redirect with the profile."""
    raw = family_request("local") | {"mail_redirect": REDIRECT}
    *_, profile, redirect = decode_request(json.dumps(raw).encode())
    assert profile is LOCAL and redirect == REDIRECT


def run_helper(module, request):
    """Run one real helper exactly as the parent does: isolated, no environment."""
    return subprocess.run(
        [sys.executable, "-I", "-m", module],
        input=json.dumps(request).encode(),
        env={},
        capture_output=True,
        timeout=20,
    )


def test_real_helpers_refuse_a_production_redirect_without_an_outcome():
    """The Family helper exits 1 with no outcome (the parent holds it as
    unsent); the readiness helper answers ``not_sent`` and exits 0."""
    raw = family_request("production") | {"mail_redirect": REDIRECT}
    result = run_helper("parishkit.stewardship.family_delivery_worker", raw)
    assert (result.returncode, result.stdout, result.stderr) == (1, b"", b"")
    readiness = {
        "settings": READINESS_SETTINGS,
        "candidate": base64.b64encode(b"synthetic").decode(),
        "mail": readiness_sample().payload(),
        "profile": "production",
        "mail_redirect": REDIRECT,
    }
    result = run_helper("parishkit.stewardship.readiness_delivery_worker", readiness)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"not_sent\n", b"")


# --- The parents ---------------------------------------------------------------------


def captured_payloads(monkeypatch):
    """Capture each one-shot Family and readiness helper request."""
    seen = []

    def family(payload, **kwargs):
        seen.append(json.loads(payload))
        return kwargs["decode"](
            json.dumps(
                FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 2).payload()
            ).encode()
        )

    def readiness(payload, **kwargs):
        seen.append(json.loads(payload))
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", family
    )
    monkeypatch.setattr(
        "parishkit.stewardship.readiness_delivery_process._submit_private", readiness
    )
    return seen


def send_both():
    """One Family submission and one readiness sample through the parents."""
    family = submit_family(
        MAIL_CATCHER_DOCUMENT, SETTINGS, sample(), seconds=5, check=lambda: None
    )
    readiness = submit_sample(
        MAIL_CATCHER_DOCUMENT,
        READINESS_SETTINGS,
        readiness_sample(),
        seconds=5,
        check=lambda: None,
    )
    return family, readiness


@pytest.mark.parametrize("profile", ["production", "local"])
def test_parent_requests_are_unchanged_without_a_redirect(
    settings, monkeypatch, profile
):
    """Unset: the helper requests carry exactly the fields they always did."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = profile
    seen = captured_payloads(monkeypatch)
    family, readiness = send_both()
    assert family.status is FamilyDeliveryStatus.ACCEPTED
    assert readiness is DeliveryOutcome.ACCEPTED
    assert set(seen[0]) == {"candidate", "settings", "mail", "profile"}
    assert set(seen[1]) == {
        "settings",
        "candidate",
        "mail",
        "banner_origin",
        "profile",
    }


def test_local_parent_requests_carry_the_redirect(settings, monkeypatch):
    """A LOCAL parent puts its redirect beside its profile in every request."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "local"
    settings.STEWARDSHIP_MAIL_REDIRECT = REDIRECT
    seen = captured_payloads(monkeypatch)
    send_both()
    assert [request["mail_redirect"] for request in seen] == [REDIRECT, REDIRECT]
    assert decode_request(json.dumps(seen[0]).encode())[4] == REDIRECT
    assert decode_readiness_request(json.dumps(seen[1]).encode())[4] == REDIRECT


def test_misconfigured_parent_sends_nothing(settings, monkeypatch):
    """A redirect recorded beside any other profile launches no helper."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "production"
    settings.STEWARDSHIP_MAIL_REDIRECT = REDIRECT
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", forbidden
    )
    monkeypatch.setattr(
        "parishkit.stewardship.readiness_delivery_process._submit_private", forbidden
    )
    family, readiness = send_both()
    assert family == FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, 2)
    assert readiness is DeliveryOutcome.NOT_SENT


@pytest.mark.parametrize("redirect", [None, REDIRECT])
def test_batched_session_header_carries_the_redirect_only_when_set(settings, redirect):
    """The batched helper's header line is unchanged without a redirect."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "local"
    if redirect is not None:
        settings.STEWARDSHIP_MAIL_REDIRECT = redirect
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
    header = json.loads(process.header)
    assert ("mail_redirect" in header) is (redirect is not None)
    assert decode_session_header(process.header)[3] == redirect


# --- smoke --send-to ------------------------------------------------------------------


def smoke_mailbox(tmp_path, monkeypatch, **changes):
    """A mail-dispatch consumer whose mailbox check passes and send is recorded."""
    configuration = replace(
        consumer(tmp_path, ServiceRole.MAIL_DISPATCH, google_workspace=b"{}"),
        **changes,
    )
    sent, candidates, reads = [], [], []
    real_credential = smoke._credential

    def credential(*args):
        reads.append(args[1])
        return real_credential(*args)

    def outcome(*args):
        reads.append("check")
        return "valid"

    monkeypatch.setattr(smoke, "_credential", credential)
    monkeypatch.setattr(smoke, "_outcome", outcome)

    def candidate(value, *, delegated_email):
        candidates.append(value)
        return SimpleNamespace(token="synthetic-token", refresh=lambda request: None)

    class Smtp:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            return 250, b""

        def docmd(self, command, argument=""):
            return 235, b""

        def send_message(self, message, *, from_addr, to_addrs):
            # The envelope is named explicitly and matches the headers.
            assert from_addr == message["From"]
            assert to_addrs == [message["To"]]
            sent.append(message)

    monkeypatch.setattr(smoke, "workspace_candidate", candidate)
    monkeypatch.setattr(smoke.smtplib, "SMTP_SSL", Smtp)
    return configuration, sent, candidates, reads


def test_local_smoke_send_goes_only_to_the_redirect(tmp_path, monkeypatch):
    """``--send-to`` in LOCAL with a redirect addresses the redirect alone."""
    configuration, sent, _, reads = smoke_mailbox(
        tmp_path, monkeypatch, profile=LOCAL, mail_redirect=REDIRECT
    )
    result = smoke.check_workspace(
        configuration,
        delegated_email="mail@parish.example",
        send_to="someone@parish.example",
    )
    assert result == {"credential": "valid", "sent": True}
    (message,) = sent
    assert message.get_all("To") == [REDIRECT]
    assert reads == ["google_workspace", "check"]


def test_local_smoke_send_without_a_redirect_is_refused_first(tmp_path, monkeypatch):
    """No redirect: refused before the key is read, checked or parsed."""
    configuration, sent, candidates, reads = smoke_mailbox(
        tmp_path, monkeypatch, profile=LOCAL
    )
    with pytest.raises(ConfigError, match="only with a mail redirect"):
        smoke.check_workspace(
            configuration,
            delegated_email="mail@parish.example",
            send_to="someone@parish.example",
        )
    assert sent == [] and candidates == [] and reads == []
