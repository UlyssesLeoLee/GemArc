# Derived: never write these (DV-001 when you do)

Keys the format refuses, per section:
{{derived}}

The expander derives, with a provenance entry for each:
- Spatial: floor levels unique per building; a room's tier and area and a building's trust are inherited by its nodes.
- Evidence: sourceKind, verification and lineRange (the verifier stamps it from `anchor`; you write id | file | anchor | topic and the repo via `@set repo`).
- Nodes: verification default (Implemented with code evidence, else Designed with any evidence, else Hypothetical), ports `<id>:in` and `<id>:out`.
- Machines: states, terminal states, transition ids, the owning `owns` edge (write `owner=`).
- Edges: id `src:rel:tgt`, type, directed, ports, static, default verification; `ev=^` means the endpoints' evidence.
- Cases: event seq = row order, default policy, linked tests (from the requirements).
- Interventions: the insert_element chain and element ports (write `rewrite=` and `use=<kind>`); combined parts are flattened.
- Requirements: edges default to the edges between its nodes.
- Downstream, never authored: diagrams, traces, causal chains, documents, prompts, layout, colours, labels.

Stays authored: `verif=` overrides, expectations, ruleId step names, prose, topic.
