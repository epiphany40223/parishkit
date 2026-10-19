"""Census Add on the review worklist: a change no Family answer asked for (#1710).

Staff and Administrators may **Add** a census change from the Census changes
worklist's selection bar (review pipeline spec, "Reviewed stage"): one
Member field or Family address of a Family that responded, with the value to
carry into ParishSoft. It starts Accepted, shown as *Added by the parish*,
under its creator's name, and may name the Family's ignored change it was
added in place of. Its value is checked by the same rules as a Family's
answer for that field and as the worklist's Edit (``census_review.
edited_value``: a Family address by the Family form's address rules, a
Member field as the Family form reads it and, for a field the handling
registry marks API-writable, as publication would send it).

An addition is its own immutable ``CensusAddition`` row, never a proposal:
proposals belong to the Family submit path, whose guards and derived rows
stay exactly as they are. Until the update run is rebuilt (#1688) nothing
writes an addition to ParishSoft, so each is To enter by hand
(``pipeline.census_addition_placement``); its one later step is a
``CensusAdditionResolution``: Entered by hand, or withdrawn by Back to
review. SQL checks the actor, the campaign, the Family, the Member, the field
and the value's shape again, and pairs each row with its audit event
(``schema/migrations/0137_census_additions.sql``).
"""

from uuid import UUID

from django.db import connection

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign

from .capabilities import capability
from .census_resolution import KeyRebound
from .census_review import (
    ADDRESS_FIELDS,
    FIELDS,
    MAX_BULK,
    NOT_EDITABLE,
    BulkStale,
    InvalidEdit,
    _authorize,
    _parish_id,
    _valid_note,
    bulk_key_for,
    campaign_today,
    edited_value,
)
from .comparison import ADDRESS_COMPONENTS
from .models import (
    ADDITION_ACTIONS,
    CensusAddition,
    CensusAdditionResolution,
    ProposedChange,
)

# What an Add may be for: the fields the worklist's Edit takes a value for,
# a Member's first, then the Family's addresses (SQL lists the same).
MEMBER_ADD_FIELDS = tuple(name for name in FIELDS if name not in NOT_EDITABLE)
FAMILY_ADD_FIELDS = ("home_address", "mailing_address")
ADD_FIELDS = MEMBER_ADD_FIELDS + FAMILY_ADD_FIELDS
# An addition's id on the worklist, which tells it from a proposal's.
ADDED_PREFIX = "add-"
# Open changes of a field that a new Add would duplicate: the Family's own
# change, not ignored, still open (SQL checks the same).
OPEN_EXECUTIONS = ("pending", "conflict", "queued", "failed")


class AddRefusal(InvalidEdit):
    """An Add the reader can correct, in plain words, with its closed code.

    ``field_ids`` name the Add dialog's fields the reason is shown at; the
    message is static and never holds a submitted value.
    """

    def __init__(self, code, message, *, field_ids):
        super().__init__(message)
        self.code = code
        self.field_ids = tuple(field_ids)


def _one(fields, name):
    """The single posted value of ``name``, else a malformed form."""
    values = fields.get(name)
    if values is None or len(values) != 1:
        raise ValueError(f"An Add sends {name} once.")
    return values[0]


def _duid(text, code, message, field_id):
    """A posted DUID as an integer, or the refusal that asks for one."""
    text = text.strip()
    if not text.isascii() or not text.isdecimal() or not 0 < int(text) < 2**31:
        raise AddRefusal(code, message, field_ids=(field_id,))
    return int(text)


def parse_add(fields):
    """Read the Add dialog's posted ``fields`` (name → list of values).

    The dialog sends ``value_field`` (what changed), ``value_family`` (the
    Family DUID), ``value_in_place_of`` (an ignored change's id, or empty)
    and, for a Member field, ``value_member`` and ``value_<field>``; for a
    Family address, each part as ``value_<part>``. Fields the dialog hides
    are not sent. Returns a dict for ``add_census_change``; a blank or
    wrong DUID or field is an ``AddRefusal``, anything else malformed a
    ValueError.
    """
    field = _one(fields, "value_field")
    if field not in ADD_FIELDS:
        raise AddRefusal(
            "add_field", "Choose what changed.", field_ids=("value_field",)
        )
    family = field in FAMILY_ADD_FIELDS
    expected = {"value_field", "value_family", "value_in_place_of"}
    if family:
        expected |= {f"value_{part}" for part in ADDRESS_COMPONENTS}
    else:
        expected |= {"value_member", f"value_{field}"}
    if set(fields) != expected:
        raise ValueError("An Add sends its own fields only.")
    found = {
        "field": field,
        "entity_kind": "family" if family else "member",
        "family_duid": _duid(
            _one(fields, "value_family"),
            "add_family",
            "Enter the Family's DUID, a number from ParishSoft.",
            "value_family",
        ),
        "member_duid": None,
        "in_place_of": _one(fields, "value_in_place_of"),
    }
    if family:
        found["value"] = {
            part: _one(fields, f"value_{part}") for part in ADDRESS_COMPONENTS
        }
    else:
        found["member_duid"] = _duid(
            _one(fields, "value_member"),
            "add_member",
            "Enter the Member's DUID, a number from ParishSoft.",
            "value_member",
        )
        found["value"] = _one(fields, f"value_{field}")
    return found


