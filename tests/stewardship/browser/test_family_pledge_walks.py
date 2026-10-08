"""Generated walks over the Family form's pledge merge (#778 review).

Each walk is a short sequence of refreshes. At every step this tab may check
or uncheck "cannot contribute", change the pledge (or make it zero), change
the frequency or switch its way to give; another device may do the same to
the recorded response, which makes the next Submit come back
``review_required`` with the refreshed form. Some walks start from a record
whose way to give the campaign no longer offers. Choices are answered by one
policy per walk: always this tab's edit ("mine"), always the updated
records ("updated"), or a seeded draw for each choice ("mixed").

The model follows the form's three-way merge field by field (each way to
give is its own field, as the form merges them by method). It keeps the
Family's standing decisions: an edit in this tab, or a choice answered with
"my edit". An edit back to the record the tab was shown, a refresh showing
the record already agreeing, or a choice answered with the update ends a
decision, so the field follows the record again. After each walk the
accepted answers must be exactly:

- each decided field as decided, and no decided field with a later change by
  the other device that no choice was asked for (nothing stale is sent and
  nothing the Family entered is overwritten without a choice);
- each undecided field as the latest record (or as Review made the walk fill
  it in);
- no pledge only when "cannot contribute" is decided, or is the record's
  with no pledge decision standing; likewise no frequency or way to give
  only for a zero pledge decided, or the record's with no such decision.

A choice must offer, as "my edit", the standing decision, and as the update,
the record the tab was shown. Review may make the walk fill in a required
field only when the Family has not decided a value for it; the fill is no
decision. Whenever Next or Review stops, the focused control must be visible,
and every walk must finish within a bounded number of attempts.

The walks come from a fixed seed, so a failure names a reproducible walk.
``PARISHKIT_PLEDGE_WALKS`` sets how many (default 60; the reviewer's
sequences always run). Chromium only: the merge is engine-independent
script, and the targeted tests cover the other engines.
"""

import os
import random
from decimal import Decimal, InvalidOperation

import pytest

from ..test_financial_answers import CHECK, OTHER
from .test_family_financial import financial_form
from .test_family_response import expect, prepare, show, visit_every_page

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)

GIVE = (
    "Because of financial limitations, I/we cannot contribute financially at this time."
)
FREQUENCIES = ["weekly", "monthly", "quarterly", "annual"]
TAB_ACTIONS = ["none", "toggle", "pledge", "zero", "frequency", "method"]
OTHER_ACTIONS = ["toggle", "pledge", "zero", "frequency", "method"]
REFRESHED = "Parish records or a previous Family response changed"
# A way to give the campaign no longer offers.
UNOFFERED = "33333333-3333-4333-8333-333333333333"
METHODS = (CHECK, OTHER, UNOFFERED)
DETAILS = ("cannot", "pledge", "frequency", *METHODS)
PLEDGE_DETAILS = ("frequency", *METHODS)
ATTEMPTS = 12


def money(value):
    """A pledge as a number, or the text when it is not one."""
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        return value


def same(field, left, right):
    """Whether two values of ``field`` mean the same answer."""
    return money(left) == money(right) if field == "pledge" else left == right


def positive(pledge):
    """Whether a pledge asks for a frequency and a way to give."""
    amount = money(pledge)
    return isinstance(amount, Decimal) and amount > 0


def record_form(record):
    """The Family form for one recorded response."""
    form = financial_form()
    form["financial"]["answers"] = {
        "annual_pledge": record["pledge"],
        "frequency": record["frequency"],
        "shares": {
            method: "Their note" if method == OTHER else ""
            for method in METHODS
            if record[method]
        },
        "cannot_give": record["cannot"],
    }
    if record[UNOFFERED]:
        option = dict(form["financial"]["options"][0])
        option["id"] = UNOFFERED
        form["financial"]["unavailable_options"] = [option]
    return form


