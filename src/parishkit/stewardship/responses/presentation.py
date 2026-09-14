"""A scoped browser projection: effective values only, never competing values."""

from datetime import date
from zoneinfo import ZoneInfo

from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.web.content import PLACEHOLDERS, render_template
from parishkit.stewardship.web.presentation import parish_date

from .census import (
    ADDRESS_LIMITS,
    FAMILY_FIELDS,
    HOUSEHOLD_FIELDS,
    blank_address,
    country_choices,
    us_regions,
)
from .comparison import ValueKind, canonical_value
from .effective import RESOLVED_EXECUTIONS, effective_fields, proposal_index
from .inputs import ADDITIONAL_MAX_LENGTH, MEMBER_FIELDS
from .member_census import browser_value
from .member_requests import MAX_PROPOSED_MEMBERS
from .merge import KnownValue
from .models import Submission


def form_presentation(form):
    """Materialize under the issuance lock and only from its exact retained inputs.

    Baseline references/digests, raw proposals, hidden current-source values and
    administrative edits are deliberately not serialized. The browser receives
    one opaque baseline reference and the complete authorized editable schema.
    """
    require_work_order()
    baseline = form.baseline
    prior = (
        Submission.objects.get(pk=baseline.prior_submission_id)
        if baseline.prior_submission_id
        else None
    )
    values = effective_fields(form.inputs, prior)
    proposals = proposal_index(prior)
    family = {
        field.field: field.effective.value.value
        for field in values
        if field.entity == "family" and field.field not in HOUSEHOLD_FIELDS
    }
    indexed = {
        (field.identity, field.field): field.effective
        for field in values
        if field.entity == "member"
    }
    members = []
    relationships = {
        field.identity: field.effective.value.value
        for field in values
        if field.entity == "member_context"
    }
    for identifier in form.inputs.member_duids:
        fields = []
        for definition in MEMBER_FIELDS:
            effective = indexed[identifier, definition.name]
            fields.append(
                {
                    "name": definition.name,
                    "label": definition.label,
                    "required": definition.required,
                    "max_length": definition.max_length,
                    "kind": definition.kind.value,
                    "choices": list(definition.choices),
                    "value": browser_value(
                        definition,
                        effective.value,
                        previous_unknown=bool(
                            definition.name == "birth_date"
                            and prior
                            and str(identifier) in prior.answers["members"]
                            and prior.answers["members"][str(identifier)].get(
                                "birth_date", ""
                            )
                            is None
                        ),
                    ),
                    "available": effective.value.available,
                    "changed": effective.changed,
                    "conflict": effective.conflict,
                }
            )
        members.append(
            {
                "id": str(identifier),
                "fields": fields,
                "relationship": relationships.get(identifier),
                "request": _terminal_presentation(identifier, indexed, proposals),
            }
        )
    campaign = CampaignConfiguration.objects.get(
        configuration_id=baseline.configuration_id,
        record_id=baseline.family.campaign_id,
    )
    return {
        "baseline": str(baseline.pk),
        "testing": baseline.mode == "test",
        "today": _now().astimezone(ZoneInfo(campaign.timezone)).date().isoformat(),
        "family": family,
        "household": _household_presentation(values, prior),
        "members": members,
        "proposed_members": _proposed_presentation(prior),
        "max_proposed_members": MAX_PROPOSED_MEMBERS,
        "new_member_fields": [
            {
                "name": field.name,
                "label": field.label,
                "required": field.required,
                "max_length": field.max_length,
                "kind": field.kind.value,
                "choices": list(field.choices),
                "value": "",
                "available": False,
                "changed": False,
                "conflict": False,
            }
            for field in MEMBER_FIELDS
        ],
        "additional_enabled": campaign.values["additional_information"],
        "additional_max_length": ADDITIONAL_MAX_LENGTH,
        "additional_information": prior.answers["additional_information"]
        if prior and campaign.values["additional_information"]
        else "",
        "last_submitted_at": prior.submitted_at.isoformat()
        if prior and prior.mode == "live"
        else None,
        "content": _page_content(baseline, campaign, family, members),
    }


