"""The one set of words for Members and Families no longer in ParishSoft.

Every Admin page, table and download that shows a Member or Family the
current ParishSoft data no longer has, or marks inactive or deceased, uses
these phrases (ui-conventions "Members and Families no longer in
ParishSoft", #1216). Pages render them with the ``{% source_status %}``
template tag, so the words and the look are defined once.

The status keys match the SQL ``stewardship_member_source_status_v1``
(#1217): ``current``, ``missing`` (not in the data), ``moved`` (a Member now
in another Family), ``inactive`` and ``deceased``. A Family that is still in
the data but no longer one of the campaign's Families is ``left`` (#1393):
it has left the campaign, which its badge says, rather than being called
inactive (ui-conventions "Campaign Families wording"). A Member or Family that
is present but has no usable name is a separate case, worded by
``NAME_NOT_RECORDED`` rather than called a removal.
"""


def gettext_noop(message):
    """Mark ``message`` for translation without translating it (identity).

    Django's own ``gettext_noop`` reads settings, which fails when this module
    is imported before Django is configured (the installer's provisioning
    imports it through ``source.loading``). The name keeps the message
    extraction marker working; the pages translate the phrases when they
    render them.
    """
    return message


NOT_IN_PARISHSOFT = gettext_noop("Not in ParishSoft")
INACTIVE_IN_PARISHSOFT = gettext_noop("Inactive in ParishSoft")
DECEASED_IN_PARISHSOFT = gettext_noop("Deceased in ParishSoft")
NAME_NOT_RECORDED = gettext_noop("Name not recorded in ParishSoft")
NOT_IN_CAMPAIGN = gettext_noop("No longer in this campaign")
# A download's own column for these words, beside the name it describes:
# blank when current, so the name cell holds only a name (#1216).
STATUS_HEADING = gettext_noop("ParishSoft status")
# The same column for the Member, in a download that also has the Family's
# (Census changes, #1752): two columns both headed "ParishSoft status" would
# not say which name each describes.
MEMBER_STATUS_HEADING = gettext_noop("Member's ParishSoft status")

# A Member who moved to another Family is no longer in this Family's
# ParishSoft record, so it reads as not in ParishSoft, as a deleted one does.
LABELS = {
    "missing": NOT_IN_PARISHSOFT,
    "moved": NOT_IN_PARISHSOFT,
    "inactive": INACTIVE_IN_PARISHSOFT,
    "deceased": DECEASED_IN_PARISHSOFT,
    "left": NOT_IN_CAMPAIGN,
}


def source_status_label(status):
    """The phrase for a ParishSoft status, or "" when there is nothing to mark.

    ``None`` and ``current`` read as "" (no mark). Any other unknown key
    raises ``KeyError``, so a new status cannot reach a page unworded.
    """
    if status in (None, "current"):
        return ""
    return LABELS[status]


def family_status(name, *, active=True):
    """A Family row's status key from what its read returned.

    ``name`` None means the current data no longer has the Family
    (``missing``); otherwise it is ``left`` when ``active`` is false (no
    longer in this campaign, #1393), and ``current`` when there is nothing
    to mark.
    """
    if name is None:
        return "missing"
    return "current" if active else "left"
