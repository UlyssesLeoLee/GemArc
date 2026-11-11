# Economics: spend tokens on facts

- One put is at most about 3K tokens; a 169-node system is about 25 puts. A repair patch is 1-6 rows (about 45 tokens each) plus about 1K tokens of diagnostics.
- Do not re-send valid rows and do not re-read slices: the `next` line of a put reply says what to do, `status view=next` (about 1 KB) is for a red draft, a resume or every ~5 puts, and `index` tells you what exists.
- Diagnostics print one root cause and count the consequences as derived. Fix the first listed error first.
- At most 3 repair loops per slice and about two puts per 11 nodes in total; past that report `blocked` with the last diagnostics instead of looping.
- Source reading dominates an as-is project: `outline(root, path)` lists a file's def/class/heading lines with line numbers and a unique anchor, so cite that anchor instead of re-reading; never paste code into rows. After the facts, the gates cost a few small calls: simulate, evidence, build, verify, then the one review.
- Keep sub, label and title to one short line; the documents add structure, you add facts.
