"""Deterministic synthetic parish for the local environment's fake ParishSoft.

``generate`` builds one fictional parish from a seed, a Family count and the
anchor date (the fake clock's local date when the deployment was created).
The same inputs produce the same records, field for field, so the fake's
responses are reproducible across restarts (see "Synthetic parish" in
docs/specs/stewardship/local-environment/spec.md). Names come from the fixed
lists below and every email address is at ``@example.test``; no record here
describes a real person.

Collections scale with the Family count ``N`` (``scaling``), reproducing the
earlier 1,100-Family design at ``N = 1,100``, with floors where a proportional
count would be too small to exercise a feature. One extra, late-added Family
is generated in addition to ``N``; the fake holds it back until its
``release_at`` instant so a refresh can be seen picking it up.
"""

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from parishkit.parishsoft import HEAD_MEMBER_TYPES

from . import LOCAL_ORGANIZATION_ID, LOCAL_ORGANIZATION_NAME

DEFAULT_FAMILIES = 100
MAXIMUM_FAMILIES = 100000
# The Family count the ratios below were designed at (the real parish's size).
REFERENCE_FAMILIES = 1100

# Family sizes one to five, in that order (mean 2.7 Members per Family).
FAMILY_SIZE_SHARES = (0.20, 0.30, 0.20, 0.20, 0.10)
# Of Families with two or more Members, the share with two heads (a couple).
COUPLE_SHARE = 0.8
# Of non-head Members, the share who are children; the rest are other adults.
CHILD_SHARE = 0.8
INACTIVE_FAMILY_SHARE = 0.08
DECEASED_MEMBER_SHARE = 0.02
NO_EMAIL_FAMILY_SHARE = 0.10
MULTIPLE_EMAIL_HEAD_SHARE = 0.03
# Ministry roster shape, as shares of non-child active Members (volunteers)
# and of volunteers (two or more, three or more Ministries), then of entries.
VOLUNTEER_SHARE = 0.40
TWO_PLUS_SHARE = 0.30
THREE_PLUS_SHARE = 0.10
ENDED_ENTRY_SHARE = 0.05
PLEDGES_PER_FAMILY = 0.64
CONTRIBUTIONS_PER_FAMILY = 13.6
CONTRIBUTION_MONTHS = 15

# Identity bases keep each collection's identifiers visibly distinct.
FAMILY_BASE = 100000
MEMBER_BASE = 500000
FAMILY_WORKGROUP_BASE = 7000
MEMBER_WORKGROUP_BASE = 8000
MINISTRY_BASE = 9000
FUND_BASE = 300
PLEDGE_BASE = 400000
CONTRIBUTION_BASE = 600000

ADULT_TYPES = frozenset({*HEAD_MEMBER_TYPES, "Other"})

