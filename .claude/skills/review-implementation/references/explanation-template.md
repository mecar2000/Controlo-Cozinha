# The explanation template

What every walkthrough must deliver. Used by **Mode 1 (review a file)** in full,
and by **Mode 2 (review a change)** narrowed to what the change touches.

Three sections, in this order. Prose over bullet-salad — this is a walkthrough,
not a checklist. Depth over breadth: the user wants to genuinely understand this
code, so err toward explaining more.

## 1. What it is for

The file's responsibility, in a paragraph. What problem it exists to solve, and
what would break or become awkward if it didn't exist. Name the single idea the
file is organized around.

## 2. Where it sits

Its place in the architecture:

- What includes it; what it includes. Which dependency layer it occupies.
- When its code runs — compile time, `setup()`, the main loop, an ISR, or the
  host-side test binary.
- Which side of the kitchen / centralPLC split it belongs to, and why that split
  puts it there.
- What talks to it and through what surface (function calls, shared structs,
  registers, serial frames).

## 3. The choices made

The design decisions embedded in the code, and the reasoning behind each:

- Data structures and layout — why this shape, what the alternatives cost.
- Constants: their **units**, where the numbers came from, what depends on them.
- `constexpr` / `static` / runtime — what is fixed at build time and why.
- Ownership, lifetime, and mutability. Who is allowed to write what.
- Error and fault handling: what fails loud, what fails safe, what is ignored.
- What was deliberately left out, and what a plausible different choice would be.

## Pacing

Long files are not one wall of text. Break the explanation where the file breaks
— a struct, a function, a related cluster — and pause at each boundary to invite
questions before continuing. The user is reviewing, not reading a report.

When you pause mid-file, that is an open thread: record it under **In progress**
in `docs/REVIEW-PROTOCOL.md` before you stop.

## Notes, not edits

Flag anything wrong, inconsistent, unfinished, or suspicious — but **as a note
for the user's decision**. Do not edit during a review unless the user
explicitly asks in that moment. Collect these under a short "Notes / things I'd
flag" heading at the end.

A note the user engages with becomes a decision — see **Mode 4** in `SKILL.md`.
Notes the user passes over stay notes; do not carry them forward or re-raise
them unprompted.

## Narrowing for a change review

In Mode 2 the file is usually already reviewed, so do not re-explain it from
scratch. Instead:

- **Section 1** becomes: what the change is for — the problem it solves, and why
  it landed here rather than elsewhere.
- **Section 2** becomes: what the change means for the architecture — new
  dependencies, a shifted layer, a new caller, a widened interface. Say
  explicitly when the answer is "nothing structural moved."
- **Section 3** is unchanged in spirit but scoped to decisions the change
  embeds, including any it silently overturns from the original review.

Always name what the change does **not** touch, so the user knows the blast
radius rather than having to infer it.
