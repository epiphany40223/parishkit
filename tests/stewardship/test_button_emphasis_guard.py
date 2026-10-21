"""At most one primary (green) button per control group (#1713).

ui-conventions "Button emphasis" is the rule: a form, a filter bar, a
dialog's action row, a bulk bar or an export pane has at most one primary
button, its main forward action; every other button in it is secondary,
destructive ones red. Like test_button_guard.py this is a plain text scan of
every portal template, so it covers branches no fixture reaches.

What counts as a group, as the scan sees it:

- a ``<form>`` or a ``<dialog>``;
- an element with one of GROUP_CLASSES, or marked ``data-control-group``;
- a row of sibling forms with only template tags between them, which a
  reader sees as one row of buttons (Retry beside Cancel, each in a form of
  its own).

A button counts toward every group around it. A button with a ``form``
attribute belongs to that form, wherever it sits: it joins the form's group
when the form has a literal id and comes first, else it is a group of its
own. Each ``{% if %}`` branch is read on its own, so a button and its
unavailable twin in the other branch count once; a button a loop repeats
inside one group counts twice. Separate ``{% if %}`` blocks are read as
able to show together, so write buttons that never show together as
branches of one ``{% if %}``/``{% elif %}``. ``{% include %}`` is not
followed: the browser test (browser/test_button_emphasis.py) counts the
rendered groups.

Rows built across branches or by a loop (two forms adjacent only once
rendered, with other markup between them in the source) are not joined;
only the browser sweep sees them, on the pages it opens.

EMPHASIS_ALLOWED names reviewed exceptions, template -> how many crowded
groups it may keep, each with its reason in a comment. Lower or remove an
entry as soon as its template follows the rule; never raise one, and never
add one without the reason.
"""

import re

import pytest

from .test_button_guard import ALL_TEMPLATES, TEMPLATES

EMPHASIS_ALLOWED = {
    # The session dialog holds two panels, the warning and the signed-out
    # notice, each with one action row and one primary button; session-v1.js
    # shows one panel at a time, which a text scan cannot see.
    "admin-banners.html": 2,
}

# Containers that hold one group's controls, beside forms and dialogs.
GROUP_CLASSES = {
    "filter-bar",
    "bulk-bar",
    "export-panel",
    "dialog-actions",
    "session-dialog-actions",
    "setup-actions",
    "campaign-step-actions",
    "health-actions",
    "actions",
}
# Elements whose open and close tags the scan follows. A group is one of
# them; the others are tracked so a group's close tag is matched right.
ELEMENTS = "form|dialog|div|section|p|fieldset|li|td|span|ul|details|nav"

ANY_COMMENT = re.compile(r"{% comment %}.*?{% endcomment %}|{#.*?#}|<!--.*?-->", re.S)
TOKEN = re.compile(
    r"(?P<button>{% button\b(?P<attrs>.*?)%})"
    rf"|<(?P<open>{ELEMENTS})\b(?P<open_attrs>[^>]*)>"
    rf"|</(?P<close>{ELEMENTS})>"
    r"|(?P<branch>{% (?:if|for)\b.*?%})"
    r"|(?P<alternative>{% (?:elif\b.*?|else|empty) %})"
    r"|(?P<end>{% end(?:if|for) %})",
    re.S,
)
TEMPLATE_TAG = re.compile(r"{%.*?%}", re.S)


def is_primary(attrs):
    """Whether a {% button %} tag's arguments draw it primary (green)."""
    return not (
        re.search(r"""variant=["'](?:secondary|link)["']""", attrs)
        or re.search(r"\b(?:button-danger|icon-button)\b", attrs)
    )


def is_group(element, attrs):
    """Whether an opening tag starts a control group."""
    if element in ("form", "dialog") or "data-control-group" in attrs:
        return True
    classes = re.search(r"""\bclass=["']([^"']*)["']""", attrs)
    return bool(classes and GROUP_CLASSES & set(classes[1].split()))


