# Streaming Guard

A streaming guardrail for LLM output. It removes Swiss AHV numbers and email
addresses from a response while it is being streamed, without waiting for the
full response.

```python
from streaming_guard import StreamingGuard

guard = StreamingGuard()
guard.push("Kontakt: max.mus")            # -> "Kontakt: "
guard.push("ter@navique.ch, AHV 756.12")  # -> "[REDACTED_EMAIL], AHV "
guard.push("34.5678.97 ok")               # -> "[REDACTED_AHV] "
guard.finish()                            # -> "ok"
```

Python 3.11+, no runtime dependencies.

```bash
python3 -m venv .venv && .venv/bin/pip install pytest hypothesis
.venv/bin/pytest
```

[DESIGN.md](DESIGN.md) has the full design: grammar, state transitions, the
lemmas the design relies on and the proofs of the buffer bounds. This README
summarises it.

## How it works

The output is defined by a **batch specification**
([`reference.py`](streaming_guard/reference.py)), and the streaming guard must
produce exactly the same output however the input is chunked:

```
p = 0
while p < len(text):
    if an email matches at p:  emit [REDACTED_EMAIL]; p = end of the longest email
    elif an AHV matches at p:  emit [REDACTED_AHV];   p = p + 16
    else:                      emit text[p];          p = p + 1
```

That is: leftmost match first; at the same start, email beats AHV (an AHV can be
the local part of an email: `756.1234.5678.97@navique.ch` is one email); an
email takes its longest valid domain.

The streaming guard is built from character-by-character state machines, not
regexes:

| Component | What it does |
|---|---|
| [`EmailRecognizer`](streaming_guard/email_recognizer.py) | Tracks the current run of local-part characters; when an `@` arrives, opens one domain candidate at the leftmost possible start and follows the domain grammar, remembering the longest valid end so far. |
| [`AhvRecognizer`](streaming_guard/ahv_recognizer.py) | Follows the 16-symbol template `756.dddd.dddd.dd`; at most two candidates are ever live. |
| [`StreamingGuard`](streaming_guard/guard.py) | Keeps the undecided characters and, after every character, forwards everything before the earliest position that could still be part of a match. |
| [`OutputBudget`](streaming_guard/budget.py) | Applies `max_output_chars`, reserving room for `[TRUNCATED]`. |

Key design decisions:

- **Recognisers never hold text, only positions.** Each character is fed once to
  every recogniser, and they run in parallel. When a candidate fails, nothing
  has to be re-scanned, because every overlapping possibility was already being
  tracked. This is what makes it O(n) rather than "O(n × buffer size)".
- **One small commit loop decides everything.** At the cursor, recognisers are
  asked in priority order (email, then AHV): a confirmed match is forwarded as a
  token; a match that might still start here makes the loop wait; otherwise the
  character is forwarded. Precedence is just the order of that list, so adding
  a pattern (e.g. IBAN) means adding one recogniser.
- **Chunk independence by construction.** Everything before the earliest open
  candidate is final, because future input can only extend open candidates or
  start new ones later. Chunk boundaries only change *when* the loop runs, never
  *what* it decides.
- **Over-redaction over leaking.** No boundary is required around emails
  (`a@b.ch1` → `[REDACTED_EMAIL]1`), and Unicode letters are allowed, so
  `müller@x.ch` does not leak `mü`.

## Assumptions

| Topic | Assumption |
|---|---|
| Email grammar | `local@domain`. Local part: 1–64 Unicode letters/numbers/combining marks or `. _ % + -`. Domain: labels of letters/numbers/marks or `-`, separated by dots, at least two labels, at most 253 chars; the last label has at least 2 letters or marks. Lengths are counted in code points. |
| Email matching | Longest valid domain prefix, no boundaries: `a@b.ch.` → `[REDACTED_EMAIL].` |
| AHV | `756.dddd.dddd.dd` with ASCII digits only, not preceded or followed by a digit. Letters may touch it (`AHV756.…` is redacted). The checksum is **not** validated: a number with a typo would otherwise leak. |
| `@` and `.` | ASCII only. Lookalikes (`＠`, `﹫`, `。`) are not recognised (see limits). |
| Budget | Counts characters returned to the caller, **including** redaction tokens and `[TRUNCATED]`, so output never exceeds `max_output_chars`. Tokens are never split. |
| Budget values | `None` = unlimited. `0` = nothing is returned, not even the marker. `1`–`10` and negative values raise `ValueError` (the 11-char marker cannot fit). |
| Lifecycle | `finish()` is idempotent. `push()` after `finish()` raises `RuntimeError`. `push()` after truncation returns `""`. A non-`str` chunk raises `TypeError`. |
| Errors | Fail closed: after an internal error the guard refuses all further calls. |

