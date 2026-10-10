"""Per-Member "cannot participate in ministries" answer, and retired talents.

The answer is recorded alongside the Ministry page, in a per-Member entry
that also holds a ``talents`` map. The Family form no longer asks for
talents (#970): every campaign resolves to no talent options, so each new
entry's map is empty, though a form opened before then may still send a
configured talent, which is stored and never shown. Older submissions keep
the talents they recorded.
``DEFAULT_TALENTS`` stays because the installed SQL guard's
``stewardship_talent_defaults_v1()`` still lists the same options.
"""

from .census import clean_text
from .financial import ShareOption

TALENT_TEXT_LIMIT = 200

# Fixed identities: a configuration without an explicit list resolves to these
# on every read, so they must never change once released.
DEFAULT_TALENTS = (
    ShareOption("6f1c6a52-2e33-4d6c-9a7a-0d5f3f3b1a01", "Painter", False),
    ShareOption("6f1c6a52-2e33-4d6c-9a7a-0d5f3f3b1a02", "Florist", False),
    ShareOption("6f1c6a52-2e33-4d6c-9a7a-0d5f3f3b1a03", "Seamstress", False),
    ShareOption("6f1c6a52-2e33-4d6c-9a7a-0d5f3f3b1a04", "Carpenter", False),
    ShareOption("6f1c6a52-2e33-4d6c-9a7a-0d5f3f3b1a05", "Attorney", False),
    ShareOption("6f1c6a52-2e33-4d6c-9a7a-0d5f3f3b1a06", "Gardener", False),
    ShareOption("6f1c6a52-2e33-4d6c-9a7a-0d5f3f3b1a07", "Other", True),
)


class InvalidServiceAnswers(ValueError):
    """Static messages keyed by trusted paths; never echo talent text."""

    def __init__(self, fields):
        """Keep private free text out of exception strings."""
        self.fields = dict(fields)
        super().__init__("Please review the talents and ministry choices.")


def configured_talent_options(configuration):
    """The talent list a campaign's configuration names; never offered.

    No campaign offers talents now (#970), but ``inputs.definition_digest``
    still hashes this list exactly as before: a Family's form issued earlier
    keeps its stored definition digest, and a different digest for the same
    configuration would refuse that Family's submission as unverifiable.
    Submissions are also validated against it, so a talent sent from a form
    opened before #970 is still stored rather than refusing the whole
    response; malformed or unknown values are still refused. Only
    Ministry-enabled campaigns name talents; a never-edited list resolves to
    ``DEFAULT_TALENTS``. Never use this to offer talents.
    """
    if "ministry" not in configuration["modules"]:
        return ()
    values = configuration.get("talent_options")
    if values is None:
        return DEFAULT_TALENTS
    return tuple(ShareOption(**value) for value in values)


def talent_options(configuration):
    """No campaign offers talents any more (#970), whatever its configuration.

    An older campaign's ``talent_options`` value stays as inert history. With
    no options offered, the Family form shows no Talents question and sends
    an empty talents map.
    """
    return ()


def _talents(value, options):
    """Normalize one Member's selections like financial share methods.

    A free-text option (such as "Other") requires its text; any other
    option must carry an empty string. Unlike share methods none is required.
    """
    allowed = {option.id: option for option in options}
    if type(value) is not dict or not set(value) <= allowed.keys():
        return None
    result = {}
    for key in sorted(value):
        text = clean_text(value[key], TALENT_TEXT_LIMIT)
        if text is None or bool(text) != allowed[key].free_text:
            return None
        result[key] = text
    return result


def validate_service_answers(payload, ministries, inputs, options):
    """Validate talents and the Ministry lock against validated Ministry answers.

    ``ministries`` is the already-normalized Ministry section; this section
    may only name its Member identities, and omitted ones default to "no
    talents, can participate". A Member who cannot
    participate must stop every current Ministry and join none; the browser
    enforces the same rule, and the database guard checks it again.
    """
    if inputs is None:
        if type(payload) is not dict or payload:
            raise InvalidServiceAnswers({"service": "This section is not enabled."})
        return {}
    # An empty section, or an omitted Member, means "no talents and can
    # participate"; the result always lists every Ministry identity.
    if payload == {}:
        payload = {"members": {}, "proposed_members": {}}
    if type(payload) is not dict or set(payload) != {"members", "proposed_members"}:
        raise InvalidServiceAnswers({"service": "Review the talents section."})
    current = {str(member): list(values) for member, values in inputs.memberships}
    errors, result = {}, {"members": {}, "proposed_members": {}}
    for group in ("members", "proposed_members"):
        entries = payload[group]
        if type(entries) is not dict or not set(entries) <= set(ministries[group]):
            errors[f"service.{group}"] = "Review the current household choices."
            continue
        for identifier in sorted(ministries[group]):
            entry = entries.get(identifier, {"cannot_serve": False, "talents": {}})
            path = f"service.{group}.{identifier}"
            if (
                type(entry) is not dict
                or set(entry) != {"cannot_serve", "talents"}
                or type(entry["cannot_serve"]) is not bool
            ):
                errors[path] = "Review this person's talents."
                continue
            talents = _talents(entry["talents"], options)
            if talents is None:
                errors[f"{path}.talents"] = "Choose only the offered talents."
                continue
            choices = ministries[group][identifier]
            if entry["cannot_serve"] and (
                choices["join"]
                or (group == "members" and choices["leave"] != current[identifier])
            ):
                errors[path] = (
                    "A person who cannot participate stops every ministry "
                    "and joins none."
                )
                continue
            result[group][identifier] = {
                "cannot_serve": entry["cannot_serve"],
                "talents": talents,
            }
    if errors:
        raise InvalidServiceAnswers(errors)
    return result
