"""Source-derived access and deliverability never use Family/publication emails."""

import pytest

from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.families import family_statuses

from .test_source_corpus import TODAY, source


def statuses(data=None, suppressed=frozenset()):
    """Normalize real shared data before projecting campaign identity inputs."""
    return family_statuses(
        normalize_core(source() if data is None else data, as_of=TODAY),
        suppressed_addresses=suppressed,
    )


def test_default_status_uses_heads_despite_unpublishable_contact():
    """A private head email is eligible; an empty Family is retained but inactive."""
    one, two = statuses()
    assert one.duid == 1 and one.active and one.portal_eligible
    assert one.email_eligible and one.email_deliverable
    assert one.status_reason == "eligible"
    assert two.duid == 2 and not two.active and not two.portal_eligible
    assert two.status_reason == "inactive"


def test_suppression_changes_deliverability_not_syntactic_eligibility_or_access():
    """All valid head addresses suppressed still leaves a code-eligible Family."""
    one, _ = statuses(suppressed=frozenset({"valid@example.org"}))
    assert one.portal_eligible and one.email_eligible and not one.email_deliverable
    assert one.deliverability_reason == "provider_suppressed"


def test_one_unsuppressed_head_address_is_enough():
    """A failed address does not suppress another valid recipient in the Family."""
    data = source()
    data.members[3]["emailAddress"] = "valid@example.org; another@example.org"
    one, _ = statuses(data, frozenset({"valid@example.org"}))
    assert one.email_deliverable


def test_family_email_does_not_replace_missing_head_email():
    """No-email Families retain access even when their Family DTO has an email."""
    data = source()
    data.members[3]["emailAddress"] = "not-an-email"
    one, _ = statuses(data)
    assert one.portal_eligible and not one.email_eligible
    assert one.deliverability_reason == "no_eligible_email"


def test_active_nonparishioner_has_no_campaign_access():
    """Shared activity and registered-parish membership remain distinct facts."""
    data = source()
    data.families[1]["registeredOrganizationID"] = 999
    one, _ = statuses(data)
    assert one.active and not one.portal_eligible and not one.email_deliverable
    assert one.status_reason == "non_parishioner"


@pytest.mark.parametrize("suppressed", [set(), [], "valid@example.org", None])
def test_suppression_input_must_be_an_explicit_immutable_set(suppressed):
    """Accidental strings or missing suppression evaluation cannot imply success."""
    with pytest.raises(TypeError, match="explicit suppression"):
        statuses(suppressed=suppressed)


@pytest.mark.parametrize("email", ["VALID@example.org", "bad", 5, None])
def test_suppression_identities_must_be_canonical(email):
    """Noncanonical suppression identities cannot silently evade matching."""
    with pytest.raises(ValueError, match="canonical"):
        statuses(suppressed=frozenset({email}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("active", 1),
        ("portal_eligible", False),
        ("email_eligible", False),
        ("schema_version", 99),
        ("schema_version", True),
        ("active_head_duids", [3, 3]),
        ("active_head_duids", [True]),
        ("active_head_duids", [999]),
    ],
)
def test_inconsistent_normalized_source_fails_closed(field, value):
    """Generic storage permits older payload schemas; identity requires its schema."""
    corpus = normalize_core(source(), as_of=TODAY)
    corpus["family"]["1"][field] = value
    with pytest.raises(InvalidSourcePayload, match="inconsistent"):
        family_statuses(corpus, suppressed_addresses=frozenset())


@pytest.mark.parametrize("part", ["member", "contact"])
def test_heads_must_belong_to_the_same_source_family(part):
    """A head ID cannot take another household's contact data into eligibility."""
    corpus = normalize_core(source(), as_of=TODAY)
    if part == "member":
        corpus[part]["3"]["family_key"] = "2"
    else:
        corpus[part]["member:3"]["owner_key"] = "99"
    with pytest.raises(InvalidSourcePayload, match="inconsistent"):
        family_statuses(corpus, suppressed_addresses=frozenset())
