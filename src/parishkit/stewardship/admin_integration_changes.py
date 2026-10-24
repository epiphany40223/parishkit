"""Integration settings change commands (ADM-11 PR 10).

``integration preview`` and ``integration confirm`` are an integration
settings page's non-secret **Save** (its review, then **Apply**) and its
**Remove**, through the page's own request-free pieces in
``accounts.integration_views`` (``review_settings``, ``review_removal``, the
page's form and salt) and ``admin_editing.confirm_intent``, so the page and
the command line cannot drift:

- ``integration preview TARGET`` admits as the page's post does (recording
  activity, so it needs a full-scope session), passes the page's privileged
  check with the live session (``sign_in_instant``, #547; a preview records
  no notice), binds the change document to the page's own
  ``IntegrationForm`` in one read snapshot and prints the page's review
  with ``preview.token``, the same signed binding the page embeds. With
  ``--remove`` it is the page's Remove review instead. It changes nothing
  and records no event, as the page's review records none.
- ``integration confirm TARGET`` takes that token (or one the page signed:
  tokens are bound to the Administrator, not the channel) and records the
  configuration request exactly as the page's **Apply** does, with the
  page's privileged check under the work lock (which records the
  ``automation_fresh_gate`` event and ``fresh_gated`` notice). In the
  request's own durable transaction it records one
  ``admin_cmd_integration_confirm`` event whose subject is the automation
  session; a Slack change or removal also leaves a ``policy_change``
  notice and observes ``automation_policy_change`` (admin-automation
  specification, "Notifications"). A repeated confirm returns the original
  request and records nothing new.

The change document (default, pending Administrator confirmation) has the
shape of ``integration show``'s ``settings`` member::

    {"settings": {"sender": "office@example.org", "sender_name": null}}

Any non-empty subset of the page's fields for the target; a field the
document leaves out keeps its applied value. Only ``sender_name`` may be
null or empty (the page then omits it and mail uses the parish name).
ParishSoft's refresh schedule is not part of it: an unchanged schedule
keeps its stored keys exactly, as the page's save does. Keys are never
accepted here; Slack, which its first key sets up, cannot be set up by
settings alone.

ParishSoft's refresh schedule is changed with ``--refresh-schedule``
(#632), a YAML or JSON document in the stored ``refresh_rules`` shape::

    {"rules": [{"kind": "full", "at": "02:00"}],
     "skips": [], "skip_around_family_emails": false}

It is turned into the page editor's own posted rows (``schedule_rows``)
and read by the page's ``ScheduleEditor`` inside the page's form, so the
command and the page apply identical rules: the row checks, the caps, kept
off-quarter-hour times, the shared validator, the derived stored settings
and "an unchanged schedule keeps its stored keys exactly".
"""

import json
from dataclasses import dataclass

from django.db import DatabaseError

from .admin_changes import (
    InvalidChange,
    _admit_change,
    _ended_or_raise,
    _problem,
    form_problems,
    unexpected_problem,
)
from .admin_reads import NotAvailable, ReadModel, _held, _recheck, config_request
from .wall_clock import is_hhmm

CHANGES_LIMIT = 64_000
# The refresh schedule document's members (the stored ``refresh_rules``
# shape) and its rows' shapes, checked before the page's editor reads them.
SCHEDULE = "refresh_schedule"
SCHEDULE_MEMBERS = frozenset({"rules", "skips", "skip_around_family_emails"})
RULE_SHAPES = (frozenset({"kind", "at"}), frozenset({"kind", "every", "from", "to"}))
SKIP_SHAPES = (frozenset({"at"}), frozenset({"from", "to"}))
TIME_MEMBERS = frozenset({"at", "from", "to"})
# The settings that may be null (or empty): the optional From name.
NULLABLE = frozenset({"sender_name"})


def setting_names(target):
    """The page's settings fields for ``target``, in the page's order."""
    from .accounts.integration_forms import IntegrationForm

    return [name for name in IntegrationForm(target).fields if name != "base_digest"]


