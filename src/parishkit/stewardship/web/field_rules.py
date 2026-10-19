"""Format rules the server and the browser check from one definition.

UX conventions, "Field errors and live checks": a rule the browser can check
is checked as the Admin leaves the field, and Save (or Save and continue)
stays unavailable while it fails, with the rule's words as the reason. The
server stays the authority and checks every save.

A ``FieldRule`` is that rule written once: a regular expression and the
plain-language refusal. The server matches the whole value against it (and
refuses with the same words and a closed error code); the field's widget
carries the same expression as its HTML ``pattern`` and the same words as
``data-missing-hint``, which the page script (``ui-v1.js``) shows in the
field's reserved error line when the field is left invalid, and which the
complete-before-submit gate shows beside the held button. Nothing is
translated between the two sides, so they cannot drift apart.

A pattern must mean the same in Python's ``re`` (with ``re.fullmatch``) and
in the browser, which compiles ``pattern`` with the "v" flag and anchors it
at both ends. Keep to the common subset: explicit classes such as ``[0-9]``,
groups, ``?``, ``*``, ``+`` and ``{n,m}``; inside a ``[...]`` class escape
``-``, ``(``, ``)``, ``/`` and ``|``, and outside one escape only syntax
characters (``\\-`` is an error there under "v"). Never write ``\\s``: the two
engines' sets differ (JavaScript's has U+FEFF, Python's U+001C to U+001F and
U+0085), so use ``SPACE``, an explicit class. Every rule has accepted and
refused examples in ``tests/stewardship/fixtures/field_rule_cases.json``,
which a unit test runs through Python and a browser test through every
engine.
"""

import re
from dataclasses import dataclass

from django import forms

# The field group with a reserved error line (shared with Parish logos and
# Off-site backups), so a rule's message coming or going moves nothing.
ERROR_LINE_TEMPLATE = "stewardship/file-size-field.html"

# Every rule by name, for the shared case table's completeness test.
RULES = {}

# Python's whitespace (``str.isspace``, which ``str.strip`` and Python's
# ``\s`` use), written out as a class body so both engines read one set. A
# rule may admit it around a value only where the server strips the value
# (a CharField does) before it checks the rule.
SPACE = r"\t\n\v\f\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"

# A host and optional port exactly as Django's URLValidator (which URLField
# and the configuration's typed() run) admits one, so a URL rule built on it
# neither admits a host the server refuses nor refuses one the server admits
# (a value saved earlier must never block an unrelated save). The host is an
# IPv4 address, a bracketed IPv6 address, "localhost" in any case, or a name:
# labels of letters, digits and inner hyphens, at most 63 characters,
# separated by single dots, ending in a label of at least two letters (or an
# "xn--" name) and an optional trailing dot; then an optional ":port". Names
# may be at most 253 characters, as URLValidator checks. Non-ASCII
# characters are admitted as there (U+00A1 to U+FFFF, which includes some
# Unicode spaces; only what follows the host refuses whitespace).
_NON_ASCII = r"\u00a1-\uffff"
_LETTER = "A-Za-z" + _NON_ASCII
_LABEL = rf"[0-9{_LETTER}]([0-9{_LETTER}\-]{{0,61}}[0-9{_LETTER}])?"
_OCTET = r"(0|25[0-5]|2[0-4][0-9]|1[0-9]?[0-9]?|[1-9][0-9]?)"
_IPV4 = rf"{_OCTET}(\.{_OCTET}){{3}}"
_H = "[0-9A-Fa-f]{1,4}"


def _ipv6():
    """The text of an IPv6 address as Python's ``ipaddress`` reads it.

    Eight groups of 1 to 4 hex digits, or fewer around one "::" (which
    stands for at least one group); the last 32 bits may be an IPv4
    address. Written as an alternation over the number of groups left of
    the "::" so the regular expression stays in the subset both engines read.
    """
    alternatives = [rf"{_H}(:{_H}){{7}}", rf"{_H}(:{_H}){{5}}:{_IPV4}"]
    for left in range(0, 8):
        head = rf"{_H}(:{_H}){{{left - 1}}}" if left else ""
        # Groups right of "::" (at most 7 in all; at most 5 before an IPv4).
        alternatives.append(
            rf"{head}::({_H}(:{_H}){{0,{6 - left}}})?" if left < 7 else f"{head}::"
        )
        if left < 6:
            alternatives.append(rf"{head}::({_H}:){{0,{5 - left}}}{_IPV4}")
    return "(" + "|".join(alternatives) + ")"


