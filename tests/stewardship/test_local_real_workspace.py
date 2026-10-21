"""A real Google Workspace key in LOCAL, only beside the mail redirect (#1399).

LOCAL admits a Google service account, and with it Gmail and the off-site
Drive copies, only when the deployment sets the forced mail redirect and the
process runs on the normal clock (``mail_catcher.admit_real_workspace``).
These tests pin that rule at every intake and use: the transport selection,
the provider and mailbox checks (whose helper requests carry the redirect
only when set), the Drive session, copy, Test access and smoke check, and
the modeled latency, which is tied to the mail catcher. Every other profile
is unchanged, and refuses a redirect. No test reaches Google: the provider
calls are stood in.
"""

import base64
import json
from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import provider_check_worker as worker
from parishkit.stewardship.backup_drive import DriveFailure
from parishkit.stewardship.deployment import DeploymentProfile, ServiceRole
from parishkit.stewardship.local.clock import FAKETIME_ENVIRONMENT
from parishkit.stewardship.mail_catcher import (
    MAIL_CATCHER_DOCUMENT,
    MailTransport,
    admit_real_workspace,
    workspace_transport,
)

from .test_integration_candidates import account
from .test_local_profile import configuration_for
from .test_provider_checks import payload as check_payload

LOCAL = DeploymentProfile.LOCAL
PRODUCTION = DeploymentProfile.PRODUCTION
OTHERS = [profile for profile in DeploymentProfile if profile is not LOCAL]
REDIRECT = "developer@example.org"
FAKED = dict(FAKETIME_ENVIRONMENT)


@pytest.fixture
def normal_clock(monkeypatch):
    """This process runs on the normal clock, whatever the test runner has."""
    for name in FAKETIME_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def faked(monkeypatch):
    """Put this process under the LOCAL fake clock's environment."""
    for name, value in FAKETIME_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


# --- The rule -------------------------------------------------------------------


def test_local_admits_a_real_key_only_beside_a_redirect_on_the_normal_clock():
    """LOCAL: redirect and normal clock, else a fixed refusal before anything."""
    admit_real_workspace(LOCAL, REDIRECT, {})
    with pytest.raises(ConfigError, match="only with a mail redirect"):
        admit_real_workspace(LOCAL, None, {})
    with pytest.raises(ConfigError, match="only on the normal clock"):
        admit_real_workspace(LOCAL, REDIRECT, FAKED)
    with pytest.raises(ConfigError, match="requires a deployment profile"):
        admit_real_workspace("local", REDIRECT, {})


@pytest.mark.parametrize("profile", OTHERS)
def test_other_profiles_are_unchanged_and_refuse_a_redirect(profile):
    """Every other profile admits a real key as before, on any clock (the
    fake clock never runs there), and never takes a redirect."""
    admit_real_workspace(profile, None, {})
    admit_real_workspace(profile, None, FAKED)
    with pytest.raises(ConfigError, match="only in the local profile"):
        admit_real_workspace(profile, REDIRECT, {})
    assert workspace_transport(account(), profile, redirect=None, environ=FAKED) is (
        MailTransport.GMAIL
    )


def test_local_transport_follows_the_installed_document():
    """The catcher needs no redirect; a real key needs one and the normal clock."""
    for redirect in (None, REDIRECT):
        assert workspace_transport(
            MAIL_CATCHER_DOCUMENT, LOCAL, redirect=redirect, environ=FAKED
        ) is (MailTransport.MAIL_CATCHER)
    assert workspace_transport(account(), LOCAL, redirect=REDIRECT, environ={}) is (
        MailTransport.GMAIL
    )
    with pytest.raises(ConfigError, match="only on the normal clock"):
        workspace_transport(account(), LOCAL, redirect=REDIRECT, environ=FAKED)


def test_web_reuses_the_one_fake_clock_test():
    """runtime_web's fake-clock check is the mail catcher's, not a copy."""
    from parishkit.stewardship import mail_catcher, runtime_web

    assert runtime_web.fake_clock is mail_catcher.fake_clock


# --- The provider and mailbox checks ------------------------------------------------


class Reached(Exception):
    """Raised by a stand-in once a check gets past the transport rule."""


