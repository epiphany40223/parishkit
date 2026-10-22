"""The conversion of the digests into report emails (#1786, spec "Conversion").

The pure conversion of one configuration document, the
``campaign-series-v12`` schema it produces, and the one request format that
may build it: every Daily and Weekly digest keeps its record ID and gets
the report values of the conversion table, with its default introduction
dropped (Daily) or reworded (Weekly, decision 17), and nothing else changes.
"""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import reports_schema
from parishkit.stewardship.accounts import users_schema as users
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validate_sections,
)
from parishkit.stewardship.accounts.content_defaults import EMAILS, RETIRED_EMAILS
from parishkit.stewardship.accounts.reports_conversion import (
    converted_values,
    default_intro,
    upgrade_patch,
)
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.campaigns.configuration import validate_campaign_change
from parishkit.stewardship.campaigns.report_values import report_values
from parishkit.stewardship.web.content import prepare_content

from .campaign_factory import campaign, schedule
from .configuration_factory import configuration_document
from .content_factory import content
from .test_users_upgrade import user

REWORDED = "Here are the additional information requests that are still open."


def template(owner, slot, html):
    """An email template revision of a digest slot, saved as content holds it."""
    prepared = prepare_content(html)
    subject = f"{{{{ campaign_name }}}} — {slot.split('_')[0]} report"
    return content(
        owner,
        kind="email",
        slot=slot,
        subject=subject,
        html=prepared.html,
        text=prepared.text,
    )


def digest_document(*, daily_html="", weekly_html=None, comparison=False):
    """A users configuration whose campaign has both digests and a template each."""
    document = configuration_document()
    document["sections"]["login_rules"] = [user()]
    owner = campaign()
    identifier = owner["id"]
    weekly_html = EMAILS["weekly_digest"].html if weekly_html is None else weekly_html
    daily_template = template(identifier, "daily_digest", daily_html)
    weekly_template = template(identifier, "weekly_digest", weekly_html)
    daily = schedule(
        identifier,
        kind="daily_digest",
        date=None,
        time="07:00:00",
        subject=daily_template["values"]["subject"],
        template_version=daily_template["id"],
        skip_dates=["2054-10-09"],
        **({"comparison": True} if comparison else {}),
    )
    weekly = schedule(
        identifier,
        kind="weekly_digest",
        date=None,
        time="08:00:00",
        weekday=0,
        subject=weekly_template["values"]["subject"],
        template_version=weekly_template["id"],
    )
    document["sections"].update(
        campaigns=[owner],
        schedules=[schedule(identifier), daily, weekly],
        content=[daily_template, weekly_template],
    )
    return document


def version(document):
    """Parse ``document`` under the schema it selects."""
    return parse_version(document, validate_sections=validate_sections)


def by_kind(document, kind):
    """The one schedule record of ``kind``."""
    return next(
        row
        for row in document["sections"]["schedules"]
        if row["values"]["kind"] == kind
    )


def test_the_daily_converts_to_the_daily_report():
    """Every value of the conversion table's Daily column, and nothing else."""
    document = digest_document(comparison=True)
    daily = by_kind(document, "daily_digest")
    values = converted_values(daily["values"], None)
    assert values == {
        "campaign_id": daily["values"]["campaign_id"],
        "kind": "report",
        "name": "Daily report",
        "subject": daily["values"]["subject"],
        "intro_html": "",
        "intro_text": "",
        "recipients": [{"group": "administrators"}],
        "schedule": {
            "type": "repeat",
            "frequency": "daily",
            "from": "day_after_campaign_start",
            "until": "day_after_campaign_end",
            "time": "07:00:00",
        },
        "as_of": "previous_day_end",
        "blocks": [
            {"block": "campaign_totals", "version": 1, "options": {}},
            {"block": "response_funnel", "version": 1, "options": {}},
            {"block": "participation_charts", "version": 1, "options": {}},
            {"block": "day_by_day", "version": 1, "options": {"days": 5}},
            {"block": "compared_with_past_campaigns", "version": 1, "options": {}},
        ],
        "content_required": False,
        "skip_dates": ["2054-10-09"],
        "paused_ranges": [],
        "slot_basis": "report_date",
    }
    assert report_values(values) == values


def test_the_weekly_converts_to_the_weekly_report():
    """Every value of the Weekly column; its default intro is reworded."""
    document = digest_document()
    weekly = by_kind(document, "weekly_digest")
    content_record = document["sections"]["content"][1]["values"]
    values = converted_values(weekly["values"], content_record)
    reworded = prepare_content(f"<p>{REWORDED}</p>")
    assert values == {
        "campaign_id": weekly["values"]["campaign_id"],
        "kind": "report",
        "name": "Weekly report",
        "subject": weekly["values"]["subject"],
        "intro_html": reworded.html,
        "intro_text": reworded.text,
        "recipients": [{"group": "administrators"}],
        "schedule": {
            "type": "repeat",
            "frequency": "weekly",
            "weekdays": [0],
            "from": "campaign_start",
            "until": "first_send_after_campaign_end",
            "time": "08:00:00",
        },
        "as_of": "send_time",
        "blocks": [
            {"block": "additional_information", "version": 1, "options": {}},
            {"block": "response_funnel", "version": 1, "options": {}},
        ],
        "content_required": True,
        "skip_dates": [],
        "paused_ranges": [],
        "slot_basis": "send_date",
    }
    assert reworded.text == REWORDED
    assert report_values(values) == values


