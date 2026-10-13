"""One phrase and one badge for Members and Families no longer in ParishSoft.

ui-conventions "Members and Families no longer in ParishSoft" (#1216): the
shared words in ``source/source_status.py``, and the ``{% source_status %}``
template tag.
"""

import pytest
from django.template import Context, Template

from parishkit.stewardship.source.source_status import (
    DECEASED_IN_PARISHSOFT,
    INACTIVE_IN_PARISHSOFT,
    NOT_IN_PARISHSOFT,
    source_status_label,
)


@pytest.mark.parametrize(
    ("status", "label"),
    [
        (None, ""),
        ("current", ""),
        ("missing", NOT_IN_PARISHSOFT),
        ("moved", NOT_IN_PARISHSOFT),
        ("inactive", INACTIVE_IN_PARISHSOFT),
        ("deceased", DECEASED_IN_PARISHSOFT),
    ],
)
def test_each_status_has_one_phrase(status, label):
    """Missing and moved read alike; current needs no mark."""
    assert source_status_label(status) == label


def test_an_unknown_status_fails_loudly():
    """A new status cannot reach a page without its words."""
    with pytest.raises(KeyError):
        source_status_label("archived")


def _render(status):
    """The tag's output for one status."""
    template = Template("{% load stewardship %}[{% source_status status %}]")
    return template.render(Context({"status": status}))


def test_the_tag_renders_the_shared_badge():
    """The badge's text is the whole phrase; a current one renders nothing."""
    assert _render("deceased") == (
        '[<span class="status-badge" data-source-status="deceased">'
        "Deceased in ParishSoft</span>]"
    )
    assert _render("moved") == (
        '[<span class="status-badge" data-source-status="moved">'
        "Not in ParishSoft</span>]"
    )
    assert _render(None) == "[]"
    assert _render("current") == "[]"
