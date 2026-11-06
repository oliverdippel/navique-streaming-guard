"""Streaming recogniser for email addresses (grammar in chars.py).

The recogniser never stores text, only positions. It sees every character
exactly once and has two cooperating parts.

1. Local-run tracker. `run_start` is where the current run of local-part
   characters began. Any position in that run, within the last 64 characters,
   could still start an email. Nothing is created until an '@' arrives.

2. Domain candidate (at most one). When '@' arrives at position `at`, the
   candidate starts at the leftmost feasible position:

       start = max(run_start, floor, at - 64)

   and then follows the domain grammar:

       EXPECT_LABEL --label char--> IN_LABEL
       EXPECT_LABEL --anything else ('.' included: empty label)--> dies
       IN_LABEL     --label char--> IN_LABEL
       IN_LABEL     --'.'--> EXPECT_LABEL (has_dot = True)
       IN_LABEL     --anything else--> dies
       any state    --domain would exceed 253 chars--> dies

   After every step, if we are in a label after at least one dot and that label
   is so far made of >= 2 TLD characters, the domain is valid up to here:
   `last_accept = pos + 1`. When the candidate dies, the email is confirmed up
   to `last_accept` (longest valid domain), or silently dropped if there is none.

`floor` is the first position an email may start at. It is raised both by our
own confirmations (immediately, so that an '@' which ends one email and starts
the next cannot give the next one an empty local part: `a@b.cc@d.ee`) and by
the guard after it commits anything (so an email never starts inside an AHV
that was already forwarded as a token).

Domain characters are a subset of local-part characters, so the run tracker
keeps running during the domain phase: an email inside a failed domain
(`a@b@c.de`) is found without rescanning.
"""

from dataclasses import dataclass

from .chars import (
    AT,
    DOMAIN_MAX,
    DOT,
    EMAIL_TOKEN,
    LOCAL_MAX,
    is_label_char,
    is_local_char,
    is_tld_char,
)


@dataclass
class _Candidate:
    start: int  # first char of the local part
    at: int  # position of the '@'
    expect_label: bool = True  # just after '@' or '.'
    has_dot: bool = False
    label_len: int = 0
    label_is_tld: bool = False  # current label consists of TLD chars so far
    domain_len: int = 0
    last_accept: int | None = None  # end of the longest valid email so far


@dataclass
class _Match:
    start: int
    end: int


class EmailRecognizer:
    TOKEN = EMAIL_TOKEN

    def __init__(self) -> None:
        self._pos = -1  # last position fed
        self._floor = 0
        self._run_start: int | None = None
        self._candidate: _Candidate | None = None
        self._confirmed: _Match | None = None

    # --- input -----------------------------------------------------------------

    def feed(self, pos: int, c: str) -> None:
        self._pos = pos
        if self._candidate is not None and not self._advance_domain(pos, c):
            self._close_candidate()
        if c == AT:
            self._open_candidate(pos)
        if is_local_char(c):
            if self._run_start is None:
                self._run_start = pos
        else:
            self._run_start = None

    def end_of_stream(self) -> None:
        if self._candidate is not None:
            self._close_candidate()
        self._run_start = None

    # --- queries from the guard ------------------------------------------------

    def earliest_open(self) -> int | None:
        """Smallest start whose outcome is still undecided."""
        if self._candidate is not None:
            return self._candidate.start
        if self._run_start is not None:
            # A future '@' at position q > pos allows starts from q - 64 on.
            start = max(self._run_start, self._floor, self._pos + 1 - LOCAL_MAX)
            if start <= self._pos:
                return start
        return None

    def match_at(self, cursor: int) -> int | None:
        """End of the confirmed email starting at `cursor`, if there is one."""
        if self._confirmed is not None and self._confirmed.start == cursor:
            return self._confirmed.end
        return None

    def discard_before(self, floor: int) -> None:
        """Everything before `floor` has been committed: no email may start there.

        A live candidate never starts before `floor` (DESIGN.md, lemma 3: an AHV
        that a candidate could start inside is always committed before the
        candidate's '@' arrives), so nothing needs clipping; the tests check
        this invariant after every character.
        """
        self._floor = max(self._floor, floor)
        if self._confirmed is not None and self._confirmed.end <= floor:
            self._confirmed = None  # committed

    # --- internals -----------------------------------------------------------------

    def _open_candidate(self, at: int) -> None:
        if self._run_start is None:
            return  # nothing before the '@' that could be a local part
        start = max(self._run_start, self._floor, at - LOCAL_MAX)
        if start < at:
            self._candidate = _Candidate(start=start, at=at)

    def _advance_domain(self, pos: int, c: str) -> bool:
        """Feed one domain character. Returns False if the candidate dies."""
        cand = self._candidate
        cand.domain_len += 1
        if cand.domain_len > DOMAIN_MAX:
            return False
        if c == DOT:
            if cand.expect_label:
                return False  # empty label: "@." or ".."
            cand.expect_label = True
            cand.has_dot = True
            return True
        if not is_label_char(c):
            return False
        if cand.expect_label:
            cand.expect_label = False
            cand.label_len = 0
            cand.label_is_tld = True
        cand.label_len += 1
        cand.label_is_tld = cand.label_is_tld and is_tld_char(c)
        if cand.has_dot and cand.label_is_tld and cand.label_len >= 2:
            cand.last_accept = pos + 1
        return True

    def _close_candidate(self) -> None:
        cand = self._candidate
        self._candidate = None
        if cand.last_accept is None:
            return
        # Invariant (DESIGN.md, lemma 4): a confirmed email is always committed
        # in the same step, so the slot is free again by the next confirmation.
        assert self._confirmed is None, "previous email was not committed"
        self._confirmed = _Match(cand.start, cand.last_accept)
        # Raise the floor now, not when the guard commits: the character that
        # ended this email may be an '@' that opens the next candidate in this
        # same step, and its local part must not start inside this email.
        self._floor = max(self._floor, cand.last_accept)
