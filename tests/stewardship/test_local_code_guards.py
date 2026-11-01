"""The code checks that keep LOCAL's egress-capable services on their fakes (#1399).

LOCAL's application services have Production's route out, so these
checks, not the network, keep them on their fakes. Each case below is one
use-time check that a LOCAL deployment refuses a real provider, with the
same input admitted (or at least not refused by that check) in Production,
so a change that removes a guard without its replacement fails here.
Later #1399 changes replace a guard with its configured successor and
update the matching case: a real Workspace key, and with it Drive, is now
admitted in LOCAL beside a mail redirect on the normal clock, so the mail
and Drive cases pin LOCAL without one (test_local_real_workspace covers
LOCAL with one). ParishSoft's is now a setting: LOCAL may choose
the real API for reads, so its cases pin that LOCAL defaults to the fake and
never writes to the real ParishSoft, whatever it reads from; it writes only
to the fake (#1884, pinned after the cases). Google sign-in may use a
real OAuth client in LOCAL too, so its tests (after the cases) pin what keeps
that sign-in on LOCAL: the normal clock, the localhost redirect and
host-only cookies.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from parishkit.config import ConfigError
from parishkit.parishsoft_http_worker import (
    DEFAULT_API_BASE_URL,
    LOCAL_SOURCE_BASE_URL,
    validate_request,
)
from parishkit.parishsoft_transport import BoundedPublicationSession
from parishkit.stewardship.backup_drive import DriveFailure
from parishkit.stewardship.deployment import (
    LOCAL_PUBLIC_ORIGIN,
    PARISHSOFT_BASE_URL_VARIABLE,
    PARISHSOFT_PUBLICATION_VARIABLE,
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)
from parishkit.stewardship.local import source_base_url
from parishkit.stewardship.local.clock import FAKETIME_ENVIRONMENT
from parishkit.stewardship.runtime_web import (
    LOCAL_OAUTH_DOCUMENT,
    LOCAL_OAUTH_SENTINEL,
    GoogleClockRefused,
    admit_google_clock,
    parse_google_client,
)

from .test_integration_candidates import account
from .test_local_profile import configuration_for

LOCAL = DeploymentProfile.LOCAL
PRODUCTION = DeploymentProfile.PRODUCTION


def mail(profile, monkeypatch, tmp_path):
    """Without a mail redirect, a Google service account is admitted only
    outside LOCAL (test_local_real_workspace covers LOCAL with one)."""
    from parishkit.stewardship.mail_catcher import workspace_transport

    workspace_transport(account(), profile, redirect=None)


def mail_route(profile, monkeypatch, tmp_path):
    """A real mailbox is reachable from LOCAL only through the mail redirect."""
    from parishkit.stewardship.mail_catcher import MailTransport, envelope

    envelope(
        ("family@example.org",),
        profile=profile,
        transport=MailTransport.GMAIL,
        redirect=None,
    )


def parishsoft(profile, monkeypatch, tmp_path):
    """Unless it chose the real API, a LOCAL helper request reaches only the fake."""
    validate_request(
        {
            "method": "POST",
            "url": DEFAULT_API_BASE_URL + "/organizations/search",
            "parameters": {},
            "timeout": 10,
            "profile": profile.value,
            "base_url": source_base_url(profile),
            "api_key": "synthetic-key",
        }
    )


def parishsoft_check(profile, monkeypatch, tmp_path):
    """Unless LOCAL chose the real API, its provider check names only the fake."""
    from parishkit.stewardship import provider_check_worker as worker

    if ("POST", DEFAULT_API_BASE_URL + "/organizations/search") not in (
        worker.endpoints(profile)
    ):
        raise ValueError("The real ParishSoft API is not admitted.")


def parishsoft_write(profile, monkeypatch, tmp_path):
    """A write-started helper sends the contact PUT only outside LOCAL, even to
    the real API that LOCAL may choose for reads."""
    validate_request(
        {
            "method": "PUT",
            "url": DEFAULT_API_BASE_URL + "/members/5/contact",
            "parameters": {},
            "timeout": 10,
            "profile": profile.value,
            "base_url": DEFAULT_API_BASE_URL,
            "api_key": "synthetic-key",
        },
        contact_writes=True,
    )


def parishsoft_publication_session(profile, monkeypatch, tmp_path):
    """Only outside LOCAL can a ParishSoft publication session be built."""
    BoundedPublicationSession(
        before_request=lambda seconds: None,
        check=lambda: None,
        profile=profile.value,
        base_url=DEFAULT_API_BASE_URL,
    )


def publication_environment(profile, base_url):
    """A deployment environment with publication on, reading ``base_url``."""
    return {
        "PARISHKIT_STEWARDSHIP_PROFILE": profile.value,
        "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": (
            LOCAL_PUBLIC_ORIGIN if profile is LOCAL else "https://parish.example"
        ),
        PARISHSOFT_BASE_URL_VARIABLE: base_url,
        PARISHSOFT_PUBLICATION_VARIABLE: "1",
    }


def parishsoft_publication_setting(profile, monkeypatch, tmp_path):
    """The census publication switch with the real API is admitted only
    outside LOCAL (#1884 admits LOCAL with the fake)."""
    load_deployment(environ=publication_environment(profile, DEFAULT_API_BASE_URL))


def drive_session(profile, monkeypatch, tmp_path):
    """The Drive session refuses LOCAL before it parses the key."""
    from parishkit.google import auth
    from parishkit.stewardship.accounts import integration_candidates
    from parishkit.stewardship.backup_drive import workspace_session

    # Accept any key, so only the profile can refuse.
    monkeypatch.setattr(integration_candidates, "workspace_info", lambda value: {})
    monkeypatch.setattr(auth, "load_service_account_info", lambda *a, **k: object())
    workspace_session(account(), subject="mail@example.org", profile=profile)


def drive_session_without_profile(profile, monkeypatch, tmp_path):
    """A Drive session whose caller names no profile is refused like LOCAL."""
    drive_session(None if profile is LOCAL else profile, monkeypatch, tmp_path)


def drive_probe(profile, monkeypatch, tmp_path):
    """The installer's Test access check binds the Drive session to its profile.

    The probe table and its transactions are faked: only the session the
    check builds, and the outcome it records, matter here.
    """
    from contextlib import nullcontext
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from parishkit.google import auth
    from parishkit.stewardship import backup_probes
    from parishkit.stewardship.accounts import integration_candidates, key_files
    from parishkit.stewardship.jobs import backup_models, ownership

    now = datetime(2026, 10, 15, tzinfo=UTC)
    row = SimpleNamespace(pk=1, created_at=now, subject="mail@example.org")
    row.folder_id = "1AbCdEfGhIjKlMnOpQrStUv"
    recorded = []

    class Rows:
        """The two probe-table queries the pass makes."""

        def order_by(self, *fields):
            return self

        def first(self):
            return row

        def update(self, **fields):
            recorded.append(fields)

    monkeypatch.setattr(
        backup_models,
        "BackupDriveProbe",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **k: Rows())),
    )
    monkeypatch.setattr(
        backup_probes, "transaction", SimpleNamespace(atomic=nullcontext)
    )
    monkeypatch.setattr(ownership, "database_now", lambda: now)
    # The check's user is still the applied one, so the check runs (#269).
    monkeypatch.setattr(backup_probes, "applied_mailbox", lambda: row.subject)
    monkeypatch.setattr(key_files, "read_private", lambda path: account())
    # Accept any key and answer the probe, so only the profile can refuse.
    monkeypatch.setattr(integration_candidates, "workspace_info", lambda value: {})
    monkeypatch.setattr(auth, "load_service_account_info", lambda *a, **k: object())
    monkeypatch.setattr(backup_probes, "DriveClient", lambda session, **k: session)
    monkeypatch.setattr(backup_probes, "probe", lambda client, folder: None)
    assert backup_probes.run_pending_probes("unused", profile=profile) == 1
    if recorded[-1]["failure_kind"] is not None:
        raise ConfigError("The Test access check was refused.")


