# Format: .atl rows (put takes the slice text)

- First line `#atlas 1` (to-be projects: `#atlas 1 mode=tobe`). `//` starts a comment line. UTF-8.
- A section starts with a header `## name: col | col | opts`. Copy the header exactly; the column order is fixed.
- A row has exactly as many `|`-separated text cells as the header names, never empty, then ONE options cell of `key=value` tokens (leave the options cell out when there is nothing to set: `~` is only for a text cell that takes its `@set` default). A value is a bare token, a "quoted string", {json} or [json]; lists are comma-joined ids (`ev=A,B`). A literal `|` in text is `\|`.
- `@set k=v` sets scalar defaults for the rest of the section (`@set room=rm.a kind=Module`); a cell `~` takes the default; `@set k=` clears it. Never `@set` an ev, nodes, edges or req list.
- `@final edges` when the edge list is complete (it releases the checks that wait for edges). `@pending id,id` silences a deliberate forward reference (rarely needed).
- Text that looks like an option (`word=word`) inside a text cell is ST-006: quote the cell. A text cell that contains an ASCII quote must be one complete JSON string; to quote the user inside a policy text use '...' or 「...」. An option never repeats a positional cell (`name=` on a nodes row is ST-007).
- A row with a syntax defect is dropped and reported; the rest of the slice is still read.

Sections (positional cells, then options):
{{headers}}

`.jsonl` carries the same rows as JSON lines (`{"_section":"nodes","id":"svc.a",...}`); prefer `.atl`, it is shorter.
