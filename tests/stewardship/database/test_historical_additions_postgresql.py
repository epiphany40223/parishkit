"""Real SQL intake checks retain removed content and Ministry identity bindings."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_snapshots import prepare_snapshot
from parishkit.stewardship.accounts.request_admission import check_historical_additions

from ..configuration_factory import configuration_version, successor_document
from ..content_factory import content_document
from ..test_ministry_activity import activity

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("section", ["content", "ministries"])
@pytest.mark.parametrize("retired", [False, True])
def test_addition_queries_compare_real_retained_ancestor_values(section, retired):
    """No patched ancestry helper or installer fallback can mask an intake SQL bug."""
    document = content_document()
    document["sections"]["ministries"] = [activity()]
    original = deepcopy(document["sections"][section][0])
    version = configuration_version(document)
    prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())
    if retired:
        document = successor_document(version)
        document["sections"][section] = []
        if section == "content":
            document["sections"]["campaigns"][0]["values"]["content_versions"] = {}
        version = configuration_version(document)
        prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())
    patch = [{"operation": "add", "section": section, **original}]
    check_historical_additions(version.version_id, patch)
    mutated = deepcopy(patch)
    if section == "content":
        mutated[0]["values"]["text"] = "Changed retained revision"
    else:
        mutated[0]["values"]["ministry_duid"] += 1
    with pytest.raises(ConfigError, match="identities"):
        check_historical_additions(version.version_id, mutated)
    if section == "ministries":
        mutated = deepcopy(patch)
        mutated[0]["id"] = str(uuid4())
        with pytest.raises(ConfigError, match="identities"):
            check_historical_additions(version.version_id, mutated)
