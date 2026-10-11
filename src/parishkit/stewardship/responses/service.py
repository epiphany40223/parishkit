"""Per-Member "cannot participate in ministries" answer, and retired talents.

The answer is recorded alongside the Ministry page, in a per-Member entry
that also holds a ``talents`` map. The Family form no longer asks for
talents (#970) and sends only ``cannot_serve``, so each new entry is stored
with an empty map, the only map the database guard accepts since migration
0056. A form opened before then may still send talents; they are dropped,
not stored. Older submissions keep the talents they recorded. The talent
list below stays only because the form definition digest still hashes it.
"""

from .financial import ShareOption

# The retired talent text limit and default list (#970) stay only as inputs
# to the form definition digest (see configured_talent_options), so neither
# may change: a different digest would refuse forms issued earlier.
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
    """Static messages keyed by trusted paths; never echo submitted text."""

    def __init__(self, fields):
        """Keep private free text out of exception strings."""
        self.fields = dict(fields)
        super().__init__("Please review the ministry choices.")


def configured_talent_options(configuration):
    """The talent list a campaign's configuration names; never offered.

    No campaign offers talents now (#970), but ``inputs.definition_digest``
    still hashes this list exactly as before: a Family's form issued earlier
    keeps its stored definition digest, and a different digest for the same
    configuration would refuse that Family's submission as unverifiable.
    Only Ministry-enabled campaigns name talents; a never-edited list
    resolves to ``DEFAULT_TALENTS``. Never use this to offer talents.
    """
    if "ministry" not in configuration["modules"]:
        return ()
    values = configuration.get("talent_options")
    if values is None:
        return DEFAULT_TALENTS
    return tuple(ShareOption(**value) for value in values)


def validate_service_answers(payload, ministries, inputs):
    """Validate the Ministry lock against the Ministry answers.

    ``ministries`` is the already-normalized Ministry section; this section
    may only name its Member identities, and omitted ones default to "can
    participate". A Member who cannot participate must stop every current
    Ministry and join none; the browser enforces the same rule, and the
    database guard checks it again. The current form sends no ``talents``
    key. A form opened before #970 may send a talents map: it is accepted
    so that Family's response is not lost, and dropped, because no one reads
    talents any more. Every stored entry gets an empty map, the shape the
    database guard requires.
    """
    if inputs is None:
        if type(payload) is not dict or payload:
            raise InvalidServiceAnswers({"service": "This section is not enabled."})
        return {}
    # An empty section, or an omitted Member, means "can participate"; the
    # result always lists every Ministry identity.
    if payload == {}:
        payload = {"members": {}, "proposed_members": {}}
    if type(payload) is not dict or set(payload) != {"members", "proposed_members"}:
        raise InvalidServiceAnswers({"service": "Review the ministry answers."})
    current = {str(member): list(values) for member, values in inputs.memberships}
    errors, result = {}, {"members": {}, "proposed_members": {}}
    for group in ("members", "proposed_members"):
        entries = payload[group]
        if type(entries) is not dict or not set(entries) <= set(ministries[group]):
            errors[f"service.{group}"] = "Review the current household choices."
            continue
        for identifier in sorted(ministries[group]):
            entry = entries.get(identifier, {"cannot_serve": False})
            path = f"service.{group}.{identifier}"
            if (
                type(entry) is not dict
                or set(entry) - {"talents"} != {"cannot_serve"}
                or type(entry["cannot_serve"]) is not bool
                or type(entry.get("talents", {})) is not dict
            ):
                errors[path] = "Review this person's ministry answer."
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
                "talents": {},
            }
    if errors:
        raise InvalidServiceAnswers(errors)
    return result