# A bracketed IPv6 address; Django's IPv6 check also refuses one of more
# than 39 characters (MAX_IPV6_ADDRESS_LENGTH), such as six full groups
# before an IPv4 address.
_IPV6_LITERAL = rf"\[(?=[0-9A-Fa-f:.]{{1,39}}\]){_ipv6()}\]"

HOST = (
    r"(?![^\/?#:]{254})"
    rf"({_IPV4}|{_IPV6_LITERAL}|[Ll][Oo][Cc][Aa][Ll][Hh][Oo][Ss][Tt]|"
    rf"{_LABEL}(\.{_LABEL})*\."
    rf"([{_LETTER}][{_LETTER}\-]{{0,61}}[{_LETTER}]|[Xx][Nn]--[A-Za-z0-9]{{1,59}})\.?)"
    r"(:[0-9]{1,5})?"
)


def trimmed(core):
    """``core`` as the browser must check a value the server strips first.

    The browser tests the raw value, the server the stripped one, so the
    pattern admits ``SPACE`` around ``core`` and forbids ``core`` itself from
    ending in a space: otherwise a host such as ``https://example.o`` plus
    U+2003 could be read as a name ending in that space (URLValidator admits
    those inside a name) and pass here, then fail on the server once
    stripped.
    """
    return rf"[{SPACE}]*(?:{core})(?<![{SPACE}])[{SPACE}]*"


@dataclass(frozen=True)
class FieldRule:
    """One format rule, its refusal and its error code, for both sides."""

    name: str
    pattern: str
    message: str
    code: str
    # What an empty required field says instead (what to type, not the
    # format); None leaves the form's own hint.
    empty: str | None = None

    def __post_init__(self):
        """Register the rule once, by its unique name."""
        if self.name in RULES:
            raise ValueError(f"Field rule {self.name!r} is defined twice.")
        RULES[self.name] = self

    def matches(self, value):
        """Whether the whole of ``value`` follows the rule."""
        return re.fullmatch(self.pattern, str(value)) is not None

    def check(self, value):
        """Refuse a value that does not follow the rule, in its own words."""
        if not self.matches(value):
            raise forms.ValidationError(self.message, code=self.code)

    def attrs(self):
        """The widget attributes that give the browser the same rule."""
        attrs = {
            "pattern": self.pattern,
            "data-missing-hint": self.message,
            "data-field-rule": self.name,
        }
        if self.empty:
            attrs["data-empty-hint"] = self.empty
        return attrs


def apply(field, rule):
    """Give ``field`` the rule's browser attributes and a reserved error line.

    The server-side check stays the caller's (``rule.check`` in the field's
    ``clean`` or the form's ``clean_<name>``), next to any further rule only
    the server can decide.
    """
    field.widget.attrs.update(rule.attrs())
    field.template_name = ERROR_LINE_TEMPLATE
    # A field's own format check (a URL field's validator, code "invalid")
    # may refuse a value before the rule is reached; it says the rule's
    # words, so the server never words a refusal the browser would not. Such
    # a field's rule uses code "invalid" too, so the field has one code.
    field.error_messages["invalid"] = rule.message
    # Marks the field for accounts.field_tips, which may still move long
    # help into a tip: the reserved-line group draws one.
    field.field_rule = rule
    return field


class RuleField(forms.CharField):
    """A text field whose value must follow ``rule``, in the browser and here.

    An empty value is the ``required`` rule's to judge, so the format rule
    checks only a value that was given, as the browser's ``pattern`` does.
    """

    def __init__(self, rule, **kwargs):
        """Build the text field, then give it the rule's attributes."""
        super().__init__(**kwargs)
        apply(self, rule)

    def validate(self, value):
        """Check ``required``, then the rule, with its words and code."""
        super().validate(value)
        if value not in self.empty_values:
            self.field_rule.check(value)