def no_pledge():
    """The record of a Family that cannot contribute (or never pledged)."""
    return {"pledge": "", "frequency": "", CHECK: False, OTHER: False, UNOFFERED: False}


class Walk:
    """One walk: the record, the Family's decisions and what was asked."""

    def __init__(self, start, steps, policy, seed=0):
        self.record = dict(start)
        self.base = dict(start)  # the record this tab was last shown
        self.view = dict(start)  # what this tab showed before the last refresh
        self.steps = steps
        self.policy = policy
        # "mixed" walks answer each choice by a seeded draw (one per choice
        # and refresh), so "my edit" and "updated" meet within one walk.
        self.draws = random.Random(seed)
        self.drawn = {}
        self.refreshes_seen = 0
        self.clock = 0
        self.changed = dict.fromkeys(DETAILS, -1)  # the other device's changes
        self.decided = {}  # field: (value, time)
        self.filled = {}  # fields Review made the walk fill in: (value, time)
        self.submissions = []
        self.staged = []  # the other device's changes, in Submit order
        # Whether "cannot contribute" is checked because this tab checked it
        # (and the record later agreed): its pledge then waits aside.
        self.own_cannot = False
        self.pledge_answered = -1  # when a pledge choice was last answered

    def tick(self):
        """Advance the walk's clock and return the new time."""
        self.clock += 1
        return self.clock

    def shown(self, field):
        """What this tab shows for ``field``."""
        if field in self.decided:
            return self.decided[field][0]
        if field in self.filled:
            return self.filled[field][0]
        return self.base[field]

    def decide(self, field, value):
        """A decision, unless it only restates the record the tab was shown."""
        if field == "cannot":
            self.own_cannot = value
        self.filled.pop(field, None)
        if same(field, value, self.base[field]):
            self.decided.pop(field, None)
        else:
            self.decided[field] = (value, self.tick())

    def follow(self, *fields):
        """The Family let these fields follow the record (no decisions)."""
        for field in fields:
            self.decided.pop(field, None)
            self.filled.pop(field, None)

    def other(self, action, index):
        """Another device changes the record (staged until the next Submit)."""
        record = dict(self.staged[-1] if self.staged else self.record)
        if action == "toggle":
            if record["cannot"]:
                record |= {"cannot": False, "pledge": f"{60 + index}.00"}
                record |= {"frequency": "monthly", CHECK: True}
            else:
                record |= {"cannot": True} | no_pledge()
        elif action == "zero":
            record |= {"cannot": False} | no_pledge() | {"pledge": "0"}
        else:
            if record["cannot"] or not positive(record["pledge"]):
                record |= {"cannot": False, "pledge": "65.00", UNOFFERED: False}
                record["frequency"] = record["frequency"] or "monthly"
                if not (record[CHECK] or record[OTHER]):
                    record[CHECK] = True
            if action == "pledge":
                record["pledge"] = f"{70 + index}.00"
            elif action == "frequency":
                current = record["frequency"] or "annual"
                record["frequency"] = FREQUENCIES[(FREQUENCIES.index(current) + 2) % 4]
            else:
                record[CHECK], record[OTHER] = record[OTHER], record[CHECK]
                if not (record[CHECK] or record[OTHER]):
                    record[OTHER] = True
        self.staged.append(record)

    def submit(self, route):
        """The submit endpoint: the other device's change if one is staged."""
        self.submissions.append(route.request.post_data_json["answers"])
        if not self.staged:
            route.fulfill(json={"accepted": True})
            return
        record = self.staged.pop(0)
        now = self.tick()
        for field in DETAILS:
            if record[field] != self.record[field]:
                self.changed[field] = now
        self.record = record
        form = record_form(record)
        route.fulfill(status=409, json={"error": "review_required", "form": form})

    def merged(self):
        """This tab was shown the refreshed record."""
        self.view = {field: self.shown(field) for field in DETAILS}
        self.base = dict(self.record)
        self.refreshes_seen += 1
        self.tick()
        for field, (value, _at) in list(self.decided.items()):
            if same(field, self.record[field], value):
                del self.decided[field]
        for field, (value, _at) in list(self.filled.items()):
            if same(field, self.record[field], value):
                del self.filled[field]
        if (
            self.own_cannot
            and "cannot" not in self.decided
            and not self.record["cannot"]
        ):
            # The record agreed with this tab's "cannot contribute", then
            # another device unchecked it: the pledge this tab set aside goes
            # with the box it was set aside for.
            self.follow("pledge", *PLEDGE_DETAILS)
            self.own_cannot = False

    def answered(self, field, mine, offered):
        """The Family answered a choice for ``field``."""
        if mine:
            standing = self.decided.get(field) or self.filled.get(field)
            if standing:
                assert same(field, offered, standing[0]), (
                    f"the {field} choice offered {offered!r} as this tab's edit, "
                    f"not {standing[0]!r}"
                )
            self.decided[field] = (offered, self.tick())
            self.filled.pop(field, None)
            return
        assert same(field, offered, self.base[field]), (
            f"the {field} choice offered {offered!r} as the update, "
            f"not the record's {self.base[field]!r}"
        )
        self.follow(field)

    def fill(self, field, value):
        """Review asked for an emptied field: never one the Family decided."""
        if field in self.decided and self.decided[field][0] not in ("", False):
            raise AssertionError(
                f"this tab's {field} {self.decided[field][0]!r} was emptied "
                "without a choice"
            )
        self.decided.pop(field, None)
        if same(field, value, self.base[field]):
            self.filled.pop(field, None)  # to the merge, no edit at all
        else:
            self.filled[field] = (value, self.tick())

    def expected(self, field):
        """The value ``field`` must be sent with.

        A Review fill is not the Family's decision, but to the form's merge it
        is this tab's edit: another device changing that field later must be
        asked about too, never sent over or replaced silently.
        """
        for store, whose in (
            (self.decided, "this tab's"),
            (self.filled, "the filled-in"),
        ):
            if field in store:
                value, at = store[field]
                assert self.changed[field] < at or same(
                    field, self.record[field], value
                ), (
                    f"another device changed {field} to {self.record[field]!r} after "
                    f"{whose} {value!r}, and no choice was asked"
                )
                return value
        return self.record[field]

    def wants_mine(self, group):
        """Whether to answer this choice with "my edit"."""
        if self.policy != "mixed":
            return self.policy == "mine"
        key = (group, self.refreshes_seen)
        if key not in self.drawn:
            self.drawn[key] = self.draws.random() < 0.5
        return self.drawn[key]

    def lost(self, fields):
        """Decided fields another device's answer dropped without a choice."""
        found = [field for field in fields if self.decided.get(field, ("", 0))[0]]
        assert not found, f"another device's answer dropped this tab's {found}"

    def check(self):
        """The accepted answers are exactly what the decisions and record say."""
        answers = self.submissions[-1]["financial"]
        sent = {
            "cannot": bool(answers.get("cannot_give")),
            "pledge": answers["annual_pledge"],
            "frequency": answers["frequency"],
        } | {method: method in answers["shares"] for method in METHODS}
        cannot = self.expected("cannot")
        assert sent["cannot"] == cannot, f"sent cannot {sent['cannot']}, not {cannot}"
        if cannot:
            if "cannot" not in self.decided and not self.own_cannot:
                self.lost(("pledge", *PLEDGE_DETAILS))
            assert {field: sent[field] for field in DETAILS[1:]} == no_pledge(), sent
            return
        pledge = self.expected("pledge")
        assert same("pledge", sent["pledge"], pledge), (
            f"sent pledge {sent['pledge']!r}, not {pledge!r}"
        )
        if not positive(pledge):
            # Another device's zero may hide this tab's frequency and ways to
            # give only through a choice answered since that zero.
            if (
                "pledge" not in self.decided
                and self.pledge_answered < self.changed["pledge"]
            ):
                self.lost(PLEDGE_DETAILS)
            assert sent["frequency"] == "" and not any(sent[m] for m in METHODS), sent
            return
        for field in PLEDGE_DETAILS:
            value = self.expected(field)
            assert sent[field] == value, f"sent {field} {sent[field]!r}, not {value!r}"


