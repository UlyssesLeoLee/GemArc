# Vocabulary (profile software): use these ids exactly, case-sensitive

- node kind: {{ids:kinds}}
- relation (edge rel): {{ids:relations}}
- verification (verif=): {{ids:verification}} (strongest first; Implemented needs source-code evidence)
- building trust: {{ids:trust}}; room tier: {{ids:tiers}}
- case category: {{ids:caseCategories}}
- event type: {{ids:eventTypes}}
- intervention type: {{ids:interventionTypes}}; outcome class: {{ids:outcomeClasses}}
- node state (expect nodes=node=State): {{ids:nodeStates}}
- element kind (use=): {{ids:elementKinds}}
- policy kind: {{ids:policyKinds}}; by: ai | user; expect by: author | user

Ids you author: ASCII letters, digits, `.`, `_`, `-`, for example `svc.loan`, `EVD-1`. Edge ids are derived: `src:rel:tgt`.
A wrong enum value is ST-003 with the allowed list; state names are case-sensitive.
