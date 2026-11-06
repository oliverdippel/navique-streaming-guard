"""Hypothesis input generators shared by the oracle and streaming tests."""

from hypothesis import strategies as st

from .cases import AHV

# ASCII text biased towards the characters and fragments that matter.
FRAGMENTS = [AHV, "756.", "1234.", "a@b.ch", "@", ".", "a" * 30, "x.y", "..", "@@"]
ASCII_CHARS = list("756.@ab1-_+% xZ")

tricky_ascii_text = st.lists(
    st.one_of(st.sampled_from(FRAGMENTS), st.sampled_from(ASCII_CHARS)), max_size=30
).map("".join)

# Arbitrary Unicode, including surrogates and control characters.
noise = st.text(max_size=40)

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

# Mixed text: tricky ASCII, planted secrets and Unicode noise.
mixed_text = st.lists(
    st.one_of(tricky_ascii_text, emails, ahvs, noise, st.sampled_from([" ", "@", "."])),
    max_size=6,
).map("".join)

budgets = st.one_of(st.none(), st.just(0), st.integers(min_value=11, max_value=80))


@st.composite
def text_and_chunks(draw, text_strategy=mixed_text):
    """A text and a random partition of it into chunks (empty chunks included)."""
    text = draw(text_strategy)
    cuts = sorted(draw(st.lists(st.integers(0, len(text)), max_size=12)))
    bounds = [0, *cuts, len(text)]
    chunks = [text[a:b] for a, b in zip(bounds, bounds[1:])]
    return text, chunks
