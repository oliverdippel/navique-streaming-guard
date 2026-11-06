"""The character classes are the grammar, so they get their own tests."""

import pytest

from streaming_guard.chars import (
    ahv_symbol_matches,
    is_ascii_digit,
    is_label_char,
    is_local_char,
    is_tld_char,
    is_word_char,
    validate_max_output_chars,
)

COMBINING_DIAERESIS = "̈"


@pytest.mark.parametrize("c", list("aZ09") + ["ü", "é", "中", "ж", "٣", "²", COMBINING_DIAERESIS])
def test_word_chars(c):
    assert is_word_char(c)


@pytest.mark.parametrize("c", [" ", "\n", "@", ".", "_", "-", "＠", "。", "😀", "[", ","])
def test_non_word_chars(c):
    assert not is_word_char(c)


def test_local_chars_add_ascii_punctuation():
    for c in "._%+-":
        assert is_local_char(c)
    for c in "@ ,;:'\"()[]<>＠":
        assert not is_local_char(c)


def test_label_chars_allow_hyphen_but_not_other_punctuation():
    assert is_label_char("-")
    for c in "._%+@":
        assert not is_label_char(c)


def test_tld_chars_are_letters_and_marks_only():
    for c in ["a", "Z", "ü", "中", COMBINING_DIAERESIS]:
        assert is_tld_char(c)
    for c in ["0", "٣", "-", "_", "."]:
        assert not is_tld_char(c)


def test_ascii_digit_rejects_other_unicode_digits():
    assert all(is_ascii_digit(c) for c in "0123456789")
    for c in ["٣", "７", "²", "a"]:  # Arabic-Indic, fullwidth, superscript
        assert not is_ascii_digit(c)


def test_ahv_template():
    text = "756.1234.5678.97"
    assert all(ahv_symbol_matches(i, c) for i, c in enumerate(text))
    assert not ahv_symbol_matches(0, "8")
    assert not ahv_symbol_matches(3, "-")
    assert not ahv_symbol_matches(4, "٣")


@pytest.mark.parametrize("value", [None, 0, 11, 12, 10_000])
def test_valid_budgets(value):
    validate_max_output_chars(value)


@pytest.mark.parametrize("value", [-1, 1, 10])
def test_invalid_budgets(value):
    with pytest.raises(ValueError):
        validate_max_output_chars(value)


@pytest.mark.parametrize("value", [1.5, "20", True])
def test_budget_type(value):
    with pytest.raises(TypeError):
        validate_max_output_chars(value)
