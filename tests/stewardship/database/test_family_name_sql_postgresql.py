"""Every SQL copy of the "Surname, heads" Family name agrees with Python.

``family_names.family_heads_name`` (through
``snapshot_names.snapshot_family_names``) is the one rule; SQL builds the same
string to sort and search by. ``NAME_CASES`` lists the edge cases the copies
have drifted on, and ``seed_name_cases`` writes them into a snapshot, so a
test of another SQL copy can reuse both and compare against ``expected``.
"""

import hashlib
import json
from uuid import uuid4

import pytest
from django.db.models import BigIntegerField, F
from django.db.models.functions import Cast

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.source.snapshot_name_sql import SnapshotFamilyName
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.source.version_models import (
    SnapshotFamily,
    SnapshotMember,
    SourceFamily,
    SourceMember,
)

from .auth_builders import unguarded
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

# What a Family without any usable name is called in these tests.
DEFAULT = "Name unknown"

# Family and Member DUIDs far from the synthetic fixture's, except 9 and 10,
# which test numeric (not text) head order and are checked to be free.
BASE = 7_000_000


def member(first, last, *, active=True):
    """A Member payload with just the fields the name reads."""
    return {"firstName": first, "lastName": last, "active": active}


# (label, Family payload, {head DUID: Member payload, or None for a head
# DUID with no Member row}, expected name). Heads are listed in
# active_head_duids in the order given here, which is not always DUID order.
NAME_CASES = (
    (
        "no-break and ideographic space padding",
        {"lastName": " Squyres　"},
        {
            BASE + 1: member("　Tracy ", "Squyres "),
            BASE + 2: member(" Jeff", "　Squyres"),
        },
        "Squyres, Tracy and Jeff",
    ),
    (
        "an inactive head",
        {"lastName": "Lee"},
        {BASE + 3: member("Ann", "Lee"), BASE + 4: member("Bob", "Lee", active=False)},
        "Lee, Ann",
    ),
    (
        "a head DUID with no Member row",
        {"lastName": "Lee"},
        {BASE + 5: member("Ann", "Lee"), BASE + 6: None},
        "Lee, Ann",
    ),
    (
        "heads 9 and 10 in numeric order",
        {"lastName": "Ord"},
        {10: member("Ten", "Ord"), 9: member("Nine", "Ord")},
        "Ord, Nine and Ten",
    ),
    (
        "a head with another surname",
        {"lastName": "Squyres"},
        {BASE + 7: member("Tracy", "Squyres"), BASE + 8: member("Jeff", "Smith")},
        "Squyres, Tracy and Jeff Smith",
    ),
    (
        "a blank first name",
        {"lastName": "Lee"},
        {BASE + 9: member("  ", "Lee"), BASE + 10: member("Bob", "Lee")},
        "Lee, Bob",
    ),
    (
        "three heads",
        {"lastName": "Lee"},
        {
            BASE + 11: member("Ann", "Lee"),
            BASE + 12: member("Bob", "Lee"),
            BASE + 13: member("Cy", "Lee"),
        },
        "Lee, Ann, Bob and Cy",
    ),
    ("no heads", {"lastName": "Lee"}, {}, "Lee"),
    (
        "the surname falls back to mailingName",
        {"lastName": " ", "mailingName": " The Lees "},
        {BASE + 14: member("Ann", "Lee")},
        "The Lees, Ann Lee",
    ),
    (
        "the surname falls back to the first name",
        {"firstName": " Pat ", "lastName": "", "mailingName": "　"},
        {},
        "Pat",
    ),
    ("no name at all", {}, {}, DEFAULT),
)


def _store(model, organization_id, key, values, **fields):
    """Insert one content-addressed payload version, as staging would."""
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return model.objects.create(
        organization_id=organization_id,
        source_key=str(key),
        digest=hashlib.sha256(canonical.encode()).hexdigest(),
        canonical=canonical,
        correlation_id=uuid4(),
        **fields,
    )


def seed_name_cases(snapshot_id, cases=NAME_CASES):
    """Add each case's Family and head Members to the snapshot.

    The loader validates and normalizes what it stages, so it cannot produce
    most of these cases; they are written directly, as only a superuser could.
    Returns {Family DUID: expected name}.
    """
    organization_id = (
        SnapshotFamily.objects.filter(snapshot_id=snapshot_id)
        .values_list("payload__organization_id", flat=True)
        .first()
    )
    member_keys = [str(duid) for _, _, heads, _ in cases for duid in heads]
    assert not SnapshotMember.objects.filter(
        snapshot_id=snapshot_id, source_key__in=member_keys
    ).exists(), "a case's Member DUID is already in the snapshot"
    expected = {}
    with unguarded():
        for offset, (_, family, heads, name) in enumerate(cases):
            duid = BASE + 1000 + offset
            # A case may give its own (malformed) active_head_duids.
            values = {"active_head_duids": list(heads), **family}
            payload = _store(SourceFamily, organization_id, duid, values)
            SnapshotFamily.objects.create(
                snapshot_id=snapshot_id,
                source_key=str(duid),
                payload=payload,
                correlation_id=uuid4(),
            )
            for head, values in heads.items():
                if values is None:
                    continue
                payload = _store(
                    SourceMember, organization_id, head, values, family_key=str(duid)
                )
                SnapshotMember.objects.create(
                    snapshot_id=snapshot_id,
                    source_key=str(head),
                    payload=payload,
                    correlation_id=uuid4(),
                )
            expected[duid] = name
    return expected


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
