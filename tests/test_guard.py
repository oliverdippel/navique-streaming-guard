"""Tests for the streaming guard.

The batch reference is the oracle: whatever the chunking, the concatenated
output must equal `redact(text, budget)`. On top of that we check the
streaming-only properties: no partial leak, low latency, bounded buffers,
linear work, and the lifecycle.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from streaming_guard import StreamingGuard
from streaming_guard.chars import EMAIL_TOKEN as E
from streaming_guard.chars import TRUNCATED_TOKEN as T
from streaming_guard.reference import redact

from .cases import AHV, ALL_CASES, BUDGET_CASES
from .strategies import budgets, mixed_text, text_and_chunks, tricky_ascii_text

PENDING_BOUND = 64 + 1 + 253  # 318, DESIGN.md section 3
RESERVE_BOUND = 11 + 16 - 1  # 26


def run(chunks, budget=None, after_push=None):
    """Stream `chunks` through a fresh guard; returns the list of outputs."""
    guard = StreamingGuard(budget)
    outputs = []
    for chunk in chunks:
        outputs.append(guard.push(chunk))
        if after_push:
            after_push(guard, outputs)
    outputs.append(guard.finish())
    return outputs


def streamed(chunks, budget=None):
    return "".join(run(chunks, budget))


def two_way_splits(text):
    return [[text[:i], text[i:]] for i in range(len(text) + 1)]


def three_way_splits(text):
    n = len(text)
    return [[text[:i], text[i:j], text[j:]] for i in range(n + 1) for j in range(i, n + 1)]


# --- The PDF example --------------------------------------------------------------------


def test_pdf_example_push_by_push():
    assert run(["Kontakt: max.mus", "ter@navique.ch, AHV 756.12", "34.5678.97 ok"]) == [
        "Kontakt: ",
        f"{E}, AHV ",
        "[REDACTED_AHV] ",
        "ok",
    ]


# --- Chunk independence on the named cases -------------------------------------------------


@pytest.mark.parametrize("text, expected", ALL_CASES)
def test_named_case_every_two_way_split(text, expected):
    for chunks in two_way_splits(text):
        assert streamed(chunks) == expected, chunks


@pytest.mark.parametrize("text, expected", [(t, e) for t, e in ALL_CASES if len(t) <= 40])
def test_named_case_every_three_way_split(text, expected):
    for chunks in three_way_splits(text):
        assert streamed(chunks) == expected, chunks


@pytest.mark.parametrize("text, expected", ALL_CASES)
def test_named_case_one_char_chunks(text, expected):
    assert streamed(list(text)) == expected


@pytest.mark.parametrize("text, budget, expected", BUDGET_CASES)
def test_budget_case_every_split(text, budget, expected):
    assert streamed(list(text), budget) == expected
    for chunks in two_way_splits(text):
        assert streamed(chunks, budget) == expected, chunks


# --- Chunk independence on generated input ----------------------------------------------------


@settings(max_examples=1500)
@given(text_and_chunks(), budgets)
def test_random_partition_matches_reference(text_chunks, budget):
    text, chunks = text_chunks
    assert streamed(chunks, budget) == redact(text, budget)


@settings(max_examples=1500)
@given(text_and_chunks(tricky_ascii_text), budgets)
def test_random_partition_matches_reference_ascii(text_chunks, budget):
    text, chunks = text_chunks
    assert streamed(chunks, budget) == redact(text, budget)


@given(mixed_text, budgets)
def test_one_char_chunks_match_reference(text, budget):
    assert streamed(list(text), budget) == redact(text, budget)


# --- No partial leak ------------------------------------------------------------------------


@settings(max_examples=1000)
@given(text_and_chunks(), budgets)
def test_output_so_far_is_always_a_prefix_of_the_final_output(text_chunks, budget):
    """If any part of a secret were forwarded early, it would not be a prefix of
    the fully redacted output."""
    text, chunks = text_chunks
    final = redact(text, budget)

    def check(_guard, outputs):
        assert final.startswith("".join(outputs))

    run(chunks, budget, check)


# --- Low latency -------------------------------------------------------------------------------


@given(mixed_text)
def test_nothing_is_held_back_after_a_space(text):
    """A space ends every possible candidate, so everything before it is decided:
    the output so far must be exactly the redaction of the prefix."""
    guard = StreamingGuard()
    out = ""
    for i, c in enumerate(text):
        out += guard.push(c)
        if c == " ":
            assert out == redact(text[: i + 1])
            assert guard.pending_chars == 0


@pytest.mark.parametrize(
    "chunk, forwarded",
    [
        ("hello ", "hello "),
        ("hello", ""),  # could still become a local part
        ("hi, ", "hi, "),
        ("a@b.ch ", f"{E} "),
        ("756.1234.5678.97 ", "[REDACTED_AHV] "),
        ("756.1234.5678.97", ""),  # an '@' or a digit could still follow
        ("a@b.ch", ""),  # the domain could still grow
        ("x: 7", "x: "),
    ],
)
def test_forwarded_immediately(chunk, forwarded):
    assert StreamingGuard().push(chunk) == forwarded


# --- Bounded buffers ------------------------------------------------------------------------------

ADVERSARIAL = {
    "longest email": "a" * 64 + "@" + "b" * 249 + ".com",
    "long local run": "a" * 5000,
    "long domain": "a@" + "b" * 5000,
    "growing domain": "a@b" + ".cc" * 2000,
    "many @": "@" * 3000,
    "a@ repeated": "a@" * 2000,
    "AHV prefixes": "756." * 2000,
    "AHVs in a run": (AHV + "x") * 300,
    "domain with AHVs": "a@x." + (AHV + ".") * 200 + "com",
    "chinese": "用户的电子邮件地址" * 300,
}


@pytest.mark.parametrize("name", ADVERSARIAL)
def test_pending_buffer_bound_on_adversarial_input(name):
    text = ADVERSARIAL[name]
    peak = 0

    def observe(guard, _):
        nonlocal peak
        check_invariants(guard)
        peak = max(peak, guard.pending_chars)

    outputs = run(list(text), after_push=observe)
    assert peak <= PENDING_BOUND
    assert "".join(outputs) == redact(text)


def test_pending_buffer_bound_is_tight():
    text = ADVERSARIAL["longest email"]
    peak = 0

    def observe(guard, _):
        nonlocal peak
        peak = max(peak, guard.pending_chars)

    assert "".join(run(list(text), after_push=observe)) == E
    assert peak == PENDING_BOUND


def check_invariants(guard, _outputs=None):
    """The invariants the design relies on (DESIGN.md, sections 3 and 4).

    They are proven there; checking them after every character turns each
    proof into an executable test.
    """
    email, ahv = guard._recognizers
    cursor = guard._cursor
    assert len(guard._pending) == guard._seen - cursor
    assert guard.pending_chars <= PENDING_BOUND
    assert guard._budget.reserved_chars <= RESERVE_BOUND
    if guard._budget.truncated:
        return  # processing stopped; the remaining state is never used again
    # Lemma 1: at most two live AHV candidates.
    assert len(ahv._live) <= 2
    # Lemma 2: nothing the AHV recogniser tracks starts before the cursor.
    assert all(start >= cursor for start, _ in ahv._live)
    assert all(start >= cursor for start in ahv._confirmed)
    # Lemma 3: a live email candidate never starts before the cursor or the floor.
    if email._candidate is not None:
        assert email._candidate.start >= max(cursor, email._floor)
    # Lemma 4: a confirmed email is committed in the same step.
    assert email._confirmed is None


@given(mixed_text, budgets)
def test_invariants_hold_after_every_char(text, budget):
    run(list(text), budget, check_invariants)


@settings(max_examples=1000)
@given(tricky_ascii_text, budgets)
def test_invariants_hold_after_every_char_ascii(text, budget):
    run(list(text), budget, check_invariants)


@pytest.mark.parametrize("text, expected", ALL_CASES)
def test_invariants_hold_on_named_cases(text, expected):
    run(list(text), after_push=check_invariants)


def test_output_reserve_bound_is_tight():
    # 14 chars returned, a 16-char token that does not fit in 40 - 11 = 29,
    # then 10 spaces (decided at once): the reserve holds exactly 26 and the
    # decided content is exactly 40.
    text = "x" * 13 + " a@b.cc" + " " * 10
    peak = 0

    def observe(guard, _):
        nonlocal peak
        peak = max(peak, guard._budget.reserved_chars)

    assert streamed(list(text), 40) == "x" * 13 + " " + E + " " * 10  # fits exactly
    run(list(text), 40, observe)
    assert peak == RESERVE_BOUND
    assert streamed(list(text + " "), 40) == "x" * 13 + " " + T  # one more char: truncated


# --- Linear work -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ADVERSARIAL)
def test_commit_loop_does_linear_work(name):
    """Every commit-loop iteration either consumes at least one char or ends the
    loop for this char, so there are at most 2 iterations per input char."""
    text = ADVERSARIAL[name]
    guard = StreamingGuard()
    calls = 0
    original = guard._decide

    def counting_decide():
        nonlocal calls
        calls += 1
        return original()

    guard._decide = counting_decide
    for c in text:
        guard.push(c)
    guard.finish()
    assert calls <= 2 * len(text) + 1


# --- Lifecycle -----------------------------------------------------------------------------------------


def test_finish_twice_returns_empty():
    guard = StreamingGuard()
    guard.push("ok")
    assert guard.finish() == "ok"
    assert guard.finish() == ""


def test_push_after_finish_raises():
    guard = StreamingGuard()
    guard.finish()
    with pytest.raises(RuntimeError):
        guard.push("x")


def test_empty_chunks_and_empty_stream():
    guard = StreamingGuard()
    assert guard.push("") == ""
    assert guard.finish() == ""


def test_input_after_truncation_is_ignored():
    guard = StreamingGuard(11)
    assert guard.push("hello world") == ""  # 11 chars: fits, but waits in the reserve
    assert guard.push("!") == T
    assert guard.push("more a@b.ch text") == ""
    assert guard.finish() == ""


def test_truncation_inside_finish():
    guard = StreamingGuard(20)
    # Only 20 - 11 = 9 chars are safe; " ab " waits in the reserve (13 <= 20).
    assert guard.push("123456789 ab a@b.cc") == "123456789"
    # The email is only decided at the end of the stream, and 13 + 16 > 20.
    assert guard.finish() == T


def test_zero_budget_returns_nothing():
    guard = StreamingGuard(0)
    assert guard.push("hello a@b.ch") == ""
    assert guard.finish() == ""


@pytest.mark.parametrize("budget", [-1, 1, 10])
def test_invalid_budget_raises(budget):
    with pytest.raises(ValueError):
        StreamingGuard(budget)


@pytest.mark.parametrize("chunk", [None, b"bytes", 42])
def test_non_string_chunk_raises(chunk):
    with pytest.raises(TypeError):
        StreamingGuard().push(chunk)


def test_fails_closed_after_internal_error():
    guard = StreamingGuard()
    guard.push("secret a@b.c")

    def boom(*_):
        raise MemoryError

    guard._recognizers[0].feed = boom
    with pytest.raises(MemoryError):
        guard.push("h")
    with pytest.raises(RuntimeError):
        guard.push("more")
    with pytest.raises(RuntimeError):
        guard.finish()


@given(st.lists(st.sampled_from(["a", "@", "b.ch", " ", AHV]), max_size=20))
def test_many_small_pushes_then_finish(chunks):
    assert streamed(chunks) == redact("".join(chunks))
