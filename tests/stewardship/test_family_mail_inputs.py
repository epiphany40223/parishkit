"""Family email names the same people a Family page does (#471)."""

from parishkit.stewardship.jobs.family_mail_inputs import (
    FamilyMailSource,
    household_names,
    public_values,
)

FAMILY = {"lastName": "Test", "mailingName": "Andy", "active_head_duids": [12, 11]}


def member(duid, first, *, active=True, deceased=False, last="Test"):
    """One snapshot Member payload, keyed as SnapshotMember stores it."""
    return str(duid), {
        "memberDUID": duid,
        "firstName": first,
        "lastName": last,
        "active": active,
        "deceased": deceased,
    }


MEMBERS = dict(
    [
        member(13, "Cy"),
        member(11, "Andrew"),
        member(12, "Betty"),
        member(14, "Dora", active=False),
        member(15, "Ed", active=False, deceased=True),
    ]
)


def test_household_names_use_every_active_head_and_every_listed_member():
    """Heads in DUID order, whether or not they have an email; Members likewise.

    Inactive and deceased Members are never named, as the Family form never
    lists them; the heads need no eligible email address, so an email greets
    exactly the people a Family page greets.
    """
    assert household_names(FAMILY, MEMBERS) == {
        "family_name": "Test",
        "head_salutation": "Andrew and Betty Test",
        "family_member_names": "Andrew and Betty Test",
        "all_family_member_names": "Andrew, Betty and Cy Test",
    }


def test_household_names_fall_back_to_the_family_display_name():
    """A Family without heads (or named Members) is addressed by its name."""
    values = household_names(
        {"lastName": "", "mailingName": "The Tests", "active_head_duids": []},
        dict([member(13, "", last="")]),
    )
    assert values == dict.fromkeys(values, "The Tests")
    assert household_names({"active_head_duids": []}, {})["head_salutation"] == (
        "Family"
    )


def test_public_values_carry_every_name_placeholder():
    """The worker's substitutions are exactly the shared name placeholders."""
    names = household_names(FAMILY, MEMBERS)
    source = FamilyMailSource(None, 1, None, names, 3)
    values = public_values(
        source,
        parish={
            "name": "Parish",
            "website": "https://parish.example.org/",
            "phone": "+12025550100",
            "email": "office@parish.example.org",
        },
        campaign={
            "name": "Renewal",
            "start_date": "2026-10-03",
            "end_date": "2026-11-01",
            "timezone": "America/New_York",
            "financial": None,
            "year_label": None,
        },
        public_origin="https://stewardship.example.org",
    )
    assert {key: values[key] for key in names} == names
    assert values["pronoun"] == "We"