def parse_settings(text, target):
    """Parse a change document's shape; the values are the page form's job.

    Refuses (``ValueError``, ``invalid``) anything but an object whose only
    member is a non-empty ``settings`` object of strings, integers or nulls.
    A member that is not one of the target's settings, or a null other than
    ``sender_name``, is ``InvalidChange`` at ``settings.<name>``.
    """
    if len(text) > CHANGES_LIMIT:
        raise ValueError("The change document is too long.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if not isinstance(value, dict) or set(value) != {"settings"}:
        raise ValueError("The change document must have only a settings object.")
    fields = value["settings"]
    if not isinstance(fields, dict) or not fields:
        raise ValueError("The change document's settings are not valid.")
    names = setting_names(target)
    problems = [
        unexpected_problem(
            f"settings.{name}", "This is not one of this integration's settings."
        )
        for name in sorted(set(fields) - set(names))
    ]
    problems += [
        {
            "field": f"settings.{name}",
            "code": "required",
            "message": "Give a value; only sender_name may be null.",
        }
        for name in sorted(fields)
        if name in names and fields[name] is None and name not in NULLABLE
    ]
    if problems:
        raise InvalidChange(problems)
    if not all(
        item is None or (isinstance(item, (str, int)) and not isinstance(item, bool))
        for item in fields.values()
    ):
        raise ValueError("Each setting must be a string, a number or null.")
    return fields


def _schedule_problem(field, message, code="invalid"):
    """One refresh schedule problem at ``refresh_schedule`` plus ``field``."""
    return _problem(f"{SCHEDULE}{field}", {"code": code, "message": message})


def _row_problems(rows, scope, shapes, noun):
    """The problems of one list of schedule rows, in fixed words.

    Each row must be an object with exactly one of ``shapes``' members, a rule's
    ``kind`` "full" (quick rules were removed, #1147), its times ``HH:MM`` text
    (YAML reads an unquoted 12:30 as a number) and a rule's ``every`` one of the
    editor's intervals. Every value the page's form would quote back in its own
    messages is checked here first, so a refusal never echoes the input; the
    page's editor checks the rest (quarter hours, spacing, caps).
    """
    from parishkit.stewardship.source.refresh_rules import STEPS

    found = []
    for index, row in enumerate(rows):
        field = f".{scope}[{index}]"
        if not isinstance(row, dict) or frozenset(row) not in shapes:
            message = f"This is not a {noun}."
        elif "kind" in row and row["kind"] != "full":
            message = 'Give kind as "full"; quick updates were removed.'
        elif not all(is_hhmm(row[name]) for name in set(row) & TIME_MEMBERS):
            message = 'Give each time as quoted HH:MM text, such as "02:00".'
        # Exactly an int: 60.0 equals a step but the form would echo it, and
        # a bool is an int subclass.
        elif "every" in row and (
            type(row["every"]) is not int or row["every"] not in STEPS
        ):
            message = "Give every as one of {}.".format(
                ", ".join(str(step) for step in STEPS)
            )
        else:
            continue
        found.append(_schedule_problem(field, message))
    return found


def parse_schedule(text):
    """Parse ``--refresh-schedule``: the stored ``refresh_rules`` shape.

    YAML or JSON (JSON is YAML), read with the configuration's bounded,
    duplicate-refusing loader. ``rules`` is required; ``skips`` defaults to
    none and ``skip_around_family_emails`` stays absent when not given (the
    schedule's current value is then kept). A malformed document is
    ``ValueError`` and a member or row of another shape ``InvalidChange``
    at ``refresh_schedule...``, never echoing the input
    (``_row_problems``); an explicit null switch is refused. Quarter hours,
    spacing and caps are the page editor's to check.
    """
    import yaml

    from parishkit.config import ConfigError, _UniqueKeySafeLoader

    if len(text) > CHANGES_LIMIT:
        raise ValueError("The refresh schedule is too long.")
    try:
        value = yaml.load(text, Loader=_UniqueKeySafeLoader)  # noqa: S506
    except (yaml.YAMLError, ConfigError):
        raise ValueError("The refresh schedule is not YAML or JSON.") from None
    if not isinstance(value, dict):
        raise ValueError("The refresh schedule must be an object with rules.")
    problems = []
    if set(value) - SCHEDULE_MEMBERS:
        # Never named: the member is the caller's input, not echoed.
        problems.append(
            _schedule_problem(
                "", "Give only rules, skips and skip_around_family_emails."
            )
        )
    if "rules" not in value:
        problems.append(_schedule_problem(".rules", "Give the rules.", "required"))
    for name, shapes, noun in (
        ("rules", RULE_SHAPES, "rule"),
        ("skips", SKIP_SHAPES, "skip"),
    ):
        if name not in value:
            continue
        if not isinstance(value[name], list):
            problems.append(_schedule_problem(f".{name}", "Give a list."))
        else:
            problems += _row_problems(value[name], name, shapes, noun)
    # Present means true or false: a null would turn the switch off.
    switch = value.get("skip_around_family_emails")
    if "skip_around_family_emails" in value and not isinstance(switch, bool):
        problems.append(
            _schedule_problem(".skip_around_family_emails", "Give true or false.")
        )
    if problems:
        raise InvalidChange(problems)
    return {"skips": [], **value}


def schedule_rows(document, editor):
    """The page editor's posted rows, switch and mode for a parsed document.

    Each row is posted as the page draws it (``rule_initial``,
    ``skip_initial``), so ``ScheduleEditor`` reads it exactly as a save from
    the page. A switch left out keeps the one the page shows (``editor`` is
    the unbound editor of the stored schedule); the mode is the one the
    page opens in (``ScheduleEditor.mode``: Advanced when the shown
    schedule has no Basic equivalent, else the one last saved, Basic when
    none was), which ``settings`` keeps only while Basic can show the new
    schedule, as the page's untouched toggle does.
    """
    from .accounts.refresh_schedule_forms import (
        MODE,
        RULE_ROWS,
        RULES,
        SKIP_ROWS,
        SKIPS,
        SWITCH,
        rule_initial,
        skip_initial,
    )

    data = {}
    for prefix, rows, most, initial in (
        (RULES, document["rules"], RULE_ROWS, rule_initial),
        (SKIPS, document["skips"], SKIP_ROWS, skip_initial),
    ):
        data |= {
            f"{prefix}-TOTAL_FORMS": str(len(rows)),
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": str(most),
        }
        for index, row in enumerate(rows):
            for name, value in initial(row).items():
                data[f"{prefix}-{index}-{name}"] = str(value)
    if document.get(SWITCH, editor.shown[SWITCH]):
        data[SWITCH] = "on"
    # The mode the page opens in, which its toggle posts when untouched.
    data[MODE] = editor.mode
    return data


def schedule_errors(editor):
    """The page editor's problems for a changed schedule, at each row.

    ``refresh_schedule.rules[N]`` or ``.skips[N]`` (the document's rows, in
    its order), or ``refresh_schedule`` for the schedule as a whole, each
    with the page's words. An unchanged schedule's problems never block.
    """
    return editor_problems(editor) if editor.blocking else []


def editor_problems(editor):
    """Every problem the page's schedule editor found, at each row.

    The same places and words as ``schedule_errors``, whether or not they
    block saving: ``integration schedule-preview`` reports them as the
    page's live check does.
    """
    return [
        _schedule_problem(
            f".{problem.scope}[{problem.index}]" if problem.scope else "",
            problem.text,
        )
        for problem in editor.problems()
    ]


def schedule_review(editor, stored, zone):
    """The preview's ``schedule`` member for a changed schedule, else None.

    The schedule as the page showed it (``before``) and as posted
    (``after``), both in the stored ``refresh_rules`` shape with wall times
    in ``zone``, and the review's differences in words.
    """
    from .accounts.refresh_schedule_forms import differences

    if editor is None or not editor.changed:
        return None
    after = editor.document()
    return {
        "timezone": zone,
        "before": editor.shown,
        "after": after,
        "differences": [
            str(item) for item in differences(stored, after, timezone=zone)
        ],
    }


def form_data(stored, names, values, expected_version):
    """The page's form post: the applied settings, then the document's values.

    ``names`` are the page's fields and ``values`` the document's. Omitted
    fields keep the applied value the page's editor shows, so the review
    reports only what the document changes; a null is the blank field. No
    refresh schedule rows are posted, so the page's schedule editor keeps
    the stored schedule exactly.
    """
    data = {name: str(stored.get(name, "")) for name in names}
    data.update(
        {name: "" if value is None else str(value) for name, value in values.items()}
    )
    data["base_digest"] = expected_version
    return data


def field_errors(form):
    """Every problem the page's form found, with the page's messages.

    The field is ``settings.<name>``, or ``settings`` for the form as a
    whole (no change).
    """
    return form_problems("settings", form.errors, "settings")


@dataclass(frozen=True)
class IntegrationPreview(ReadModel):
    """The page's review of an integration settings change, and its token.

    ``version`` is the applied configuration's digest the change was made
    against; ``removing`` whether this is the page's Remove. Each change
    names its ``field`` and the stored values ``before`` and ``after`` (None
    for an absent setting), in the page's order; a removal lists none.
    ``schedule`` is ParishSoft's changed refresh schedule
    (``schedule_review``), else None. ``preview.token`` is the signed
    binding ``integration confirm`` takes, valid for fifteen minutes.
    """

    version: str
    target: str
    name: str
    removing: bool
    changes: list
    schedule: dict | None
    preview: dict


@dataclass(frozen=True)
class IntegrationConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def _review(
    service, actor, configuration, target, fields, expected_version, schedule=None
):
    """The page's Save review of ``fields``: ``(changes, schedule, token)``.

    As the page's ``_preview``: an off-site folder is refused in the local
    environment, a required integration with no record is the page's 404
    (``not_available``), and Slack not yet set up needs its key (the page's
    refusal at the key field). The page's field errors and "No settings
    have changed." are ``invalid`` at each field. ``schedule`` is a parsed
    ParishSoft refresh schedule document, posted as the editor's rows; its
    problems are ``invalid`` at ``refresh_schedule...``.
    """
    from .accounts.integration_forms import SETUP_KEY_FIELDS
    from .accounts.integration_views import (
        _form,
        _optional,
        _refuse_drive_in_local,
        _schedule_zone,
        review_settings,
    )
    from .accounts.refresh_schedule_forms import ScheduleEditor

    if target == "backup":
        _refuse_drive_in_local()
    try:
        record = _optional(configuration, target)
    except LookupError:
        raise NotAvailable("That integration is not set up.") from None
    if record is None and target != "backup":
        raise InvalidChange(
            [
                {
                    "field": "key",
                    "code": "required",
                    "message": str(SETUP_KEY_FIELDS[target][2]),
                }
            ]
        )
    stored = {} if record is None else record["values"]["settings"]
    names = setting_names(target)
    data = form_data(stored, names, fields, expected_version)
    if schedule is not None:
        data |= schedule_rows(schedule, ScheduleEditor(stored=stored))
    form = _form(target, data, stored=record)
    review = review_settings(service, actor, configuration, target, form)
    if review is None:
        problems = field_errors(form)
        if form.schedule is not None:
            problems += schedule_errors(form.schedule)
        raise InvalidChange(problems)
    before, after = review["before"], review["after"]
    changes = [
        {"field": name, "before": before.get(name), "after": after.get(name)}
        for name in names
        if before.get(name) != after.get(name)
    ]
    found = schedule_review(form.schedule, before, _schedule_zone(configuration))
    return changes, found, review["preview"]


def preview_integration(
    caller,
    service,
    target,
    *,
    expected_version,
    changes=None,
    remove=False,
    refresh_schedule=None,
):
    """``integration preview``: an integration settings page's review.

    Admits as the page's post does, passes the page's privileged check with the
    live full-scope session, then, in one read snapshot, checks the version
    (another one is ``stale_version``) and builds the page's own review of
    the change document and, for ParishSoft, the ``refresh_schedule``
    document (either may be None, not both), or of removing the integration
    (``remove``): one that cannot be removed or is not set up is
    ``not_available``, as the page's 404. A resulting document the
    configuration refuses is ``invalid``. Rechecks access afterwards.
    Changes nothing.
    """
    from django.db import transaction

    from parishkit.config import ConfigError

    from .accounts.admin_editing import editable_configuration
    from .accounts.integration_forms import LABELS
    from .accounts.integration_views import review_removal
    from .accounts.policy import Capability
    from .accounts.sessions import sign_in_instant
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    actor = _admit_change(caller, service)
    schedule = None
    if refresh_schedule is not None:
        if target != "parishsoft":
            raise InvalidChange(
                [_schedule_problem("", "Only ParishSoft has a refresh schedule.")]
            )
        schedule = parse_schedule(refresh_schedule)
    fields = {} if remove or changes is None else parse_settings(changes, target)

    def step():
        """Pass the privileged check, then check and review in one snapshot."""
        # The page checks the session before its review (#547); the
        # session's FOR SHARE lock needs a writable transaction, so it is
        # checked just before the read-only snapshot. A preview has no
        # command type, so it records no notice.
        with transaction.atomic():
            sign_in_instant(caller)
        with read_transaction():
            configuration = editable_configuration(service)
            if configuration.active_configuration.digest != expected_version:
                raise StaleRecordError("The configuration changed since then.")
            try:
                if remove:
                    try:
                        token = review_removal(service, actor, configuration, target)
                    except LookupError:
                        raise NotAvailable(
                            "That integration cannot be removed."
                        ) from None
                    found, schedule_found = [], None
                else:
                    found, schedule_found, token = _review(
                        service,
                        actor,
                        configuration,
                        target,
                        fields,
                        expected_version,
                        schedule,
                    )
            except ConfigError:
                # Raised past _held, which would read it as unavailable: the
                # configuration refuses the resulting document, as the
                # page's review would.
                raise ValueError("The configuration refuses this change.") from None
        return IntegrationPreview(
            version=expected_version,
            target=target,
            name=str(LABELS[target]),
            removing=remove,
            changes=found,
            schedule=schedule_found,
            preview={"token": token},
        )

    try:
        model = _held(step)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def applied_settings(service, target):
    """The applied settings of ``target`` the prompt compares against, or {}.

    A read before any transaction is held (the prompt waits for a person);
    an integration not set up has none.
    """
    from .accounts.admin_editing import editable_configuration
    from .accounts.integration_views import _optional
    from .campaigns.work_locks import read_transaction

    def read():
        """The stored settings in one snapshot."""
        with read_transaction():
            try:
                record = _optional(editable_configuration(service), target)
            except LookupError:
                return {}
        return {} if record is None else dict(record["values"]["settings"])

    return _held(read)


def prompt_lines(target, token, applied):
    """The confirmation prompt's summary, from the token's reviewed patch.

    Names the integration and the settings the review changes against the
    ``applied`` ones (so an unchanged stored refresh schedule's keys are
    never listed), never a value. A token that is not this target's is
    ``invalid`` before the prompt (an expired or out-of-date one is checked
    properly by ``confirm_intent`` afterwards).
    """
    from django.core import signing

    from .accounts.integration_forms import LABELS
    from .accounts.integration_views import SALT

    try:
        [operation] = signing.loads(token, salt=SALT + target)["patch"]
        kind = operation["operation"]
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    label = LABELS[target]
    if kind == "remove":
        return [f"This removes {label}."]
    after = operation["values"].get("settings", {})
    changed = sorted(
        name
        for name in set(applied) | set(after)
        if applied.get(name) != after.get(name)
    )
    if not changed:
        # The applied settings already changed since the review; the
        # confirmation then refuses the out-of-date token.
        return [f"This applies the reviewed {label} settings."]
    return [f"This changes the {label} settings: {', '.join(changed)}."]


def confirm_integration(caller, service, target, *, token, context):
    """``integration confirm``: the page's **Apply** for a reviewed change.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the page's salt for ``target`` (expired or out of date is
    ``stale_version``, altered or another target's ``invalid``, another
    Administrator's ``denied``), passes the page's privileged check again under
    the work lock and records the request with
    ``admin_cmd_integration_confirm`` (and, for Slack, the policy notice)
    in its own durable transaction. The configuration must be editable
    first (``_held``), so a restore under review or an activating change is
    ``unavailable``. ``context["committed"]`` and ``context["request_id"]``
    follow ``parish confirm``'s rules, so a later error is
    ``outcome_unknown`` naming the request.
    """
    from uuid import UUID

    from django.core import signing

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.automation_sessions import notify, observe
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.integration_views import SALT
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.operational_content import IncidentKind
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    _held(lambda: editable_configuration(service))
    salt = SALT + target
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=salt)["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []

    def attach(request, extra):
        """Record the command's event (and Slack's notice) with the request."""
        record_action(
            Action.ADMIN_CMD_INTEGRATION_CONFIRM,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        if target == "slack":
            # Slack notifications are notification settings: changing or
            # removing them tells every Administrator (admin-automation
            # specification, "Notifications").
            notify(
                "policy_change",
                caller.automation_session,
                command_type=caller.command_type,
                actor_id=actor.identity,
            )
            observe(IncidentKind.AUTOMATION_POLICY_CHANGE)
        created.append(request.pk)

    try:
        receipt = confirm_intent(
            caller,
            service,
            actor,
            token=token,
            salt=salt,
            link=None,
            current_scope=lambda service: (editable_configuration(service), None),
            attach=attach,
            # Checked again under the work lock, as the page's Apply (#547).
            privileged=True,
        )
    except signing.BadSignature:
        # Altered, or signed for another integration or page (an expired
        # one is stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return IntegrationConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
