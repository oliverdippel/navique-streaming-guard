"""Tests for the batch oracle.

Three layers:
1. Named edge cases with hand-computed expected output.
2. A second, independent oracle (a single Python regex) that must agree with
   the reference on ASCII inputs.
3. Property tests: planted secrets are always redacted; budget invariants hold.
"""

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from streaming_guard.chars import AHV_TOKEN as A
from streaming_guard.chars import EMAIL_TOKEN as E
from streaming_guard.chars import TRUNCATED_TOKEN as T
from streaming_guard.reference import is_valid_domain, redact, redact_pieces

AHV = "756.1234.5678.97"


def check(text, expected):
    assert redact(text) == expected


# --- 1. Named edge cases ------------------------------------------------------------


def test_pdf_example():
    check(
        "Kontakt: max.muster@navique.ch, AHV 756.1234.5678.97 ok",
        f"Kontakt: {E}, AHV {A} ok",
    )


def test_empty_input():
    check("", "")


@pytest.mark.parametrize(
    "text, expected",
    [
        (AHV, A),
        (f"x {AHV}, y", f"x {A}, y"),
        (f"{AHV}.", f"{A}."),
        (f"{AHV}.1234", f"{A}.1234"),  # followed by '.', not a digit
        (f"AHV{AHV}", f"AHV{A}"),  # letters next to it are not a boundary
        (f"a{AHV}b", f"a{A}b"),
        (f"{AHV} 756.1234.5678.98", f"{A} {A}"),
        ("756.756.1234.5678.90", f"756.{A}"),  # candidate inside a failing candidate
        # Not AHV numbers:
        ("756.1234.5678.9", "756.1234.5678.9"),  # too short
        ("756.1234.5678.971", "756.1234.5678.971"),  # followed by a digit
        ("1756.1234.5678.97", "1756.1234.5678.97"),  # preceded by a digit
        ("757.1234.5678.97", "757.1234.5678.97"),
        ("756-1234-5678-97", "756-1234-5678-97"),
        ("756 1234 5678 97", "756 1234 5678 97"),
        ("756.١٢٣٤.5678.97", "756.١٢٣٤.5678.97"),  # Arabic-Indic digits
        ("７５６.1234.5678.97", "７５６.1234.5678.97"),  # fullwidth digits
    ],
)
def test_ahv(text, expected):
    check(text, expected)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a@b.ch", E),
        ("ok", "ok"),
        ("user.name+tag@sub.example.co.uk", E),
        ("first-last@ex-ample.ch", E),
        ("A@B.CH", E),
        ("mail me: a@b.ch!", f"mail me: {E}!"),
        ("😀a@b.ch", f"😀{E}"),
        (".@x.ch", E),  # simplified grammar: any local chars, even a lone dot
        # Partial / invalid emails stay as they are:
        ("a@", "a@"),
        ("a@b", "a@b"),
        ("a@.ch", "a@.ch"),
        ("a@b..ch", "a@b..ch"),
        ("@x.ch", "@x.ch"),
        ("a@b.c", "a@b.c"),  # one-letter TLD
        ("a@b.c1de", "a@b.c1de"),  # no valid TLD anywhere
        ("a @b.ch", "a @b.ch"),
        # Longest valid domain prefix, no right boundary (over-redaction):
        ("a@b.ch1", f"{E}1"),
        ("a@b.ch.", f"{E}."),
        ("a@b.ch-x", f"{E}-x"),
        ("a@b.cc.d1", f"{E}.d1"),
        # Several '@':
        ("a@b@c.de", f"a@{E}"),
        ("a@@b.cc", "a@@b.cc"),
        ("a@b.cc@d.ee", f"{E}@d.ee"),  # the bug found in design review
        ("a@b.cc.y@z.de", f"{E}{E}"),
        # Unicode:
        ("müller@x.ch", E),
        ("a@müller.ch", E),
        ("müller@x.ch", E),  # decomposed ü (combining mark)
        ("用户@例子.中国", E),
        # Documented limitation: lookalike '@' is not recognised.
        ("max＠navique.ch", "max＠navique.ch"),
    ],
)
def test_email(text, expected):
    check(text, expected)


def test_local_part_length_limit():
    check("a" * 64 + "@x.ch", E)
    check("a" * 65 + "@x.ch", "a" + E)  # leftmost start that keeps the local part <= 64
    check("a" * 100 + "@x.ch", "a" * 36 + E)


