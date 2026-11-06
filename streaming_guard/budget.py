"""Output budget with a reserve for the [TRUNCATED] marker.

Pieces arrive in order and are atomic (a literal char or a whole token).

- Safe zone: while the piece still fits in `max - 11`, it is returned at once.
  Even if truncation happens later, the marker will still fit.
- Reserve: the first piece that does not fit, and every piece after it, is held
  back (order is preserved).
  - If the decided content ever exceeds `max`, the reserve is dropped and
    [TRUNCATED] is returned (nothing at all for a zero budget).
  - If the stream ends first, the reserve is flushed: everything fit.

The reserve never holds more than 11 + 16 - 1 = 26 chars (DESIGN.md, section 3).
"""

from .chars import TRUNCATED_TOKEN, validate_max_output_chars


class OutputBudget:
    def __init__(self, max_output_chars: int | None) -> None:
        validate_max_output_chars(max_output_chars)
        self._max = max_output_chars
        self._returned = 0  # chars handed to the caller so far
        self._reserve: list[str] = []
        self._reserved = 0  # total length of the reserve
        self.truncated = False

    @property
    def reserved_chars(self) -> int:
        return self._reserved

    def offer(self, piece: str) -> str:
        """Accept one decided piece; returns what may be forwarded right now."""
        if self.truncated:
            return ""
        if self._max is None:
            return piece
        safe_limit = self._max - len(TRUNCATED_TOKEN)
        if not self._reserve and self._returned + len(piece) <= safe_limit:
            self._returned += len(piece)
            return piece
        self._reserve.append(piece)
        self._reserved += len(piece)
        if self._returned + self._reserved <= self._max:
            return ""
        # The content cannot fit any more: end the stream.
        self.truncated = True
        self._reserve.clear()
        self._reserved = 0
        if self._max == 0:
            return ""  # the marker itself would break a zero budget
        self._returned += len(TRUNCATED_TOKEN)
        return TRUNCATED_TOKEN

    def flush(self) -> str:
        """End of stream without truncation: everything held back fits."""
        out = "".join(self._reserve)
        self._returned += self._reserved
        self._reserve.clear()
        self._reserved = 0
        return out
