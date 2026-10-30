# Oracle: expectations are intent

- An `expect` row (and an intervention's class and terminal) states what SHOULD happen. Write it from the requirement or from the code you read, before any simulation. Never copy an engine result into it.
- A write call seals an expectation once every node it names resolves. Sealed before any verdict it stays `sealed`; edited after a mismatch report or a reveal it becomes `revised`; `by=user` counts only after the user confirmed it.
- Verdicts are blind: pass or fail and how many asserted nodes disagree, never which. Naming them is a reveal and is recorded.
- Asserted nodes must cover at least max(2, 35% of the case's participants). An abnormal, recovery or combined case that asserts only Completed gets OR-002.
- A release build needs every expectation sealed or confirmed. The oracle sheet lists those the user has not confirmed; the user approves it, you never write that file.
