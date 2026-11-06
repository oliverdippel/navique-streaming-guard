"""Batch reference implementation: the executable specification.

This is the test oracle. It is deliberately naive and has nothing in common
with the streaming state machines except the character predicates in
`chars.py`. It favours being obviously correct over being fast (worst case is
roughly quadratic, which is fine for test inputs).

Matching semantics (leftmost-first, email before AHV, longest email):

    p = 0
    while p < len(text):
        if an email matches at p:  emit EMAIL_TOKEN, p = end of the longest email
        elif an AHV matches at p:  emit AHV_TOKEN,   p = p + 16
        else:                      emit text[p],     p = p + 1
"""

import re

from .chars import (
    AHV_LEN,
    AHV_TOKEN,
    AT,
    DOMAIN_MAX,
    DOT,
    EMAIL_TOKEN,
    LOCAL_MAX,
    TRUNCATED_TOKEN,
    is_ascii_digit,
    is_label_char,
    is_local_char,
    is_tld_char,
    validate_max_output_chars,
)

# Written as a regex on purpose, so it is independent of the template-driven
# AHV state machine. [0-9] instead of \d: \d would also match non-ASCII digits.
_AHV_BODY = re.compile(r"756\.[0-9]{4}\.[0-9]{4}\.[0-9]{2}")


# --- Email --------------------------------------------------------------------


def is_valid_domain(s: str) -> bool:
    """Does `s` as a whole satisfy the domain grammar?"""
    if not 1 <= len(s) <= DOMAIN_MAX:
        return False
    labels = s.split(DOT)
    if len(labels) < 2:
        return False
    if not all(label and all(is_label_char(c) for c in label) for label in labels):
        return False
    tld = labels[-1]
    return len(tld) >= 2 and all(is_tld_char(c) for c in tld)


def longest_domain_end(text: str, at: int) -> int | None:
    """End index of the longest valid domain starting right after text[at] == '@'.

    "Longest valid prefix" is checked literally: try every possible end, from the
    longest allowed one down, and keep the first that is a valid domain.
    """
    start = at + 1
    for end in range(min(len(text), start + DOMAIN_MAX), start, -1):
        if is_valid_domain(text[start:end]):
            return end
    return None


def email_end_at(text: str, p: int, domain_cache: dict[int, int | None]) -> int | None:
    """End index of the longest email starting exactly at p, or None.

    The local part is forced: it runs from p up to the next non-local char,
    which must be '@', and it may be at most LOCAL_MAX chars long.
    """
    q = p
    while q < len(text) and is_local_char(text[q]):
        q += 1
    if q == p or q >= len(text) or text[q] != AT or q - p > LOCAL_MAX:
        return None
    # Every start position that reaches the same '@' shares its domain, so cache it.
    if q not in domain_cache:
        domain_cache[q] = longest_domain_end(text, q)
    return domain_cache[q]


# --- AHV ------------------------------------------------------------------------


def ahv_matches_at(text: str, p: int) -> bool:
    """Does an AHV number start exactly at p (including the digit boundaries)?"""
    if not _AHV_BODY.match(text, p):
        return False
    if p > 0 and is_ascii_digit(text[p - 1]):
        return False
    end = p + AHV_LEN
    if end < len(text) and is_ascii_digit(text[end]):
        return False
    return True


# --- Redaction --------------------------------------------------------------------


def redact_pieces(text: str) -> list[str]:
    """Redacted output as a list of atomic pieces: single literal chars or tokens."""
    pieces: list[str] = []
    domain_cache: dict[int, int | None] = {}
    p = 0
    while p < len(text):
        email_end = email_end_at(text, p, domain_cache)
        if email_end is not None:
            pieces.append(EMAIL_TOKEN)
            p = email_end
        elif ahv_matches_at(text, p):
            pieces.append(AHV_TOKEN)
            p += AHV_LEN
        else:
            pieces.append(text[p])
            p += 1
    return pieces


def apply_budget(pieces: list[str], max_output_chars: int | None) -> str:
    """Apply the output budget to a sequence of atomic pieces.

    - Everything fits      -> all pieces, no marker.
    - Budget 0 and overflow -> "" (the marker itself would break the budget).
    - Otherwise            -> the longest prefix of pieces that fits in
                              max - len(TRUNCATED_TOKEN), then the marker.
                              Pieces are never split, so tokens stay atomic.
    The result never exceeds max_output_chars.
    """
    validate_max_output_chars(max_output_chars)
    if max_output_chars is None or sum(len(p) for p in pieces) <= max_output_chars:
        return "".join(pieces)
    if max_output_chars == 0:
        return ""
    room = max_output_chars - len(TRUNCATED_TOKEN)
    kept: list[str] = []
    used = 0
    for piece in pieces:
        if used + len(piece) > room:
            break  # stop at the first piece that does not fit; never skip ahead
        kept.append(piece)
        used += len(piece)
    return "".join(kept) + TRUNCATED_TOKEN


def redact(text: str, max_output_chars: int | None = None) -> str:
    """The full expected output of the streaming guard for `text`."""
    validate_max_output_chars(max_output_chars)
    return apply_budget(redact_pieces(text), max_output_chars)