def drive_copy(profile, monkeypatch, tmp_path):
    """The off-site uploader's copy contacts Drive only outside LOCAL (#235)."""
    from parishkit.stewardship import runtime_paths, uploader_commands

    reached = []

    def contacted(*args, **kwargs):
        reached.append(args)
        raise LookupError("The copy went past its profile check.")

    # Outside LOCAL the copy goes on to take the copy lock; stop it there.
    monkeypatch.setattr(runtime_paths, "private_directory", lambda path: path)
    monkeypatch.setattr(uploader_commands, "_copy_lock", contacted)
    configuration = configuration_for(profile, tmp_path)
    result = uploader_commands.run_upload(
        configuration, wait_seconds=1, session_factory=contacted
    )
    if result == {"state": "not_configured"} and not reached:
        raise ConfigError("The off-site copy was skipped.")


def drive_smoke(profile, monkeypatch, tmp_path):
    """The off-site backup smoke check runs only outside LOCAL."""
    from parishkit.stewardship import smoke, uploader_boundaries

    monkeypatch.setattr(uploader_boundaries, "admit_uploader_service", lambda c: None)
    configuration = replace(
        configuration_for(profile, tmp_path), service_role=ServiceRole.BACKUP_UPLOADER
    )
    smoke.check_backup_drive(
        configuration, delegated_email="mail@example.org", folder_link=""
    )