# Fixed catalogs. Nothing below names a real person or place.
FAMILY_GROUPS = (
    (1, "Active"),
    (2, "Inactive"),
    (3, "Contributor Only"),
    (4, "Staff"),
    (5, "Visitor"),
    (6, "Moved"),
)
ACTIVE_GROUP, INACTIVE_GROUP, CONTRIBUTOR_GROUP = 1, 2, 3
FAMILY_WORKGROUP_NAMES = (
    "Business Logistics Email",
    "Parish Council",
    "Finance Council",
    "Festival Volunteers",
    "Welcome Committee",
    "Bereavement Support",
    "Garden Club",
    "Bulletin Recipients",
)
MEMBER_WORKGROUP_NAMES = (
    "Lectors",
    "Ushers",
    "Choir",
    "Altar Servers",
    "Parish Staff",
    "Catechists",
    "Youth Group Leaders",
    "Money Counters",
    "Sacristans",
    "Greeters",
)
MINISTRY_NAMES = (
    "Altar Servers",
    "Lectors",
    "Extraordinary Ministers of Holy Communion",
    "Ushers and Greeters",
    "Adult Choir",
    "Children's Choir",
    "Cantors",
    "Music Ministry",
    "Sacristans",
    "Art and Environment",
    "Religious Education Catechists",
    "RCIA Team",
    "Youth Ministry",
    "Young Adult Ministry",
    "Baptism Preparation",
    "Marriage Preparation",
    "Bereavement Ministry",
    "Homebound Ministry",
    "Hospital Visitors",
    "Prayer Chain",
    "Food Pantry",
    "St. Vincent de Paul Society",
    "Knights of Columbus",
    "Women's Club",
    "Men's Club",
    "Parish Festival Committee",
    "Hospitality Ministry",
    "Welcome Committee",
    "Parish Council",
    "Finance Council",
    "Stewardship Committee",
    "Buildings and Grounds",
    "Garden Ministry",
    "Technology Ministry",
    "Communications Committee",
    "Bulletin Team",
    "Vacation Bible School",
    "Scouting Liaisons",
    "Respect Life Committee",
    "Social Justice Committee",
)
FUNDS = (
    ("Offertory", True),
    ("Building Fund", True),
    ("Outreach", False),
    ("Religious Education", False),
    ("Capital Campaign", True),
    ("School Support", False),
)
MALE_NAMES = (
    "James",
    "John",
    "Robert",
    "Michael",
    "William",
    "David",
    "Joseph",
    "Thomas",
    "Daniel",
    "Matthew",
    "Anthony",
    "Mark",
    "Paul",
    "Steven",
    "Andrew",
    "Kevin",
    "Brian",
    "Timothy",
    "Patrick",
    "Peter",
    "Gregory",
    "Francis",
    "Luke",
    "Samuel",
    "Benjamin",
    "Henry",
    "Jacob",
    "Nicholas",
    "Dominic",
    "Vincent",
)
FEMALE_NAMES = (
    "Mary",
    "Patricia",
    "Jennifer",
    "Linda",
    "Elizabeth",
    "Barbara",
    "Susan",
    "Margaret",
    "Sarah",
    "Karen",
    "Lisa",
    "Nancy",
    "Sandra",
    "Ashley",
    "Emily",
    "Michelle",
    "Carol",
    "Amanda",
    "Melissa",
    "Deborah",
    "Rebecca",
    "Laura",
    "Catherine",
    "Teresa",
    "Anne",
    "Claire",
    "Grace",
    "Rose",
    "Lucy",
    "Clare",
)
LAST_NAMES = (
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Miller",
    "Davis",
    "Wilson",
    "Anderson",
    "Taylor",
    "Thomas",
    "Moore",
    "Martin",
    "Jackson",
    "Thompson",
    "White",
    "Harris",
    "Clark",
    "Lewis",
    "Robinson",
    "Walker",
    "Young",
    "Allen",
    "King",
    "Wright",
    "Scott",
    "Green",
    "Baker",
    "Adams",
    "Nelson",
    "Hill",
    "Campbell",
    "Mitchell",
    "Roberts",
    "Carter",
    "Phillips",
    "Evans",
    "Turner",
    "Parker",
    "Collins",
    "Edwards",
    "Stewart",
    "Morris",
    "Murphy",
    "Cook",
    "Rogers",
    "Morgan",
    "Cooper",
    "Reed",
    "Bailey",
    "Bell",
    "Kelly",
    "Howard",
    "Ward",
    "Cox",
    "Richardson",
    "Wood",
    "Watson",
    "Brooks",
    "Bennett",
    "Gray",
    "Hughes",
    "Price",
    "Sanders",
    "Myers",
    "Long",
    "Ross",
    "Foster",
    "Powell",
    "Sullivan",
    "Russell",
    "Fisher",
    "Henderson",
    "Coleman",
    "Patterson",
    "Graham",
    "Hamilton",
    "Wallace",
    "Fleming",
    "O'Brien",
    "Nowak",
    "Kowalski",
    "Schmidt",
    "Fischer",
    "Weber",
    "Garcia",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Rossi",
    "Russo",
    "Nguyen",
    "Tran",
    "Pham",
    "Kim",
    "Santos",
    "Reyes",
    "Dela Cruz",
    "Okafor",
)
STREETS = (
    "Maple Street",
    "Oak Avenue",
    "Church Street",
    "Hillcrest Drive",
    "Riverside Road",
    "Elm Court",
    "Cedar Lane",
    "Park Avenue",
    "Lakeview Drive",
    "Orchard Way",
    "Chestnut Street",
    "Meadow Lane",
    "Spring Street",
    "Sunset Boulevard",
    "Willow Road",
    "Highland Avenue",
)
TOWNS = (
    ("Springfield", "40110"),
    ("Riverton", "40114"),
    ("Lakeside", "40118"),
    ("Hillcrest", "40122"),
    ("Maple Grove", "40126"),
)
STATE = "KY"
# A few Families have a second address line (an apartment or unit).
UNIT_SHARE = 0.15
PLEDGE_AMOUNTS = (250, 500, 600, 1000, 1200, 1500, 2400, 3000, 5000)
CONTRIBUTION_AMOUNTS = (10, 20, 25, 40, 50, 75, 100, 150, 200, 250, 500)
# Of contributions, the share recorded without a Family (loose cash).
ANONYMOUS_CONTRIBUTION_SHARE = 0.01
# Of contributions, the share to the first fund (the Sunday offertory).
OFFERTORY_SHARE = 0.75
ROLES = ((1, "Member"), (2, "Leader"))


