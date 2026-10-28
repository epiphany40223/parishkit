"""The shared edge cases every SQL copy of the "Surname, heads" name must pass.

``family_names.family_heads_name`` (through
``snapshot_names.snapshot_family_names``) is the one rule; SQL builds the same
string to sort and search by. ``NAME_CASES`` lists the edge cases the copies
have drifted on, and ``seed_name_cases`` writes them into a snapshot, so a
test of any SQL copy can seed them and compare against ``expected`` (#932).
``test_family_name_sql_postgresql.py`` (``SnapshotFamilyName``, #965) and
``test_family_name_functions_postgresql.py`` (migration 0045's functions)
both import them from here.
"""

import hashlib
import json
from uuid import uuid4

from parishkit.stewardship.source.version_models import (
    SnapshotFamily,
    SnapshotMember,
    SourceFamily,
    SourceMember,
)

from .auth_builders import unguarded

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
        {"lastName": "\u00a0Squyres\u3000"},
        {
            BASE + 1: member("\u3000Tracy\u00a0", "Squyres\u00a0"),
            BASE + 2: member(" Jeff", "\u3000Squyres"),
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
        "Squyres / Smith, Tracy / Jeff",
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
        # No head shares the Family's name, so only the heads' surnames lead.
        "Lee, Ann",
    ),
    (
        "the surname falls back to the first name",
        {"firstName": " Pat ", "lastName": "", "mailingName": "　"},
        {},
        "Pat",
    ),
    ("no name at all", {}, {}, DEFAULT),
    # Heads of different surnames (#2320): the surnames lead, the Family's
    # first, then each surname's first names in the same order.
    (
        "two surnames, the Family surname's head second",
        {"lastName": "Kelly"},
        {
            BASE + 15: member("Linda", "Thieneman"),
            BASE + 16: member("Maureen", "Kelly"),
        },
        "Kelly / Thieneman, Maureen / Linda",
    ),
    (
        "three heads in two surname groups",
        {"lastName": "Kelly"},
        {
            BASE + 17: member("Maureen", "Kelly"),
            BASE + 18: member("Linda", "Thieneman"),
            BASE + 19: member("Bob", "Kelly"),
        },
        "Kelly / Thieneman, Maureen and Bob / Linda",
    ),
    (
        "a head without a surname follows the groups",
        {"lastName": "Kelly"},
        {
            BASE + 20: member("Pat", " "),
            BASE + 21: member("Linda", "Thieneman"),
            BASE + 22: member("Maureen", "Kelly"),
        },
        "Kelly / Thieneman, Maureen / Linda / Pat",
    ),
    (
        "a head without a surname beside one surname",
        {"lastName": "Kelly"},
        {BASE + 23: member("Pat", ""), BASE + 24: member("Maureen", "Kelly")},
        "Kelly, Pat and Maureen",
    ),
    (
        "every head shares the surname",
        {"lastName": "Squyres"},
        {BASE + 25: member("Tracy", "Squyres"), BASE + 26: member("Jeff", "squyres")},
        "Squyres, Tracy and Jeff",
    ),
    (
        "no head shares the Family surname",
        {"lastName": "Kelly"},
        {BASE + 27: member("Ann", "Lee"), BASE + 28: member("Bo", "Fox")},
        "Lee / Fox, Ann / Bo",
    ),
    (
        "surnames group ignoring letter case",
        {"lastName": "Kelly"},
        {
            BASE + 29: member("Ann", "LEE"),
            BASE + 30: member("Bob", "kelly"),
            BASE + 31: member("Cy", "Lee"),
            BASE + 32: member("Di", "KELLY"),
        },
        "Kelly / LEE, Bob and Di / Ann and Cy",
    ),
    (
        "a surname without first names leaves no empty part",
        {"lastName": "Kelly"},
        {BASE + 33: member("Maureen", "Kelly"), BASE + 34: member("", "Thieneman")},
        "Kelly / Thieneman, Maureen",
    ),
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


def seed_name_cases(snapshot_id, cases=NAME_CASES, *, first=BASE + 1000):
    """Add each case's Family and head Members to the snapshot.

    The loader validates and normalizes what it stages, so it cannot produce
    most of these cases; they are written directly, as only a superuser could.
    Families get DUIDs from ``first`` on, in case order. Returns {Family
    DUID: expected name}.
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
            duid = first + offset
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
