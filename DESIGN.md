# StreamingGuard: design

Status: implemented. Stage 1: grammar + batch oracle. Stage 2: streaming
recognisers, budget and guard.

## 1. Specification

The streaming guard must produce exactly what the batch reference
(`streaming_guard/reference.py`) produces for the full text, whatever the chunking:

```
p = 0
while p < len(text):
    if an email matches at p:  emit [REDACTED_EMAIL]; p = end of the longest email
    elif an AHV matches at p:  emit [REDACTED_AHV];   p = p + 16
    else:                      emit text[p];          p = p + 1
```

Leftmost-first; at the same start, email beats AHV; an email takes its longest
valid domain.

### Grammar (`streaming_guard/chars.py`)

| Element | Rule |
|---|---|
| local part | 1–64 chars; Unicode letter/number/combining mark, or one of `. _ % + -` |
| domain | `label ("." label)+`, at most 253 chars |
| label | 1+ chars; Unicode letter/number/combining mark, or `-` |
| TLD (last label) | 2+ Unicode letters or combining marks |
| email boundaries | none; the longest valid domain *prefix* wins (`a@b.ch1` → `[REDACTED_EMAIL]1`) |
| AHV | `756.dddd.dddd.dd`, ASCII digits only |
| AHV boundaries | not preceded and not followed by an ASCII digit; letters may touch it |
| AHV checksum | not validated (a typo'd number would otherwise leak) |
| `@` and `.` | ASCII only |

Lengths are counted in code points.

### Budget

| `max_output_chars` | Behaviour |
|---|---|
| `None` | unlimited |
| `0` | nothing is ever returned, not even `[TRUNCATED]` |
| `1`–`10` | `ValueError` (the 11-char marker cannot fit) |
| `< 0` | `ValueError` |
| `≥ 11` | total output, marker included, never exceeds the budget |

When the content does not fit, the output is the longest prefix of whole pieces
(literal chars or tokens) that fits in `max − 11`, followed by `[TRUNCATED]`.
Redaction tokens are never split.

### Lifecycle

`finish()` is idempotent (second call returns `""`). `push()` after `finish()`
raises `RuntimeError`. `push()` after truncation returns `""`.

### Known limitations

- Lookalike punctuation is not recognised: `max＠navique.ch` (fullwidth `＠`),
  `﹫`, ideographic dots `。` `．`. Extension: map a small fixed set to `@` / `.`.
- No Unicode normalisation (it cannot be done chunk-safely without extra
  buffering); combining marks are accepted directly instead.
- Character categories come from Python's `unicodedata`, whose version follows
  the Python version.
- Scripts written without spaces (Chinese, Japanese) form long local-part runs,
  so up to 64 chars are held back until punctuation ends the run.
- Domains longer than 253 chars only match their longest valid prefix within
  253 chars; if none exists the address is not redacted.

## 2. Streaming architecture

| File | Role |
|---|---|
| `guard.py` | `StreamingGuard`: pending buffer, cursor, commit loop, lifecycle |
| `email_recognizer.py` | local-run tracker + at most one domain candidate |
| `ahv_recognizer.py` | at most two live candidates + a small queue of confirmed matches |
| `budget.py` | `OutputBudget`: safe zone, reserve, `[TRUNCATED]` |

- Recognisers see every character exactly once and only track positions, never
  text. Each has `feed`, `end_of_stream`, `earliest_open` (the smallest start
  whose outcome is still undecided), `match_at(cursor)` and `discard_before(floor)`.
- Commit loop, after every character, recognisers in priority order
  `[email, ahv]`: a confirmed match at the cursor is committed; an open
  candidate at or before the cursor blocks; otherwise the char is a literal.
  After each commit the guard calls `discard_before(new cursor)` on both.
- The email recogniser raises its own `floor` the moment it confirms a match,
  so an `@` that ends one email and starts the next cannot give the next one an
  empty local part (`a@b.cc@d.ee`). The guard raises it after every commit, so
  no email starts inside something already forwarded.
- Fail closed: if anything raises inside `push`/`finish`, the guard refuses all
  further calls.

### Lemmas the design relies on

Each is checked after every character by `check_invariants` in the tests.

1. **At most two live AHV candidates.** A second start must be a `7` right after
   one of the first candidate's dots (offset 4, 9 or 14); each offset
   contradicts the first template within three chars, and no three offsets fit.
2. **Nothing the AHV recogniser tracks starts before the cursor.** An AHV holds
   only ASCII digits and dots; every email ends in a letter or mark. So an AHV
   starting inside a committed email lies entirely inside it and was resolved
   before the email was confirmed. `discard_before` only drops stale confirmed
   matches.
3. **A live email candidate never starts before the cursor.** Suppose an AHV at
   `c` is committed while a candidate starting at `s` inside it (`c < s < c+16`)
   is live. All chars from `c` to `s` are local-part chars, so the run started at
   or before `c`, and `s > c` can only come from the 64-char limit:
   `s = at − 64 > c`. But then, one step before the `@`, the email's earliest
   open start (`at − 64`) was already past `c`, and nothing else was open at or
   before `c`, so the AHV had already been committed and the floor was already
   `≥ c + 16` when the candidate was created. Contradiction. So no clipping is
   ever needed (an earlier draft had clipping code; it was unreachable and was
   removed).
4. **A confirmed email is committed in the same step.** A live AHV before the
   email's start would need every char since its start to be a digit or dot,
   but the email's `@` comes after that start and ends the AHV first; a
   confirmed AHV before it was released when the email's start was fixed past
   it. So one slot for the confirmed email is enough.

## 3. Bounds, and why they hold

### Pending buffer ≤ 318 characters

Measured after each character has been fed and the commit loop has run.

The commit loop only stops at a position where some recogniser still has an
open candidate. A confirmed AHV waits only behind an open email, and is
therefore *inside* the buffer, not extending it. So:

```
pending = pos + 1 − cursor ≤ pos + 1 − min(open candidate starts)
```

Each kind of open candidate is bounded:

1. **AHV candidate.** It has matched `i ≤ 16` chars ending at `pos`, so its start
   is at least `pos − 15`. At most **16** chars.
2. **Email local run, no `@` yet.** The earliest open start is
   `max(run_start, floor, pos + 1 − 64)`: a future `@` at `q > pos` allows a
   local part starting no earlier than `q − 64`. At most **64** chars.
3. **Email domain candidate.** Its start is at least `at − 64` (64-char local
   limit), and the domain dies on the character that would make it 254 chars
   long, so `pos − at ≤ 253` for a live candidate. Hence
   `pending ≤ pos − (at − 64) + 1 ≤ 253 + 64 + 1 = `**318**.
   When the candidate dies, its match (if any) is committed in the same step, and
   what is left behind is covered by case 2.

The bound is tight: `"a" * 64 + "@" + "b" * 249 + ".com"` holds exactly
64 + 1 + 253 = 318 chars after the last `m`. Inside a single step, just before
the commit loop runs, the buffer can momentarily hold one more character (319).

No input can grow the buffer further: long runs without `@` are capped by the
64-char window, long domains by the 253-char limit, and repeated `@`s each kill
the previous candidate before starting a new one.

### Output reserve ≤ 26 characters

Let `B` be the budget, `M = 11` the marker length, `E` the chars already
returned and `R` the chars held in the reserve.

- A piece is returned immediately while `E + len(piece) ≤ B − M`.
- The first piece `p₁` that does not fit goes into the reserve, and so does
  every later piece, so order is preserved; `E` no longer grows.
- Because `p₁` did not fit, `E ≥ B − M − len(p₁) + 1`.
- The reserve only exists while total decided content `E + R ≤ B`; the moment a
  piece pushes it over `B`, the guard returns `[TRUNCATED]` and drops the reserve.

So `R ≤ B − E ≤ M + len(p₁) − 1 ≤ 11 + 16 − 1 = `**26**, since the longest
piece is `[REDACTED_EMAIL]` (16 chars).

Tight example: `B = 40`, 14 literal chars are returned, then a 16-char email
token (14 + 16 > 29, so reserved), then 10 more literal chars: `R = 26`, `E + R = 40`.
One more char triggers truncation: output is the 14 chars plus `[TRUNCATED]` = 25 ≤ 40.

The output is never longer than the budget:
- before truncation, `E ≤ B − M`;
- with truncation, `E + M ≤ B`;
- without truncation, `E + R ≤ B` when the reserve is flushed at `finish()`.

Latency cost: none until the output is within ~11 chars of the budget; after
that, at most 26 decided chars wait until `finish()` or truncation.

With `B = 0`, every piece overflows at once; truncation is silent (no marker).

## 4. Testing

Named cases live in `tests/cases.py`; Hypothesis strategies in
`tests/strategies.py`.

Oracle (`tests/test_reference.py`, `tests/test_chars.py`):
- named edge cases with hand-computed outputs (AHV, email, overlap, budget);
- a second, independent oracle (one Python regex, ASCII inputs) that must agree
  with the reference;
- property tests: planted secrets are always redacted; budget invariants
  (length ≤ budget, prefix of the unbudgeted output, whole pieces only).

Streaming guard (`tests/test_guard.py`):
- chunk independence: every named case under every 2-way split, every 3-way
  split (inputs ≤ 40 chars) and one-char chunks; random partitions of generated
  text, with and without a budget, compared with the reference;
- no partial leak: after every push, the output so far is a prefix of the final
  reference output;
- latency: after a space, the output so far equals the reference for the
  prefix and nothing is pending;
- bounds: pending ≤ 318 and reserve ≤ 26 on adversarial inputs, with
  inputs that reach both bounds exactly;
- lemmas 1–4 checked after every character;
- linear work: at most two commit-loop iterations per input char (counted);
- lifecycle: idempotent `finish`, `push` after `finish`, after truncation,
  truncation decided inside `finish`, zero budget, bad input, fail-closed.

Both stages were also checked by planting bugs (wrong priority, missing
boundary checks, off-by-one limits, missing floor, budget without reserve, …):
every planted bug made at least one test fail.

Measured (Python 3.13, one-char and 64-char pushes): 4× the input takes about
4× the time on prose and on adversarial inputs, ~1.1–1.8 µs per char.
