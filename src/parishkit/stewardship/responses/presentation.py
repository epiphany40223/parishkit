"""A scoped browser projection: effective values only, never competing values."""

import re
from html import unescape
from zoneinfo import ZoneInfo

from parishkit.parishsoft import HEAD_MEMBER_TYPES
from parishkit.stewardship.accounts.branding_context import campaign_artwork
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.pipeline import Shown
from parishkit.stewardship.source.family_names import (
    family_display_name,
    name_placeholders,
)
from parishkit.stewardship.web.presentation import parish_date, parish_instant

from .census import (
    ADDRESS_LIMITS,
    FAMILY_FIELDS,
    HOUSEHOLD_FIELDS,
    blank_address,
    country_choices,
    us_region_choices,
    us_regions,
)
from .comparison import ValueKind, canonical_value
from .effective import (
    RESOLVED_EXECUTIONS,
    census_submission,
    effective_field,
    effective_fields,
    effective_household,
    proposal_index,
)
from .financial import household_pronoun
from .financial_presentation import financial_presentation
from .inputs import ADDITIONAL_MAX_LENGTH, MEMBER_FIELDS
from .member_census import browser_value
from .member_requests import MAX_PROPOSED_MEMBERS
from .merge import KnownValue
from .ministry_requests import ministry_presentation
from .models import Submission
from .page_content import family_page_slots, public_substitutions, render_pages
from .reviewed import (
    CENSUS,
    FINANCIAL,
    MINISTRY,
    census_additions,
    census_fields,
    census_key,
    drawn_entries,
    entries_of,
    financial_draw,
    proposed_draw,
)


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
    census = "census" in form.inputs.modules
    census_prior = census_submission(prior) if census else None
    # A live form drawn as the reviewed form (#1705) draws each item from
    # the shown items its baseline recorded; every other form keeps the
    # Effective-value merge of the Family's own answers.
    reviewed = baseline.shown_items if baseline.reviewed_view else None
    census_shown = entries_of(reviewed, CENSUS) if census else []
    # The census fields drawn with a change the parish added (#2008), which
    # the browser marks "Added by the parish"; never on a Testing form.
    additions = census_additions(census_shown)
    added = frozenset(
        key
        for key, entry in drawn_entries(census_shown, additions).items()
        if entry["shown"] == Shown.PARISH
    )
    if census and reviewed is not None:
        values = census_fields(form.inputs, census_shown, additions)
    elif census:
        values = effective_fields(form.inputs, prior)
    else:
        values = tuple(effective_field(field) for field in form.inputs.fields)
    proposals = proposal_index(prior) if census else {}
    # The census items the reviewed form shows as carried forward, so an
    # entered move or death request is still presented in its state.
    carried = frozenset(
        census_key(entry) for entry in census_shown if entry["shown"] != "baseline"
    )
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
    members, names = [], []
    relationships = {
        field.identity: field.effective.value.value
        for field in values
        if field.entity == "member_context" and field.field == "relationship"
    }
    context = {
        (field.identity, field.field): field.effective.value.value
        for field in values
        if field.entity == "member_context"
    }
    for identifier in form.inputs.member_duids:
        fields = []
        for definition in MEMBER_FIELDS if census else ():
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
                            and census_prior
                            and str(identifier) in census_prior.answers["members"]
                            and census_prior.answers["members"][str(identifier)].get(
                                "birth_date", ""
                            )
                            is None
                        ),
                    ),
                    "available": effective.value.available,
                    "changed": effective.changed,
                    "conflict": effective.conflict,
                    "added": ("member", str(identifier), definition.name) in added,
                }
            )
        first, last = (
            (indexed[identifier, name].value.value or "")
            if census
            else (context.get((identifier, name)) or "")
            for name in ("first_name", "last_name")
        )
        # Separate names for the salutation, so the browser payload does not
        # grow. A listed Member whose source relationship is a head role is one
        # of the Family's active heads (the source's active_head_duids are its
        # head-role Members who are active, and only active Members are listed).
        names.append(
            {
                "first": first,
                "last": last,
                "head": relationships.get(identifier) in HEAD_MEMBER_TYPES,
            }
        )
        members.append(
            {
                "id": str(identifier),
                "fields": fields,
                "relationship": relationships.get(identifier),
                "display_name": f"{first} {last}".strip(),
                "request": _terminal_presentation(
                    identifier, indexed, proposals, carried
                )
                if census
                else None,
            }
        )
    campaign = CampaignConfiguration.objects.get(
        configuration_id=baseline.configuration_id,
        record_id=baseline.family.campaign_id,
    )
    if census and reviewed is not None:
        proposed = _proposed_rows(proposed_draw(census_shown))
    else:
        proposed = _proposed_presentation(prior) if census else []
    composition = (
        effective_household(form.inputs.member_duids, prior) if not census else None
    )
    ministries = ministry_presentation(
        form.inputs.ministries,
        prior,
        terminal_members=frozenset(row["id"] for row in members if row["request"]),
        proposed_members=frozenset(row["id"] for row in proposed),
        unavailable_members=composition.terminal_members
        if composition
        else frozenset(),
        shown=entries_of(reviewed, MINISTRY) if reviewed is not None else None,
    )
    member_count = (
        sum(not member["request"] for member in members) + len(proposed)
        if census
        else composition.member_count
    )
    return {
        "baseline": str(baseline.pk),
        "testing": baseline.mode == "test",
        "parish_name": form.inputs.parish_name,
        "today": _now().astimezone(ZoneInfo(campaign.timezone)).date().isoformat(),
        "family": family,
        "modules": list(form.inputs.modules),
        "effective_member_count": member_count,
        "household": _household_presentation(values, census_prior, added)
        if census
        else None,
        "members": members,
        "proposed_members": proposed,
        "ministries": ministries,
        "service": _service_presentation(ministries, prior),
        "cannot_attend": bool(prior and prior.answers.get("cannot_attend")),
        "financial": financial_presentation(
            form.inputs.financial,
            prior,
            parish_name=form.inputs.parish_name,
            shown=financial_draw(entries_of(reviewed, FINANCIAL), prior)
            if reviewed is not None
            else None,
        ),
        "max_proposed_members": MAX_PROPOSED_MEMBERS if census else 0,
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
            if census
        ],
        "additional_enabled": campaign.values["additional_information"],
        "additional_max_length": ADDITIONAL_MAX_LENGTH,
        "additional_information": prior.answers["additional_information"]
        if prior and campaign.values["additional_information"]
        else "",
        # The baseline's prior submission is already scoped to this session's
        # mode and, in Testing, its rehearsal epoch; the welcome page banner
        # tells a returning Family when they last submitted.
        "last_submitted_at": prior.submitted_at.isoformat() if prior else None,
        "last_submitted_display": parish_instant(prior.submitted_at, campaign.timezone)
        if prior
        else None,
        "content": _page_content(
            baseline, campaign, family, names, member_count, form.inputs.financial
        ),
        # Optional campaign banner and page icons (#248); absent when unset.
        "images": campaign_artwork(campaign.values),
    }