@dataclass(frozen=True)
class SyntheticParish:
    """One generated parish, in the stable order every fake response uses.

    Rows carry exactly the provider fields the application reads; the fake adds
    paging fields (counts and ordinals) when it serves them. Rosters map an
    identity to its membership rows. ``late_family_id`` names the one Family
    the fake holds back until ``release_at``; it has no giving, workgroup or
    Ministry rows, as a newly registered Family would not.
    """

    organization_id: int
    seed: int
    family_count: int
    anchor_date: str
    organization: dict
    families: list = field(repr=False)
    members: list = field(repr=False)
    contacts: list = field(repr=False)
    family_groups: list = field(repr=False)
    family_workgroups: list = field(repr=False)
    family_workgroup_rosters: dict = field(repr=False)
    member_workgroups: list = field(repr=False)
    member_workgroup_rosters: dict = field(repr=False)
    ministries: list = field(repr=False)
    ministry_rosters: dict = field(repr=False)
    funds: list = field(repr=False)
    pledges: list = field(repr=False)
    contributions: list = field(repr=False)
    late_family_id: int


def _scaled(families, per_reference, floor):
    """Round ``per_reference`` at 1,100 Families to this size, with a floor."""
    return max(floor, round(per_reference * families / REFERENCE_FAMILIES))


def scaling(families):
    """The collection sizes the specification's scaling table pins for ``N``.

    Member and child counts follow from the Family size mix and are not fixed
    here; ``generate`` draws them, and tests check them as proportions.
    """
    if type(families) is not int or not 1 <= families <= MAXIMUM_FAMILIES:
        raise ValueError("The synthetic parish needs a Family count.")
    return {
        "families": families,
        "inactive_families": round(INACTIVE_FAMILY_SHARE * families),
        "no_email_families": round(NO_EMAIL_FAMILY_SHARE * families),
        "family_groups": len(FAMILY_GROUPS),
        "family_workgroups": _scaled(families, 8, 4),
        "member_workgroups": _scaled(families, 10, 5),
        "ministries": _scaled(families, 40, 25),
        "funds": _scaled(families, 6, 4),
        "pledges": round(PLEDGES_PER_FAMILY * families),
        "contributions": round(CONTRIBUTIONS_PER_FAMILY * families),
    }


def _quota(total, shares):
    """Split ``total`` by ``shares`` with largest-remainder rounding."""
    exact = [total * share for share in shares]
    counts = [int(value) for value in exact]
    for index in sorted(
        range(len(shares)), key=lambda i: exact[i] - counts[i], reverse=True
    )[: total - sum(counts)]:
        counts[index] += 1
    return counts


def _day(value):
    """ParishSoft serialises civil dates as midnight timestamps."""
    return None if value is None else value.isoformat() + "T00:00:00"


def _months_before(day, months):
    """The same day of the month ``months`` earlier, clipped to that month."""
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    for attempt in range(4):
        try:
            return day.replace(year=year, month=month, day=day.day - attempt)
        except ValueError:
            continue
    return day.replace(year=year, month=month, day=1)


def age_at(born, today):
    """Whole years from ``born`` to ``today``."""
    years = today.year - born.year
    if (today.month, today.day) < (born.month, born.day):
        years -= 1
    return years


def is_child(member, anchor):
    """The specification's child test: typed ``Child`` or under 18 at the anchor."""
    if member.get("memberType") == "Child":
        return True
    born = member.get("birthdate")
    if not born:
        return False
    return age_at(date.fromisoformat(born[:10]), anchor) < 18