def test_local_provider_check_reaches_google_only_beside_a_redirect(
    monkeypatch, normal_clock
):
    """Without a redirect the key is invalid before Google; with one the
    check goes on to the Google token exchange (stood in here)."""

    def candidate(value, *, delegated_email):
        raise Reached

    monkeypatch.setattr(worker, "workspace_candidate", candidate)
    settings = json.loads(check_payload("google_workspace"))["settings"]
    session = type("Session", (), {"profile": LOCAL, "redirect": None})()
    assert worker._workspace(account(), settings, session) is False
    session.redirect = REDIRECT
    with pytest.raises(Reached):
        worker._workspace(account(), settings, session)


def request(profile, redirect=None):
    """One real provider-check request for a real key, as a parent writes it."""
    extra = {} if redirect is None else {"mail_redirect": redirect}
    return check_payload(
        "google_workspace",
        candidate=base64.b64encode(account()).decode(),
        profile=profile.value,
        **extra,
    )


def test_helper_requests_carry_the_redirect_only_beside_local(
    monkeypatch, normal_clock
):
    """The helper decodes the redirect beside ``local`` only; anything else
    is "invalid" with nothing contacted."""
    reached = []
    monkeypatch.setattr(
        worker,
        "classify",
        lambda *args, **kwargs: reached.append(kwargs["redirect"]) or "valid",
    )
    assert worker.check_request(request(LOCAL, REDIRECT)) == "valid"
    assert worker.check_request(request(LOCAL)) == "valid"
    assert reached == [REDIRECT, None]
    for profile in OTHERS:
        assert worker.check_request(request(profile, REDIRECT)) == "invalid"
    assert worker.check_request(request(LOCAL, "two@x.org, three@x.org")) == "invalid"
    assert reached == [REDIRECT, None]


def test_parent_adds_the_redirect_only_when_set(monkeypatch):
    """The helper request is exactly as before without a redirect; a redirect
    beside any other profile is refused before a helper is launched."""
    from parishkit.stewardship import provider_checks

    sent = []

    class Stop(Exception):
        """Stops the parent once it has written its request."""

    def exchange(process, payload, **kwargs):
        sent.append(json.loads(payload))
        raise Stop

    monkeypatch.setattr(provider_checks, "_check_owner", lambda check: None)
    monkeypatch.setattr(provider_checks, "_exchange", exchange)
    settings = json.loads(check_payload("google_workspace"))["settings"]
    options = dict(seconds=5, check=lambda: None)
    for profile, redirect in ((PRODUCTION, None), (LOCAL, None), (LOCAL, REDIRECT)):
        with pytest.raises(Stop):
            provider_checks.check_candidate(
                "google_workspace",
                settings,
                account(),
                profile=profile,
                redirect=redirect,
                **options,
            )
    assert [set(item) - worker.REQUEST_FIELDS for item in sent] == [
        set(),
        set(),
        {"mail_redirect"},
    ]
    assert sent[-1]["mail_redirect"] == REDIRECT
    monkeypatch.setattr(provider_checks.subprocess, "Popen", Reached)
    with pytest.raises(ValueError, match="Invalid provider check invocation"):
        provider_checks.check_candidate(
            "google_workspace",
            settings,
            account(),
            profile=PRODUCTION,
            redirect=REDIRECT,
            **options,
        )


def test_mailbox_check_names_the_deployment_redirect(settings, monkeypatch):
    """mail-dispatch's mailbox check passes the recorded redirect to its helper."""
    from parishkit.stewardship import provider_checks
    from parishkit.stewardship.accounts import key_files
    from parishkit.stewardship.jobs import mail_halt

    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "local"
    settings.STEWARDSHIP_MAIL_REDIRECT = REDIRECT
    seen = []
    monkeypatch.setattr(mail_halt, "_workspace_settings", lambda: {})
    monkeypatch.setattr(key_files, "read_private", lambda path: account())
    monkeypatch.setattr(
        provider_checks,
        "check_candidate",
        lambda *args, **kwargs: seen.append(kwargs["redirect"]) or True,
    )
    assert mail_halt.check_mailbox("unused", LOCAL, check=lambda: None) == "passed"
    del settings.STEWARDSHIP_MAIL_REDIRECT
    assert mail_halt.check_mailbox("unused", LOCAL, check=lambda: None) == "passed"
    assert seen == [REDIRECT, None]


