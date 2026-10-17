"""The code checks that keep LOCAL's egress-capable services on their fakes (#1399).

LOCAL's application services have Production's route out, so these
checks, not the network, keep them on their fakes. Each case below is one
use-time check that a LOCAL deployment refuses a real provider, with the
same input admitted (or at least not refused by that check) in Production,
so a change that removes a guard without its replacement fails here.
Later #1399 changes replace a guard with its configured successor and
update the matching case. ParishSoft's is now a setting: LOCAL may choose
the real API for reads, so its cases pin that LOCAL defaults to the fake and
never writes to ParishSoft, whatever it reads from.
"""

from dataclasses import replace

import pytest

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
    PARISHSOFT_PUBLICATION_VARIABLE,
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)
from parishkit.stewardship.local import source_base_url

from .test_integration_candidates import account
from .test_local_profile import configuration_for

LOCAL = DeploymentProfile.LOCAL
PRODUCTION = DeploymentProfile.PRODUCTION


def mail(profile, monkeypatch, tmp_path):
    """The mail transport admits a Google service account only outside LOCAL."""
    from parishkit.stewardship.mail_catcher import workspace_transport

    workspace_transport(account(), profile)


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


def parishsoft_publication_setting(profile, monkeypatch, tmp_path):
    """The census publication switch is admitted only outside LOCAL."""
    load_deployment(
        environ={
            "PARISHKIT_STEWARDSHIP_PROFILE": profile.value,
            "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": (
                LOCAL_PUBLIC_ORIGIN if profile is LOCAL else "https://parish.example"
            ),
            PARISHSOFT_PUBLICATION_VARIABLE: "1",
        }
    )


def oauth(profile, monkeypatch, tmp_path):
    """A real Google OAuth client document is admitted only outside LOCAL."""
    from parishkit.stewardship.runtime_web import parse_google_client

    parse_google_client(
        b'{"client_id": "id.apps.googleusercontent.com", "client_secret": "s"}',
        profile=profile,
    )


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
    """The backup's off-site copy contacts Drive only outside LOCAL."""
    from parishkit.stewardship import backup_offsite

    reached = []

    def contacted(*args, **kwargs):
        reached.append(args)
        raise LookupError("The copy went past its profile check.")

    # Outside LOCAL the copy goes on to read the destination; stop it there.
    monkeypatch.setattr(backup_offsite, "destination", contacted)
    configuration = configuration_for(profile, tmp_path)
    result = backup_offsite.copy_offsite(configuration, session_factory=contacted)
    if result == {"state": "not_configured"} and not reached:
        raise ConfigError("The off-site copy was skipped.")


def drive_smoke(profile, monkeypatch, tmp_path):
    """The off-site backup smoke check runs only outside LOCAL."""
    from parishkit.stewardship import smoke

    configuration = replace(
        configuration_for(profile, tmp_path), service_role=ServiceRole.BACKUP_WORKER
    )
    smoke.check_backup_drive(
        configuration, delegated_email="mail@example.org", folder_link=""
    )


# Each case and what it raises when LOCAL refuses.
CASES = {
    "mail": (mail, ConfigError),
    "parishsoft": (parishsoft, ValueError),
    "parishsoft_check": (parishsoft_check, ValueError),
    "parishsoft_write": (parishsoft_write, ValueError),
    "parishsoft_publication_session": (parishsoft_publication_session, TypeError),
    "parishsoft_publication_setting": (parishsoft_publication_setting, ConfigError),
    "oauth": (oauth, ConfigError),
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
        # Production reads the destination, which the fake refuses.
        with pytest.raises(LookupError):
            CASES[name][0](PRODUCTION, monkeypatch, tmp_path)
        return
    CASES[name][0](PRODUCTION, monkeypatch, tmp_path)


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
