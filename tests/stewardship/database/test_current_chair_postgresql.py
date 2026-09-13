"""Narrow policy reads are snapshot-atomic and cannot expose full census data."""

from copy import deepcopy

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.chairs import chair_suggestions
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.snapshots import promote_snapshot

from ..test_ministry_activity import policy_document
from ..test_source_corpus import TODAY, source
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_source_families_postgresql import (
    prepare,
    source_singletons,  # noqa: F401
)
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def rows():
    """Read exactly the view's public column contract, with deterministic ordering."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM stewardship_current_chair "
            "ORDER BY member_duid,ministry_duid,email,roster_key"
        )
        assert [column.name for column in cursor.description] == [
            "snapshot_id",
            "generation",
            "organization_id",
            "member_duid",
            "ministry_duid",
            "email",
            "publish_email",
            "roster_key",
        ]
        return cursor.fetchall()


def publish(data):
    """Use actual normalization, staging and promotion, not fabricated view rows."""
    snapshot, claim = prepare(data)

    def effects(value):
        """Configured policy fixtures use the real owning source effect too."""
        from parishkit.stewardship.accounts.chair_reconciliation import (
            reconcile_source_chairs,
        )
        from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

        runtime = SystemConfiguration.objects.first()
        if runtime is not None and runtime.active_configuration_id is not None:
            reconcile_source_chairs(
                value.pk, claim, campaign_id=runtime.current_campaign_id
            )
        return True

    with work_transaction():
        promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=effects)
    release_source(claim)
    snapshot.refresh_from_db()
    return snapshot


@pytest.mark.parametrize(
    "variant",
    [
        "normal",
        "inactive",
        "ended",
        "future",
        "participant",
        "missing_role",
        "uppercase",
        "unicode_lookalike",
        "no_email",
        "invalid_email",
        "multiple_emails",
        "duplicate_dates",
        "shared_email",
        "upstream_inactive",
    ],
)
def test_projection_agrees_with_normalized_chair_candidates(variant):
    """SQL and pure policy agree on every relationship and publication flag."""
    data = source()
    roster = data.ministry_type_memberships[4]["membership"][0]
    if variant == "inactive":
        data.members[3]["memberStatus"] = "Inactive"
    elif variant == "ended":
        roster["endDate"] = TODAY.isoformat()
    elif variant == "future":
        roster["startDate"] = "2099-01-01"
    elif variant == "participant":
        roster["ministryRoleName"] = "Participant"
    elif variant == "missing_role":
        del roster["ministryRoleName"]
    elif variant == "uppercase":
        roster["ministryRoleName"] = "CHAIRPERSON"
    elif variant == "unicode_lookalike":
        roster["ministryRoleName"] = "Chairperſon"
    elif variant in {"no_email", "invalid_email", "multiple_emails"}:
        data.members[3]["emailAddress"] = {
            "no_email": "",
            "invalid_email": "invalid",
            "multiple_emails": "one@example.org; TWO@EXAMPLE.ORG",
        }[variant]
    elif variant == "duplicate_dates":
        data.ministry_type_memberships[4]["membership"].append(
            dict(roster, startDate="2026-02-01")
        )
    elif variant == "shared_email":
        data.members[6] = deepcopy(data.members[3]) | {"memberDUID": 6}
        data.ministry_type_memberships[4]["membership"].append(dict(roster, memberId=6))
    elif variant == "upstream_inactive":
        data.ministry_types[4]["active"] = False
    # prepare() normalizes the copied tenant ID; compare only relationship fields.
    candidates = chair_suggestions(
        normalize_core(data, as_of=TODAY), policy_document(), organization_id=5
    )
    snapshot = publish(data)
    actual = rows()
    assert all(row[:3] == (snapshot.pk, snapshot.generation, 12345) for row in actual)
    assert [row[3:] for row in actual] == sorted(
        (item.member_duid, item.ministry_duid, item.email, item.publish_email, key)
        for group in candidates
        for item in group.candidates
        for key in item.roster_keys
    )


def test_staged_and_historical_contacts_cannot_supply_current_relationship():
    """A cleared current contact never falls back to an older snapshot's address."""
    first = publish(source())
    original = rows()
    data = source()
    data.members[3]["emailAddress"] = ""
    second, claim = prepare(data)
    assert rows() == original and original[0][0] == first.pk

    def inside_promotion(snapshot):
        """The policy effect sees the new complete generation before commit."""
        assert snapshot.pk == second.pk
        assert not rows()
        return True

    with work_transaction():
        promote_snapshot(second.pk, claim, admit=permit, reconcile=inside_promotion)
    assert not rows()
    release_source(claim)


def test_failed_promotion_restores_exact_previous_projection():
    """The view cannot retain effects from a rejected source-generation switch."""
    publish(source())
    before = rows()
    data = source()
    data.members[3]["emailAddress"] = "changed@example.org"
    snapshot, claim = prepare(data)

    def fail(snapshot):
        """Exercise rollback after the source pointer was actually switched."""
        assert rows()[0][5] == "changed@example.org"
        raise RuntimeError("Synthetic later policy failure")

    with pytest.raises(RuntimeError, match="later policy"), work_transaction():
        promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=fail)
    assert rows() == before
    release_source(claim)


@pytest.mark.usefixtures("config_role")
def test_restricted_installer_can_read_only_narrow_relationships():
    """Real session authorization cannot escape the view into private base tables."""
    publish(source())
    expected = rows()
    with as_config_installer():
        admit_configuration_database()
        assert rows() == expected
        for table in (
            "stewardship_source_member",
            "stewardship_source_contact",
            "stewardship_source_family",
            "stewardship_source_contribution",
            "stewardship_snapshot_contact",
        ):
            with pytest.raises(DatabaseError) as raised, connection.cursor() as cursor:
                cursor.execute(f'SELECT * FROM "{table}"')
            assert raised.value.__cause__.sqlstate == "42501"
        # A JOIN view is non-updatable even with excessive write grants. Check
        # actual privileges rather than accepting its structural 55000 error.
        with connection.cursor() as cursor:
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                cursor.execute(
                    "SELECT has_table_privilege(current_user,"
                    "'stewardship_current_chair',%s)",
                    [privilege],
                )
                assert cursor.fetchone() == (False,)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT reloptions FROM pg_class WHERE relname='stewardship_current_chair'"
        )
        assert cursor.fetchone() == (["security_barrier=true"],)
