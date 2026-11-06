"""Named edge cases with hand-computed expected output.

Shared by the oracle tests (does the specification say what we think?) and the
streaming tests (does the guard match it under every chunking?).
"""

from streaming_guard.chars import AHV_TOKEN as A
from streaming_guard.chars import EMAIL_TOKEN as E
from streaming_guard.chars import TRUNCATED_TOKEN as T

AHV = "756.1234.5678.97"
PDF_TEXT = "Kontakt: max.muster@navique.ch, AHV 756.1234.5678.97 ok"
PDF_EXPECTED = f"Kontakt: {E}, AHV {A} ok"

AHV_CASES = [
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
]

EMAIL_CASES = [
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
]

LENGTH_LIMIT_CASES = [
    ("a" * 64 + "@x.ch", E),
    ("a" * 65 + "@x.ch", "a" + E),  # leftmost start that keeps the local part <= 64
    ("a" * 100 + "@x.ch", "a" * 36 + E),
    ("x@" + "a" * 249 + ".com ", E + " "),  # domain of exactly 253 chars
    ("x@" + "a" * 250 + ".com", E + "m"),  # 254: longest valid prefix ends in ".co"
    ("x@" + "a" * 251 + ".com", "x@" + "a" * 251 + ".com"),  # 255: no valid prefix
]

OVERLAP_CASES = [
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
]

ALL_CASES = (
    [(PDF_TEXT, PDF_EXPECTED), ("", "")]
    + AHV_CASES
    + EMAIL_CASES
    + LENGTH_LIMIT_CASES
    + OVERLAP_CASES
)

BUDGET_CASES = [
    ("hello world", None, "hello world"),
    ("hello world", 11, "hello world"),  # exact fit: no marker
    ("hello world!", 11, T),  # 1 char over: everything gives way to the marker
    ("abcdefghijklmnopqrst", 15, "abcd" + T),
    ("ab a@b.ch", 18, "ab " + T),  # token does not fit and is never cut
    ("ab a@b.ch", 19, "ab " + E),  # exact fit including the token
    ("", 0, ""),
    ("anything", 0, ""),  # zero budget: not even the marker
    # 40 - 11 = 29 chars of content: "Kontakt: " (9) + token (16) + ", AH" (4).
    (PDF_TEXT, 40, f"Kontakt: {E}, AH" + T),
]