def crowded_groups(source):
    """The groups in a template source that can show more than one primary
    button at once: {group: (line, most primary buttons shown together)}.

    One pass over the tokens. ``open`` is the stack of followed elements,
    each with the group it starts (or None). ``frames`` is the stack of
    {% if %}/{% for %} blocks: each holds the per-group counts of the
    alternatives already read and of the one being read; when a block ends,
    each group takes the most of any one alternative.
    """
    # Comments keep their line breaks, so reported lines match the file.
    source = ANY_COMMENT.sub(lambda found: "\n" * found[0].count("\n"), source)
    open_elements = []
    frames = [{"done": [], "counts": {}}]
    loops = 0
    opened = {}  # group -> (line, loop depth where it opened)
    last_form = None  # (end offset, stack depth, group) of the last form
    form_ids = {}  # a form's literal id -> its group

    def line(offset):
        return source.count("\n", 0, offset) + 1

    def count(group, offset):
        opened.setdefault(group, (line(offset), loops))
        counts = frames[-1]["counts"]
        # A loop that repeats the button inside one group shows it twice.
        times = 2 if loops > opened[group][1] else 1
        counts[group] = counts.get(group, 0) + times

    for token in TOKEN.finditer(source):
        if token["button"]:
            attrs = token["attrs"]
            if not is_primary(attrs):
                continue
            owner = re.search(r"""\bform=(["']?)([^\s"'%]+)\1""", attrs)
            # A form= button joins its form's group when the form, with a
            # literal id, came first; otherwise it is a group of its own.
            groups = (
                [form_ids.get(owner[2], f"form={owner[2]}")]
                if owner
                else [group for _element, group in open_elements if group]
            )
            for group in groups:
                count(group, token.start())
        elif token["open"]:
            element, group = token["open"], None
            if element == "form":
                # A form right after a sibling form, with only template tags
                # between, joins that form's row.
                if (
                    last_form
                    and last_form[1] == len(open_elements)
                    and not TEMPLATE_TAG.sub(
                        "", source[last_form[0] : token.start()]
                    ).strip()
                ):
                    group = last_form[2]
                else:
                    group = f"form@{token.start()}"
                literal_id = re.search(r'\bid="([^"{}]+)"', token["open_attrs"])
                if literal_id:
                    form_ids[literal_id[1]] = group
            elif is_group(element, token["open_attrs"]):
                group = f"{element}@{token.start()}"
            if group:
                opened.setdefault(group, (line(token.start()), loops))
            open_elements.append((element, group))
        elif token["close"]:
            for index in range(len(open_elements) - 1, -1, -1):
                if open_elements[index][0] == token["close"]:
                    if token["close"] == "form":
                        last_form = (token.end(), index, open_elements[index][1])
                    del open_elements[index:]
                    break
        elif token["branch"]:
            loop = token["branch"].startswith("{% for")
            loops += loop
            frames.append({"done": [], "counts": {}, "loop": loop})
        elif token["alternative"]:
            frames[-1]["done"].append(frames[-1]["counts"])
            frames[-1]["counts"] = {}
        elif token["end"] and len(frames) > 1:
            frame = frames.pop()
            loops -= frame["loop"]
            alternatives = [*frame["done"], frame["counts"]]
            for group in set().union(*alternatives):
                most = max(choice.get(group, 0) for choice in alternatives)
                counts = frames[-1]["counts"]
                counts[group] = counts.get(group, 0) + most
    # An open tag that differs per branch would leave a stale group on the
    # stack and miscount every later button; fail loudly instead.
    if open_elements:
        raise ValueError(f"unclosed elements: {[e for e, _g in open_elements]}")
    return {
        group: (opened[group][0], shown)
        for group, shown in frames[0]["counts"].items()
        if shown > 1
    }


def crowded(name):
    """crowded_groups() of one portal template."""
    return crowded_groups((TEMPLATES / name).read_text())


def test_the_exceptions_name_real_templates():
    """A renamed template must not leave a stale exception behind."""
    assert set(EMPHASIS_ALLOWED) <= set(ALL_TEMPLATES)