def usable(locator):
    """Whether ``locator`` exists, is shown and can be used."""
    return (
        locator.count() > 0
        and locator.first.is_visible()
        and locator.first.is_enabled()
    )


def method_box(page, method):
    """The checkbox for one way to give."""
    return page.locator(f"#financial-option-{method}")


def tab_act(page, walk, action, index):
    """This tab makes one change (or several, in order) when it can."""
    if isinstance(action, tuple):
        for each in action:
            tab_act(page, walk, each, index)
        return
    if action == "none":
        return
    give = page.get_by_label(GIVE)
    show(page, give)
    if action == "toggle":
        if not give.is_enabled():
            return
        checked = give.is_checked()
        give.click()
        expect(page.get_by_label(GIVE)).to_be_checked(checked=not checked)
        walk.decide("cannot", not checked)
        return
    pledge = page.get_by_label("Annual pledge (USD)")
    if action in ("pledge", "zero"):
        if not usable(pledge):
            return
        value = f"{30 + index}.00" if action == "pledge" else "0"
        pledge.fill(value)
        walk.decide("pledge", value)
        if action == "zero":
            # The Family's own zero clears the frequency and ways to give.
            for field in PLEDGE_DETAILS:
                walk.decide(field, "" if field == "frequency" else False)
        return
    if action == "frequency":
        frequency = page.get_by_label("Pledge frequency (required)")
        if not usable(frequency):
            return
        current = frequency.input_value() or "annual"
        value = FREQUENCIES[(FREQUENCIES.index(current) + 1) % 4]
        frequency.select_option(value)
        walk.decide("frequency", value)
        return
    check, other = method_box(page, CHECK), method_box(page, OTHER)
    if not (usable(check) and usable(other)):
        return
    to_other = check.is_checked()
    if to_other:
        check.uncheck()
        if not other.is_checked():
            other.check()
            page.get_by_label("Details for I will share another way").fill("My note")
    else:
        if other.is_checked():
            other.uncheck()
        check.check()
    walk.decide(CHECK, not to_other)
    walk.decide(OTHER, to_other)