class _Generator:
    """One deterministic pass; every random draw comes from ``self.rng`` in order."""

    def __init__(self, seed, families, anchor, organization_id):
        self.rng = random.Random(seed)
        self.seed, self.count, self.anchor = seed, families, anchor
        self.organization_id = organization_id
        self.counts = scaling(families)
        self.families, self.members, self.contacts = [], [], []
        self.heads = {}  # family DUID -> head Member rows
        self.late_family_id = None

    # -- Members and Families -------------------------------------------------

    def _birthdate(self, age):
        """A birthday ``age`` years before the anchor, somewhere in that year."""
        born = self.anchor - timedelta(days=365 * age + self.rng.randint(0, 364))
        return _day(born)

    def _email(self, first, last, duid):
        return (
            f"{first}.{last}{duid}@example.test".lower()
            .replace("'", "")
            .replace(" ", "")
        )

    def _phone(self):
        return f"555-{self.rng.randint(200, 999)}-{self.rng.randint(1000, 9999)}"

    def _member(self, family_id, last, *, member_type, sex, age, married):
        """One Member search row plus its contact-list row, both appended."""
        duid = MEMBER_BASE + len(self.members) + 1
        first = self.rng.choice(MALE_NAMES if sex == "Male" else FEMALE_NAMES)
        adult = member_type in ADULT_TYPES
        middle = (
            self.rng.choice("ABCDEFGHJKLMNPRSTW") + "."
            if self.rng.random() < 0.3
            else None
        )
        nick = (
            first[:3] if adult and len(first) > 4 and self.rng.random() < 0.1 else None
        )
        email = self._email(nick or first, last, duid) if adult else None
        if (
            adult
            and member_type in HEAD_MEMBER_TYPES
            and (self.rng.random() < MULTIPLE_EMAIL_HEAD_SHARE)
        ):
            email = f"{email}; {first.lower()}.{duid}@example.test"
        mobile = self._phone() if adult and self.rng.random() < 0.85 else None
        if married:
            status, status_id = "Married", 2
        elif adult:
            status, status_id = self.rng.choice((("Single", 1), ("Widowed", 4)))
        else:
            status, status_id = "Single", 1
        row = {
            "memberDUID": duid,
            "familyDUID": family_id,
            "firstName": first,
            "middleName": middle,
            "lastName": last,
            "nickName": nick,
            "salutation": (
                ("Mr." if sex == "Male" else "Mrs." if married else "Ms.")
                if adult
                else None
            ),
            "suffix": None,
            "maidenName": None,
            "memberType": member_type,
            "memberStatus": "Active",
            "sex": sex,
            "maritalStatus": status,
            "maritalStatusID": status_id,
            "language": "English",
            "birthdate": self._birthdate(age),
            "dateOfDeath": None,
            "emailAddress": email,
            "homePhone": None,
            "mobilePhone": mobile,
            "workPhone": None,
            "family_PublishEMail": True,
            "family_PublishPhone": True,
            "dateModified": _day(self.anchor - timedelta(days=200)),
        }
        self.members.append(row)
        return row

    def _family(self, index, size):
        """Build one Family with ``size`` Members: heads first, then dependants."""
        duid = FAMILY_BASE + index + 1
        last = self.rng.choice(LAST_NAMES)
        couple = size >= 2 and self.rng.random() < COUPLE_SHARE
        heads = []
        if couple:
            age = self.rng.randint(25, 85)
            heads.append(
                self._member(
                    duid, last, member_type="Husband", sex="Male", age=age, married=True
                )
            )
            heads.append(
                self._member(
                    duid,
                    last,
                    member_type="Wife",
                    sex="Female",
                    age=max(22, age + self.rng.randint(-5, 5)),
                    married=True,
                )
            )
        else:
            heads.append(
                self._member(
                    duid,
                    last,
                    member_type="Head",
                    sex=self.rng.choice(("Male", "Female")),
                    age=self.rng.randint(22, 90),
                    married=False,
                )
            )
        for _ in range(size - len(heads)):
            sex = self.rng.choice(("Male", "Female"))
            if self.rng.random() < CHILD_SHARE:
                self._member(
                    duid,
                    last,
                    member_type="Child",
                    sex=sex,
                    age=self.rng.randint(0, 17),
                    married=False,
                )
            else:
                self._member(
                    duid,
                    last,
                    member_type="Other",
                    sex=sex,
                    age=self.rng.choice(
                        (self.rng.randint(18, 30), self.rng.randint(65, 95))
                    ),
                    married=False,
                )
        self.heads[duid] = heads
        home = self._phone() if self.rng.random() < 0.7 else None
        town, postal = self.rng.choice(TOWNS)
        family = {
            "familyDUID": duid,
            "familyID": index + 1,
            "firstName": " and ".join(head["firstName"] for head in heads),
            "lastName": last,
            "mailingName": None,  # filled once the heads' final state is known
            "envelopeNumber": index + 101,
            "registeredOrganizationID": self.organization_id,
            "famGroupID": ACTIVE_GROUP,
            "status": True,
            "familyParticipationStatus": "Active",
            "hasMembers": size,
            "hasSuspense": False,
            "sendNoMail": False,
            "eMailAddress": None,
            "familyHomePhone": home,
            "primaryPhone": home or heads[0]["mobilePhone"],
            "primaryPublishEMail": True,
            "primaryPublishPhone": True,
            "primaryPublishAddress": True,
            "primaryAddress1": (
                f"{self.rng.randint(1, 9999)} {self.rng.choice(STREETS)}"
            ),
            "primaryAddress2": (
                f"Apt {self.rng.randint(1, 40)}"
                if self.rng.random() < UNIT_SHARE
                else None
            ),
            "primaryAddress3": None,
            "primaryCity": town,
            "primaryState": STATE,
            "primaryPostalCode": postal,
            "primaryZipPlus": f"{self.rng.randint(1000, 9999)}",
            "dateModified": _day(self.anchor - timedelta(days=200)),
        }
        self.families.append(family)
        return family

    def _finish_family(self, family):
        """Derive the Family's name and email fields from its heads' final state."""
        heads = self.heads[family["familyDUID"]]
        family["firstName"] = " and ".join(head["firstName"] for head in heads)
        if family["mailingName"] is None:
            family["mailingName"] = f"{family['firstName']} {family['lastName']}"
        emails = [head["emailAddress"] for head in heads if head["emailAddress"]]
        family["eMailAddress"] = "; ".join(emails) if emails else None

    def _contact(self, member, family):
        """The contact-list and household-member-list row for one Member."""
        return {
            "memberDUID": member["memberDUID"],
            "familyDUID": member["familyDUID"],
            "middleName": member["middleName"],
            "nickName": member["nickName"],
            "maidenName": member["maidenName"],
            "dateOfBirth": member["birthdate"],
            "dateOfDeath": member["dateOfDeath"],
            "gender": member["sex"],
            "emailAddress": member["emailAddress"],
            "homePhone": family["familyHomePhone"],
            "cellPhone": member["mobilePhone"],
            "workPhone": member["workPhone"],
        }

    def build_households(self):
        """Families, Members and the data-quality cases the specification lists."""
        sizes = []
        for size, count in enumerate(_quota(self.count, FAMILY_SIZE_SHARES), start=1):
            sizes.extend([size] * count)
        self.rng.shuffle(sizes)
        for index, size in enumerate(sizes):
            self._family(index, size)
        regular = list(self.families)
        # The late-added Family: a couple with one child, registered after the
        # initial load. Generated last so every other identity is unaffected.
        late = self._family(len(sizes), 3)
        self.late_family_id = late["familyDUID"]
        late["dateModified"] = _day(self.anchor)

        # Inactive and non-parishioner Families, half each.
        chosen = self.rng.sample(regular, self.counts["inactive_families"])
        for position, family in enumerate(chosen):
            if position % 2 == 0:
                family["famGroupID"] = INACTIVE_GROUP
                family["familyParticipationStatus"] = "Inactive"
                family["status"] = False
                for member in self.members:
                    if member["familyDUID"] == family["familyDUID"]:
                        member["memberStatus"] = "Inactive"
            else:
                family["registeredOrganizationID"] = self.organization_id + 1
                family["famGroupID"] = CONTRIBUTOR_GROUP
        # Families whose heads have no email, among the ordinary ones so the
        # effect on eligibility is visible.
        ordinary = [family for family in regular if family not in chosen]
        for family in self.rng.sample(
            ordinary, min(len(ordinary), self.counts["no_email_families"])
        ):
            for head in self.heads[family["familyDUID"]]:
                head["emailAddress"] = None
        # Deceased Members, drawn from adults whose Family keeps another adult
        # so the Family's own activity is unchanged.
        adults_by_family = {}
        for member in self.members:
            if member["memberType"] in ADULT_TYPES:
                adults_by_family.setdefault(member["familyDUID"], []).append(member)
        candidates = [
            member
            for duid, adults in adults_by_family.items()
            if len(adults) >= 2 and duid != self.late_family_id
            for member in adults
        ]
        regular_members = sum(
            1 for member in self.members if member["familyDUID"] != self.late_family_id
        )
        for member in self.rng.sample(
            candidates,
            min(len(candidates), round(DECEASED_MEMBER_SHARE * regular_members)),
        ):
            member["memberStatus"] = "Deceased"
            member["dateOfDeath"] = _day(
                self.anchor - timedelta(days=self.rng.randint(30, 2000))
            )
        # Data-quality cases: a blank mailing name and envelope number 0.
        regular[7 % len(regular)]["mailingName"] = ""
        regular[11 % len(regular)]["envelopeNumber"] = 0
        for family in self.families:
            self._finish_family(family)
        by_family = {family["familyDUID"]: family for family in self.families}
        self.contacts = [
            self._contact(member, by_family[member["familyDUID"]])
            for member in self.members
        ]

    # -- Catalogs, workgroups, Ministries ------------------------------------

    def _regular_families(self):
        return [f for f in self.families if f["familyDUID"] != self.late_family_id]

    def _volunteers(self):
        """Active non-child Members of the regular Families, in Member order."""
        return [
            member
            for member in self.members
            if member["familyDUID"] != self.late_family_id
            and member["memberStatus"] == "Active"
            and not is_child(member, self.anchor)
        ]

    def build_workgroups(self):
        """Family and Member workgroups with small random rosters."""
        families = self._regular_families()
        adults = self._volunteers()
        self.family_workgroups, self.family_workgroup_rosters = [], {}
        for index in range(self.counts["family_workgroups"]):
            duid = FAMILY_WORKGROUP_BASE + index
            name = (
                FAMILY_WORKGROUP_NAMES[index]
                if index < len(FAMILY_WORKGROUP_NAMES)
                else f"Family Workgroup {index + 1}"
            )
            self.family_workgroups.append(
                {"workgroupDUID": duid, "workgroupName": name, "workgroupID": index + 1}
            )
            chosen = self.rng.sample(
                families, min(len(families), max(1, round(len(families) * 0.1)))
            )
            self.family_workgroup_rosters[duid] = [
                {
                    "familyId": family["familyDUID"],
                    "familyName": family["mailingName"],
                    "email": family["eMailAddress"],
                }
                for family in sorted(chosen, key=lambda f: f["familyDUID"])
            ]
        self.member_workgroups, self.member_workgroup_rosters = [], {}
        for index in range(self.counts["member_workgroups"]):
            identifier = MEMBER_WORKGROUP_BASE + index
            name = (
                MEMBER_WORKGROUP_NAMES[index]
                if index < len(MEMBER_WORKGROUP_NAMES)
                else f"Member Workgroup {index + 1}"
            )
            self.member_workgroups.append({"id": identifier, "name": name})
            chosen = self.rng.sample(
                adults, min(len(adults), max(1, round(len(adults) * 0.05)))
            )
            self.member_workgroup_rosters[identifier] = [
                {
                    "memberId": member["memberDUID"],
                    "familyId": member["familyDUID"],
                    "firstName": member["firstName"],
                    "lastName": member["lastName"],
                    "emailAddress": member["emailAddress"],
                }
                for member in sorted(chosen, key=lambda m: m["memberDUID"])
            ]

    def _roster_row(self, member, ministry, role, *, ended):
        """One minister-list row; ended entries end before the anchor date."""
        started = self.anchor - timedelta(days=self.rng.randint(30, 3650))
        end = None
        if ended:
            end = started + timedelta(
                days=self.rng.randint(1, max(1, (self.anchor - started).days - 1))
            )
        return {
            "memberId": member["memberDUID"],
            "familyId": member["familyDUID"],
            "firstName": member["firstName"],
            "lastName": member["lastName"],
            "ministryTypeId": ministry["id"],
            "ministryTypeName": ministry["name"],
            "ministryRoleId": role[0],
            "ministryRoleName": role[1],
            "ministryEventId": None,
            "ministryEventName": None,
            "trained": self.rng.random() < 0.6,
            "subOnly": False,
            "startDate": _day(started),
            "endDate": _day(end),
        }

    def build_ministries(self):
        """Ministries whose rosters hold only active non-child Members.

        About 40% of those Members volunteer; of the volunteers about 30% serve
        two or more Ministries and about 10% three or more. Each Ministry first
        receives one volunteer (its leader, never an ended entry), so every
        Ministry has an active Member; about 5% of the remaining entries are
        ended. With very few Families the leader round gives some volunteers
        more Ministries than their target, so the shares are only approximate
        there.
        """
        self.ministries = [
            {
                "id": MINISTRY_BASE + index,
                "name": (
                    MINISTRY_NAMES[index]
                    if index < len(MINISTRY_NAMES)
                    else f"Ministry {index + 1}"
                ),
                "active": True,
            }
            for index in range(self.counts["ministries"])
        ]
        eligible = self._volunteers()
        if not eligible:
            raise ValueError("The synthetic parish has no eligible volunteers.")
        # At least one volunteer, so a one-Family parish still has rosters.
        volunteers = self.rng.sample(
            eligible, max(1, round(VOLUNTEER_SHARE * len(eligible)))
        )
        three, two = (
            round(THREE_PLUS_SHARE * len(volunteers)),
            round((TWO_PLUS_SHARE - THREE_PLUS_SHARE) * len(volunteers)),
        )
        targets = {
            member["memberDUID"]: 3 if i < three else 2 if i < three + two else 1
            for i, member in enumerate(volunteers)
        }
        assigned = {member["memberDUID"]: set() for member in volunteers}
        entries = []  # (member, ministry, role, is_leader)
        for index, ministry in enumerate(self.ministries):
            member = volunteers[index % len(volunteers)]
            assigned[member["memberDUID"]].add(ministry["id"])
            entries.append((member, ministry, ROLES[1], True))
        by_id = {ministry["id"]: ministry for ministry in self.ministries}
        for member in volunteers:
            while len(assigned[member["memberDUID"]]) < targets[member["memberDUID"]]:
                open_ids = sorted(set(by_id) - assigned[member["memberDUID"]])
                if not open_ids:
                    break
                ministry = by_id[self.rng.choice(open_ids)]
                assigned[member["memberDUID"]].add(ministry["id"])
                entries.append((member, ministry, ROLES[0], False))
        ordinary = [index for index, entry in enumerate(entries) if not entry[3]]
        ended = set(
            self.rng.sample(ordinary, round(ENDED_ENTRY_SHARE * len(entries)))
            if ordinary
            else ()
        )
        self.ministry_rosters = {ministry["id"]: [] for ministry in self.ministries}
        for index, (member, ministry, role, _leader) in enumerate(entries):
            self.ministry_rosters[ministry["id"]].append(
                self._roster_row(member, ministry, role, ended=index in ended)
            )
        for rows in self.ministry_rosters.values():
            rows.sort(key=lambda row: row["memberId"])

    # -- Giving ---------------------------------------------------------------

    def build_giving(self):
        """Funds, pledges for the current and previous years, and 15 months of gifts."""
        self.funds = [
            {
                "fundId": FUND_BASE + index,
                "name": FUNDS[index][0] if index < len(FUNDS) else f"Fund {index + 1}",
                "active": True,
                "fundStartDate": None,
                "fundEndDate": None,
                "requiresPledges": FUNDS[index][1] if index < len(FUNDS) else False,
            }
            for index in range(self.counts["funds"])
        ]
        pledge_funds = [fund for fund in self.funds if fund["requiresPledges"]]
        givers = [
            family
            for family in self._regular_families()
            if family["registeredOrganizationID"] == self.organization_id
            and family["famGroupID"] != INACTIVE_GROUP
        ] or self._regular_families()
        self.pledges = []
        pledged = {}  # (family, fund, year) -> pledge id
        for index in range(self.counts["pledges"]):
            family = self.rng.choice(givers)
            fund = pledge_funds[index % len(pledge_funds)]
            year = self.anchor.year if self.rng.random() < 0.7 else self.anchor.year - 1
            start = date(year, 1, 1)
            identifier = PLEDGE_BASE + index + 1
            self.pledges.append(
                {
                    "pledgeID": identifier,
                    "organizationID": self.organization_id,
                    "fundID": fund["fundId"],
                    "familyID": family["familyDUID"],
                    "memberID": None,
                    "pledgeDate": _day(
                        start - timedelta(days=self.rng.randint(10, 60))
                    ),
                    "pledgeStartDate": _day(start),
                    "currentPledgeAmount": self.rng.choice(PLEDGE_AMOUNTS),
                    "pledgeFrequency": self.rng.choice(("Weekly", "Monthly", "Annual")),
                }
            )
            pledged.setdefault((family["familyDUID"], fund["fundId"], year), identifier)
        first = _months_before(self.anchor, CONTRIBUTION_MONTHS)
        span = (self.anchor - first).days
        drafts = []
        for _ in range(self.counts["contributions"]):
            day = first + timedelta(days=self.rng.randint(0, span))
            fund = (
                self.funds[0]
                if self.rng.random() < OFFERTORY_SHARE
                else self.rng.choice(self.funds)
            )
            anonymous = self.rng.random() < ANONYMOUS_CONTRIBUTION_SHARE
            family = None if anonymous else self.rng.choice(givers)
            amount = self.rng.choice(CONTRIBUTION_AMOUNTS)
            if self.rng.random() < 0.1:
                amount += 0.5
            drafts.append((day, fund, family, amount))
        drafts.sort(key=lambda draft: draft[0])
        self.contributions = []
        for index, (day, fund, family, amount) in enumerate(drafts):
            family_id = 0 if family is None else family["familyDUID"]
            self.contributions.append(
                {
                    "contributionID": CONTRIBUTION_BASE + index + 1,
                    "organizationId": self.organization_id,
                    "fundId": fund["fundId"],
                    "familyId": family_id,
                    "memberId": None,
                    "pledgeId": pledged.get((family_id, fund["fundId"], day.year)),
                    "contributionDate": _day(day),
                    "contributionAmount": amount,
                    "contributionType": "Check" if amount >= 50 else "Cash",
                }
            )

    def parish(self):
        """Run every phase in its fixed order and assemble the result."""
        self.build_households()
        self.build_workgroups()
        self.build_ministries()
        self.build_giving()
        return SyntheticParish(
            organization_id=self.organization_id,
            seed=self.seed,
            family_count=self.count,
            anchor_date=self.anchor.isoformat(),
            organization={
                "organizationID": self.organization_id,
                "organizationReportName": LOCAL_ORGANIZATION_NAME,
            },
            families=self.families,
            members=self.members,
            contacts=self.contacts,
            family_groups=[
                {"famGroupID": identifier, "famGroup": name}
                for identifier, name in FAMILY_GROUPS
            ],
            family_workgroups=self.family_workgroups,
            family_workgroup_rosters=self.family_workgroup_rosters,
            member_workgroups=self.member_workgroups,
            member_workgroup_rosters=self.member_workgroup_rosters,
            ministries=self.ministries,
            ministry_rosters=self.ministry_rosters,
            funds=self.funds,
            pledges=self.pledges,
            contributions=self.contributions,
            late_family_id=self.late_family_id,
        )


