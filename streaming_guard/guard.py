"""StreamingGuard: redacts AHV numbers and emails from a stream of text chunks.

Each character is fed once to every recogniser. The guard keeps the
characters that are not yet decided in `_pending` (from `_cursor` up to the
last character seen) and, after every character, runs the commit loop:

    at the cursor, for each recogniser in priority order (email, then AHV):
        it has a confirmed match starting here  -> forward its token, jump to its end
        it may still start a match here or before -> stop and wait for more input
    no recogniser claims the cursor             -> forward the character

Everything before the earliest still-open candidate is final: future input can
only extend open candidates or start new ones later. That is why the output
does not depend on how the input is chunked.
"""

from collections import deque

from .ahv_recognizer import AhvRecognizer
from .budget import OutputBudget
from .email_recognizer import EmailRecognizer

_ACTIVE, _FINISHED, _FAILED = "active", "finished", "failed"


class StreamingGuard:
    def __init__(self, max_output_chars: int | None = None) -> None:
        self._budget = OutputBudget(max_output_chars)
        # Priority order matters: at the same start, an email beats an AHV.
        self._recognizers = (EmailRecognizer(), AhvRecognizer())
        self._pending: deque[str] = deque()
        self._cursor = 0  # stream position of _pending[0]
        self._seen = 0  # number of characters fed so far
        self._state = _ACTIVE

    @property
    def pending_chars(self) -> int:
        """Characters held back because they might still belong to a match."""
        return len(self._pending)

    def push(self, chunk: str) -> str:
        """Feed a chunk; returns the text that is safe to forward now."""
        if not isinstance(chunk, str):
            raise TypeError("chunk must be a str")
        self._check_usable()
        if self._budget.truncated:
            return ""  # the stream already ended with [TRUNCATED]
        out: list[str] = []
        try:
            for c in chunk:
                self._pending.append(c)
                for recognizer in self._recognizers:
                    recognizer.feed(self._seen, c)
                self._seen += 1
                self._commit(out)
                if self._budget.truncated:
                    break  # ignore the rest of the input
        except BaseException:
            self._state = _FAILED  # fail closed: never forward anything again
            raise
        return "".join(out)

    def finish(self) -> str:
        """End of stream: returns the rest of the (redacted) text."""
        if self._state == _FINISHED:
            return ""
        self._check_usable()
        self._state = _FINISHED
        if self._budget.truncated:
            return ""
        out: list[str] = []
        try:
            for recognizer in self._recognizers:
                recognizer.end_of_stream()
            self._commit(out)
            if not self._budget.truncated:
                assert not self._pending, "undecided text after end of stream"
                out.append(self._budget.flush())
        except BaseException:
            self._state = _FAILED
            raise
        return "".join(out)

    # --- internals -------------------------------------------------------------------

    def _check_usable(self) -> None:
        if self._state == _FINISHED:
            raise RuntimeError("push() called after finish()")
        if self._state == _FAILED:
            raise RuntimeError("guard failed earlier; refusing to continue")

    def _commit(self, out: list[str]) -> None:
        """Forward everything that is decided, in order."""
        while self._cursor < self._seen and not self._budget.truncated:
            decision = self._decide()
            if decision is None:
                return  # the cursor might still be part of a match
            piece, end = decision
            for _ in range(end - self._cursor):
                self._pending.popleft()
            self._cursor = end
            for recognizer in self._recognizers:
                recognizer.discard_before(end)
            out.append(self._budget.offer(piece))

    def _decide(self) -> tuple[str, int] | None:
        """(piece, end) for the cursor position, or None to wait."""
        for recognizer in self._recognizers:
            end = recognizer.match_at(self._cursor)
            if end is not None:
                return recognizer.TOKEN, end
            start = recognizer.earliest_open()
            if start is not None and start <= self._cursor:
                return None
        return self._pending[0], self._cursor + 1