def focused_is_visible(page):
    """Whether the focused element is shown (not inside a hidden ancestor)."""
    return page.evaluate(
        """() => {
          const e = document.activeElement;
          if (!e || e === document.body) return true;
          const box = e.getBoundingClientRect();
          return box.width > 0 && box.height > 0 && !e.closest('[hidden]');
        }"""
    )


def resolve_choice(walk, path, mine, label):
    """Update the model for one choice the walk is about to answer."""
    text = label.strip().split(": ", 1)[1]
    if path == "financial.cannot_give":
        if mine:
            # "my pledge" brings back the pledge this tab showed; to the
            # merge, what equals the record shown is no edit.
            for field in ("pledge", *PLEDGE_DETAILS):
                if field in walk.decided:
                    walk.answered(field, True, walk.view[field])
                else:
                    walk.decide(field, walk.view[field])
            walk.answered("cannot", True, False)
            walk.own_cannot = False
        else:
            walk.answered("cannot", False, True)
            walk.follow("pledge", *PLEDGE_DETAILS)
            walk.own_cannot = False
        return
    field = {"financial.annual_pledge": "pledge", "financial.frequency": "frequency"}[
        path
    ]
    value = "" if text == "Blank" else text
    shown_before = walk.shown(field)
    walk.answered(field, mine, value)
    if field == "pledge":
        walk.pledge_answered = walk.tick()
        if positive(shown_before) and not positive(value):
            # Choosing a zero over a shown pledge clears the frequency and
            # ways to give, as typing the zero would. A zero chosen while the
            # pledge was already zero keeps them hidden, and a later pledge
            # brings them back.
            for name in PLEDGE_DETAILS:
                walk.decide(name, "" if name == "frequency" else False)