@pytest.mark.parametrize(
    "email", [EMAILS["weekly_digest"], *RETIRED_EMAILS["weekly_digest"]]
)
def test_every_built_in_weekly_intro_is_reworded(email):
    """Current and retired defaults alike (spec, decision 17)."""
    document = digest_document(weekly_html=email.html)
    record = document["sections"]["content"][1]["values"]
    values = converted_values(by_kind(document, "weekly_digest")["values"], record)
    assert values["intro_text"] == REWORDED


@pytest.mark.parametrize(
    "email", [EMAILS["daily_digest"], *RETIRED_EMAILS["daily_digest"]]
)
def test_every_built_in_daily_intro_becomes_empty(email):
    """The Daily already left a default intro out, so its output is unchanged."""
    document = digest_document(daily_html=email.html)
    record = document["sections"]["content"][0]["values"]
    values = converted_values(by_kind(document, "daily_digest")["values"], record)
    assert (values["intro_html"], values["intro_text"]) == ("", "")


def test_an_edited_intro_is_kept_as_written():
    """One changed word keeps the Administrator's own text, in both reports."""
    edited = "<p>Here are this week’s requests for the team.</p>"
    document = digest_document(daily_html="<p>Good morning.</p>", weekly_html=edited)
    daily, weekly = document["sections"]["content"]
    assert not default_intro(
        "weekly_digest", weekly["values"]["html"], weekly["values"]["text"]
    )
    converted = {item["id"]: item["values"] for item in upgrade_patch(document)}
    daily_values = converted[by_kind(document, "daily_digest")["id"]]
    weekly_values = converted[by_kind(document, "weekly_digest")["id"]]
    assert (daily_values["intro_html"], daily_values["intro_text"]) == (
        daily["values"]["html"],
        daily["values"]["text"],
    )
    assert (weekly_values["intro_html"], weekly_values["intro_text"]) == (
        weekly["values"]["html"],
        weekly["values"]["text"],
    )


def test_the_patch_converts_only_the_digests_in_id_order():
    """One complete update per digest; the invitation is not touched."""
    document = digest_document()
    patch = upgrade_patch(document)
    digests = sorted(
        row["id"]
        for row in document["sections"]["schedules"]
        if row["values"]["kind"] != "initial"
    )
    assert [item["id"] for item in patch] == digests
    assert {item["operation"] for item in patch} == {"update"}
    assert {item["values"]["kind"] for item in patch} == {"report"}
    assert upgrade_patch(configuration_document()) == []


def converted(document):
    """The conversion's candidate of ``document``."""
    base = version(document)
    return build_candidate(
        base,
        upgrade_patch(document),
        candidate_id=uuid4(),
        request_schema=reports_schema.UPGRADE_REQUEST_SCHEMA,
    )


def test_the_conversion_candidate_is_v12_and_keeps_every_other_record():
    """Same record IDs, digest values replaced whole, everything else equal."""
    document = digest_document(comparison=True)
    result = converted(document)
    candidate = result.candidate.document()
    assert schema_for(candidate) == reports_schema.SCHEMA == "campaign-series-v12"
    before = {row["id"]: row for row in document["sections"]["schedules"]}
    after = {row["id"]: row for row in candidate["sections"]["schedules"]}
    assert before.keys() == after.keys()
    for identifier, row in after.items():
        if before[identifier]["values"]["kind"] == "initial":
            assert row == before[identifier]
        else:
            assert row["values"]["kind"] == "report"
            assert "template_version" not in row["values"]
    for name in ("campaigns", "content", "login_rules"):
        assert candidate["sections"][name] == sorted(
            document["sections"][name], key=lambda row: row["id"]
        )
    # The candidate's own schema accepts it, as the installer revalidates.
    assert version(candidate) == result.candidate


def test_the_conversion_refuses_any_other_patch():
    """A partial, reordered or edited patch, or a non-users base, is refused."""
    document = digest_document()
    base = version(document)
    patch = upgrade_patch(document)
    edited = deepcopy(patch)
    edited[0]["values"]["name"] = "Something else"
    for wrong in (patch[:1], list(reversed(patch)), edited, []):
        with pytest.raises(ConfigError):
            build_candidate(
                base,
                wrong,
                candidate_id=uuid4(),
                request_schema=reports_schema.UPGRADE_REQUEST_SCHEMA,
            )


def test_v12_refuses_a_digest_and_older_schemas_refuse_a_report():
    """The kinds never mix, whichever validator reads the document."""
    candidate = converted(digest_document()).candidate.document()
    mixed = deepcopy(candidate)
    mixed["sections"]["schedules"].append(
        by_kind(digest_document(), "daily_digest") | {"id": str(uuid4())}
    )
    mixed["sections"]["schedules"][-1]["values"]["campaign_id"] = candidate["sections"][
        "campaigns"
    ][0]["id"]
    with pytest.raises(ConfigError):
        version(mixed)
    with pytest.raises(ConfigError):
        reports_schema.validate_sections(mixed)
    from parishkit.stewardship.accounts.configuration_schema import validator_for

    with pytest.raises(ConfigError):
        parse_version(candidate, validate_sections=validator_for(users.SERIES_SCHEMA))


def test_no_change_adds_a_digest():
    """After the conversion, a new Daily or Weekly digest is refused."""
    document = digest_document()
    after = deepcopy(document)
    extra = deepcopy(by_kind(document, "weekly_digest"))
    extra["id"] = str(uuid4())
    after["sections"]["schedules"].append(extra)
    with pytest.raises(ConfigError):
        validate_campaign_change(document, after)
    # Carried unchanged, an existing digest stays valid.
    validate_campaign_change(document, deepcopy(document))
