"""Create the campaign's "Families in this campaign" section (#1393).

The Administrator chooses the campaign's Families rule
(``campaigns.campaign_families``) once, on Create the campaign: Basic offers
the two presets, Advanced the three conditions (admin-portal spec,
"Families in this campaign"). The section follows the shared Basic and
Advanced modes: the Advanced fields always hold the rule, which the Basic
choice rewrites, and the posted mode says which form is stored (a preset in
Basic, the Advanced rule otherwise).

Its count preview reads the latest promoted ParishSoft snapshot: how many
Families the choice selects and how many of them can be emailed. The facts
it needs (registration, group, Member statuses, heads with a valid email)
are read once per snapshot and kept in this process, so each change of the
choice is a computation, not a database read. No name or address leaves
this module; only the two counts do.
"""

from dataclasses import dataclass

from django import forms
from django.db import connection
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from parishkit.parishsoft import (
    FAMILY_RULE_MEMBER_STATUSES,
    FAMILY_RULE_PRESET_GROUPS,
    HEAD_MEMBER_TYPES,
    NO_FAMILY_GROUP_ID,
    NO_FAMILY_GROUP_NAME,
    family_matches_rule,
    family_rule_member_statuses,
)
from parishkit.stewardship.campaigns import campaign_families
from parishkit.stewardship.web.admin_forms import AdminForm

MODE = "families_mode"
PRESET = "families_preset"
REGISTERED = "families_registered"
GROUPS = "families_groups"
STATUSES = "families_statuses"
FIELDS = (MODE, PRESET, REGISTERED, GROUPS, STATUSES)
PRESET_LABELS = {
    "active_parishioners": gettext_lazy("Active parishioners"),
    "active_parishioners_and_staff": gettext_lazy("Active parishioners and Staff"),
}
PRESET_HELP = {
    "active_parishioners": gettext_lazy(
        "Families registered to this parish, in the Active Family group, with "
        "at least one Member whose status is Active."
    ),
    "active_parishioners_and_staff": gettext_lazy(
        "The same, but Families in the Staff Family group are included too."
    ),
}
NO_MATCH = gettext_lazy("No Families in the latest ParishSoft data match this choice.")
NO_GROUP_CHOSEN = gettext_lazy("Choose at least one Family group.")
NO_STATUS_CHOSEN = gettext_lazy("Choose at least one Member status.")
NO_PRESET_CHOSEN = gettext_lazy("Choose which Families this campaign is sent to.")
LOCKED = gettext_lazy("Chosen when the campaign was created; it can't be changed.")
LEGACY_WORDS = gettext_lazy(
    "Chosen before this choice existed: Families registered to this parish, "
    "not in the Inactive Family group, with at least one Member who is not "
    "Inactive or Deceased."
)


@dataclass(frozen=True)
class Group:
    """One Family group the section offers: its id, name and Family count."""

    id: int
    name: str
    families: int


@dataclass(frozen=True)
class Source:
    """The latest promoted snapshot's facts the section reads.

    ``facts`` holds one tuple per Family: (registered here, famGroupID,
    group name, Member statuses, statuses of heads with a valid email).
    """

    snapshot_id: object
    promoted_at: object
    groups: tuple
    facts: tuple


# One snapshot's facts, kept while it is the latest promoted one.
_CACHE = {}

FACTS_SQL = """
WITH family AS (
    SELECT m.source_key, p.canonical::jsonb AS v
    FROM stewardship_snapshot_family m
    JOIN stewardship_source_family p ON p.id=m.payload_id
    WHERE m.snapshot_id=%(snapshot)s
), member AS (
    SELECT p.family_key, p.canonical::jsonb AS v, m.source_key
    FROM stewardship_snapshot_member m
    JOIN stewardship_source_member p ON p.id=m.payload_id
    WHERE m.snapshot_id=%(snapshot)s
), emailed AS (
    SELECT p.owner_key
    FROM stewardship_snapshot_contact m
    JOIN stewardship_source_contact p ON p.id=m.payload_id
    WHERE m.snapshot_id=%(snapshot)s AND p.owner_kind='member'
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(p.canonical::jsonb->'emails') e
                  WHERE e->'valid'='true'::jsonb)
)
SELECT f.v->'parishioner'='true'::jsonb,
    CASE WHEN jsonb_typeof(f.v->'famGroupID')='number'
        THEN (f.v->>'famGroupID')::bigint END,
    f.v->>'family_group',
    coalesce((SELECT array_agg(DISTINCT coalesce(x.v->>'memberStatus',''))
        FROM member x WHERE x.family_key=f.source_key),'{}'),
    coalesce((SELECT array_agg(DISTINCT coalesce(x.v->>'memberStatus',''))
        FROM member x JOIN emailed e ON e.owner_key=x.source_key
        WHERE x.family_key=f.source_key AND x.v->>'memberType' = ANY(%(heads)s)),'{}')
FROM family f
"""