def resolve(page, walk):
    """Answer what stops Review: a choice by the walk's policy, or a field.

    Returns False when nothing on the page can be answered.
    """
    radios = page.locator('input[type="radio"][name^="resolve-financial."]')
    for index in range(radios.count()):
        radio = radios.nth(index)
        if not radio.is_visible():
            continue
        group = radio.get_attribute("name")
        if page.locator(f'input[name="{group}"]:checked').count():
            continue  # already answered; the choice stays shown until Review
        label = radio.evaluate("e => e.closest('label').textContent")
        mine = label.strip().startswith("Use my edit")
        if mine != walk.wants_mine(group):
            continue
        resolve_choice(walk, group[len("resolve-") :], mine, label)
        radio.click()
        return True
    # A way to give that changed on both sides is asked with two buttons.
    buttons = page.locator('[data-conflict^="financial.shares."] button')
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if not button.is_visible():
            continue
        text = button.text_content()
        mine = text.startswith("Keep my edit")
        path = button.evaluate("e => e.closest('[data-conflict]').dataset.conflict")
        if mine != walk.wants_mine(path):
            continue
        method = path[len("financial.shares.") :]
        walk.answered(method, mine, not text.endswith(": Not selected"))
        button.click()
        return True
    remove = page.locator(f"#financial-removed-{UNOFFERED}")
    if usable(remove) and not remove.is_checked():
        walk.fill(UNOFFERED, False)
        remove.click()
        return True
    pledge = page.get_by_label("Annual pledge (USD)")
    if usable(pledge) and not pledge.input_value():
        walk.fill("pledge", "45.00")
        pledge.fill("45.00")
        return True
    frequency = page.get_by_label("Pledge frequency (required)")
    if usable(frequency) and not frequency.input_value():
        walk.fill("frequency", "annual")
        frequency.select_option("annual")
        return True
    check = method_box(page, CHECK)
    if usable(check) and not page.locator('[id^="financial-option-"]:checked').count():
        walk.fill(CHECK, True)
        check.check()
        return True
    return False


def send(page, walk, *, refreshed):
    """Review and Submit, answering whatever stops it, then wait for the reply."""
    for _attempt in range(ATTEMPTS):
        if visit_every_page(page, required=False):
            page.get_by_role("button", name="Review response").click()
            submit = page.get_by_role("button", name="Submit to Sample Parish")
            if submit.count():
                count = len(walk.submissions)
                submit.click()
                if refreshed:
                    expect(page.get_by_text(REFRESHED, exact=False)).to_be_visible()
                    walk.merged()
                else:
                    expect(
                        page.get_by_role(
                            "heading", name="Thank you!", include_hidden=True
                        )
                    ).to_be_attached()
                assert len(walk.submissions) == count + 1
                return
        assert focused_is_visible(page), "Next or Review focused a hidden control"
        assert resolve(page, walk), "Review stopped on something it cannot answer"
    raise AssertionError("the walk did not finish (choices keep coming back)")


def run(browser_engine, origin, walk):
    """Play one walk in a fresh browser context."""
    context = browser_engine.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        prepare(page, origin, form=record_form(walk.record), submit=walk.submit)
        for index, (tab, other) in enumerate(walk.steps):
            tab_act(page, walk, tab, index)
            if other is None:
                send(page, walk, refreshed=False)
            else:
                walk.other(other, index)
                send(page, walk, refreshed=True)
        walk.check()
    finally:
        context.close()


