"""Chairperson confirmations as previewed, confirmed, installed requests."""

import re
from html import unescape
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.chair_models import (
    ChairSeedEvidence,
    ChairSeedIntent,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.accounts.policy_models import (
    AddressRule,
    AssignmentOverlay,
    MinistryAssignment,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import domain
from ..test_source_corpus import source
from .auth_builders import auth_runtime, signed_in
from .test_background_grants_postgresql import task_login
from .test_chair_suggestions_postgresql import publish, shared, suggestion_row
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_user_views_postgresql import URL as PAGE

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/users/suggestions"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def post(browser, values):
    """Use the genuine CSRF cookie; selections may repeat, as ticked boxes do."""
    return browser.post(
        URL, values | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
    )


def token(response):
    """Confirm only server-rendered signed intent rather than a fabricated patch."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def proposal(store, **values):
    """A preview submission against the applied digest, as the page's form sends it."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "selection": ["4:valid@example.org"],
    } | values


def recorded(browser, values):
    """Preview and confirm as the web role; the request is recorded, not applied."""
    with web():
        signed = token(post(browser, values))
        response = post(browser, {"action": "confirm", "preview": signed})
    assert response.status_code == 302, response.content
    return ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )


def install(store, request):
    """Install as the restricted configuration installer, never the owner."""
    with as_config_installer():
        admit_configuration_database()
        return install_request(store, request_id=request.pk, correlation_id=uuid4())


def confirmed(store, browser, values):
    """Preview, confirm and install a confirmation, asserting it applied."""
    request = recorded(browser, values)
    receipt = install(store, request)
    assert receipt.state == "applied", receipt
    return request


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_a_confirmation_creates_the_seeded_rule_assignment_and_evidence(
    tmp_path, settings, real_limiter, google
):
    """End to end under the real roles: the seed is born confirmed, not suspended."""
    from ..policy_factory import address

    service = auth_runtime(
        tmp_path,
        settings,
        real_limiter,
        [address(), domain("example.org", roles=("staff",))],
    )
    store = service.store
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        body = browser.get(PAGE).content.decode()
    assert 'name="selection" value="4:valid@example.org"' in body
    request = confirmed(store, browser, proposal(store))
    assert request.request_schema == "chair-seed-patch-v9"
    rule = AddressRule.objects.filter(email="valid@example.org").latest("created_at")
    assert rule.creation_origin == "chair-seed"
    assert rule.creation_operation == request.pk
    assert rule.roles == ["ministry_leader", "staff"]
    grants = {grant.role: grant.origins for grant in rule.grants.all()}
    # Staff was inherited from the domain rule; Ministry leader is the seed.
    assert grants == {
        "staff": {"manual": str(request.pk)},
        "ministry_leader": {"chair-seed": str(request.pk)},
    }
    seeded = MinistryAssignment.objects.get(
        configuration=rule.configuration, email="valid@example.org", source="chair-seed"
    )
    assert seeded.ministry_duid == 4 and seeded.operation_id == request.pk
    intent = ChairSeedIntent.objects.get(request=request)
    assert intent.assignment_record_id == seeded.record_id
    assert (intent.organization_id, intent.member_duid) == (12345, 3)
    evidence = ChairSeedEvidence.objects.get(assignment=seeded)
    assert (evidence.organization_id, evidence.member_duid) == (12345, 3)
    assert len(evidence.roster_keys) == 1
    overlay = AssignmentOverlay.objects.get(assignment_record_id=seeded.record_id)
    assert overlay.active is True and overlay.reason == "current_chair"
    with web():
        body = browser.get(PAGE).content.decode()
    row = suggestion_row(body, "valid@example.org")
    assert "Parish source Chairperson</td>" in row and "suspended" not in row
    assert "Exact-address rule: Staff, Ministry leader" in row
    # Confirming the same Ministry again is refused before anything is signed.
    with web():
        response = post(browser, proposal(store))
    assert response.status_code == 400
    assert "already assigned to the address" in response.content.decode()


