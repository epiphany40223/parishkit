"""Ministry leaders as ParishSoft defines them (#922), for PostgreSQL tests.

A Ministry leader is an active Member holding one of the campaign's leader
roles on a Ministry's current roster, signing in with an email address their
Member record lists. Tests therefore make leaders the way ParishSoft does:
they add Members to the synthetic source and promote it.

Leader Members belong to the fixture's memberless Family 2, which is put in
ParishSoft's "Inactive" Family group so it stays inactive and ineligible as
before: no Family, code or eligibility changes. Each leader holds the "Staff"
role by default, one of the default leader roles, so the Choir fixture's
Chairperson column is untouched.
"""

from copy import deepcopy
from uuid import uuid4

from .test_source_families_postgresql import prepare, promote

# The fixture Family that holds every leader Member.
LEADER_FAMILY = 2
# A Family group the fixture does not use otherwise, named as ParishSoft
# names its inactive group.
INACTIVE_GROUP = 8
# Leader Member DUIDs start here, above every fixture Member.
FIRST_LEADER = 900


def leader(email, *ministries, role="Staff", status="Active", ended=False):
    """One leader Member: its address, Ministries, role and state.

    ``status`` is the Member's ParishSoft status ("Inactive" makes it an
    inactive Member); ``ended`` gives every roster row a past end date, so
    none is current.
    """
    return dict(
        email=email, ministries=ministries, role=role, status=status, ended=ended
    )


def with_leaders(data, leaders):
    """Return a copy of ``data`` with one Member per leader.

    ``leaders`` maps an address to its Ministry DUIDs (each a Staff leader)
    or is a list of ``leader()`` values; two entries with the same address are
    two Members sharing it. A Ministry the source does not list yet is added
    with a plain name.
    """
    data = deepcopy(data)
    if isinstance(leaders, dict):
        leaders = [leader(email, *duids) for email, duids in sorted(leaders.items())]
    data.family_groups[INACTIVE_GROUP] = "Inactive"
    data.families[LEADER_FAMILY]["famGroupID"] = INACTIVE_GROUP
    for offset, item in enumerate(leaders):
        duid = FIRST_LEADER + offset
        data.members[duid] = {
            "memberDUID": duid,
            "familyDUID": LEADER_FAMILY,
            "firstName": "Leader",
            "lastName": f"Example {offset}",
            "memberType": "Adult",
            "memberStatus": item["status"],
            "emailAddress": item["email"],
        }
        for ministry in item["ministries"]:
            data.ministry_types.setdefault(
                ministry, {"id": ministry, "name": f"Ministry {ministry}"}
            )
            data.ministry_type_memberships.setdefault(ministry, {"membership": []})[
                "membership"
            ].append(
                {
                    "memberId": duid,
                    "ministryRoleId": 7,
                    "ministryRoleName": item["role"],
                    "startDate": "2020-01-01",
                    "endDate": "2021-01-01" if item["ended"] else None,
                }
            )
    return data


def promote_leaders(harness, data, leaders):
    """Promote ``data`` with these leaders, as a full refresh would.

    The harness needs the campaign and key rings a promotion reconciles
    Families with (``campaign`` and ``rings``, as the response harness has).
    Returns the promoted snapshot.
    """
    snapshot, claim = prepare(with_leaders(data, leaders))
    return promote(snapshot, claim, harness.campaign, harness.rings)


def make_leader(store, campaign, email, ministries=(9,)):
    """Make ``email`` a ParishSoft leader of ``ministries`` in ``campaign``.

    For tests without the response harness: this promotes the response
    fixture's source with the leader Member (setting up the source singletons
    and key rings a first promotion needs) and turns on Ministry stewardship
    for these Ministries in the draft ``campaign``.
    """
    from parishkit.stewardship.campaigns.domain import EnabledModules
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

    from .campaign_builders import change
    from .credential_builders import keys
    from .response_builders import response_source

    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    snapshot, claim = prepare(with_leaders(response_source(), {email: ministries}))
    promote(snapshot, claim, campaign, keys())
    campaign = Campaign.objects.get(pk=campaign.pk)
    values = campaign.active_configuration.values
    if "ministry" in values["modules"] and set(ministries) <= set(
        values["ministry_duids"]
    ):
        return
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {
                    "modules": EnabledModules.from_list(
                        sorted({*values["modules"], "ministry"})
                    ).to_list(),
                    "ministry_duids": sorted({*values["ministry_duids"], *ministries}),
                },
            }
        ],
    )
    assert result.state == "applied"


def grant_role(store, email, role):
    """Give ``email`` one Admin role as the current model grants it.

    Administrator and Staff come from an exact-address sign-in rule. A
    Ministry leader leads through a ParishSoft role in the current campaign
    (#922): this creates a draft campaign if there is none and makes
    ``email`` a ParishSoft leader of Ministry 9 in it (``make_leader``).
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.models import Campaign

    from ..policy_factory import address
    from .campaign_builders import add_draft, change

    if role == "ministry_leader":
        current = SystemConfiguration.objects.get().current_campaign_id
        if current is None:
            add_draft(store, store.active(), uuid4())
            current = SystemConfiguration.objects.get().current_campaign_id
        make_leader(store, Campaign.objects.get(pk=current), email)
        return
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address(email, roles=(role,)),
            }
        ],
    )
    assert result.state == "applied"
