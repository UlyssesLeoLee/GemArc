# GemArc Atlas work order

Goal: turn a system (as-is: its code and docs; to-be: the user's requirement text) into validated facts. You write only facts in a compact format; tested code derives and renders everything else (documents, diagrams, prompts, simulation, the HTML viewer). Never write HTML or documents yourself, and never invent a fact: an unknown is a TBD or a lower `verif=`.

## Loop
1. `init` once (project, title, mode asis|tobe; the user sets `GEMARC_ALLOW_WRITE=1` and `GEMARC_WORKSPACE_ROOT` in the server config: on `GEMARC_AUTH_002` stop and tell the user). Save a to-be requirement text verbatim to `atlas/user/<name>.txt` (the directory exists).
2. Repeat: `status view=next` -> author the slice it names with `put` -> read the `next` line in `put`'s own reply and follow it; ask `status view=next` again only after a red draft, on resume, about every 5 puts and at a phase change. If `put` says invalid, fix with `put mode=patch`. A reply with `retry:true` is not a repair round: send it again in a few seconds.
3. Stop when `status` says `done:true`, or `phase:needs-user` (ask the user, never decide for them), or you reach 3 repair loops on one slice (report `blocked` with the last diagnostics); a `0 STOP` line in `next` says the same.
4. Then ONE self-review: `gemarc_atlas_review action=report`, fix only the listed findings, `action=record` (shell: `python -m gemarc_mcp.atlas review <project> --action report|record --human`), then the final report.

## What to write
- Slices are named `[a-z0-9-]{1,40}` in reading order: `00-space` (buildings, floors, rooms), `10-structure`, `11-structure` ... (nodes, edges, evidences, machines, transitions), `20-scenarios` ... (tests, cases, events, expect, interventions, requirements, tbds, policies).
- Sizing: about 11 nodes with their edges and evidences per slice (4-7 KB); over 8 KB is a warning, over 64 KiB is refused. Roughly two puts per 11 nodes in total, a 169-node system about 25 puts of at most 3K tokens.
- Format: `#atlas 1`, then `## name: col | col | opts` headers copied exactly, rows `cell | cell | key=value ...`, `@set key=value` defaults (scalars only), `~` takes the default, `@final edges` when the edges are complete. Section headers: `brief section=format`; ids: `brief section=vocab`. To-be: follow the `tobe step N/7` lines of `status`; a TBD answered by the user goes verbatim into `atlas/user/<name>.txt` and the `by=user` policy quotes it ('...').
- Evidence rows `id | file | anchor | topic` (anchor = a quoted line of the source; to-be: `user:<name>` and the quoted requirement sentence). Implemented needs a code evidence; unsure -> `verif=Designed|Inferred|Hypothetical`.

## Derived: never write (DV-001)
Edge ids, ports, verification defaults, evidence sourceKind and lineRange, machine states, terminal states, transition ids, the owns edge, event seq, case policy and linked tests, element chains and ports, requirement edges, and everything downstream (diagrams, traces, documents, prompts, layout, labels). Full list: `brief section=derived`.

## Repair
`put` with an ERROR commits nothing and stores your text as the slice's red draft (nothing is lost). Fix it with `put mode=patch` carrying only the changed rows: same identity replaces (id; edge from|rel|to; expect case; transition machine+from|to), `-id` deletes a row, an events block `@set case=X` replaces all events of X. Never re-send or re-read the whole slice. Fix the first listed error first: the rest are often consequences. WARN and INFO never block; an id that is not defined yet is pending, write the slice that defines it next.

## Reading `status view=next`
`{done, phase, next[], blockers[], needsUser[]}`. `next` has at most 10 lines; the leading digit is the priority (1 red draft or error, 2 BLOCK slot, 3 undefined ids, 4 requirement gaps, 5 cases without expectation, 6 unreachable states, 7 coverage); the last line is the dimension readiness 1D-5D. Phases: collect, author, repair, build, review, done, needs-user. `index` lists what exists (nodes, room:, req:, case:, ids:prefix); `check` never writes (mode=slice per slice, mode=full for the whole project).

## Expectations
`expect` rows state what SHOULD happen, written from the requirement or the code, before any simulation, never copied from an engine result. Details: `brief section=oracle`.

## Economics
Do not re-send valid rows, do not re-read slices, cite unique anchors instead of pasting code, keep sub/label/title to one short line. Details: `brief section=economics`; documents: `brief section=docs`.