def _terminal_presentation(identifier, indexed, proposals):
    """Expose only effective terminal choices, not hidden source or Admin edits."""
    for name in ("moved_household", "deceased_status"):
        proposal = proposals.get(("member", str(identifier), name))
        if proposal is not None and proposal.execution in RESOLVED_EXECUTIONS:
            continue
        effective = indexed[identifier, name]
        if effective.value.value is True:
            result = {name: True, "confirmed": True}
            if name == "deceased_status":
                result["death_date"] = (
                    indexed[identifier, "death_date"].value.value or ""
                )
            return result
    return None


def _proposed_presentation(prior):
    """Keep local identity stable on revisit while excluding resolved manual work."""
    return [
        {
            "id": key,
            "relationship": "Proposed household member",
            "fields": [
                {
                    "name": field.name,
                    "label": field.label,
                    "required": field.required,
                    "max_length": field.max_length,
                    "kind": field.kind.value,
                    "choices": list(field.choices),
                    "value": browser_value(
                        field,
                        KnownValue(True, row.submitted_value[field.name]),
                        previous_unknown=field.name == "birth_date",
                    ),
                    "available": True,
                    "changed": True,
                    "conflict": False,
                }
                for field in MEMBER_FIELDS
            ],
        }
        for (entity, key, name), row in sorted(proposal_index(prior).items())
        if entity == "proposed_member"
        and name == "new_member"
        and row.execution not in RESOLVED_EXECUTIONS | {"cancelled", "superseded"}
    ]


def _household_presentation(values, prior):
    """Expose effective household values once, without raw competing source data."""
    indexed = {
        field.field: field.effective for field in values if field.entity == "family"
    }
    fields = []
    for definition in FAMILY_FIELDS:
        effective = indexed[definition.name]
        value = effective.value.value
        fields.append(
            {
                "name": definition.name,
                "label": definition.label,
                "kind": definition.kind.value,
                "value": blank_address()
                if definition.kind is ValueKind.ADDRESS and value is None
                else value,
                "available": effective.value.available,
                "changed": effective.changed,
                "conflict": effective.conflict,
            }
        )
    home = indexed["home_address"].value.value
    mailing = indexed["mailing_address"].value.value
    # A historical convenience flag cannot copy a newly changed home address
    # over an independently merged mailing value during an otherwise no-change
    # revisit. Only retain that flag when the effective addresses still agree.
    same = bool(
        prior
        and prior.answers["family"]["mailing_same_as_home"]
        and home is not None
        and canonical_value(ValueKind.ADDRESS, home)
        == canonical_value(ValueKind.ADDRESS, mailing)
    )
    return {
        "fields": fields,
        "mailing_same_as_home": same,
        "address_limits": dict(ADDRESS_LIMITS),
        "countries": country_choices(),
        "us_regions": sorted(us_regions()),
    }


def _page_content(baseline, campaign, family, members):
    """Render selected immutable blocks through the existing inert sanitizer.

    Email-only credential substitutions are empty in the authenticated flow;
    rendering a page must never mint or decrypt a link/code. All Family names
    come from the effective projection, not a second, competing source read.
    """
    parish = baseline.configuration.parish
    substitutions = dict.fromkeys(PLACEHOLDERS, "")
    substitutions.update(
        parish_name=parish.name,
        parish_website=parish.website,
        parish_phone=parish.phone,
        campaign_name=campaign.values["name"],
        campaign_start=parish_date(date.fromisoformat(campaign.values["start_date"])),
        campaign_end=parish_date(date.fromisoformat(campaign.values["end_date"])),
        campaign_timezone=campaign.timezone,
        campaign_year=campaign.values.get("year_label")
        or campaign.values["start_date"][:4],
        family_name=family.get("mailingName") or family.get("lastName") or "",
        family_member_names=", ".join(
            " ".join(
                field["value"]
                for field in member["fields"]
                if field["name"] in {"first_name", "last_name"}
            )
            for member in members
        ),
        generic_family_url="/",
        family_url="/family/",
        pronoun="I" if len(members) == 1 else "We",
    )
    slots = {"welcome", "census", "review", "thank_you"}
    if campaign.values["additional_information"]:
        slots.add("additional")
    return {
        row.slot: render_template(row.html, substitutions, html=True)
        for row in ContentVersion.objects.filter(
            configuration_id=baseline.configuration_id,
            campaign_id=campaign.record_id,
            kind="page",
            slot__in=slots,
            record_id__in=campaign.values["content_versions"].values(),
        )
    }
