# Review Protocol — full-repo walkthrough before further development

**Status: ACTIVE. Started 2026-09-04.**

Run it with the `/review-implementation` skill
(`.claude/skills/review-implementation/SKILL.md`), which holds the procedure.
**This document holds the state** — the order, the progress table, the in-progress
note, and the decisions log below are the source of truth, and the skill reads and
updates them.

## The rule

Development is **paused**. Before any new feature work, refactor, or file is added,
every existing file gets a thorough, one-at-a-time review with the user.

Do not skip ahead. Do not batch. Do not "quickly fix while we're here" unless the
user says so during that file's review.

## Order

Files are reviewed **in order of implementation**, which follows the dependency
graph from the bottom up — not alphabetically, not by directory listing, and not by
file timestamp. A file is reviewed only after everything it includes has been
reviewed, so each explanation can lean on ground already covered.

Dependency layers in `kitchen/` (derived from the `#include` graph):

- **L0** — only `<stdint.h>`: `RunSpec.h`, `Kitchen_Settings.h`
- **L1** — RunSpec + Settings: `KitchenCore.h`, `RegisterSequencer.h`
- **L2** — implementations, then the test that pulls both headers

Review order for `kitchen/`:

1. `kitchen/RunSpec.h` — root, no project includes
2. `kitchen/Kitchen_Settings.h`
3. `kitchen/KitchenCore.h`
4. `kitchen/KitchenCore.cpp`
5. `kitchen/RegisterSequencer.h`
6. `kitchen/RegisterSequencer.cpp`
7. `kitchen/test/test_safety.cpp`

**Architecture change 2026-09-07:** `SafetyCore` + `ExperimentCore` (the
request/veto two-layer design) were merged into a single `KitchenCore` — see
`docs/implementation-plan.md` "Core architecture: one state machine". This
happened after items 1-2 below were signed off but before `SafetyCore.h` (item 3
under the old numbering) was reviewed, so the row-3-onward part of the table below
was never actually reviewed under the old design and needs no re-review for that
reason — it simply reviews the merged file instead.

(then `centralPLC/`, then anything remaining — order to be confirmed with the user
when we get there.)

## What each review must deliver

For the file under review, produce a **detailed explanation**, covering:

1. **What it is for** — the responsibility of this file in one paragraph. What
   problem it exists to solve.
2. **Where it sits** — its place in the architecture. What includes it, what it
   includes, what depends on it, what it depends on. Where it runs (compile time,
   setup, loop, ISR) and on which side of the kitchen/centralPLC split.
3. **The choices made** — the design decisions embedded in the code and *why*:
   data structures picked, constants and their units, `constexpr` vs runtime,
   ownership and lifetime, error handling strategy, what was deliberately left
   out, and any spots where a different choice was plausible.

Flag anything that looks wrong, inconsistent, or unfinished as we go — but keep it
as a note for the user's decision rather than editing unprompted.

## In progress

Where the last session stopped, if it stopped mid-file. Read this first; report it
to the user before doing anything else. Cleared when the file is signed off.

> _(nothing in progress — last session ended on a file boundary)_

Format when set:

- **File:** `path/to/file`
- **Reached:** how far through the explanation we got
- **Open:** any question left hanging, or "none"

## Progress

| # | File | Status |
|---|------|--------|
| 1 | `kitchen/RunSpec.h` | ✅ reviewed — comments touched 2026-09-07 for the merge (struct itself unchanged; not yet re-confirmed with the user) |
| 2 | `kitchen/Kitchen_Settings.h` | ✅ reviewed — comments touched 2026-09-07 for the merge (constants themselves unchanged; not yet re-confirmed with the user) |
| 3 | `kitchen/KitchenCore.h` | ⬜ next |
| 4 | `kitchen/KitchenCore.cpp` | ⬜ |
| 5 | `kitchen/RegisterSequencer.h` | ⬜ |
| 6 | `kitchen/RegisterSequencer.cpp` | ⬜ |
| 7 | `kitchen/test/test_safety.cpp` | ⬜ |
|   | `centralPLC/centralPLC.ino` | ⬜ order TBD |
|   | `centralPLC/server.py` | ⬜ order TBD |

### Open questions on ordering

- `centralPLC/centralPLC.ino` includes `"Secrets.h"`, which is not present in the
  repo. Untracked/gitignored, or not yet written? Its absence means the `.ino`
  cannot currently be placed by dependency alone.
- `centralPLC/server.py` is Python and sits outside the C++ include graph. It
  needs its own placement decision — likely reviewed alongside the `.ino` it
  talks to, since they form one client/server pair.
- `kitchenold.ino` and `Old/` appear to be superseded. Confirm whether they are in
  scope for review at all, or excluded as dead weight.

Status values: `⬜` not yet reviewed · `⬜ next` up next · `✅ reviewed` signed off
· `🔄 re-reviewed <date>` changed after review and re-explained.

Update this table as each file is signed off.

## Decisions

Conclusions reached during reviews. Logged here, **not applied** — an `open` row is
edited into the code only when the user explicitly says so in that moment, and the
row then flips to `applied`. `declined` rows are kept so the same flag is not
raised again next session.

| Date | File | Decision | Rationale | Status |
|---|---|---|---|---|
| — | — | _(none yet)_ | — | — |
