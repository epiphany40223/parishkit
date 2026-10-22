"""The shared toggletip template component's accessible, escaped markup."""

from django.template.loader import render_to_string


def render(**context):
    """Render the component with the given include variables."""
    return render_to_string("stewardship/components/toggletip.html", context)


def test_toggletip_renders_a_closed_disclosure_button_and_bubble():
    """The button controls a hidden bubble and is described by its label."""
    html = render(tip_id="sender-tip", tip_label_id="sender-label", tip_text="Shown.")
    assert 'type="button" class="toggletip-button"' in html
    assert 'aria-expanded="false" aria-controls="sender-tip"' in html
    # A generic name, so the button never matches its field's label text.
    assert 'aria-label="More information"' in html
    assert 'aria-describedby="sender-label"' in html
    assert '<span class="toggletip-bubble" id="sender-tip" hidden>Shown.</span>' in html
    # The usage comment never reaches the page.
    assert "label-row" not in html


def test_toggletip_escapes_its_text_and_omits_a_missing_label():
    """Help text is data, never markup; no label id means no description."""
    html = render(tip_id="t", tip_text="<script>x</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "aria-describedby" not in html
