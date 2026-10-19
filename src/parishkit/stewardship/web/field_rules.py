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
from dataclasses import dataclass, replace

from django import forms
from django.utils.translation import gettext_lazy as _

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
    stripped. A value of only spaces is admitted: stripped, it is empty,
    which is the ``required`` rule's to judge (the complete gate counts it
    as empty), as the browser's own pattern leaves an empty value alone.
    """
    return rf"[{SPACE}]*(?:(?:{core})(?<![{SPACE}])[{SPACE}]*)?"


def char_class(spans, without="", *, negated=False):
    """A ``[...]`` class of the inclusive code point ``spans``, less ``without``.

    A point in the Basic Multilingual Plane is written as ``\\uXXXX``, which
    both engines read alike and which needs no escaping under the "v" flag;
    one beyond it is written as itself, which both read as one character
    (their escapes for it differ). ``negated`` gives the class of every
    other character instead.
    """

    def written(point):
        """One point as both engines read it inside a class."""
        return f"\\u{point:04x}" if point <= 0xFFFF else chr(point)

    points = sorted(
        {point for low, high in spans for point in range(low, high + 1)}
        - {ord(char) for char in without}
    )
    parts, start = [], None
    for index, point in enumerate(points):
        start = point if start is None else start
        if index + 1 == len(points) or points[index + 1] != point + 1:
            parts.append(
                written(start) + ("" if point == start else f"-{written(point)}")
            )
            start = None
    return ("[^" if negated else "[") + "".join(parts) + "]"


def spans_of(chars):
    """The code point spans of the characters in ``chars``."""
    return [(ord(char), ord(char)) for char in chars]


# The characters str.splitlines() breaks a value's lines at, and Python's
# whitespace (SPACE) as single characters, for a list of one value per line.
LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
SPACES = "".join(chr(point) for point in range(0x10000) if chr(point).isspace())


# An email address exactly as Django's EmailValidator (an EmailField's own
# check, and normalized_email's) admits one, so the browser and the server
# give one verdict. Its patterns are compiled with IGNORECASE, under which
# Python's re also reads the dotted and dotless i (U+0130, U+0131), the long
# s (U+017F) and the Kelvin sign (U+212A) as ASCII letters; those are
# written out here (test_field_rules pins every class to Django's own).
_FOLDS = ((0x130, 0x131), (0x17F, 0x17F), (0x212A, 0x212A))
_ASCII_LETTERS = ((0x41, 0x5A), (0x61, 0x7A))
# The dot-atom's characters, the quoted string's, and what may follow a
# backslash inside quotes.
_ATEXT = (
    (0x21, 0x21), (0x23, 0x27), (0x2A, 0x2B), (0x2D, 0x2D), (0x2F, 0x39),
    (0x3D, 0x3D), (0x3F, 0x3F), (0x41, 0x5A), (0x5E, 0x7E), *_FOLDS,
)  # fmt: skip
_QTEXT = ((0x1, 0x8), (0xB, 0xC), (0xE, 0x1F), (0x21, 0x21), (0x23, 0x5B),
          (0x5D, 0x7F), *_FOLDS)  # fmt: skip
_QPAIR = ((0x1, 0x9), (0xB, 0xC), (0xE, 0x7F), *_FOLDS)
# A domain label's characters (DomainNameValidator's, with any character
# from U+00A1 to U+FFFF), and an "xn--" name's.
_DOMAIN_EDGE = ((0x30, 0x39), *_ASCII_LETTERS, (0xA1, 0xFFFF))
_DOMAIN_INNER = ((0x2D, 0x2D), *_DOMAIN_EDGE)
_TLD = ((0x2D, 0x2D), *_ASCII_LETTERS, (0xA1, 0xFFFF))
_PUNYCODE = ((0x30, 0x39), *_ASCII_LETTERS, *_FOLDS)


def email_pattern(without=""):
    """The address rule's pattern, with no character of ``without`` in it.

    The user part is a dot-atom or a quoted string; then "@" and a domain
    name ending in a label of two or more characters (no trailing dot), an
    IPv4 or IPv6 address in brackets, or exactly "localhost". ``without``
    leaves characters out of every class, for a line of a list whose line
    breaks separate addresses.
    """

    def of(spans):
        """One class of the rule, less ``without``."""
        return char_class(spans, without)

    edge, inner = of(_DOMAIN_EDGE), of(_DOMAIN_INNER)
    user = (
        rf"({of(_ATEXT)}+(\.{of(_ATEXT)}+)*"
        rf'|"({of(_QTEXT)}|\\{of(_QPAIR)})*")'
    )
    domain = (
        rf"{edge}({inner}{{0,61}}{edge})?(\.(?!-){inner}{{1,63}}(?<!-))*"
        rf"\.(?!-)({of(_TLD)}{{2,63}}|[Xx][Nn]--{of(_PUNYCODE)}{{1,59}})(?<!-)"
    )
    return rf"{user}@({domain}|\[{_IPV4}\]|{_IPV6_LITERAL}|localhost)"


def email_lines_pattern(limit, longest):
    """At most ``limit`` addresses, one per line, as a list field cleans them.

    The value is split at its line breaks (str.splitlines) and each line is
    stripped; blank lines are skipped, and each other line must be one
    address (``email_pattern``) of at most ``longest`` characters. So
    spaces may surround each address, an address holds no line break, and
    it ends in a character that is not a space (a stripped line could not
    end in one). Each separator is the spaces on the address's own line,
    then exactly the first line break, then any spaces and line breaks:
    written so, a run of blank lines can be read only one way, and a
    refused value is answered at once rather than after trying every split
    (test_field_rules times the worst cases).
    """
    breaks = char_class(spans_of(LINE_BREAKS))
    within_line = char_class(spans_of(SPACES), LINE_BREAKS)
    one = (
        # The address's line, without its trailing spaces, is short enough.
        rf"(?={char_class(spans_of(LINE_BREAKS), negated=True)}{{1,{longest}}}"
        rf"(?<![{SPACE}]){within_line}*({breaks}|$))"
        rf"(?:{email_pattern(without=LINE_BREAKS)})(?<![{SPACE}])"
    )
    # The trailing spaces are inside the optional group, so a value of only
    # spaces is read by the leading run alone, not split between two runs.
    return (
        rf"[{SPACE}]*({one}({within_line}*{breaks}[{SPACE}]*{one}){{0,{limit - 1}}}"
        rf"[{SPACE}]*)?"
    )


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
    # Set on a rule ``worded`` for one field: the same pattern (and case
    # table entry) as the registered rule of that name, in that field's
    # words, so it is not registered again.
    worded_from: str | None = None

    def __post_init__(self):
        """Register the rule once, by its unique name."""
        if self.worded_from is not None:
            return
        if self.name in RULES:
            raise ValueError(f"Field rule {self.name!r} is defined twice.")
        RULES[self.name] = self

    def worded(self, message, *, code=None, empty=None):
        """This rule in one field's own words (and code, when it has its own).

        Several fields share one format (every email address) but say what
        each one is ("Enter a valid From address."); the pattern, and so the
        shared case table's verdicts, stay the registered rule's.
        """
        return replace(
            self,
            message=message,
            code=code or self.code,
            empty=empty,
            worded_from=self.name,
        )

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
    attrs = rule.attrs()
    if isinstance(field.widget, forms.Textarea):
        # A textarea has no pattern attribute of its own; the page script
        # checks data-pattern there instead (wirePattern, also with "v").
        attrs["data-pattern"] = attrs.pop("pattern")
    field.widget.attrs.update(attrs)
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


def placeholder_text_pattern(names, refused=""):
    """A text with only the ``{{ name }}`` placeholders of ``names`` in it.

    What web.content.validate_template admits of a text that may hold no
    hosted file (an email subject, a Share option label): each placeholder
    found (two braces, spaces, a name, spaces, two braces) names one of
    ``names``, and once they are taken out, what is left holds none of the
    template markers "{{", "}}", "{%", "%}", "{#" or "#}" (a hosted-file
    placeholder starts with "{{", so it is refused too). ``refused`` are
    characters the text may not hold at all, also inside a placeholder's
    spaces.

    The pattern reads the text as placeholders and single characters. A
    brace, "%" or "#" that is not part of a placeholder may not be followed,
    past any placeholders (which are taken out first), by the character that
    would make it a marker; a "{" that starts a placeholder is part of it.
    test_field_rules pins this to validate_template in both directions.
    """
    space = char_class(spans_of(SPACES), refused)
    tag = rf"\{{\{{{space}*(?:{'|'.join(sorted(names))}){space}*\}}\}}"
    other = char_class(spans_of("{}%#" + refused), negated=True)
    # Any placeholders, then a marker's second character.
    then = rf"(?:{tag})*"
    return (
        rf"(?:{tag}"
        rf"|\{{(?!{then}(?:[%#]|(?!{tag})\{{))"
        rf"|[%#\}}](?!{then}\}})"
        rf"|{other})*"
    )


# One email address (#1577), as an EmailField cleans it: spaces around it
# are stripped first. Each field says what its address is (``worded``).
EMAIL = FieldRule(
    name="email_address",
    pattern=trimmed(email_pattern()),
    message=_("Enter an email address, such as name@example.org."),
    # EmailValidator's own code; one code per field.
    code="invalid",
)


def email_input(attrs=None):
    """A text box that asks phones for an email keyboard.

    Not ``type="email"``: the browser's own address check admits addresses
    the server refuses (``a@b``) and refuses some it admits (a quoted user
    name, a padded value), so the rule's pattern is the one check both sides
    share. Autocapitalize and spellcheck are off, as ``type="email"`` implies.
    """
    return forms.TextInput(
        attrs={
            "inputmode": "email",
            "autocapitalize": "none",
            "spellcheck": "false",
            **(attrs or {}),
        }
    )


class EmailRuleField(forms.EmailField):
    """An email address field checked by ``rule`` (EMAIL in its own words).

    EmailValidator stays the field's validator and the rule admits exactly
    what it admits (test_field_rules pins both); the rule is also checked
    here, so a refusal always carries the rule's words and code.
    """

    def __init__(self, rule=EMAIL, **kwargs):
        """An email field with the rule's text box and reserved error line."""
        kwargs.setdefault("widget", email_input())
        super().__init__(**kwargs)
        apply(self, rule)

    def validate(self, value):
        """Check ``required``, then the rule, with its words and code."""
        super().validate(value)
        if value not in self.empty_values:
            self.field_rule.check(value)


# A chosen file of 0 bytes, which Django's FileField refuses (its code
# "empty"): said in the browser as soon as it is chosen (data-max-bytes's
# check in ui-v1.js, #1577) and by the server in the same words.
EMPTY_FILE = _("The chosen file is empty (0 bytes). Choose another file.")


def refuse_empty_file(field, message=EMPTY_FILE):
    """Have ``field`` refuse a 0-byte file in the browser too, in its words."""
    field.error_messages["empty"] = message
    field.widget.attrs["data-empty-file-message"] = message
    return field
