"""Cloning imports configuration only, never an earlier campaign's identities."""

from copy import deepcopy
from uuid import uuid4

from parishkit.stewardship.accounts.campaign_cloning import (
    clone_initial,
    clone_patch,
    clone_structures,
)
from parishkit.stewardship.accounts.schedule_forms import Schedules

from .campaign_factory import financial, schedule
from .content_factory import content, content_document
from .test_schedule_forms import data_for


def example():
    """One page, invitation, reminder and Weekly digest, each per campaign.

    The digest is as a deployment upgraded from before report emails held
    it (#1786); a clone leaves it and its email behind.
    """
    document = content_document()
    source = document["sections"]["campaigns"][0]
    for kind, day in (
        ("initial", "2054-10-01"),
        ("reminder", "2054-10-08"),
        ("weekly_digest", None),
    ):
        template = content(source["id"], kind="email", slot=kind)
        document["sections"]["content"].append(template)
        document["sections"].setdefault("schedules", []).append(
            schedule(
                source["id"],
                kind=kind,
                date=day,
                weekday=0 if kind == "weekly_digest" else None,
                template_version=template["id"],
                subject=template["values"]["subject"],
            )
        )
    return document, source


def test_clone_records_are_detached_stable_and_fresh():
    """Retrying a signed target retains new IDs without changing any source bytes."""
    document, source = example()
    before = deepcopy(document)
    target = uuid4()
    result = clone_structures(document, source, target)
    assert result == clone_structures(document, source, target)
    assert result != clone_structures(document, source, uuid4())
    previous, content, schedules = result
    assert set(previous) == {"modules", "share_options", "content_versions"}
    assert previous["content_versions"]["welcome"] == content[0]["id"]
    assert {row["values"]["campaign_id"] for row in content + schedules} == {
        str(target)
    }
    old_ids = {row["id"] for rows in document["sections"].values() for row in rows}
    assert not old_ids.intersection(row["id"] for row in content + schedules)
    assert all(row["values"]["date"] is None for row in schedules)
    # Only Family mail is cloned: the digest and its email stay behind.
    assert [row["values"]["kind"] for row in schedules] == ["initial", "reminder"]
    assert "weekly_digest" not in {row["values"]["slot"] for row in content}
    assert document == before
    previous["modules"].append("financial")
    assert document == before


def test_clone_resets_dates_funds_names_and_financial_acknowledgment():
    """A past financial mapping is not approval to reuse current fund identities."""
    document, source = example()
    source["values"].update(modules=["financial", "ministry"], financial=financial())
    initial = clone_initial(
        source,
        digest="b" * 64,
        timezone="America/Chicago",
        ministries=[("99", "Current")],
    )
    for name in (
        "start_date",
        "end_date",
        "financial_start",
        "financial_end",
        "comparison_start",
        "comparison_end",
    ):
        assert initial[name] is None
    assert initial["name"] == initial["year_label"] == ""
    assert initial["fund_duids"] == initial["comparison_fund_duids"] == []
    assert "overlap_confirmed" not in initial
    assert initial["timezone"] == "America/Chicago"
    assert initial["ministry_duids"] == ["99"]


def test_clone_requires_explicit_mail_dates():
    """An undated invitation fails; deletion is explicit."""
    document, source = example()
    target = uuid4()
    previous, content, rows = clone_structures(document, source, target)
    data = data_for(rows)
    arguments = dict(
        prefix="schedules",
        templates=content,
        previous=rows,
        campaign_id=target,
        campaign=source["values"],
    )
    formset = Schedules(data, **arguments)
    assert not formset.is_valid()
    data["schedules-0-date"] = "2054-10-01"
    data["schedules-1-DELETE"] = "on"
    formset = Schedules(data, **arguments)
    assert formset.is_valid(), formset.errors
    patch = clone_patch(target, source["values"] | previous, content, formset)
    schedules = [row for row in patch if row["section"] == "schedules"]
    assert len(schedules) == 1 and schedules[0]["values"]["kind"] == "initial"
    assert all(row["operation"] == "add" for row in patch)


def test_clone_leaves_a_retired_critical_alert_behind():
    """A clone cannot re-author the never-sent critical alert template (#913)."""
    document, source = example()
    alert = content(source["id"], kind="email", slot="critical_alert")
    document["sections"]["content"].append(alert)
    _, cloned, _ = clone_structures(document, source, uuid4())
    # The alert and the Weekly digest's email (#1786) are left behind.
    assert len(cloned) == len(document["sections"]["content"]) - 2
    assert "critical_alert" not in {row["values"]["slot"] for row in cloned}


def test_clone_carries_share_option_short_labels():
    """A cloned option keeps its short label (#1569) under a fresh id."""
    document, source = example()
    source["values"]["share_options"] = [
        {"id": str(uuid4()), "label": "By check", "free_text": False},
        {
            "id": str(uuid4()),
            "label": "Online",
            "free_text": False,
            "short_label": "Web",
        },
    ]
    previous, _content, _schedules = clone_structures(document, source, uuid4())
    cloned = previous["share_options"]
    assert [row.get("short_label") for row in cloned] == [None, "Web"]
    assert not {row["id"] for row in cloned} & {
        row["id"] for row in source["values"]["share_options"]
    }


def test_clone_leaves_report_emails_behind():
    """A copy takes Family mail only, never the source's report emails.

    Its reports start from New report email's Start from: the copy has
    Family schedules, so its schedule step suggests none (report-emails
    spec, "Suggested report emails", #2038).
    """
    from parishkit.stewardship.campaigns.report_presets import DAILY, starting_point

    document, source = example()
    report = starting_point(DAILY, source["id"])
    report["schedule"]["time"] = "07:00:00"
    document["sections"]["schedules"].append({"id": str(uuid4()), "values": report})
    _previous, _content, schedules = clone_structures(document, source, uuid4())
    assert [row["values"]["kind"] for row in schedules] == ["initial", "reminder"]
