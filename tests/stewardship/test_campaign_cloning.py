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
    """One page, invitation and digest with distinct per-campaign identities."""
    document = content_document()
    source = document["sections"]["campaigns"][0]
    for kind in ("initial", "weekly_digest"):
        template = content(source["id"], kind="email", slot=kind)
        document["sections"]["content"].append(template)
        document["sections"].setdefault("schedules", []).append(
            schedule(
                source["id"],
                kind=kind,
                date=None if kind == "weekly_digest" else "2026-10-01",
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
    assert (
        schedules[1]["values"]["weekday"]
        == document["sections"]["schedules"][1]["values"]["weekday"]
    )
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
    assert not initial["overlap_confirmed"]
    assert initial["timezone"] == "America/Chicago"
    assert initial["ministry_duids"] == ["99"]


def test_clone_requires_explicit_mail_dates_but_preserves_digest_structure():
    """An undated invitation fails; deletion is explicit and unchanged digests copy."""
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
    data["schedules-0-DELETE"] = "on"
    formset = Schedules(data, **arguments)
    assert formset.is_valid(), formset.errors
    patch = clone_patch(target, source["values"] | previous, content, formset)
    schedules = [row for row in patch if row["section"] == "schedules"]
    assert len(schedules) == 1 and schedules[0]["values"]["kind"] == "weekly_digest"
    assert all(row["operation"] == "add" for row in patch)