# --- Off-site Drive copies ---------------------------------------------------------


def accept_any_key(monkeypatch):
    """Accept any key, so only the profile, redirect and clock can refuse."""
    from parishkit.google import auth
    from parishkit.stewardship.accounts import integration_candidates

    monkeypatch.setattr(integration_candidates, "workspace_info", lambda value: {})
    monkeypatch.setattr(auth, "load_service_account_info", lambda *a, **k: object())


def test_drive_session_opens_in_local_only_beside_a_redirect(monkeypatch, normal_clock):
    """The Drive session is built in LOCAL with a redirect on the normal clock,
    and refused (before the key is parsed) otherwise."""
    from parishkit.stewardship.backup_drive import workspace_session

    accept_any_key(monkeypatch)
    options = dict(subject="mail@example.org", profile=LOCAL)
    assert workspace_session(account(), redirect=REDIRECT, **options) is not None
    with pytest.raises(DriveFailure):
        workspace_session(account(), redirect=None, **options)
    faked(monkeypatch)
    with pytest.raises(DriveFailure):
        workspace_session(account(), redirect=REDIRECT, **options)
    # Production is unchanged and never takes a redirect.
    assert workspace_session(account(), subject="m@example.org", profile=PRODUCTION)
    with pytest.raises(DriveFailure):
        workspace_session(
            account(), subject="m@example.org", profile=PRODUCTION, redirect=REDIRECT
        )


def test_drive_copy_runs_in_local_only_beside_a_redirect(
    monkeypatch, normal_clock, tmp_path
):
    """With a redirect the LOCAL copy goes on to read its destination (and
    binds the session to the redirect); without one, or under the fake clock,
    it is ``not_configured`` with nothing read."""
    from parishkit.stewardship import backup_offsite

    read = []

    def destination():
        read.append(True)
        return None

    monkeypatch.setattr(backup_offsite, "destination", destination)
    monkeypatch.setattr(backup_offsite, "_newest_state", lambda: None)
    local = configuration_for(LOCAL, tmp_path)
    assert backup_offsite.copy_offsite(local) == {"state": "not_configured"}
    assert read == []
    redirected = replace(local, mail_redirect=REDIRECT)
    assert backup_offsite.copy_offsite(redirected) == {"state": "not_configured"}
    assert read == [True]
    faked(monkeypatch)
    assert backup_offsite.copy_offsite(redirected) == {"state": "not_configured"}
    assert read == [True]


def test_drive_test_access_binds_the_installer_redirect(monkeypatch):
    """The installer's Test access check builds its session with the redirect."""
    from parishkit.stewardship import backup_probes

    bound = []

    def partial(function, **kwargs):
        """Record the session's binding, then stop before the probe table."""
        bound.append((function, kwargs))
        raise Reached

    monkeypatch.setattr(backup_probes, "partial", partial)
    with pytest.raises(Reached):
        backup_probes.run_pending_probes("unused", profile=LOCAL, redirect=REDIRECT)
    assert bound == [
        (backup_probes.workspace_session, {"profile": LOCAL, "redirect": REDIRECT})
    ]


def test_drive_smoke_runs_in_local_only_beside_a_redirect(
    monkeypatch, normal_clock, tmp_path
):
    """With a redirect the LOCAL check goes on to the folder link; without
    one, or under the fake clock, it is refused before anything is read."""
    from parishkit.stewardship import smoke

    local = replace(
        configuration_for(LOCAL, tmp_path), service_role=ServiceRole.BACKUP_WORKER
    )
    options = dict(delegated_email="mail@example.org", folder_link="")
    with pytest.raises(ConfigError, match="only with a mail redirect"):
        smoke.check_backup_drive(local, **options)
    redirected = replace(local, mail_redirect=REDIRECT)
    with pytest.raises(ValueError, match="drive.google.com"):
        smoke.check_backup_drive(redirected, **options)
    faked(monkeypatch)
    with pytest.raises(ConfigError, match="only on the normal clock"):
        smoke.check_backup_drive(redirected, **options)