def test_domain_length_limit():
    domain_253 = "a" * 249 + ".com"
    assert len(domain_253) == 253 and is_valid_domain(domain_253)
    check(f"x@{domain_253} ", f"{E} ")
    # 254 chars: the longest valid prefix within 253 chars ends in ".co".
    check("x@" + "a" * 250 + ".com", E + "m")
    # 255 chars: no valid prefix within 253 chars, so no email at all.
    long_domain = "x@" + "a" * 251 + ".com"
    check(long_domain, long_domain)


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"{AHV}@navique.ch", E),  # email wins over the AHV it contains
        (f"{AHV}@invalid", f"{A}@invalid"),
        (f"{AHV}@", f"{A}@"),
        (f"a@x.{AHV}.com", E),  # AHV inside a valid domain
        (f"a@x.ch.{AHV} ", f"{E}.{A} "),  # AHV in the tail after the longest domain
        (f"a@b.cc{AHV}", f"{E}{A}"),
        (f"{AHV} a@b.ch", f"{A} {E}"),
        # AHV beats the email because the email cannot start at the AHV's start
        # (local part would be 65 chars); the email then starts after the AHV.
        (AHV + "a" * 49 + "@x.ch", A + E),
        (AHV + "a" * 48 + "@x.ch", E),  # local part is exactly 64: email wins
        # Two AHVs, the email's 64-char window starts inside the first one:
        (f"{AHV}x{AHV}x" + "a" * 40 + "@x.ch", A + E),
        (f"{AHV}x{AHV}x" + "a" * 40 + "@x", f"{A}x{A}x" + "a" * 40 + "@x"),
    ],
)
def test_overlap(text, expected):
    check(text, expected)


# --- Budget --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, budget, expected",
    [
        ("hello world", None, "hello world"),
        ("hello world", 11, "hello world"),  # exact fit: no marker
        ("hello world!", 11, T),  # 1 char over: everything gives way to the marker
        ("abcdefghijklmnopqrst", 15, "abcd" + T),
        ("ab a@b.ch", 18, "ab " + T),  # token does not fit and is never cut
        ("ab a@b.ch", 19, "ab " + E),  # exact fit including the token
        ("", 0, ""),
        ("anything", 0, ""),  # zero budget: not even the marker
    ],
)
def test_budget(text, budget, expected):
    assert redact(text, budget) == expected


@pytest.mark.parametrize("budget", [-1, 1, 10])
def test_invalid_budget(budget):
    with pytest.raises(ValueError):
        redact("x", budget)


# --- 2. Independent regex oracle (ASCII only) -------------------------------------------
#
# Python's re.sub scans left to right and, at each position, tries alternatives
# in order: that is exactly leftmost-first with email before AHV. The greedy
# domain pattern finds the longest valid domain. The 253-char domain limit is
# not expressed here, so generated inputs stay well below it.

_REGEX_ORACLE = re.compile(
    r"(?P<email>[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})"
    r"|(?P<ahv>(?<![0-9])756\.[0-9]{4}\.[0-9]{4}\.[0-9]{2}(?![0-9]))"
)


def regex_redact(text):
    return _REGEX_ORACLE.sub(lambda m: E if m.group("email") else A, text)


FRAGMENTS = [AHV, "756.", "1234.", "a@b.ch", "@", ".", "a" * 30, "x.y", "..", "@@"]
ASCII_CHARS = list("756.@ab1-_+% xZ")

tricky_ascii_text = st.lists(
    st.one_of(st.sampled_from(FRAGMENTS), st.sampled_from(ASCII_CHARS)), max_size=30
).map("".join)


@settings(max_examples=3000)
@given(tricky_ascii_text)
def test_reference_agrees_with_regex_oracle(text):
    assert redact(text) == regex_redact(text)


# --- 3. Properties -------------------------------------------------------------------

noise = st.text(max_size=40)  # arbitrary Unicode, including surrogates and controls

local_part = st.text(alphabet="abcxyz0123456789._%+-üé中", min_size=1, max_size=64)
label = st.text(alphabet="abcxyz0123456789-ü中", min_size=1, max_size=20)
tld = st.text(alphabet="abcdefghüч", min_size=2, max_size=6)
emails = st.builds(
    lambda local, labels, top: f"{local}@{'.'.join(labels)}.{top}",
    local_part,
    st.lists(label, min_size=1, max_size=4),
    tld,
)
ahvs = st.from_regex(r"756\.[0-9]{4}\.[0-9]{4}\.[0-9]{2}", fullmatch=True)


@given(noise, st.one_of(emails, ahvs), noise)
def test_planted_secret_surrounded_by_spaces_is_redacted(before, secret, after):
    out = redact(f"{before} {secret} {after}")
    assert secret not in out
    assert f" {E} " in out or f" {A} " in out


@given(noise)
def test_pieces_join_to_output(text):
    assert "".join(redact_pieces(text)) == redact(text)


@given(tricky_ascii_text, st.one_of(st.just(0), st.integers(min_value=11, max_value=60)))
def test_budget_invariants(text, budget):
    full = redact(text)
    out = redact(text, budget)
    assert len(out) <= budget
    if len(full) <= budget:
        assert out == full
    elif budget == 0:
        assert out == ""
    else:
        assert out.endswith(T)
        body = out[: -len(T)]
        assert full.startswith(body)  # truncation only ever cuts, never alters
        # The body is a whole number of pieces: no token is ever cut in half.
        pieces = redact_pieces(text)
        joined = ""
        for piece in pieces:
            if len(joined) == len(body):
                break
            joined += piece
        assert joined == body
