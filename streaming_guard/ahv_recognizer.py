"""Streaming recogniser for Swiss AHV numbers (756.XXXX.XXXX.XX).

The recogniser never stores text, only positions. It sees every character
exactly once.

A candidate is a pair (start, matched): `matched` symbols of the 16-symbol
template have been seen since `start`.

    no candidate --'7' with a non-digit (or nothing) before it--> (pos, 1)
    matched < 16 --char fits template[matched]------------------> matched + 1
    matched < 16 --anything else--------------------------------> dies
    matched = 16 --non-digit------------------------------------> confirmed
    matched = 16 --digit----------------------------------------> dies
    matched = 16 --end of stream--------------------------------> confirmed

At most two candidates are ever live: a second one can only start at a '7'
right after one of the first one's dots (offset 4, 9 or 14), and each of those
contradicts the first one's template within three characters.

Confirmed matches never overlap, so they wait in start order in a small queue
until the guard commits them (they may be held up behind an open email).
"""

from collections import deque

from .chars import AHV_LEN, AHV_TOKEN, ahv_symbol_matches, is_ascii_digit


class AhvRecognizer:
    TOKEN = AHV_TOKEN

    def __init__(self) -> None:
        self._live: list[tuple[int, int]] = []  # (start, matched), in start order
        self._confirmed: deque[int] = deque()  # starts of confirmed matches
        self._prev_is_digit = False

    def feed(self, pos: int, c: str) -> None:
        survivors: list[tuple[int, int]] = []
        for start, matched in self._live:
            if matched == AHV_LEN:
                # Complete: `c` is the character right after it.
                if not is_ascii_digit(c):
                    self._confirmed.append(start)
            elif ahv_symbol_matches(matched, c):
                survivors.append((start, matched + 1))
        if ahv_symbol_matches(0, c) and not self._prev_is_digit:
            survivors.append((pos, 1))
        self._live = survivors
        self._prev_is_digit = is_ascii_digit(c)

    def end_of_stream(self) -> None:
        for start, matched in self._live:
            if matched == AHV_LEN:
                self._confirmed.append(start)
        self._live = []

    def earliest_open(self) -> int | None:
        """Smallest start whose outcome is still undecided."""
        return self._live[0][0] if self._live else None

    def match_at(self, cursor: int) -> int | None:
        """End of the confirmed match starting at `cursor`, if there is one."""
        if self._confirmed and self._confirmed[0] == cursor:
            return cursor + AHV_LEN
        return None

    def discard_before(self, floor: int) -> None:
        """Everything before `floor` has been committed: drop confirmed matches
        that were committed or swallowed by an email.

        Live candidates never start before `floor`: an AHV contains no letters
        and every email ends in one, so an AHV starting inside a committed email
        lies entirely inside it and was resolved before the email was confirmed
        (DESIGN.md, lemma 2). The tests check this invariant after every char.
        """
        while self._confirmed and self._confirmed[0] < floor:
            self._confirmed.popleft()