def two_ministries():
    """The shared address chairs two Ministries: two ambiguous rows at once."""
    from copy import deepcopy

    data = shared()
    data.ministry_types[9] = {"id": 9, "name": "Ushers"}
    data.ministry_type_memberships[9] = deepcopy(data.ministry_type_memberships[4])
    return data


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_an_ambiguous_address_needs_its_member_chosen_for_each_row(
    auth_service, google
):
    """Two Chairpersons on one address: the Administrator names the Member per row."""
    store = auth_service.store
    publish(two_ministries())
    browser, login = signed_in()
    assert login.status_code == 302
    both = ["4:valid@example.org", "9:valid@example.org"]
    with web():
        refused = post(browser, proposal(store, selection=both))
        assert refused.status_code == 400
        assert "Choose the Member" in refused.content.decode()
        # One row answered, the other left at its blank choice.
        partial = post(
            browser,
            proposal(store, selection=both, member=["4:valid@example.org:6", ""]),
        )
        assert partial.status_code == 400
        foreign = post(browser, proposal(store, member=["4:valid@example.org:99"]))
        assert foreign.status_code == 400
    request = confirmed(
        store,
        browser,
        proposal(
            store,
            selection=both,
            member=["4:valid@example.org:6", "9:valid@example.org:3"],
        ),
    )
    intents = {
        intent.assignment_record_id: intent.member_duid
        for intent in ChairSeedIntent.objects.filter(request=request)
    }
    seeded = {
        row.record_id: row.ministry_duid
        for row in MinistryAssignment.objects.filter(
            email="valid@example.org", source="chair-seed"
        )
    }
    assert {seeded[key]: duid for key, duid in intents.items()} == {4: 6, 9: 3}
    assert ChairSeedEvidence.objects.filter(member_duid__in=(3, 6)).count() == 2


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_a_source_that_lost_the_chairperson_refuses_the_activation_durably(
    auth_service, google
):
    """Confirmed, then promoted away: the request fails, the previous policy holds."""
    from parishkit.stewardship.accounts.request_models import (
        ConfigurationRequestCheckpoint,
    )

    from .test_user_rule_views_postgresql import applied
    from .test_user_rule_views_postgresql import proposal as rule_proposal

    store = auth_service.store
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    request = recorded(browser, proposal(store))
    base = store.active()
    data = source()
    data.members[3]["emailAddress"] = "moved@example.org"
    publish(data)
    receipt = install(store, request)
    assert receipt.state == "failed"
    assert (
        ConfigurationRequestCheckpoint.objects.filter(request=request)
        .latest("sequence")
        .failure_code
        == "invalid_candidate"
    )
    assert store.active() == base
    # The refused candidate keeps its immutable projections; nothing seeded
    # reached the active configuration, and no evidence was recorded.
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    active = SystemConfiguration.objects.get().active_configuration_id
    assert active == base.version_id
    assert not MinistryAssignment.objects.filter(
        source="chair-seed", configuration_id=active
    ).exists()
    assert not ChairSeedEvidence.objects.exists()
    # The installer is not wedged: an ordinary rule change still installs.
    applied(
        store,
        browser,
        rule_proposal(
            store, kind="address", identity="new@example.org", roles=["staff"]
        ),
    )


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_seeded_authority_enters_only_through_the_confirmation_schema(
    auth_service, google
):
    """The ordinary schema, a foreign intent and web-tier evidence are refused."""
    from parishkit.stewardship.accounts.chair_confirmation import seed_patch

    store = auth_service.store
    publish(source())
    records = store.active().document()["sections"]["login_rules"]
    actor, key = uuid4(), uuid4()
    patch, _ = seed_patch(
        records, [("valid@example.org", 4)], operation_id=str(uuid4())
    )
    with pytest.raises(ConfigError):
        record_request(
            base_digest=store.active().digest,
            patch=patch,
            actor_id=actor,
            request_key=key,
            correlation_id=uuid4(),
        )
    browser, login = signed_in()
    assert login.status_code == 302
    request = confirmed(store, browser, proposal(store))
    seeded = MinistryAssignment.objects.get(
        email="valid@example.org", source="chair-seed"
    )
    with web():
        # An intent for a record the request never added is refused by SQL.
        with (
            pytest.raises(IntegrityError, match="confirmation request"),
            transaction.atomic(),
        ):
            ChairSeedIntent.objects.create(
                request=request,
                assignment_record_id=uuid4(),
                organization_id=12345,
                member_duid=3,
                actor_id=request.actor_id,
                correlation_id=uuid4(),
            )
        # The web role holds no INSERT on the retained identity evidence.
        with pytest.raises(DatabaseError), transaction.atomic():
            connection.cursor().execute(
                "INSERT INTO stewardship_chair_seed_evidence"
                " (id,actor_id,correlation_id,assignment_record_id,assignment_id,"
                "  snapshot_id,organization_id,member_duid,roster_keys)"
                " VALUES (%s,%s,%s,%s,%s,%s,12345,3,'[]'::jsonb)",
                [uuid4(), request.actor_id, uuid4(), uuid4(), seeded.pk, uuid4()],
            )


@pytest.mark.usefixtures("source_singletons")
def test_a_source_promoted_after_the_preview_makes_it_stale(auth_service, google):
    """The review was drawn against one promoted snapshot; another refuses it."""
    store = auth_service.store
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        signed = token(post(browser, proposal(store)))
    publish(source())
    with web():
        response = post(browser, {"action": "confirm", "preview": signed})
    assert response.status_code == 409
    assert not ConfigurationChangeRequest.objects.filter(
        request_schema="chair-seed-patch-v9"
    ).exists()
    assert not ChairSeedIntent.objects.exists()