def source():
    """The latest promoted snapshot's group lookup and Family facts, or None.

    The group lookup is the one the load recorded (``family_groups``
    evidence); a snapshot loaded before that evidence existed offers the
    groups its Families are in. Groups are by id, then the one "(No Family
    Group)" choice.
    """
    from parishkit.stewardship.source.snapshot_models import (
        SourceCurrent,
        SourceSnapshot,
    )

    current = SourceCurrent.objects.filter(singleton=True).first()
    if current is None or current.snapshot_id is None:
        return None
    cached = _CACHE.get(current.snapshot_id)
    if cached is not None:
        return cached
    snapshot = (
        SourceSnapshot.objects.only("id", "promoted_at", "cursor")
        .filter(pk=current.snapshot_id)
        .first()
    )
    if snapshot is None:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            FACTS_SQL,
            {"snapshot": snapshot.pk, "heads": sorted(HEAD_MEMBER_TYPES)},
        )
        facts = tuple(
            (bool(row[0]), row[1], row[2], frozenset(row[3]), frozenset(row[4]))
            for row in cursor.fetchall()
        )
    load = snapshot.cursor.get("load") if type(snapshot.cursor) is dict else None
    lookup = load.get(campaign_families.GROUPS_KEY) if type(load) is dict else None
    if type(lookup) is list:
        names = {item["id"]: item["name"] for item in lookup}
    else:
        names = {fact[1]: fact[2] for fact in facts if fact[1] and fact[2]}
    names.pop(NO_FAMILY_GROUP_ID, None)
    counts = {}
    for fact in facts:
        key = fact[1] if fact[1] else NO_FAMILY_GROUP_ID
        counts[key] = counts.get(key, 0) + 1
    # A group named exactly "No Family Group" joins the one no-group choice
    # (data spec, "Campaign Families"), so it is never listed twice.
    joined = [
        identifier for identifier, name in names.items() if name == NO_FAMILY_GROUP_NAME
    ]
    groups = tuple(
        Group(identifier, name, counts.get(identifier, 0))
        for identifier, name in sorted(names.items())
        if identifier not in joined
    ) + (
        Group(
            NO_FAMILY_GROUP_ID,
            campaign_families.NO_GROUP["name"],
            counts.get(NO_FAMILY_GROUP_ID, 0)
            + sum(counts.get(identifier, 0) for identifier in joined),
        ),
    )
    value = Source(snapshot.pk, snapshot.promoted_at, groups, facts)
    _CACHE.clear()
    _CACHE[snapshot.pk] = value
    return value


def counts(data, rule):
    """``(matching, emailable)`` Families for ``rule`` in ``data``'s facts.

    Registration is each Family's own flag, passed to the shared predicate
    as its own organization, as the Families check does.
    """
    statuses = set(family_rule_member_statuses(rule))
    matching = emailable = 0
    for registered, group_id, group, members, emailed in data.facts:
        family = {
            "registeredOrganizationID": 1 if registered else None,
            "famGroupID": group_id,
            "py family group": group,
            "py members": [{"memberStatus": status} for status in members],
        }
        if family_matches_rule(family, rule, 1):
            matching += 1
            emailable += bool(statuses & emailed)
    return matching, emailable


def preset_rule(preset, groups):
    """The Advanced rule a preset equals in ``groups`` (Basic → Advanced).

    A preset whose group the data does not have still maps to the groups it
    does have (possibly none).
    """
    names = FAMILY_RULE_PRESET_GROUPS[preset]
    return {
        "rule": campaign_families.ADVANCED,
        "registered": True,
        "family_groups": [
            {"id": group.id, "name": group.name}
            for group in groups
            if group.id != NO_FAMILY_GROUP_ID and group.name in names
        ],
        "member_statuses": ["Active"],
    }


