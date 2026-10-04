"""LOCAL refusals at the Workspace, Slack and Drive intakes (#476, OPS-10.05).

The web intakes (setup wizard and settings page) share one profile-aware
admission: LOCAL takes only the mail-catcher document and no Slack token;
every other profile refuses the mail-catcher document. The off-site Google
Drive target cannot be set or tested in LOCAL, and the backup worker's Drive
session never accepts the mail-catcher document as a key.
"""

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.setup_credentials import admit_candidate
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.mail_catcher import MAIL_CATCHER_DOCUMENT

from .test_integration_candidates import account

PROFILES = list(DeploymentProfile)
LOCAL = DeploymentProfile.LOCAL


@pytest.mark.parametrize("profile", PROFILES)
def test_admit_candidate_applies_the_rule_at_the_web_intakes(profile):
    """The wizard and settings-page intakes share one profile-aware admission."""
    if profile is LOCAL:
        admit_candidate("google_workspace", MAIL_CATCHER_DOCUMENT, profile)
        with pytest.raises(ConfigError):
            admit_candidate("google_workspace", account(), profile)
        with pytest.raises(ConfigError, match="Slack is not available"):
            admit_candidate("slack", b"xoxb-synthetic", profile)
    else:
        admit_candidate("google_workspace", account(), profile)
        with pytest.raises(ConfigError):
            admit_candidate("google_workspace", MAIL_CATCHER_DOCUMENT, profile)
        with pytest.raises(ConfigError, match="Google Workspace credential"):
            admit_candidate("google_workspace", b"not a key", profile)
        admit_candidate("slack", b"xoxb-synthetic", profile)


@pytest.mark.parametrize("profile", PROFILES)
def test_drive_target_is_refused_only_in_local(settings, profile):
    """The off-site Drive folder cannot be set or tested in LOCAL; others proceed."""
    from parishkit.stewardship.accounts.integration_views import _refuse_drive_in_local
    from parishkit.stewardship.web.refusals import UserFacingError

    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = profile.value
    if profile is LOCAL:
        with pytest.raises(UserFacingError, match="not available in the local"):
            _refuse_drive_in_local()
    else:
        _refuse_drive_in_local()


def test_drive_session_refuses_the_mail_catcher_document():
    """The backup worker's Drive session never accepts the catcher as a key."""
    from parishkit.stewardship.backup_drive import DriveFailure, workspace_session

    with pytest.raises(DriveFailure) as error:
        workspace_session(MAIL_CATCHER_DOCUMENT, subject="mail@example.org")
    assert error.value.kind == "credential"
