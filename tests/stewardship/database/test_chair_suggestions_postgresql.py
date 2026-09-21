"""Chairperson suggestions on the Portal users page under the real web role."""

import re
from copy import deepcopy

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts.policy_models import AssignmentOverlay
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.source.chairs import chair_suggestions
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.snapshots import promote_snapshot

from ..policy_factory import address, assignment
from ..test_ministry_activity import policy_document
from ..test_source_corpus import TODAY, source
from .auth_builders import auth_runtime, signed_in
from .test_background_grants_postgresql import task_login
from .test_source_families_postgresql import (
    prepare,
    source_singletons,  # noqa: F401
)
from .test_source_snapshots_postgresql import permit
from .test_user_views_postgresql import URL, views

pytestmark = pytest.mark.django_db(transaction=True)


def publish(data, *, seeded=False):
    """Promote real normalized source; a seeded policy needs the real effect.

    The promotion's receipt guard requires the Chairperson reconciliation
    whenever the applied policy holds a seeded assignment, so that case runs
    the owning effect exactly as the refresh service does.
    """
    snapshot, claim = prepare(data)

    def effects(value):
        """The real source effect over the synthetic tenant's configuration."""
        from parishkit.stewardship.accounts.chair_reconciliation import (
            reconcile_source_chairs,
        )
        from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

        runtime = SystemConfiguration.objects.get()
        reconcile_source_chairs(
            value.pk, claim, campaign_id=runtime.current_campaign_id
        )
        return True

    with work_transaction():
        promote_snapshot(
            snapshot.pk,
            claim,
            admit=permit,
            reconcile=effects if seeded else lambda _: True,
        )
    release_source(claim)
    return snapshot


def shared():
    """A second active Member who is also a Chairperson on the same address."""
    data = source()
    roster = data.ministry_type_memberships[4]["membership"][0]
    data.members[6] = deepcopy(data.members[3]) | {
        "memberDUID": 6,
        "firstName": "Another",
    }
    data.ministry_type_memberships[4]["membership"].append(dict(roster, memberId=6))
    return data


def suggestion_row(body, email):
    """The one suggestion row for this address; the rules tables may name it too.

    A suggestion row begins with its selection cell, before the header cell
    that names the address.
    """
    section = body[body.index('id="chair-suggestions"') :]
    found = re.findall(
        rf'<tr>\s*<td><input[^>]*></td>\s*<th scope="row">{re.escape(email)}[<\s]'
        r".*?</tr>",
        section,
        flags=re.S,
    )
    assert len(found) == 1, email
    return found[0]


def page():
    """The Administrator's page body under the restricted web role."""
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(URL)
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.usefixtures("source_singletons")
def test_administrator_sees_current_chairpersons_with_names_only(auth_service, google):
    """The row names the Member and Ministry; the web role reads nothing wider."""
    data = source()
    snapshot = publish(data)
    body = page()
    suggestion = suggestion_row(body, "valid@example.org")
    assert "Choir (Ministry DUID 4)" in suggestion
    assert "Member Middle Example (DUID 3)" in suggestion
    assert suggestion.count("<td>None</td>") == 3  # rule, assignment, ambiguity
    assert "<td>No</td>" in suggestion  # contact not publishable
    # Four tables now; the audit counts every rendered row and no address.
    (context,) = views()
    assert context["count"] == 2 and "valid@example.org" not in str(context)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        with work_transaction(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT member_name,ministry_name,email,publish_email,address_members"
                " FROM stewardship_chair_suggestion WHERE snapshot_id=%s",
                [snapshot.pk],
            )
            assert cursor.fetchall() == [
                ("Member Middle Example", "Choir", "valid@example.org", False, [3])
            ]
        # The web role reads the Family form's source tables already; what it
        # is not given is the reconciliation owners' Chairperson projection,
        # so the page cannot redefine "current Chairperson" on its own.
        with pytest.raises(DatabaseError), work_transaction():
            connection.cursor().execute("SELECT * FROM stewardship_current_chair")
    # The projection agrees with the pure candidates on every shown fact.
    (group,) = chair_suggestions(
        normalize_core(data, as_of=TODAY), policy_document(), organization_id=5
    )
    (candidate,) = group.candidates
    assert (candidate.member_name, candidate.ministry_name) == (
        "Member Middle Example",
        "Choir",
    )


@pytest.mark.usefixtures("source_singletons")
def test_shared_addresses_and_existing_policy_are_shown_not_guessed(
    tmp_path, settings, real_limiter, google
):
    """Two Chairpersons on one address are both listed; the seed reads as current."""
    seeded = assignment("valid@example.org", ministry=4, seeded=True)
    auth_runtime(
        tmp_path,
        settings,
        real_limiter,
        [
            address(),
            address("valid@example.org", ("ministry_leader",), seeded=True),
            seeded,
        ],
    )
    publish(shared(), seeded=True)
    body = page()
    suggestion = suggestion_row(body, "valid@example.org")
    # The copied Member has no contact-information middle name of its own;
    # an ambiguous row offers each Member as a choice for confirmation.
    assert "Another Example (DUID 6)</label><br>" in suggestion
    assert "Member Middle Example (DUID 3)</label>" in suggestion
    assert 'name="member" value="4:valid@example.org:6"' in suggestion
    assert "2 active Members use this address" in suggestion
    # What the evaluator grants now, not the configured roles: nothing.
    assert "Exact-address rule: no role in effect (Ministry leader suspended)" in (
        suggestion
    )
    # The real effect ran and found no retained identity for the seed, so the
    # assignment is suspended, exactly as a sign-in would see it.
    assert "Parish source Chairperson; suspended" in suggestion
    overlay = AssignmentOverlay.objects.get(assignment_record_id=seeded["id"])
    assert overlay.active is False and overlay.reason == "missing_binding"


def test_without_a_promoted_source_nothing_is_suggested(auth_service, google):
    """No source, no suggestion; the page still renders and audits."""
    body = page()
    assert "records no current Chairperson" in body
    assert 'id="chair-suggestions"' in body and "Current Chairpersons" not in body