def _service_presentation(ministries, prior):
    """ "Cannot participate" for each Member on the Ministry page.

    Kept apart from the Ministry entries (whose shape is the Ministry answer)
    but keyed by the same identities. Stored talents (#970) are never shown.
    """
    if ministries is None:
        return None
    previous = (prior.answers.get("service") or {}) if prior else {}
    # A tab loaded before #970 runs the old family-v1.js, which reads this
    # list whenever it takes a refreshed form; it must stay an empty list
    # until no such tab can remain open.
    result = {"talent_options": []}
    for group in ("members", "proposed_members"):
        result[group] = {
            key: {
                "cannot_serve": bool(
                    ((previous.get(group) or {}).get(key) or {}).get("cannot_serve")
                )
            }
            for key in ministries[group]
        }
    return result


def _terminal_presentation(identifier, indexed, proposals, carried=frozenset()):
    """Expose only effective terminal choices, not hidden source or Admin edits.

    An entered terminal request is no longer presented, except on a
    reviewed form, which shows it in its state until it is Confirmed
    (``carried``: the census items that form shows carried forward).
    """
    for name in ("moved_household", "deceased_status"):
        key = ("member", str(identifier), name)
        proposal = proposals.get(key)
        if (
            proposal is not None
            and proposal.execution in RESOLVED_EXECUTIONS
            and key not in carried
        ):
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
    return _proposed_rows(
        (key, row)
        for (entity, key, name), row in sorted(proposal_index(prior).items())
        if entity == "proposed_member"
        and name == "new_member"
        and row.execution not in RESOLVED_EXECUTIONS | {"cancelled", "superseded"}
    )


def _proposed_rows(pairs):
    """The browser shape of each (local key, New Member change) pair, in order."""
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
        for key, row in pairs
    ]


def _household_presentation(values, prior, added=frozenset()):
    """Expose effective household values once, without raw competing source data.

    ``added`` are the census fields drawn with a change the parish added
    (#2008); a Family address among them is marked ``added``.
    """
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
                "added": any(
                    entity == "family" and field == definition.name
                    for entity, _key, field in added
                ),
            }
        )
    home = indexed["home_address"].value.value
    mailing = indexed["mailing_address"].value.value
    # A historical convenience flag cannot copy a newly changed home address
    # over an independently merged mailing value during an otherwise no-change
    # revisit. Only retain that flag when the effective addresses still agree.
    same = bool(
        prior
        and prior.answers["family"].get("mailing_same_as_home", False)
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
        "us_region_choices": us_region_choices(),
    }


def _page_content(baseline, campaign, family, names, member_count, financial):
    """Render selected immutable blocks through the existing inert sanitizer.

    Email-only credential substitutions are empty in the authenticated flow;
    rendering a page must never mint or decrypt a link/code. All Family names
    come from the effective projection, not a second, competing source read;
    ``names`` holds each listed Member's first and last name, in page order,
    and whether the Member is a head of household. The name placeholders are
    built by the rule the Family email uses, so both name the same people.
    """
    substitutions = public_substitutions(baseline.configuration.parish, campaign)
    substitutions.update(
        name_placeholders(
            family_display_name(family),
            [name for name in names if name["head"]],
            names,
        ),
        family_url="/family/",
        pronoun=household_pronoun(member_count),
    )
    if financial is not None:
        period = financial.definition.upcoming
        start, end = parish_date(period.start), parish_date(period.end)
        substitutions.update(
            financial_start=start,
            financial_end=end,
            financial_period=f"{start} – {end}",
        )
    slots = family_page_slots(campaign.values)
    pages = render_pages(baseline.configuration_id, campaign, slots, substitutions)
    # An Admin skips the optional closing page by removing or emptying its
    # content; markup with no visible text must not add an empty page.
    if "closing" in pages and not _has_text(pages["closing"]):
        del pages["closing"]
    return pages


def _has_text(html):
    """True when rendered page HTML shows visible text, not only empty tags."""
    return bool(unescape(re.sub(r"<[^>]*>", "", html)).strip())