def _value(add, campaign):
    """The added value in the census form, checked as Edit checks a value.

    By the handling registry's own handling for the field, so a value
    publication could not send is never stored, and dates against the
    campaign's today. A refusal names the dialog's field (an address: each
    part with a reason).
    """
    field, entity = add["field"], add["entity_kind"]
    try:
        return edited_value(
            field,
            add["value"],
            today=campaign_today(campaign),
            handling=capability(entity, field).handling.value,
            entity=entity,
        )
    except InvalidEdit as error:
        # A blank address has no part of its own to blame: its street line.
        ids = (
            [f"value_{part}" for part in error.fields] or ["value_line1"]
            if field in ADDRESS_FIELDS
            else [f"value_{field}"]
        )
        raise AddRefusal("add_value", str(error), field_ids=ids) from None


def _family(campaign_id, family_duid):
    """The campaign's Family with that DUID, which must have responded."""
    family = (
        FamilyCampaign.objects.filter(campaign_id=campaign_id, family_duid=family_duid)
        .exclude(first_live_submission_id=None)
        .first()
    )
    if family is None:
        raise AddRefusal(
            "add_no_response",
            "No Family with that DUID has responded in this campaign. A change "
            "can be added only for a Family that responded.",
            field_ids=("value_family",),
        )
    return family


