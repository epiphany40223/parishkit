"""The ``aboutpage`` template tag renders one safe, remembered-panel wrapper."""

import pytest
from django.template import Context, Template, TemplateSyntaxError


def render(source, **context):
    """Render a template that loads the stewardship tag library."""
    return Template("{% load stewardship %}" + source).render(Context(context))


def test_panel_wraps_content_open_with_its_key():
    """The panel starts open, carries its key and keeps escaping intact."""
    html = render(
        '{% aboutpage "setup-"|add:step %}<p>{{ note }}</p>{% endaboutpage %}',
        step="mail",
        note="<b>x</b>",
    )
    assert html.startswith(
        '<details class="about-page" data-about-page="setup-mail" open>'
        "<summary>About this page</summary>"
    )
    assert "<p>&lt;b&gt;x&lt;/b&gt;</p>" in html


@pytest.mark.parametrize("key", ["", "Setup", 'a"b', "a--b", "-a"])
def test_panel_refuses_keys_that_are_not_slugs(key):
    """Keys end up in markup and browser storage names, so only slugs are allowed."""
    with pytest.raises(TemplateSyntaxError):
        render("{% aboutpage key %}x{% endaboutpage %}", key=key)
