"""LOCAL rules at the Workspace, Slack and Drive intakes (#476, OPS-10.05).

The web intakes (setup wizard and settings page) share one profile-aware
admission: only LOCAL takes the mail-catcher document, and LOCAL takes a
Google service account only beside a mail redirect, on the normal clock
(#1399); a Slack token is parsed alike in every profile (#1399). The
off-site Google Drive target can be set or tested in LOCAL only on the same
terms, and the backup worker's Drive session never accepts the mail-catcher
document as a key.
"""

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.setup_credentials import admit_candidate
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.local.clock import FAKETIME_ENVIRONMENT
from parishkit.stewardship.mail_catcher import MAIL_CATCHER_DOCUMENT

from .test_integration_candidates import account

PROFILES = list(DeploymentProfile)
LOCAL = DeploymentProfile.LOCAL
REDIRECT = "developer@example.org"


@pytest.mark.parametrize("profile", PROFILES)
def test_admit_candidate_applies_the_rule_at_the_web_intakes(profile, monkeypatch):
    """The wizard and settings-page intakes share one profile-aware admission."""
    for name in FAKETIME_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    if profile is LOCAL:
        admit_candidate(
            "google_workspace", MAIL_CATCHER_DOCUMENT, profile, redirect=None
        )
        with pytest.raises(ConfigError, match="only with a mail redirect"):
            admit_candidate("google_workspace", account(), profile, redirect=None)
        # Beside a redirect a real key is admitted, by the same strict parser.
        admit_candidate("google_workspace", account(), profile, redirect=REDIRECT)
        with pytest.raises(ConfigError, match="Google Workspace credential"):
            admit_candidate(
                "google_workspace", b"not a key", profile, redirect=REDIRECT
            )
        # Never under the fake clock, whose past date Google cannot check.
        for name, value in FAKETIME_ENVIRONMENT.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(ConfigError, match="only on the normal clock"):
            admit_candidate("google_workspace", account(), profile, redirect=REDIRECT)
    else:
        admit_candidate("google_workspace", account(), profile, redirect=None)
        with pytest.raises(ConfigError):
            admit_candidate(
                "google_workspace", MAIL_CATCHER_DOCUMENT, profile, redirect=None
            )
        with pytest.raises(ConfigError, match="Google Workspace credential"):
            admit_candidate("google_workspace", b"not a key", profile, redirect=None)
        with pytest.raises(ConfigError, match="only in the local profile"):
            admit_candidate("google_workspace", account(), profile, redirect=REDIRECT)
    # A Slack token is parsed alike in every profile, LOCAL included (#1399).
    admit_candidate("slack", b"xoxb-synthetic", profile, redirect=None)
    with pytest.raises(ConfigError):
        admit_candidate("slack", b"not a token", profile, redirect=None)


@pytest.mark.parametrize("profile", PROFILES)
def test_drive_target_is_refused_only_in_local(settings, profile, monkeypatch):
    """The off-site Drive folder cannot be set or tested in LOCAL without a
    mail redirect, or under the fake clock; others proceed."""
    from parishkit.stewardship.accounts.integration_views import _refuse_drive_in_local
    from parishkit.stewardship.web.refusals import UserFacingError

    for name in FAKETIME_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = profile.value
    if profile is LOCAL:
        with pytest.raises(UserFacingError, match="not available in this local") as e:
            _refuse_drive_in_local()
        assert e.value.reason == "drive_not_in_local"
        assert "deploy --mail-redirect" in str(e.value.refusal.fix)
        settings.STEWARDSHIP_MAIL_REDIRECT = REDIRECT
        _refuse_drive_in_local()
        for name, value in FAKETIME_ENVIRONMENT.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(UserFacingError, match="not available in this local"):
            _refuse_drive_in_local()
    else:
        _refuse_drive_in_local()


def test_drive_session_refuses_the_mail_catcher_document():
    """The backup worker's Drive session never accepts the catcher as a key."""
    from parishkit.stewardship.backup_drive import DriveFailure, workspace_session

    with pytest.raises(DriveFailure) as error:
        workspace_session(
            MAIL_CATCHER_DOCUMENT,
            subject="mail@example.org",
            profile=DeploymentProfile.PRODUCTION,
        )
    assert error.value.kind == "credential"
