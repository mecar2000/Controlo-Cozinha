---
name: review-implementation
description: Walk the codebase file-by-file in implementation order, explaining what each file is for, where it sits, and the design choices made — pausing for questions and recording decisions. Use when the user wants to review, understand, or be walked through existing code; when they ask "what's next to review", "explain this file", "what haven't we covered yet", or "where were we"; when code has changed and they want the change explained against the architecture ("explain what changed", "walk me through this diff"); or when a review discussion produces a decision to apply or defer.
---

# Review Implementation

Development on this project is **paused** for a thorough walkthrough of every file
before new work continues. This skill runs that walkthrough, and keeps running it
as the code changes.

The authoritative state lives in `docs/REVIEW-PROTOCOL.md` — the review order, the
progress table, the in-progress note, and the decisions log. **Read it first, every
time.** It is the source of truth; this skill is the procedure that operates on it.

The shape of every explanation lives in
`references/explanation-template.md`. Read it before Mode 1 or Mode 2.

## Modes

Pick based on what the user asked for.

| The user says | Mode |
|---|---|
| "next", "continue", "review X", nothing in particular | **1 — Review a file** |
| "explain what changed", "walk me through this diff", or a reviewed file has been edited | **2 — Review a change** |
| "what's left", "where are we", "what's the order" | **3 — Report status** |
| "let's change that", "you're right, fix it", "leave it" — during or after a review | **4 — Record a decision** |
| "I added X", or new unreviewed files exist | **5 — Re-derive the order** |

**Always start by reading `docs/REVIEW-PROTOCOL.md`.** If its **In progress**
section is non-empty, say where we left off and what was still open before doing
anything else — the user may have been away for days.

---

## Mode 1: Review a file

The default. One file per invocation — never batch, never run ahead.

1. Read `docs/REVIEW-PROTOCOL.md` and take the first `⬜` row, unless the user
   named a specific file.
2. Read that file **in full**. Also read anything it includes that has already
   been reviewed, enough to speak accurately about how they connect — but do not
   re-explain reviewed ground beyond what this file needs.
3. Produce the explanation, following `references/explanation-template.md`.
   Pause at section boundaries for questions rather than emitting it all at once.
4. Stop. Wait for the user. Do not move to the next file unprompted.
5. Before ending a turn mid-file, write the **In progress** note: the file, how
   far you got, and any question left hanging.
6. When the user signs off, flip that row to `✅ reviewed`, clear the
   **In progress** note, and mark the next `⬜ next`.

**Done when:** the user has accepted the explanation and the table reflects it,
or the turn ended with an accurate In progress note.

---

## Mode 2: Review a change

For code that has changed since it was reviewed, and for new files the user wants
explained now rather than in backlog order.

1. Establish what changed. This project is not a git repository, so do not reach
   for `git diff` — ask the user what they changed, or compare against what the
   protocol doc records about the file if that is enough.
2. Read the changed file in full, plus anything it now includes that it did not
   before.
3. Explain it using the **"Narrowing for a change review"** section of
   `references/explanation-template.md` — what the change is for, what it means
   architecturally, what decisions it embeds, and what it leaves untouched.
4. Check the decisions log. If the change contradicts a logged decision, say so
   explicitly and ask whether the decision is being reversed.
5. If the file was already `✅ reviewed`, mark it `🔄 re-reviewed <date>` rather
   than inventing a new row. If it was never in the table, this is new code — run
   Mode 5 to place it, then review it here.

**Done when:** the user understands the change's blast radius, and any conflict
with a logged decision has been raised.

---

## Mode 3: Report status

Read the protocol doc and report: what's reviewed, what's next, what remains,
whether any files exist on disk that the table doesn't list, and any open
decisions awaiting the user's word. Lead with the **In progress** note if there
is one. Keep it short — a table and a sentence.

---

## Mode 4: Record a decision

Run this when a review discussion reaches a conclusion — the user agrees a flag
is real, rejects it, or decides on a different approach.

Append a row to the **Decisions** table in `docs/REVIEW-PROTOCOL.md`:

| Date | File | Decision | Rationale | Status |

`Status` is one of:

- **`open`** — agreed it needs doing, not done yet. Nothing has been edited.
- **`applied <date>`** — the edit has been made.
- **`declined`** — considered and deliberately not doing it. Record these too;
  they stop the same flag being raised again next session.

**Do not edit code in this mode.** Logging a decision is not permission to apply
it. Apply an `open` decision only when the user explicitly says to in that moment
— then flip the row to `applied` and say what you changed.

When resuming a session, surface `open` decisions in the status report so
accepted-but-unapplied work cannot quietly vanish.

**Done when:** the decision is in the table with a rationale, and no code changed
unless the user asked in that turn.

---

## Mode 5: Re-derive the order

Run this when new code has been written, when files are missing from the table, or
when the user says the order is wrong.

**The ordering rule: implementation order, which means the `#include` dependency
graph bottom-up.** A file is reviewed only after everything it includes has been
reviewed, so each explanation can build on ground already covered.

**Never order by file mtime.** Timestamps reflect last edit, not authoring order,
and have already misled once on this project.

To derive it:

1. Enumerate source files (`kitchen/`, `centralPLC/`, and any new directories).
2. Grep the include graph:
   `Grep(pattern: "^\\s*#include", path: "<dir>", output_mode: "content", -n: true)`
3. Assign layers. L0 = system headers only. L(n) = includes something at L(n-1).
4. Within a layer, order by dependents: a header other files depend on comes
   before a leaf nothing includes. Headers before their own `.cpp`. Tests last —
   they sit above everything they exercise.
5. If two files genuinely tie, **say so and ask** rather than silently picking.

Then update the progress table in `docs/REVIEW-PROTOCOL.md`: insert the new files
at their correct positions, preserve every existing `✅`, and mark the first
unreviewed row `⬜ next`. Tell the user what moved and why.

**Done when:** every file on disk has a row, no `✅` was lost, and the user has
been told what moved.

### Newly developed, not yet explored

Any file present on disk but absent from the table is **new code awaiting review**.
Never treat it as reviewed because it is recent or because you wrote it. Slot it
into the dependency order and call it out explicitly to the user, so it is clear
the backlog grew.

---

## Keeping state honest

- `docs/REVIEW-PROTOCOL.md` is the record. Update it as part of the review, not
  as an afterthought.
- A row goes `✅` only after the user has actually seen and accepted the
  explanation — never on the strength of having read the file.
- Never end a turn mid-file without writing the **In progress** note. A lost
  thread costs the user the whole file again.
- An `open` decision is a promise. It stays visible until applied or declined.
- If the doc is missing, rebuild it from Mode 5 and tell the user.
