"""How the portal names the parish's ParishSoft organization (#1554).

UX conventions "ParishSoft organization names": wherever a page, notice,
help text, problem, CLI message or log message meant for people names the
organization, it is "Name (ID)", or "ParishSoft organization ID" while no
name is recorded. Pages never build the string themselves; they call
``organization_label`` (with a name they already hold) or
``current_organization_label`` (the current snapshot's recorded name).
"""

from django.utils.translation import gettext as _


def organization_label(organization_id, name=None):
    """``organization_id`` named as the portal names it: "Name (ID)".

    Without a ``name`` (an install set up before #1554 until its next full
    load, or an organization ParishSoft names nothing) it is "ParishSoft
    organization ID". The ID is written without digit grouping, as
    ParishSoft support and the configuration write it.
    """
    if name:
        return _("%(name)s (%(id)s)") % {"name": name, "id": organization_id}
    return _("ParishSoft organization %(id)s") % {"id": organization_id}


def current_organization_name(organization_id):
    """The name the current snapshot's load recorded for the organization.

    None when there is no current snapshot, it is another organization's,
    or its load recorded no name (data spec "ParishSoft organization name").
    One query.
    """
    from .loading import snapshot_organization

    found = snapshot_organization()
    # The configuration may hold the ID as text ("1234"), the snapshot as
    # an integer.
    if found is None or str(found[0]) != str(organization_id):
        return None
    return found[1]


def current_organization_label(organization_id):
    """``organization_label`` with the current snapshot's recorded name."""
    return organization_label(
        organization_id, current_organization_name(organization_id)
    )
