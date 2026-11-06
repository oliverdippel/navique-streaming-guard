"""The grammar: character classes, length limits and output tokens.

Both the batch reference (`reference.py`) and the streaming guard use these
predicates, so this module *is* the definition of what counts as an email or an
AHV number. Every predicate takes exactly one character and runs in O(1).

Email grammar (simplified, documented in DESIGN.md):

    email  = local "@" domain
    local  = 1..64 LOCAL chars
    domain = label 1*("." label), at most 253 chars in total
    label  = 1* LABEL chars
    the last label (TLD) = 2* TLD chars

AHV grammar:

    ahv = "756" "." 4DIGIT "." 4DIGIT "." 2DIGIT     (DIGIT = ASCII 0-9 only)
    not preceded by an ASCII digit, not followed by an ASCII digit
"""

import unicodedata

# --- Output tokens -----------------------------------------------------------

EMAIL_TOKEN = "[REDACTED_EMAIL]"
AHV_TOKEN = "[REDACTED_AHV]"
TRUNCATED_TOKEN = "[TRUNCATED]"

# --- Limits ------------------------------------------------------------------

LOCAL_MAX = 64  # RFC 5321 limit for the local part (we count code points)
DOMAIN_MAX = 253  # RFC 1035 limit for a domain name (we count code points)

# --- Structural characters (deliberately ASCII only) --------------------------

AT = "@"
DOT = "."

# AHV template: "d" stands for one ASCII digit, every other symbol is literal.
AHV_TEMPLATE = "756.dddd.dddd.dd"
AHV_LEN = len(AHV_TEMPLATE)  # 16

_LOCAL_EXTRA = frozenset("._%+-")


def is_ascii_digit(c: str) -> bool:
    """'0'..'9' only. Never use str.isdigit(): it also accepts e.g. Arabic-Indic digits."""
    return "0" <= c <= "9"


def is_word_char(c: str) -> bool:
    """Unicode letter, number or combining mark (general categories L*, N*, M*).

    Combining marks are included so that decomposed text (e.g. 'u' + U+0308 for
    'ü') does not split an email address in two.
    """
    if c.isascii():
        return c.isalnum()
    return unicodedata.category(c)[0] in "LNM"


def is_local_char(c: str) -> bool:
    """A character allowed in the local part (before the '@')."""
    return is_word_char(c) or c in _LOCAL_EXTRA


def is_label_char(c: str) -> bool:
    """A character allowed inside a domain label (between dots)."""
    return is_word_char(c) or c == "-"


def is_tld_char(c: str) -> bool:
    """A character allowed in the last domain label: letters and combining marks."""
    if c.isascii():
        return c.isalpha()
    return unicodedata.category(c)[0] in "LM"


def validate_max_output_chars(max_output_chars: int | None) -> None:
    """Budget rules shared by the reference and the guard.

    None  -> unlimited.
    0     -> zero-output budget: nothing is ever returned, not even [TRUNCATED].
    1..10 -> rejected: the output can neither be truncated cleanly (the marker
             itself is 11 chars) nor stay within the budget.
    < 0   -> rejected.
    """
    if max_output_chars is None:
        return
    if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int):
        raise TypeError("max_output_chars must be an int or None")
    if max_output_chars < 0:
        raise ValueError("max_output_chars must not be negative")
    if 0 < max_output_chars < len(TRUNCATED_TOKEN):
        raise ValueError(
            f"max_output_chars must be 0 or at least {len(TRUNCATED_TOKEN)} "
            f"(the length of {TRUNCATED_TOKEN})"
        )


def ahv_symbol_matches(index: int, c: str) -> bool:
    """Does character `c` match position `index` (0..15) of the AHV template?"""
    expected = AHV_TEMPLATE[index]
    if expected == "d":
        return is_ascii_digit(c)
    return c == expected