@pytest.mark.parametrize("name", ALL_TEMPLATES)
def test_one_primary_button_per_group(name):
    """No group shows two primary buttons at once (ui-conventions, Button
    emphasis): make the other actions variant="secondary"."""
    if name in EMPHASIS_ALLOWED:
        return
    assert crowded(name) == {}, (
        f"{name}: at most one primary button per group; draw the others "
        'with variant="secondary" (ui-conventions spec, Button emphasis)'
    )


@pytest.mark.parametrize("name", sorted(EMPHASIS_ALLOWED))
def test_emphasis_exceptions_are_still_needed(name):
    """An exception keeps exactly its reviewed count of crowded groups:
    fewer means lower the entry, more is a new violation in that template."""
    assert len(crowded(name)) == EMPHASIS_ALLOWED[name]


@pytest.mark.parametrize(
    "source,crowded_count",
    [
        # Two primary buttons in one form; then the second made secondary.
        (
            '<form>{% button type="submit" %}A{% endbutton %}'
            '{% button type="submit" %}B{% endbutton %}</form>',
            2,
        ),
        (
            '<form>{% button type="submit" %}A{% endbutton %}'
            '{% button type="submit" variant="secondary" %}B{% endbutton %}</form>',
            0,
        ),
        # Separate forms, apart on the page, are separate groups.
        (
            '<form>{% button type="submit" %}A{% endbutton %}</form><p>x</p>'
            '<form>{% button type="submit" %}B{% endbutton %}</form>',
            0,
        ),
        # Sibling forms with only template tags between them are one row.
        (
            '{% if a %}<form>{% button type="submit" %}A{% endbutton %}</form>'
            '{% endif %}\n{% if b %}<form>{% button type="submit" %}B'
            "{% endbutton %}</form>{% endif %}",
            2,
        ),
        # A button and its unavailable twin are alternatives.
        (
            '<form>{% if ok %}{% button type="submit" %}A{% endbutton %}'
            '{% else %}{% button type="button" disabled %}A{% endbutton %}'
            "{% endif %}</form>",
            0,
        ),
        # A loop of primary buttons inside one bar; a form per row is fine.
        (
            '<div class="bulk-bar">{% for x in xs %}{% button type="button" %}'
            "{{ x }}{% endbutton %}{% endfor %}</div>",
            2,
        ),
        (
            '{% for x in xs %}<form>{% button type="submit" %}{{ x }}'
            "{% endbutton %}</form><p>{{ x }}</p>{% endfor %}",
            0,
        ),
        # Danger and icon buttons are not primary; a marked group counts.
        (
            '<div data-control-group>{% button type="submit" %}A{% endbutton %}'
            '{% button type="submit" class="button-danger" %}B{% endbutton %}'
            "</div>",
            0,
        ),
        (
            '<div data-control-group><a>x</a>{% button href="/a" %}A{% endbutton %}'
            '{% button href="/b" %}B{% endbutton %}</div>',
            2,
        ),
        # A form= button counts with its form.
        (
            '<form id="f">{% button type="submit" %}A{% endbutton %}</form>'
            '<p>{% button type="submit" form="f" %}B{% endbutton %}</p>',
            2,
        ),
        # Buttons outside any group are not counted.
        (
            '<p>{% button href="/a" %}A{% endbutton %}</p>'
            '<p>{% button href="/b" %}B{% endbutton %}</p>',
            0,
        ),
    ],
)
def test_the_scan_finds_what_it_describes(source, crowded_count):
    """The group, branch and loop rules behave as the docstring says."""
    found = crowded_groups(source)
    assert max((shown for _line, shown in found.values()), default=0) == crowded_count


def test_a_branch_dependent_open_tag_fails_loudly():
    """An element left open (an open tag that differs per branch, closed
    once after them) would skew every later count, so the scan refuses it."""
    with pytest.raises(ValueError):
        crowded_groups("{% if a %}<form x>{% else %}<form y>{% endif %}</form>")
