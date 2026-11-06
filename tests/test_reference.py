"""Tests for the batch oracle.

Three layers:
1. Named edge cases with hand-computed expected output (tests/cases.py).
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

from .cases import ALL_CASES, BUDGET_CASES
from .strategies import ahvs, emails, noise, tricky_ascii_text

# --- 1. Named edge cases ------------------------------------------------------------


@pytest.mark.parametrize("text, expected", ALL_CASES)
def test_named_case(text, expected):
    assert redact(text) == expected


def test_domain_253_is_valid():
    domain = "a" * 249 + ".com"
    assert len(domain) == 253 and is_valid_domain(domain)
    assert not is_valid_domain("a" * 250 + ".com")


@pytest.mark.parametrize("text, budget, expected", BUDGET_CASES)
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


@settings(max_examples=3000)
@given(tricky_ascii_text)
def test_reference_agrees_with_regex_oracle(text):
    assert redact(text) == regex_redact(text)


# --- 3. Properties -------------------------------------------------------------------


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
        joined = ""
        for piece in redact_pieces(text):
            if len(joined) == len(body):
                break
            joined += piece
        assert joined == body
