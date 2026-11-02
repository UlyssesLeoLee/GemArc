# Interface prototype: ui-* slices (independent of the 5D model)

- A second kind of product: slice names start with `ui-` (`ui-10-login`), the first line is `#atlas-ui 1`; the 5D parser and this one refuse each other's header. Write facts only (screens, state variants, widget tree, navigation); layout, SVG and screen cards are generated: never write HTML or SVG.
- 4-6 screens per slice (about 750 bytes each): a screen's variants, widgets and the nav rows that start from it go in one slice. Put budget 4 + 2 x ceil(screens/4). Repair with `mode=patch`.
- The syntax is .atl (copy the headers, no empty cells, `@set`, `~`). Sections:
{{uiheaders}}
- Ids: a screen id starts with a letter, has no `. : # ~`, is not INDEX, NAV, START, CARD or a device name (CON, NUL...) in any case, and no two differ only in case; a widget local is lower case and not `root` or `state`; full ids are `screen.local` and `screen#state` (`default` always exists: never write it). Derived keys (id, order, parentId, widgetIds...) are DV-001.
- Widget tree: `parent` is a container of the same screen, row order is sibling order; `in=error,empty` shows a widget only in those states; w and h take fill, hug, pixels or Nfr; align takes start, center or end. A container label is not drawn: add a heading widget for a visible title. A variant `text=` is interface copy (empty and error states draw it), `note` is a design remark.
- Navigation: `from` is a screen or `screen.widget` (a tappable kind), `to` is a screen or `screen#state`; a second row for the same from and to needs `alias=`. Write `@final nav` when it is complete.
- Links to 5D (actor, req, api, case, rule, machine, state) are optional: checked when a 5D model exists, else one INFO line; an id deleted in 5D turns the link red.
- Kinds: widget {{uiids:widgetKinds}}; screen {{uiids:screenKinds}}; frame {{uiids:frames}}; state {{uiids:variantKinds}}; emphasis {{uiids:emphasis}}; trigger {{uiids:triggers}}. The kind-to-DOM table is in docs/atlas/spec/ui-schema.md.
- A frontend check verdict goes in a `ui-verdict-*` slice (conform, diffs only; ui_sha8 unchanged): `sha=` is the stamp on the screen card, `checked` + the unverifiable rows = its check points, `basis` is `alias:path:line`; record differences only, write skipped points as unverifiable.
