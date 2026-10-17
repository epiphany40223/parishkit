"""Local laptop environment (#476): the constants its fake providers share.

The LOCAL deployment profile runs the production-shaped stack on a laptop,
by default with synthetic data from the fake ParishSoft (#1399 lets it read
the real ParishSoft instead). These constants are referenced where a value
is installed and again where it is used, so a misconfigured file cannot
cross between LOCAL and Production in either direction (see the safety
guarantees in docs/specs/stewardship/local-environment/spec.md).

The ParishSoft base-URL rule itself lives in the shared, deployment-agnostic
``parishkit.parishsoft_http_worker`` (the helper must apply it without
importing application code); this module maps ``DeploymentProfile`` onto it.
"""

from parishkit.parishsoft_http_worker import (
    LOCAL_SOURCE_BASE_URL,
    PROFILES,
    admitted_base_url,
    default_base_url,
)
from parishkit.stewardship.deployment import DeploymentProfile

__all__ = [
    "LOCAL_ORGANIZATION_ID",
    "LOCAL_ORGANIZATION_NAME",
    "LOCAL_PARISHSOFT_KEY",
    "LOCAL_SOURCE_BASE_URL",
    "PROFILES",
    "source_base_url",
]

# The one API key the fake accepts. It is a code constant, not a secret: `up`
# installs it as the LOCAL `parishsoft` credential, and the fake answers 401 to
# anything else.
LOCAL_PARISHSOFT_KEY = "local-fake-parishsoft-key"
# The synthetic parish's ParishSoft organization, entered in the setup wizard.
LOCAL_ORGANIZATION_ID = 1001
LOCAL_ORGANIZATION_NAME = "Synthetic Parish"


def source_base_url(profile, base_url=None):
    """The one ParishSoft base URL a deployment's clients talk to.

    ``base_url`` is the deployment's ``parishsoft_base_url`` setting; None is
    the profile's default (the fake in LOCAL, the real API elsewhere). Only
    LOCAL may name the fake (#1399). Every ``ParishSoftConfig`` and helper
    session the application builds takes its base URL from here, so the
    choice is never a per-call-site decision.
    """
    if not isinstance(profile, DeploymentProfile):
        raise ValueError("A deployment profile is required.")
    return admitted_base_url(
        profile.value, default_base_url(profile.value) if base_url is None else base_url
    )