## Latency

The guard holds back only text that could still become part of a match:

- a word that could still become the local part of an email (`ok` is released
  only by `finish()`, as in the challenge's example);
- an `@` and the domain after it until the domain cannot grow any more;
- an AHV prefix, and a complete AHV until the next character shows it is not
  followed by a digit. An AHV is also made of local-part characters, so, like
  any word, it is held while it could still turn out to be the start of an
  email.

After any space, every candidate is resolved, so everything up to that point
has been forwarded. The tests check this for generated inputs.

One cost of Unicode support: text in scripts written without spaces (Chinese,
Japanese) forms long local-part runs, so up to 64 characters lag behind until
punctuation ends the run.

## Complexity

| | Bound | Why |
|---|---|---|
| Time | **O(n)** | Each character is appended once, fed once to each recogniser (constant work: at most 2 AHV candidates, 1 email candidate) and forwarded once. Each commit-loop iteration either forwards something or ends the loop for that character: at most 2 iterations per character, which a test counts. |
| Pending text | **≤ 318 chars** | The worst case is an email still in progress: 64 (local part) + 1 (`@`) + 253 (domain). The bound is tight; a test reaches it exactly. |
| Budget reserve | **≤ 26 chars** | 11 (`[TRUNCATED]`) + 16 (`[REDACTED_EMAIL]`) − 1. Also reached exactly by a test. |
| Auxiliary memory | **O(1)** in the stream length | The two bounds above plus a few counters. Each `push` returns a string of size O(chunk). |

Measured on Python 3.13: 4× the input takes about 4× the time, on prose and on
adversarial inputs (long words, `756.756.…`, `a@a@…`, long domains), at about
1.1–1.8 µs per character with 1- or 64-character chunks.

## Tests

425 tests (`pytest`, `hypothesis`), about 15 seconds.

- **The oracle is tested on its own first** (`test_reference.py`): named edge
  cases with hand-computed outputs, and a cross-check against a second,
  independent oracle written as a single regex.
- **Chunk independence** (`test_guard.py`): every named case under every
  2-way split, every 3-way split and one-character chunks; random partitions of
  generated text (Unicode noise, planted emails and AHVs, tricky ASCII), with
  and without a budget. The streamed result must equal the oracle.
- **No partial leak**: after every `push`, the output so far must be a prefix of
  the final redacted output. Forwarding any part of a secret early would break
  this.
- **Latency**: after a space, the output so far must equal the oracle for that
  prefix, with nothing pending.
- **Bounds and invariants**: the 318 and 26 bounds on adversarial inputs, and the
  four lemmas from DESIGN.md checked after every character.
- **Linear work**, **lifecycle** and **budget** edge cases.

To check that the tests can fail, I planted bugs in both the oracle and the
guard (wrong priority, missing boundary checks, off-by-one limits, a budget
without the reserve, …). Every one made at least one test fail.

## Project layout

```
streaming_guard/
  chars.py              grammar: character classes, limits, tokens
  reference.py          batch specification / test oracle
  email_recognizer.py   streaming email state machine
  ahv_recognizer.py     streaming AHV state machine
  budget.py             output budget with [TRUNCATED] reserve
  guard.py              StreamingGuard
tests/
  cases.py              named edge cases shared by all tests
  strategies.py         Hypothesis input generators
  test_chars.py, test_reference.py, test_guard.py
DESIGN.md               full design, lemmas and bound proofs
```

## Use of AI tools

I used **Claude Code** (Anthropic's coding agent, Claude Opus 5.5) throughout,
as a pair programmer, in this order:

1. **Design before code.** I asked for a design only, then reviewed it over
   several rounds. I chose character-level state machines over regex + bounded
   buffering, and made the policy decisions: Unicode support, counting
   `[TRUNCATED]` towards the budget, the zero budget, accepting the latency for
   scripts without spaces.
2. **Adversarial review.** I asked it to challenge its own design. That found a
   real bug before any code existed: an `@` that ends one email and starts the
   next (`a@b.cc@d.ee`) would have produced an email with an empty local part.
3. **Oracle first.** The batch reference and its tests were written and
   checked before any streaming code.
4. **Streaming code, then trying to break the tests.** Planting bugs showed two
   pieces of defensive code were unreachable; they were removed and the
   reasoning was turned into invariants that the tests check.

What I relied on the tools for: drafting code and tests, enumerating edge
cases, and the proofs of the bounds. What I relied on checks for, not the
tool's word: two independent oracles, property-based tests against them, and
the planted-bug check. Three of the hand-written test expectations were wrong
during development; each time the oracle and the guard agreed, and the
expectation was corrected.

## Integrating into an LLM gateway (optional part)

**Where it sits.** In the gateway's streaming proxy, between the model provider
and anything downstream (client, logs, tool runners, caches):

```
provider stream ──► parse SSE events ──► StreamingGuard per text stream ──► re-emit events ──► client
                                                     │
                                                     └──► redaction counts (never the values) ──► metrics / audit
```

- **One guard per text stream.** One per response, and one per `choice` /
  content block if the API returns several in parallel. Tool-call arguments are
  a separate stream and need their own guard.
- **Only guard text deltas.** Parse the provider's events, run the text through
  `push()`, and re-emit the event with the guarded text. Skip events whose text
  became empty (the guard is holding it back). On the provider's end-of-stream
  event, call `finish()` and emit the rest before the final event.
- **Cancellation and errors.** If the client disconnects, drop the guard: held
  text is simply never sent. If the provider fails mid-stream, `finish()` is
  still safe to call, since it only releases text that is redacted. If the
  guard itself raises, end the stream with an error event: fail closed, never
  fall back to forwarding raw text.
- **Configuration per tenant.** Which recognisers run and the output budget
  come from the tenant's policy. `max_output_chars` is in characters; if
  policies are in tokens, convert or add a token-based budget.
- **Observability.** Count redactions per type, per tenant and model; log
  positions and types, never the redacted values.
- **Performance.** ~1.5 µs per character in pure Python is far below the rate
  at which a model produces text (hundreds of characters per second per
  stream), and memory per stream is constant (≤ ~350 chars). At very high
  concurrency the per-character Python loop would be the cost to optimise
  (Rust/C extension, or a compiled DFA library).

**Where this approach reaches its limits.**

- **Pattern-based only.** It finds what matches a format. It does not find
  names, addresses, health data or anything that needs context or semantics.
  That needs NER or an LLM-based classifier, which need far more lookahead than
  a few hundred characters, so the "hold back only what could still match"
  guarantee would turn into "hold back a sentence or a paragraph".
- **Easy to evade by formatting.** `max at navique dot ch`, `756 1234 5678 97`,
  line breaks or Markdown inside an address (`max**@**navique.ch`), lookalike
  characters (`＠`), HTML entities, JSON escapes (`@`), Base64, or data
  split across tool-call arguments. The model can produce any of these by
  accident or under prompt injection. Some can be handled with more
  recognisers or a normalisation step in front of the guard; obfuscation in
  general cannot.
- **Output only.** Sensitive data in the prompt, in retrieved documents or in
  tool results needs its own guard on those paths.
- **Holdback grows with the patterns.** Each new pattern's maximum length adds to
  the worst-case holdback. Fine for IBANs (34 chars); patterns without a length
  limit would break the latency guarantee.
- **Forwarded text cannot be taken back.** Anything that decides later than the
  guard (a classifier on the full response, a human review) can block the rest
  of the response, but not undo what was already streamed.

## What I would do next

- Emit `[REDACTED_EMAIL]` as soon as the address is known to be an email, instead
  of waiting for the domain to end (same output, lower latency).
- Map a small fixed set of lookalike characters (`＠`, `﹫`, `。`, `．`) to `@` / `.`.
- Add recognisers that Swiss regulated customers would expect next (IBAN,
  phone numbers), each with its own oracle cases.
- Run the property tests longer in CI, and add a benchmark job to catch
  performance regressions.