# Each case and what it raises when LOCAL refuses.
CASES = {
    "mail": (mail, ConfigError),
    "mail_route": (mail_route, ConfigError),
    "parishsoft": (parishsoft, ValueError),
    "parishsoft_check": (parishsoft_check, ValueError),
    "parishsoft_write": (parishsoft_write, ValueError),
    "parishsoft_publication_session": (parishsoft_publication_session, TypeError),
    "parishsoft_publication_setting": (parishsoft_publication_setting, ConfigError),
    "drive_session": (drive_session, DriveFailure),
    "drive_session_without_profile": (drive_session_without_profile, DriveFailure),
    "drive_probe": (drive_probe, ConfigError),
    "drive_copy": (drive_copy, ConfigError),
    "drive_smoke": (drive_smoke, ConfigError),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_local_refuses_the_real_provider(name, monkeypatch, tmp_path):
    """Each egress-capable path refuses its real provider in LOCAL."""
    check, error = CASES[name]
    with pytest.raises(error):
        check(LOCAL, monkeypatch, tmp_path)


@pytest.mark.parametrize("name", sorted(CASES))
def test_production_passes_the_same_check(name, monkeypatch, tmp_path):
    """The same input gets past that check in Production, so the case is live."""
    if name == "drive_smoke":
        # Production goes past the profile check to the folder link, which
        # this case leaves empty; that later refusal names the link.
        with pytest.raises(ValueError, match="drive.google.com"):
            CASES[name][0](PRODUCTION, monkeypatch, tmp_path)
        return
    if name == "drive_copy":
        # Production takes the copy lock, which the fake refuses.
        with pytest.raises(LookupError):
            CASES[name][0](PRODUCTION, monkeypatch, tmp_path)
        return
    CASES[name][0](PRODUCTION, monkeypatch, tmp_path)


def test_local_writes_only_to_the_fake_parishsoft():
    """#1884: LOCAL reading the fake passes all three write guards (the
    loader's switch, the publication session and the helper's PUT check),
    the same layers that refuse LOCAL with the real API in the cases above."""
    configuration = load_deployment(
        environ=publication_environment(LOCAL, LOCAL_SOURCE_BASE_URL)
    )
    assert configuration.parishsoft_publication is True
    # Naming the fake is LOCAL's default, so it is recorded as unset.
    assert configuration.parishsoft_base_url is None
    unnamed = publication_environment(LOCAL, "")
    assert load_deployment(environ=unnamed).parishsoft_publication is True
    BoundedPublicationSession(
        before_request=lambda seconds: None,
        check=lambda: None,
        profile=LOCAL.value,
        base_url=LOCAL_SOURCE_BASE_URL,
    )
    put = {
        "method": "PUT",
        "url": LOCAL_SOURCE_BASE_URL + "/members/5/contact",
        "parameters": {},
        "timeout": 10,
        "profile": LOCAL.value,
        "base_url": LOCAL_SOURCE_BASE_URL,
        "api_key": "synthetic-key",
    }
    assert validate_request(put, contact_writes=True) is put
    # The fake's URL under the real API's base, or a frame that claims the
    # fake while naming the real API's URL, is still refused.
    for frame in (
        put | {"url": DEFAULT_API_BASE_URL + "/members/5/contact"},
        put | {"base_url": DEFAULT_API_BASE_URL},
    ):
        with pytest.raises(ValueError):
            validate_request(frame, contact_writes=True)
    # Production can never name the fake, so it cannot write to it either.
    with pytest.raises(ConfigError):
        load_deployment(
            environ=publication_environment(PRODUCTION, LOCAL_SOURCE_BASE_URL)
        )


def test_local_parishsoft_defaults_to_the_fake_and_may_choose_the_real_api():
    """LOCAL reaches the fake by default and the real API only when it chose
    it (#1399), one base URL at a time; Production can never choose the fake."""
    from parishkit.stewardship import provider_check_worker as worker

    fake = ("POST", LOCAL_SOURCE_BASE_URL + "/organizations/search")
    real = ("POST", DEFAULT_API_BASE_URL + "/organizations/search")
    assert fake in worker.endpoints(LOCAL) and real not in worker.endpoints(LOCAL)
    chosen = worker.endpoints(LOCAL, DEFAULT_API_BASE_URL)
    assert real in chosen and fake not in chosen
    with pytest.raises(ValueError):
        worker.endpoints(PRODUCTION, LOCAL_SOURCE_BASE_URL)


# Google sign-in (#1399). LOCAL admits a real OAuth client, a separate one the
# developer registers for the localhost origin only; the sentinel stays the
# default and only LOCAL takes it.
REAL_CLIENT = b'{"client_id": "id.apps.googleusercontent.com", "client_secret": "s"}'


def local_web(tmp_path, client):
    """A LOCAL web consumer at the localhost origin holding ``client``."""
    from .test_smoke import consumer

    return replace(
        consumer(tmp_path, ServiceRole.WEB, google_oauth=client),
        profile=LOCAL,
        public_origin=LOCAL_PUBLIC_ORIGIN,
    )


@pytest.mark.parametrize("profile", list(DeploymentProfile))
def test_every_profile_admits_a_real_oauth_client_and_only_local_the_sentinel(
    profile,
):
    """A real client is admitted everywhere; the sentinel never leaves LOCAL."""
    assert parse_google_client(REAL_CLIENT, profile=profile)["client_secret"] == "s"
    if profile is LOCAL:
        assert parse_google_client(LOCAL_OAUTH_DOCUMENT, profile=profile) == (
            LOCAL_OAUTH_SENTINEL
        )
    else:
        with pytest.raises(ConfigError, match="only in the local profile"):
            parse_google_client(LOCAL_OAUTH_DOCUMENT, profile=profile)


def test_a_real_oauth_client_needs_the_normal_clock():
    """Under the fake clock a real client is refused; the sentinel still runs.

    Google's ID token timestamps and TLS certificates cannot be checked on a
    past clock (#1423 review). The refusal reads the libfaketime variables
    the fake-clock override sets, so it is inert in Production.
    """
    real = parse_google_client(REAL_CLIENT, profile=LOCAL)
    with pytest.raises(GoogleClockRefused, match="needs the normal clock"):
        admit_google_clock(real, dict(FAKETIME_ENVIRONMENT))
    admit_google_clock(LOCAL_OAUTH_SENTINEL, dict(FAKETIME_ENVIRONMENT))
    admit_google_clock(real, {})
    admit_google_clock(real, {"LD_PRELOAD": "/usr/lib/libjemalloc.so.2"})


@pytest.mark.parametrize("faked", [True, False])
def test_web_start_refuses_a_real_client_before_django_is_configured(
    monkeypatch, faked
):
    """``configure_web_runtime`` (web and the seeder) runs the clock check.

    The key files are stood in, so only the OAuth client and the clock
    decide. Under the fake clock it stops before Django is configured; on
    the normal clock it goes on past the check (to the stand-in metrics
    parse, which stops it there).
    """
    from parishkit.stewardship import runtime_web

    configured = []
    monkeypatch.setattr(
        "django.conf.settings",
        SimpleNamespace(configured=False, configure=lambda **v: configured.append(v)),
    )
    names = {
        "django_signing",
        "general_encryption",
        "family_code_mac",
        "token_public",
        "google_oauth",
        "metrics",
    }
    monkeypatch.setattr(
        runtime_web,
        "read_private",
        lambda path: REAL_CLIENT if path == "google_oauth" else b"key",
    )
    monkeypatch.setattr(runtime_web, "parse_keyring", lambda value, name: name)
    monkeypatch.setattr(runtime_web, "independent_keyrings", lambda *rings: None)

    def past_the_check(value):
        """Stop the assembly just after the clock check."""
        raise LookupError("past the clock check")

    monkeypatch.setattr(runtime_web.MetricsCredential, "parse", past_the_check)
    for name, value in FAKETIME_ENVIRONMENT.items():
        if faked:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    configuration = SimpleNamespace(
        secrets={name: name for name in names}, profile=LOCAL
    )
    with pytest.raises(GoogleClockRefused if faked else LookupError):
        runtime_web.configure_web_runtime(configuration)
    assert not configured


def test_the_refusal_reaches_the_developer(tmp_path, monkeypatch, capsys):
    """Web start and the smoke console show the refusal's own fixed text.

    Every other web admission failure stays the generic line.
    """
    from parishkit.stewardship import runtime_process, runtime_web, smoke

    lease = SimpleNamespace(check=lambda: None)
    refusal = GoogleClockRefused("A real Google OAuth client needs the normal clock.")

    def refuse(configuration):
        raise refusal

    monkeypatch.setattr(runtime_web, "configure_web", refuse)
    with pytest.raises(GoogleClockRefused, match="needs the normal clock"):
        runtime_process.load_web_application("unused", lease)
    monkeypatch.setattr(runtime_web, "configure_web", lambda configuration: 1 / 0)
    with pytest.raises(ConfigError, match="Web worker admission failed"):
        runtime_process.load_web_application("unused", lease)

    configuration = local_web(tmp_path, REAL_CLIENT)
    monkeypatch.setattr(smoke, "load_deployment", lambda path: configuration)
    monkeypatch.setattr(smoke, "configure_logging", lambda: None)
    for name, value in FAKETIME_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    args = SimpleNamespace(
        config="web.yaml",
        target="google_oauth",
        organization_id=None,
        delegated_email=None,
        send_to=None,
        channel_id=None,
        send=None,
    )
    assert smoke.execute_smoke(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "ERROR: A real Google OAuth client needs the normal clock" in (captured.err)
    assert "client_secret" not in captured.err and '"s"' not in captured.err


def test_local_google_sign_in_returns_only_to_localhost(tmp_path):
    """A LOCAL sign-in's redirect URI is the localhost origin's, and only that.

    allauth builds the redirect URI from the request, which the canonical-host
    check holds to the configured origin's host, and LOCAL's origin can only
    be ``https://localhost:8443``. Google refuses a redirect URI its client
    does not list, so Production's client cannot complete a LOCAL sign-in and
    a LOCAL client cannot complete a Production one; the ID token's audience
    (its client ID) is checked as well.
    """
    from parishkit.stewardship import smoke
    from parishkit.stewardship.accounts.authentication import SignedGoogleAdapter
    from parishkit.stewardship.web.security import browser_settings

    callback = LOCAL_PUBLIC_ORIGIN + "/admin/oauth/callback"
    with pytest.raises(ConfigError, match="local public_origin must be"):
        load_deployment(
            environ={
                "PARISHKIT_STEWARDSHIP_PROFILE": "local",
                "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": "https://parish.example",
            }
        )
    web = browser_settings(configuration_for(LOCAL, tmp_path))
    assert web["STEWARDSHIP_CANONICAL_HOST"] == "localhost"
    assert web["ALLOWED_HOSTS"] == ["localhost"]
    assert web["CSRF_TRUSTED_ORIGINS"] == [LOCAL_PUBLIC_ORIGIN]
    request = RequestFactory().get(
        "/admin/login", HTTP_HOST="localhost:8443", secure=True
    )
    adapter = SignedGoogleAdapter(request)
    assert adapter.get_callback_url(request, None) == callback
    assert smoke.check_google_oauth(local_web(tmp_path, REAL_CLIENT)) == {
        "credential": "valid",
        "redirect_uri": callback,
    }


@pytest.mark.parametrize("profile", [LOCAL, PRODUCTION])
def test_session_and_csrf_cookies_are_host_only(profile, tmp_path):
    """No cookie names a domain, so a browser never sends LOCAL's to another host.

    Each deployment also has its own signing key and stores its sessions in
    its own database, so a copied cookie names no session elsewhere.
    """
    from parishkit.stewardship.settings import base
    from parishkit.stewardship.web.security import browser_settings

    web = browser_settings(configuration_for(profile, tmp_path))
    for name in ("SESSION_COOKIE_DOMAIN", "CSRF_COOKIE_DOMAIN"):
        assert getattr(base, name, None) is None and name not in web
