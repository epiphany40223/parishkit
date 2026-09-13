"""Deterministic source suggestions honor local activity and expose ambiguities."""

from copy import deepcopy

import pytest

from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.chairs import chair_suggestions
from parishkit.stewardship.source.corpus import normalize_core

from .test_ministry_activity import activity, policy_document
from .test_source_corpus import TODAY, source


def suggestions(corpus=None, *overrides):
    """Supply a single tenant's synthetic normalized corpus and explicit policy."""
    return chair_suggestions(
        normalize_core(source(), as_of=TODAY) if corpus is None else corpus,
        policy_document(*overrides),
        organization_id=5,
    )


def test_current_chair_with_private_email_is_suggested_without_granting_access():
    """Publication preference labels contact; it does not discard a usable login."""
    (group,) = suggestions()
    assert group.email == "valid@example.org" and group.ministry_duid == 4
    assert not group.ambiguous and group.email_member_duids == (3,)
    (candidate,) = group.candidates
    assert (candidate.organization_id, candidate.member_duid) == (5, 3)
    assert candidate.member_name == "Member Middle Example"
    assert candidate.ministry_name == "Choir"
    assert candidate.publish_email is False
    assert len(candidate.roster_keys) == 1
    assert "valid@example.org" not in repr(group)
    assert "Member Middle Example" not in repr(group)


def test_local_inactivity_wins_and_other_tenant_does_not_hide():
    """The same DUID in another tenant has no effect on this catalog."""
    corpus = normalize_core(source(), as_of=TODAY)
    assert not suggestions(corpus, activity(organization_id=5, ministry_duid=4))
    assert suggestions(corpus, activity(organization_id=6, ministry_duid=4))
    assert suggestions(
        corpus, activity(organization_id=5, ministry_duid=4, active=True)
    )
    # Upstream's unreliable optional flag is not the local administrative choice.
    corpus["ministry"]["4"]["active"] = False
    assert suggestions(corpus)


@pytest.mark.parametrize(
    "defect",
    [
        "inactive_member",
        "ended",
        "different_role",
        "no_role",
        "no_contact",
        "invalid_email",
    ],
)
def test_no_suggestion_without_actual_active_relationship_and_member_email(defect):
    """Do not use Family email, infer role from a numeric ID or ignore activity."""
    corpus = normalize_core(source(), as_of=TODAY)
    roster = next(iter(corpus["roster"].values()))
    if defect == "inactive_member":
        corpus["member"]["3"]["active"] = False
    elif defect == "ended":
        roster["current"] = False
    elif defect == "different_role":
        roster["ministryRoleName"] = "Participant"
    elif defect == "no_role":
        del roster["ministryRoleName"]
    elif defect == "no_contact":
        del corpus["contact"]["member:3"]
    else:
        corpus["contact"]["member:3"]["emails"] = [{"valid": False, "value": "bad"}]
    assert not suggestions(corpus)


def test_dated_duplicate_roles_group_one_candidate_but_retain_all_evidence():
    """Overlapping valid roster records cannot silently duplicate seed suggestions."""
    data = source()
    row = deepcopy(data.ministry_type_memberships[4]["membership"][0])
    row["startDate"] = "2026-02-01"
    data.ministry_type_memberships[4]["membership"].append(row)
    (group,) = suggestions(normalize_core(data, as_of=TODAY))
    (candidate,) = group.candidates
    assert len(candidate.roster_keys) == 2
    assert candidate.roster_keys == tuple(sorted(candidate.roster_keys))


@pytest.mark.parametrize("also_chair", [False, True])
def test_shared_email_is_ambiguous_even_if_other_member_is_not_a_chair(also_chair):
    """Keep every Member ID; shared email cannot identify one authenticated human."""
    data = source()
    data.members[6] = deepcopy(data.members[3]) | {
        "memberDUID": 6,
        "firstName": "Another",
    }
    if also_chair:
        row = deepcopy(data.ministry_type_memberships[4]["membership"][0])
        row["memberId"] = 6
        data.ministry_type_memberships[4]["membership"].append(row)
    corpus = normalize_core(data, as_of=TODAY)
    (group,) = suggestions(corpus)
    assert group.ambiguous and group.email_member_duids == (3, 6)
    assert len(group.candidates) == (2 if also_chair else 1)
    # Iteration order never changes the visible order or grouping.
    reversed_corpus = {
        kind: dict(reversed(tuple(rows.items()))) for kind, rows in corpus.items()
    }
    assert suggestions(reversed_corpus) == (group,)


@pytest.mark.parametrize(
    "defect",
    [
        "schema",
        "wrong_member",
        "wrong_ministry",
        "wrong_contact",
        "noncanonical_email",
        "invalid_current",
        "invalid_publish",
        "unknown_catalog",
        "invalid_roster_key",
        "member_payload_id",
        "ministry_payload_id",
        "nonnumeric_key",
        "noncanonical_key",
        "invalid_name",
        "invalid_email_flag",
        "invalid_role",
    ],
)
def test_malformed_pinned_corpus_fails_closed_with_private_free_errors(defect):
    """Broken linkage is not silently interpreted as a source-driven revocation."""
    corpus = normalize_core(source(), as_of=TODAY)
    roster = next(iter(corpus["roster"].values()))
    contact = corpus["contact"]["member:3"]
    if defect == "schema":
        corpus["member"]["3"]["schema_version"] = True
    elif defect == "wrong_member":
        roster["member_key"] = "8"
    elif defect == "wrong_ministry":
        roster["ministry_key"] = "8"
    elif defect == "wrong_contact":
        contact["owner_key"] = "8"
    elif defect == "noncanonical_email":
        contact["emails"] = [{"value": "PRIVATE@EXAMPLE.ORG", "valid": True}]
    elif defect == "invalid_current":
        roster["current"] = 1
    elif defect == "invalid_publish":
        contact["publish_email"] = "PRIVATE"
    elif defect == "unknown_catalog":
        corpus["ministry"]["4"]["catalog_present"] = False
    elif defect == "member_payload_id":
        corpus["member"]["3"]["memberDUID"] = 8
    elif defect == "ministry_payload_id":
        corpus["ministry"]["4"]["id"] = 8
    elif defect == "nonnumeric_key":
        corpus["member"]["PRIVATE"] = corpus["member"].pop("3")
    elif defect == "noncanonical_key":
        corpus["member"]["03"] = corpus["member"].pop("3")
    elif defect == "invalid_name":
        corpus["ministry"]["4"]["name"] = 1
    elif defect == "invalid_email_flag":
        contact["emails"][0]["valid"] = 1
    elif defect == "invalid_role":
        roster["ministryRoleName"] = 1
    else:
        corpus["roster"] = {"PRIVATE": roster}
    with pytest.raises(InvalidSourcePayload) as caught:
        suggestions(corpus)
    assert str(caught.value) == "Source Chairperson data is inconsistent."


def test_calculation_never_mutates_source_or_applied_configuration():
    """Suggestions cannot install a grant or overwrite source or configuration."""
    corpus = normalize_core(source(), as_of=TODAY)
    document = policy_document()
    before = deepcopy((corpus, document))
    assert chair_suggestions(corpus, document, organization_id=5)
    assert (corpus, document) == before
