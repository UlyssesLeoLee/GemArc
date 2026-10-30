# Documents: derived from the facts you write

- REQ, BD and DD documents (Markdown), Mermaid diagrams and prompts are built from the model; you never write them. A slot with no fact prints `[NOT-AUTHORED:slot]`; `status view=report` lists the open slots (BLOCK means the document is not complete).
- Facts that fill slots: node `sub` (one line), edge `label`, case `title`, requirement `title` and `source`, evidence `topic`.
- Chapter 9 needs a policy: `## policies: id | text` with `src=<requirement or tbd id> kind=nfr`; chapter 10 uses `kind=constraint`. `by=user` only for a statement the user made.
- Entity and Table nodes need `props={"columns":[...]}` (DD level).
- Every room needs a floor and a building; every node needs a room (3D).
- `doc` is structure-only: it never shows an engine result.