UNCHECKED = {"cannot": False} | no_pledge() | {"pledge": "12.00", "frequency": "annual"}
UNCHECKED[CHECK] = True
CHECKED = {"cannot": True} | no_pledge()
UNOFFERED_START = UNCHECKED | {CHECK: False, UNOFFERED: True}
STARTS = [UNCHECKED, CHECKED, UNOFFERED_START]


def walks():
    """The reviewer's sequences, then seeded random walks of 0 to 4 refreshes."""
    yield (
        "loaded-checked",
        CHECKED,
        [("none", "toggle"), ("frequency", "frequency"), ("none", None)],
        "mine",
    )
    yield (
        "updated-then-unchecked",
        UNCHECKED,
        [("pledge", "toggle"), ("none", "toggle"), ("pledge", None)],
        "updated",
    )
    # Two refreshes over a hidden choice: recorded 12; this tab types 20 and
    # checks the box; another device sets a pledge, then changes something
    # else; this tab unchecks (round 1 of the #778 review).
    for policy in ("mine", "updated"):
        yield (
            f"hidden-choice-two-refreshes-{policy}",
            UNCHECKED,
            [(("pledge", "toggle"), "pledge"), ("none", "frequency"), ("toggle", None)],
            policy,
        )
    # The same with the choice first shown, then hidden by the box.
    yield (
        "shown-then-hidden-two-refreshes",
        UNCHECKED,
        [
            ("pledge", "pledge"),
            ("toggle", "frequency"),
            ("none", "method"),
            ("toggle", None),
        ],
        "mine",
    )
    # Another device's zero while this tab's changed frequency waits aside
    # behind its own "cannot contribute" (found by these walks).
    yield (
        "zero-behind-own-cannot",
        UNCHECKED,
        [(("frequency", "toggle"), "pledge"), ("none", "zero"), ("toggle", None)],
        "updated",
    )
    chooser = random.Random(784)

    def tab_step():
        """One or (a third of the time) two changes by this tab."""
        first = chooser.choice(TAB_ACTIONS)
        if chooser.random() < 1 / 3:
            return (first, chooser.choice(TAB_ACTIONS))
        return first

    for number in range(int(os.environ.get("PARISHKIT_PLEDGE_WALKS", "60"))):
        policy = chooser.choice(["mine", "updated", "mixed"])
        if number % 6 == 5:
            # A random walk of the two-refresh shape: a pledge this tab set
            # aside behind "cannot contribute", another device's pledge, a
            # second refresh that leaves the pledge alone (an open choice must
            # survive it), then this tab's uncheck and maybe more changes.
            steps = [
                (("pledge", "toggle"), "pledge"),
                (
                    chooser.choice(["none", "frequency"]),
                    chooser.choice(["frequency", "method"]),
                ),
                (("toggle", chooser.choice(TAB_ACTIONS)), None),
            ]
            yield f"walk-{number}", UNCHECKED, steps, policy
            continue
        # Most walks have two or more refreshes: the bugs hide there.
        refreshes = chooser.choice([0, 1, 2, 2, 3, 3, 4, 4, 5])
        steps = [
            (tab_step(), chooser.choice(OTHER_ACTIONS)) for _ in range(refreshes)
        ] + [(tab_step(), None)]
        start = chooser.choice(STARTS)
        yield f"walk-{number}", start, steps, policy


def test_generated_pledge_walks_keep_the_invariants(browser_engine, component_origin):
    """Every walk sends exactly what the Family decided or the record says."""
    failures = []
    for number, (name, start, steps, policy) in enumerate(walks()):
        walk = Walk(start, steps, policy, seed=number)
        try:
            run(browser_engine, component_origin, walk)
        except Exception as error:  # a timeout names the walk too
            failures.append(f"{name} {steps} {policy}: {error}")
    assert not failures, f"{len(failures)} walks failed:\n" + "\n".join(failures)