def _member_listed(family, member_key):
    """Refuse a Member ParishSoft's current data does not list as current there.

    The standing the worklists filter on (``stewardship_member_source_
    status_v1``, #1217), so Add never makes a change nobody can enter.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_member_source_status_v1("
            "(SELECT snapshot_id FROM stewardship_source_current WHERE singleton),"
            "%s,%s)",
            [member_key, family.family_duid],
        )
        (status,) = cursor.fetchone()
    if status != "current":
        raise AddRefusal(
            "add_not_in_parishsoft",
            "ParishSoft's data does not list that Member in that Family, or lists "
            "them as inactive or deceased, so no change can be added for them.",
            field_ids=("value_member",),
        )


def _no_open_change(family, entity_kind, entity_key, field):
    """Refuse an Add while the same field already has an open change.

    An open addition, or the Family's own change that is not ignored, is
    decided with its own icons (an Edit corrects the Family's value).
    """
    added = CensusAddition.objects.filter(
        family=family,
        entity_kind=entity_kind,
        entity_key=entity_key,
        field=field,
        resolution__isnull=True,
    ).exists()
    asked = (
        ProposedChange.objects.filter(
            submission__family=family,
            submission__mode="live",
            entity_kind=entity_kind,
            entity_key=entity_key,
            field=field,
            execution__in=OPEN_EXECUTIONS,
        )
        .exclude(decision="ignored")
        .exists()
    )
    if added or asked:
        raise AddRefusal(
            "add_exists",
            "This field already has an open change. Use that change's own icons "
            "instead (Edit corrects the value).",
            field_ids=("value_field",),
        )


def _in_place_of(family, text):
    """The Family's open ignored change the addition replaces, or None."""
    if not text:
        return None
    try:
        identity = UUID(text)
    except ValueError:
        raise ValueError("Invalid change to add in place of.") from None
    found = (
        ProposedChange.objects.filter(
            pk=identity,
            submission__family=family,
            submission__mode="live",
            decision="ignored",
            handling__in=("api", "manual"),
            execution__in=("pending", "conflict", "failed"),
        )
        .only("id")
        .first()
    )
    if found is None:
        raise AddRefusal(
            "add_in_place_of",
            "The change to add in place of must be an ignored change of the same "
            "Family.",
            field_ids=("value_in_place_of",),
        )
    return found


def add_census_change(store, actor_id, campaign_id, add, *, note, request_key):
    """Record one parish-added census change; (addition, created).

    ``add`` is ``parse_add``'s answer. Under the work order, for a current
    Staff member or Administrator in a campaign that admits changes: the
    Family must have responded, a Member must be current in it, the value
    must pass its field's rules, no open change of that field may wait
    already, and the change it is added in place of must be the Family's
    ignored one. The same ``request_key`` with the same Add returns the
    first addition (``created`` false); with another it is refused. Raises
    ``AddRefusal`` for what the reader can correct.
    """
    if not isinstance(actor_id, UUID) or not isinstance(request_key, UUID):
        raise ValueError("Invalid census addition.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        admit_campaign(campaign_id, mutating=True)
        family = _family(campaign_id, add["family_duid"])
        entity_key = (
            str(add["member_duid"])
            if add["entity_kind"] == "member"
            else str(family.family_duid)
        )
        identity = (family.pk, add["entity_kind"], entity_key, add["field"])
        previous = CensusAddition.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if identity != (
                previous.family_id,
                previous.entity_kind,
                previous.entity_key,
                previous.field,
            ):
                raise KeyRebound("This request key is already bound.")
            return previous, False
        if add["entity_kind"] == "member":
            _member_listed(family, entity_key)
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=campaign_id
        )
        value = _value(add, campaign)
        _no_open_change(family, add["entity_kind"], entity_key, add["field"])
        replaced = _in_place_of(family, add["in_place_of"])
        addition = CensusAddition.objects.create(
            actor_id=actor_id,
            request_key=request_key,
            family=family,
            entity_kind=add["entity_kind"],
            entity_key=entity_key,
            field=add["field"],
            value=value,
            in_place_of=replaced,
            note=note,
        )
        # The audit names the addition and its version, never the value.
        record_action(
            Action.CENSUS_CHANGE_UPDATED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor_id,
            subject_id=addition.pk,
            parish_id=_parish_id(),
            campaign_id=campaign_id,
            context={"outcome": Outcome.CHANGED, "version": 1},
        )
        return addition, True


def resolve_census_additions(
    store, actor_id, campaign_id, versions, action, *, note, request_key, bulk=False
):
    """Mark additions Entered by hand, or withdraw them, all or none.

    ``versions`` maps each addition id to the version its row showed (1
    while open); ``action`` is ``entered`` or ``withdrawn``. Each gets its
    own resolution row and audit event, its request key derived from the
    page's bulk key for ``bulk`` (``bulk_key_for``), so a repeated post
    records nothing more. One already resolved, or shown at another
    version, raises ``BulkStale`` naming it. Nestable inside the caller's
    work transaction, so a bulk decision over proposals and additions
    together is all or none. Returns the resolutions in id order.
    """
    if (
        not isinstance(actor_id, UUID)
        or not isinstance(request_key, UUID)
        or action not in ADDITION_ACTIONS
        or not isinstance(versions, dict)
        or not 1 <= len(versions) <= MAX_BULK
        or any(
            not isinstance(key, UUID) or type(version) is not int
            for key, version in versions.items()
        )
    ):
        raise ValueError("Invalid census addition decision.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        admit_campaign(campaign_id, mutating=True)
        additions = list(
            CensusAddition.objects.filter(
                pk__in=list(versions), family__campaign_id=campaign_id
            ).order_by("pk")
        )
        if len(additions) != len(versions):
            raise CensusAddition.DoesNotExist(
                "A chosen census addition is unavailable."
            )
        parish = _parish_id()
        recorded = []
        for addition in additions:
            key = bulk_key_for(request_key, addition.pk) if bulk else request_key
            previous = CensusAdditionResolution.objects.filter(
                actor_id=actor_id, request_key=key
            ).first()
            if previous is not None:
                if (previous.addition_id, previous.action) != (addition.pk, action):
                    raise KeyRebound("This request key is already bound.")
                recorded.append(previous)
                continue
            done = CensusAdditionResolution.objects.filter(addition=addition).exists()
            if done or versions[addition.pk] != 1:
                raise BulkStale(addition.pk)
            resolution = CensusAdditionResolution.objects.create(
                actor_id=actor_id,
                request_key=key,
                addition=addition,
                action=action,
                note=note,
            )
            record_action(
                Action.CENSUS_CHANGE_UPDATED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor_id,
                subject_id=resolution.pk,
                parish_id=parish,
                campaign_id=campaign_id,
                context={
                    "outcome": Outcome.CHANGED,
                    "before_version": 1,
                    "after_version": 2,
                },
            )
            recorded.append(resolution)
        return recorded