def generate(
    seed,
    families=DEFAULT_FAMILIES,
    anchor_date=None,
    *,
    organization_id=LOCAL_ORGANIZATION_ID,
):
    """Generate the parish for ``seed``, ``families`` and the anchor date.

    ``anchor_date`` defaults to today only for ad hoc use; the fake always
    passes the date recorded in its configuration file, so that restarts keep
    the same ages, roster dates and giving history. ``organization_id`` is a
    test hook; deployments use the LOCAL constant.
    """
    if type(seed) is not int or isinstance(seed, bool) or not 0 <= seed < 2**63:
        raise ValueError("The synthetic parish seed must be an integer.")
    if type(families) is not int or not 1 <= families <= MAXIMUM_FAMILIES:
        raise ValueError("The synthetic parish needs a Family count.")
    anchor = date.today() if anchor_date is None else anchor_date
    if type(anchor) is not date:
        raise ValueError("The synthetic parish anchor must be a civil date.")
    if type(organization_id) is not int or not 1 <= organization_id < 2**31 - 1:
        raise ValueError("The synthetic organization identity is invalid.")
    return _Generator(seed, families, anchor, organization_id).parish()


def digest(parish):
    """SHA-256 of the parish's canonical JSON: the determinism tests' evidence."""
    encoded = json.dumps(
        asdict(parish), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
