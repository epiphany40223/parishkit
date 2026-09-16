"""Recipient projection shares identity validation without widening mail scope."""

from copy import deepcopy

import pytest

from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.families import (
    FamilySuppressions,
    family_recipients,
    family_statuses,
)

from .test_source_corpus import TODAY, source


def project(data=None, *, suppressed=frozenset()):
    """Exercise the shared normalizer, including its active-head selection."""
    return family_recipients(
        normalize_core(data or source(), as_of=TODAY),
        suppressed_addresses=suppressed,
    )


def test_recipient_projection_uses_heads_not_family_or_publishability():
    """A head's unpublished email remains eligible; the Family DTO is not mail."""
    first, empty = project()
    assert first.eligible == first.deliverable == ("valid@example.org",)
    assert empty.eligible == empty.deliverable == ()
    assert "@" not in repr(first)


def test_sorted_deduplication_preserves_partial_deliverability():
    """Shared addresses and capitalization produce one deterministic To entry."""
    data = source()
    data.members[3]["emailAddress"] = "z@example.org; VALID@example.org; a@example.org"
    data.members[4] = {
        **data.members[3],
        "memberDUID": 4,
        "memberType": "Spouse",
        "emailAddress": "valid@example.org; a@example.org",
    }
    first, _ = project(data, suppressed=frozenset({"valid@example.org"}))
    assert first.eligible == ("a@example.org", "valid@example.org", "z@example.org")
    assert first.deliverable == ("a@example.org", "z@example.org")
    assert first.status.email_eligible and first.status.email_deliverable


def test_all_suppressed_retains_eligibility_but_never_an_empty_message_recipient():
    """Callers can distinguish source eligibility from current deliverability."""
    first, _ = project(suppressed=frozenset({"valid@example.org"}))
    assert first.eligible == ("valid@example.org",)
    assert first.deliverable == ()
    assert first.status.email_eligible and not first.status.email_deliverable


@pytest.mark.parametrize("registered", [False, True])
def test_inactive_or_nonparishioner_never_has_message_recipients(registered):
    """Even a valid contact cannot make an ineligible household a mail target."""
    data = source()
    if registered:
        data.members[3]["memberStatus"] = "Inactive"
    else:
        data.families[1]["registeredOrganizationID"] = 99
    first, _ = project(data)
    assert not first.status.portal_eligible
    assert first.eligible == first.deliverable == ()


def test_projection_does_not_mutate_snapshot_and_agrees_with_identity_status():
    """Identity and eventual rendering cannot independently reinterpret contacts."""
    corpus = normalize_core(source(), as_of=TODAY)
    original = deepcopy(corpus)
    recipients = family_recipients(corpus, suppressed_addresses=frozenset())
    assert tuple(row.status for row in recipients) == family_statuses(
        corpus, suppressed_addresses=frozenset()
    )
    assert corpus == original


def test_invalid_contact_never_leaks_its_value_in_error():
    """Malformed private values fail closed with the existing safe error."""
    corpus = normalize_core(source(), as_of=TODAY)
    email = next(row for row in corpus["contact"]["member:3"]["emails"] if row["valid"])
    email["value"] = "PRIVATE@EXAMPLE.ORG"
    with pytest.raises(
        InvalidSourcePayload, match="eligibility is inconsistent"
    ) as exc:
        family_recipients(corpus, suppressed_addresses=frozenset())
    assert "PRIVATE" not in str(exc.value)


def test_scoped_refusal_does_not_cross_households_with_shared_address():
    """A permanent failure identifies one Family, not a parish-wide email ban."""
    data = source()
    data.families[2] = {**data.families[1], "familyDUID": 2, "familyID": 22}
    data.members[4] = {**data.members[3], "memberDUID": 4, "familyDUID": 2}
    first, second = project(
        data, suppressed=FamilySuppressions(frozenset({(1, "valid@example.org")}))
    )
    assert first.deliverable == ()
    assert second.deliverable == ("valid@example.org",)


@pytest.mark.parametrize(
    "entry",
    [
        (0, "valid@example.org"),
        (True, "valid@example.org"),
        (1, "INVALID"),
        (1,),
        "bad",
    ],
)
def test_scoped_suppressions_reject_invalid_identity(entry):
    """Typed suppression inputs fail before an address can escape matching."""
    with pytest.raises(ValueError, match="canonical identities"):
        FamilySuppressions(frozenset({entry}))


def test_scoped_suppressions_are_immutable_and_have_private_repr():
    """Refusal identities must not be mutable caller-owned collections."""
    with pytest.raises(TypeError, match="immutable"):
        FamilySuppressions(set())
    assert "@" not in repr(FamilySuppressions(frozenset({(1, "valid@example.org")})))