class DescribedRadioSelect(forms.RadioSelect):
    """Radios each described by the help line drawn after it.

    The section draws a preset's description as ``<radio id>-help`` under
    its label, so a screen reader reads the definition with the choice.
    """

    def create_option(self, *args, **kwargs):
        """Name the option's own help line in its ``aria-describedby``."""
        option = super().create_option(*args, **kwargs)
        if option["attrs"].get("id"):
            option["attrs"]["aria-describedby"] = f"{option['attrs']['id']}-help"
        return option


def add_fields(form, data):
    """Add the section's fields to a creation form, over ``data``'s groups.

    It opens in Basic with Active parishioners chosen, its Advanced fields
    holding that preset's rule.
    """
    groups = data.groups if data is not None else ()
    start = preset_rule("active_parishioners", groups)
    form.families_source = data
    form.fields[MODE] = forms.ChoiceField(
        choices=[("basic", _("Basic")), ("advanced", _("Advanced"))],
        initial="basic",
        required=False,
    )
    form.fields[PRESET] = forms.ChoiceField(
        label=_("Families in this campaign"),
        choices=list(PRESET_LABELS.items()),
        initial="active_parishioners",
        required=False,
        widget=DescribedRadioSelect,
    )
    form.fields[REGISTERED] = forms.ChoiceField(
        label=_("Registered to this parish"),
        choices=[("required", _("Required")), ("not_required", _("Not required"))],
        initial="required",
        # Not posted with a Basic choice by the command line; absent is
        # Required, as the page draws it.
        required=False,
        widget=forms.RadioSelect,
    )
    form.fields[GROUPS] = forms.TypedMultipleChoiceField(
        label=_("In any of these Family groups"),
        choices=[
            (
                str(group.id),
                ngettext(
                    "%(name)s (%(count)s Family)",
                    "%(name)s (%(count)s Families)",
                    group.families,
                )
                % {"name": group.name, "count": f"{group.families:,}"},
            )
            for group in groups
        ],
        coerce=int,
        initial=[str(group["id"]) for group in start["family_groups"]],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    form.fields[STATUSES] = forms.MultipleChoiceField(
        label=_("With at least one Member whose status is"),
        choices=[(status, status) for status in FAMILY_RULE_MEMBER_STATUSES],
        initial=["Active"],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    form.fields[REGISTERED].tip = _(
        "Not required also includes Families registered to another parish, or to none."
    )
    form.fields[STATUSES].tip = _(
        "The campaign's emails go only to a head of the Family with one of "
        "these statuses."
    )


def posted_rule(data, groups):
    """The rule a bound section holds, or ``(None, field, message)``.

    ``data`` is the form's cleaned data. In Basic the preset is stored; in
    Advanced the three conditions, each list in canonical order.
    """
    if data.get(MODE) != "advanced":
        preset = data.get(PRESET)
        if preset not in PRESET_LABELS:
            return None, PRESET, NO_PRESET_CHOSEN
        return {"rule": preset}, None, None
    names = {group.id: group.name for group in groups}
    chosen = sorted(set(data.get(GROUPS) or []))
    if not chosen:
        return None, GROUPS, NO_GROUP_CHOSEN
    statuses = data.get(STATUSES) or []
    if not statuses:
        return None, STATUSES, NO_STATUS_CHOSEN
    return (
        {
            "rule": campaign_families.ADVANCED,
            "registered": data.get(REGISTERED) != "not_required",
            "family_groups": [
                campaign_families.NO_GROUP
                if identifier == NO_FAMILY_GROUP_ID
                else {"id": identifier, "name": names[identifier]}
                for identifier in chosen
            ],
            "member_statuses": [
                status for status in FAMILY_RULE_MEMBER_STATUSES if status in statuses
            ],
        },
        None,
        None,
    )


def describe(rule, groups=None):
    """The rule in words, as Campaign settings and the confirmation say it.

    ``groups`` are the latest data's groups; an Advanced group it no longer
    has reads "*name* (no longer in ParishSoft)".
    """
    if rule is None:
        return str(LEGACY_WORDS)
    if rule["rule"] in PRESET_LABELS:
        return f"{PRESET_LABELS[rule['rule']]}: {PRESET_HELP[rule['rule']]}"
    present = None if groups is None else {group.id for group in groups}
    names = [
        group["name"]
        if present is None or group["id"] in present
        else _("%(name)s (no longer in ParishSoft)") % {"name": group["name"]}
        for group in rule["family_groups"]
    ]
    registered = (
        _("registered to this parish")
        if rule["registered"]
        else _("registered anywhere or nowhere")
    )
    return _(
        "Advanced: Families %(registered)s, in any of the Family groups "
        "%(groups)s, with at least one Member whose status is %(statuses)s."
    ) % {
        "registered": registered,
        "groups": ", ".join(names),
        "statuses": _(" or ").join(rule["member_statuses"]),
    }


def preview_line(data, rule):
    """The count preview's words: ``{"count", "emailable", "matches"}``.

    ``rule`` None means the choice is incomplete, and ``count`` then says
    what is missing (the caller passes that message as ``rule``'s reason).
    """
    if data is None:
        return {
            "count": str(_("There is no ParishSoft data yet.")),
            "emailable": "",
            "matches": 0,
        }
    matching, emailable = counts(data, rule)
    if not matching:
        return {"count": str(NO_MATCH), "emailable": "", "matches": 0}
    return {
        "count": ngettext(
            "%(count)s Family matches in the ParishSoft data as of",
            "%(count)s Families match in the ParishSoft data as of",
            matching,
        )
        % {"count": f"{matching:,}"},
        "emailable": _("; %(count)s of them can be emailed.")
        % {"count": f"{emailable:,}"},
        "matches": matching,
    }


class SectionForm(AdminForm):
    """The section's fields alone, for the count preview's post."""

    def __init__(self, *args, source=None, **kwargs):
        """Offer ``source``'s groups, as the creation form does."""
        super().__init__(*args, **kwargs)
        add_fields(self, source)


def section_context(form):
    """What the section's template draws beyond its fields.

    ``mode`` is the mode it opens in (Basic unless a bound form chose
    Advanced), ``line`` the count preview for the shown choice, ``as_of``
    the snapshot's promotion instant and ``groups`` each offered group's
    name by id, which the page script maps a preset with.
    """
    import json

    data = getattr(form, "families_source", None)
    if form.is_bound and hasattr(form, "cleaned_data"):
        mode = "advanced" if form.cleaned_data.get(MODE) == "advanced" else "basic"
        rule, _field, message = posted_rule(
            form.cleaned_data, data.groups if data is not None else ()
        )
    else:
        mode, rule, message = "basic", campaign_families.DEFAULT, None
    line = (
        {"count": str(message), "emailable": "", "matches": 0}
        if rule is None
        else preview_line(data, rule)
    )
    return {
        "mode": mode,
        "line": line,
        "as_of": data.promoted_at if data is not None else None,
        "groups": json.dumps(
            {str(group.id): group.name for group in (data.groups if data else ())}
        ),
        "presets": json.dumps(
            {name: list(groups) for name, groups in FAMILY_RULE_PRESET_GROUPS.items()}
        ),
        "help": PRESET_HELP,
    }


def locked_words(values):
    """Campaign settings' words for a campaign's saved rule.

    Only an Advanced rule reads the latest data's groups, to mark a chosen
    group ParishSoft no longer has; a preset or the legacy rule reads
    nothing.
    """
    rule = campaign_families.stored_rule(values)
    groups = None
    if rule is not None and rule["rule"] == campaign_families.ADVANCED:
        data = source()
        groups = data.groups if data is not None else ()
    return describe(rule, groups)


def families_post(rule):
    """The section's posted fields for a stored rule (the CLI's binding).

    A preset is Basic with that preset; an Advanced rule is Advanced with
    its three conditions, so the page's own form validates it.
    """
    if rule["rule"] in PRESET_LABELS:
        return {MODE: "basic", PRESET: rule["rule"]}
    return {
        MODE: "advanced",
        REGISTERED: "required" if rule["registered"] else "not_required",
        GROUPS: [str(group["id"]) for group in rule["family_groups"]],
        STATUSES: list(rule["member_statuses"]),
    }
