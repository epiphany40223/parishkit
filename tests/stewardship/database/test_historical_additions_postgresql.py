"""Real SQL intake checks retain removed content identity bindings.

The retired Ministry activity (#1267) has no additions left to check: a new
``ministries`` record is refused by validation.
"""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_snapshots import prepare_snapshot
from parishkit.stewardship.accounts.request_admission import check_historical_additions

from ..configuration_factory import configuration_version, successor_document
from ..content_factory import content_document

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("retired", [False, True])
def test_addition_queries_compare_real_retained_ancestor_values(retired):
    """No patched ancestry helper or installer fallback can mask an intake SQL bug."""
    section = "content"
    document = content_document()
    original = deepcopy(document["sections"][section][0])
    version = configuration_version(document)
    prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())
    if retired:
        document = successor_document(version)
        document["sections"][section] = []
        document["sections"]["campaigns"][0]["values"]["content_versions"] = {}
        version = configuration_version(document)
        prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())
    patch = [{"operation": "add", "section": section, **original}]
    check_historical_additions(version.version_id, patch)
    mutated = deepcopy(patch)
    mutated[0]["values"]["text"] = "Changed retained revision"
    with pytest.raises(ConfigError, match="identities"):
        check_historical_additions(version.version_id, mutated)
