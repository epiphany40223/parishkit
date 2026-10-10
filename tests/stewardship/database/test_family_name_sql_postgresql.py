"""Every SQL copy of the "Surname, heads" Family name agrees with Python.

``family_names.family_heads_name`` (through
``snapshot_names.snapshot_family_names``) is the one rule; SQL builds the same
string to sort and search by. The edge cases and the helper that seeds them
live in ``family_name_cases``, shared with the migration 0045 name function
tests, so every SQL copy is held to the same table.
"""

import pytest
from django.db.models import BigIntegerField, F
from django.db.models.functions import Cast

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_metadata import with_name_keys
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.source.snapshot_name_sql import SnapshotFamilyName
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.source.version_models import SnapshotFamily

from .family_name_cases import BASE, DEFAULT, NAME_CASES, seed_name_cases
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


def sql_family_names(snapshot_id, duids, default=""):
    """{Family DUID: name} from SnapshotFamilyName, read as the web role."""
    with task_login(ServiceRole.WEB), work_transaction():
        return dict(
            SnapshotFamily.objects.filter(
                snapshot_id=snapshot_id, source_key__in=[str(duid) for duid in duids]
            )
            .annotate(duid=Cast("source_key", BigIntegerField()))
            .annotate(name=SnapshotFamilyName(snapshot_id, F("duid"), default=default))
            .values_list("duid", "name")
        )


def test_sql_family_name_matches_python_on_every_edge_case(response_service):
    """SnapshotFamilyName and snapshot_family_names agree on each NAME_CASES
    entry, and both give the expected name."""
    snapshot_id = SourceCurrent.objects.get().snapshot_id
    expected = seed_name_cases(snapshot_id)
    python = snapshot_family_names(snapshot_id, list(expected), DEFAULT)
    sql = sql_family_names(snapshot_id, expected, DEFAULT)
    labels = {BASE + 1000 + offset: case[0] for offset, case in enumerate(NAME_CASES)}
    for duid, name in expected.items():
        assert (labels[duid], python[duid]) == (labels[duid], name)
        assert (labels[duid], sql[duid]) == (labels[duid], name)


def test_sql_family_name_treats_non_array_heads_as_none(response_service):
    """A malformed active_head_duids names the Family by surname alone
    instead of failing the whole query."""
    snapshot_id = SourceCurrent.objects.get().snapshot_id
    cases = (
        (
            "heads as a string",
            {"lastName": "Lee", "active_head_duids": "12"},
            {},
            "Lee",
        ),
        (
            "heads as an object",
            {"lastName": "Ray", "active_head_duids": {"a": 1}},
            {},
            "Ray",
        ),
        ("heads as a number", {"lastName": "Fox", "active_head_duids": 5}, {}, "Fox"),
    )
    expected = seed_name_cases(snapshot_id, cases)
    assert sql_family_names(snapshot_id, expected) == expected


def test_outgoing_mail_name_keys_match_every_edge_case(response_service):
    """Outgoing mail's Family sort keys (delivery_metadata.with_name_keys,
    #934) are each NAME_CASES name, lowercased, and its surname alone; they
    name a Family without any usable name "Family", as the page does."""
    snapshot_id = SourceCurrent.objects.get().snapshot_id
    expected = seed_name_cases(snapshot_id)
    with task_login(ServiceRole.WEB, exact=True), work_transaction():
        keys = {
            duid: (surname, name)
            for duid, surname, name in with_name_keys(
                SnapshotFamily.objects.filter(
                    snapshot_id=snapshot_id,
                    source_key__in=[str(duid) for duid in expected],
                ).annotate(duid=Cast("source_key", BigIntegerField())),
                snapshot_id,
                duid="duid",
            ).values_list("duid", "family_sort_surname", "family_sort_name")
        }
    labels = {BASE + 1000 + offset: case[0] for offset, case in enumerate(NAME_CASES)}
    for duid, name in expected.items():
        name = "Family" if name == DEFAULT else name
        # No surname in these cases holds a comma, so it ends at the first.
        surname = name.split(", ")[0]
        assert (labels[duid], keys[duid]) == (
            labels[duid],
            (surname.lower(), name.lower()),
        )
