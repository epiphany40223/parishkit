"""The Families counted populations' words, untranslated (#2324).

They are spelled only here; ``reports.participation.SCOPE_LABELS``
translates them for every page, caption and export. This module imports
nothing, so pk-admin can build its ``--help`` text from the same words
before Django's settings are configured.
"""


def gettext_noop(message):
    """Mark ``message`` for translation and return it as is.

    Django's own ``gettext_noop`` reads its settings, which pk-admin's
    parser runs before; this one has the same name, so message extraction
    still finds the words.
    """
    return message


SCOPE_WORDS = {
    "historical": gettext_noop("Including Families that left"),
    "current": gettext_noop("In this campaign today"),
}
