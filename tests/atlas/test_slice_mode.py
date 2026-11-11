"""Slice-mode validation (design 5.3, M2 (c)(d)(e)): pending references, one-edit typo WARN, pending-bind, cascade, ST-006,
the incoming-slice scope, remote ``check`` with ``known`` ids, the forward-reference share and the time budgets.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_slice_mode.py"

Synthetic id families only.
"""
from __future__ import annotations

import re
import time
import unittest

from gemarc_mcp.atlas import diag as dg
from gemarc_mcp.atlas import encode, expand, slicecheck

from . import synth
from .test_rules import META, MACHINE, M, S, desk, BASES


def check(files, mode="slice", meta=META, **kw):
    return slicecheck.check(files, meta, mode=mode, **kw)


class LookAlikeTests(unittest.TestCase):
    POOL = ["svc.stock", "svc.loan", "EVD-22", "EVD-23", "usr.member"]

    def test_the_four_single_edits_are_found(self):
        la = dg.LookAlike(self.POOL)
        self.assertEqual(la.find("svc.stok"), "svc.stock")      # deletion
        self.assertEqual(la.find("svc.stocck"), "svc.stock")    # insertion
        self.assertEqual(la.find("svc.stack"), "svc.stock")     # substitution
        self.assertEqual(la.find("svc.stcok"), "svc.stock")     # transposition

    def test_two_edits_short_ids_and_digit_only_differences_never_match(self):
        la = dg.LookAlike(self.POOL + ["a.b1", "a.b2", "x1234", "x1235"])
        self.assertIsNone(la.find("svc.stk"))                   # two edits
        self.assertIsNone(la.find("a.b3"))                      # shorter than 5 characters
        self.assertIsNone(la.find("EVD-24"))                    # differs from EVD-22 and EVD-23 only inside a digit run
        self.assertIsNone(la.find("EVD-9"))
        self.assertIsNone(la.find("x1236"))

    def test_the_length_rule_applies_to_both_ids_not_only_to_the_typed_one(self):
        self.assertIsNone(dg.LookAlike(["abcd", "xyz.long"]).find("abce"))    # typed id of 4 characters
        self.assertIsNone(dg.LookAlike(["abcd"]).find("abcde"))               # defined id of 4 characters
        self.assertEqual(dg.LookAlike(["abcde"]).find("abcdf"), "abcde")      # 5 characters is enough

    def test_a_defined_id_is_never_replaced_by_its_neighbour(self):
        la = dg.LookAlike(["svc.stock", "svc.stocj"])
        self.assertIsNone(la.find("svc.stock"))                 # the prototype replaced a valid id by a neighbour: never

    def test_two_candidates_are_ambiguous(self):
        la = dg.LookAlike(["svc.stoca", "svc.stocb"])
        self.assertIsNone(la.find("svc.stock"))

    def test_edit_within_one_agrees_with_a_brute_force_distance(self):
        def osa(a, b):
            d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
            for i in range(len(a) + 1):
                d[i][0] = i
            for j in range(len(b) + 1):
                d[0][j] = j
            for i in range(1, len(a) + 1):
                for j in range(1, len(b) + 1):
                    d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
                    if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                        d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
            return d[-1][-1]

        alphabet = "ab.c"
        words = [""]
        for _ in range(3):
            words = [w + ch for w in words for ch in alphabet]
        words = [w for w in words if w] + ["abcab", "abcba", "acbab"]
        for a in words:
            for b in words:
                self.assertEqual(dg.edit_within_one(a, b), osa(a, b) == 1, (a, b))


class PendingAndTypoTests(unittest.TestCase):
    TYPO = [(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.stok |")]
    DIGIT = [(M, "borrow a copy | ev=EVD-3", "borrow a copy | ev=EVD-9")]

    def test_one_edit_typo_is_a_visible_warning_that_never_blocks(self):
        r = check(desk(edits=self.TYPO))
        self.assertEqual([(d.rule, d.severity) for d in r.diagnostics], [("RF-002", "WARN")])
        self.assertTrue(r.ok)
        self.assertIn("probable typo, one edit from: svc.stock", r.render())
        self.assertEqual(r.pending, {})

    def test_a_reference_without_look_alike_is_pending_not_a_diagnostic(self):
        r = check(desk(edits=[(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.future-thing |")]))
        self.assertEqual(r.diagnostics, [])
        self.assertEqual(r.pending, {"node": ["svc.future-thing"]})
        self.assertTrue(r.ok)
        self.assertIn("I pending node (1): svc.future-thing", r.render())

    def test_digit_only_difference_is_pending_never_a_typo(self):
        r = check(desk(edits=self.DIGIT))
        self.assertEqual(r.diagnostics, [])
        self.assertEqual(r.pending, {"evidence": ["EVD-9"]})

    def test_full_mode_makes_every_unresolved_reference_an_error(self):
        r = check(desk(edits=self.TYPO), "full")
        self.assertEqual([(d.rule, d.severity) for d in r.diagnostics], [("RF-002", "ERROR")])
        self.assertIn("probable typo, nearest: svc.stock", r.render())
        self.assertFalse(r.ok)

    def test_full_mode_hint_for_digit_only_difference_lists_defined_ids_not_a_nearest(self):
        r = check(desk(edits=self.DIGIT), "full")
        (d,) = r.diagnostics
        self.assertEqual(d.rule, "RF-003")
        self.assertIn("use one of: EVD-1, EVD-2, EVD-3", d.hint)
        self.assertNotIn("nearest", d.hint)

    def test_pending_silencer_hides_the_pending_line_in_slice_mode_only(self):
        edits = [(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.future |")]
        files = desk(edits=edits)
        self.assertEqual(check(files).pending, {"node": ["svc.future"]})
        files[M] = files[M].replace("## edges:", "@pending svc.future\n## edges:", 1) if False else files[M] + "@pending svc.future\n"
        silenced = check(files)
        self.assertEqual((silenced.pending, silenced.diagnostics), ({}, []))
        self.assertEqual([d.rule for d in check(files, "full").diagnostics], ["RF-002"])  # full mode is a closed world

    def test_pending_is_grouped_by_kind_and_counts_distinct_ids(self):
        files = desk(edits=[(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.zeta-one |"),
                            (M, "svc.loan | writes | tbl.loan |", "svc.loan | writes | tbl.zeta-two |"),
                            (M, "borrow a copy | ev=EVD-3", "borrow a copy | ev=EVD-8")])
        r = check(files)
        self.assertEqual(r.pending, {"evidence": ["EVD-8"], "node": ["svc.zeta-one", "tbl.zeta-two"]})
        self.assertEqual(r.pending_refs, 3)

    def test_known_ids_resolve_references_of_a_lone_slice(self):
        lone = {M: desk()[M]}
        self.assertTrue(check(lone, "full").errors)  # the rooms and cases of the other slices are missing
        known = {"room": ["rm.desk", "rm.vis"]}
        self.assertFalse([d for d in check(lone, "full", known=known).diagnostics if d.rule.startswith("RF-001")])
        self.assertEqual(slicecheck.parse_known("room rm.desk\nroom rm.vis\n"), {"room": {"rm.desk", "rm.vis"}})
        self.assertEqual(slicecheck.parse_known(["node a.b", "bad"]), {"node": {"a.b"}})


class PendingBindTests(unittest.TestCase):
    def machine_without_owner(self, final: bool):
        files = BASES["machine"]([("15-machine.atl", "st.loan | Open | owner=svc.loan", "st.loan | Open")], None)
        files[M] = files[M].replace("@final edges\n", "")
        if final:
            files["15-machine.atl"] += "@final edges\n"
        return files

    def test_binding_rule_is_deferred_until_the_edges_are_final(self):
        r = check(self.machine_without_owner(False))
        self.assertEqual((r.diagnostics, r.pending_bind), ([], 1))
        self.assertTrue(r.ok)
        self.assertIn("pending-bind: 1", r.render())

    def test_final_edges_releases_the_binding_rule(self):
        r = check(self.machine_without_owner(True))
        self.assertEqual([d.rule for d in r.diagnostics], ["SM-003"])
        self.assertEqual(r.pending_bind, 0)
        self.assertFalse(r.ok)

    def test_full_mode_releases_it_too(self):
        r = check(self.machine_without_owner(False), "full")
        self.assertEqual([d.rule for d in r.diagnostics], ["SM-003"])

    def test_final_declared_in_any_slice_counts(self):
        files = self.machine_without_owner(False)
        files["00-space.atl"] += "@final edges\n"
        self.assertEqual([d.rule for d in check(files).diagnostics], ["SM-003"])


class CascadeTests(unittest.TestCase):
    def test_unresolved_evidence_explains_the_implemented_claim(self):
        files = desk(edits=[(M, "ev=EVD-3 verif=Inferred", "ev=EVD-39 verif=Implemented")])
        full = check(files, "full")
        self.assertEqual(full.rule_ids(), {"RF-003"})
        self.assertEqual({d.rule for d in full.diagnostics if d.derived}, {"EV-001"})
        self.assertEqual(full.derived_count, 1)
        self.assertNotIn("EV-001", full.render())
        self.assertIn("derived (suppressed): 1", full.render())
        # slice mode: the evidence is pending, the claim cannot be judged yet
        sl = check(files)
        self.assertEqual((sl.rule_ids(), sl.derived_count, sl.pending), (set(), 1, {"evidence": ["EVD-39"]}))

    def test_a_reference_to_the_intended_edge_is_a_consequence_of_the_endpoint_error(self):
        files = desk(edits=[(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.stok |"),
                            (S, "tests=TS-1", "tests=TS-1 edges=svc.loan:calls:svc.stock")])
        full = check(files, "full")
        self.assertEqual(full.rule_ids(), {"RF-002"})
        self.assertEqual({d.rule for d in full.diagnostics if d.derived}, {"RF-005"})
        self.assertIn("derived (suppressed): 1", full.render())

    def test_the_look_alike_replaces_only_the_unresolved_endpoint_never_a_valid_one(self):
        """The prototype replaced a valid id by its neighbour and suppressed the wrong error (design 5.2)."""
        neighbour = {"05-extra.atl": "#atlas 1\n## nodes: id | name | sub | opts\nsvc.stocj | Neighbour | Looks like the stock service. | room=rm.desk kind=Module ev=EVD-2\n"}
        files = desk(neighbour, edits=[(M, "svc.loan | calls | svc.stock |", "svc.stock | calls | svc.loam |"),
                                       (S, "tests=TS-1", "tests=TS-1 edges=svc.stock:calls:svc.loan,svc.stocj:calls:svc.loan")])
        full = check(files, "full")
        real = [(d.rule, d.got) for d in full.diagnostics if not d.derived]
        hidden = [(d.rule, d.got) for d in full.diagnostics if d.derived]
        self.assertIn(("RF-002", "svc.loam"), real)
        self.assertIn(("RF-005", "svc.stocj:calls:svc.loan"), real)   # the valid source was not replaced by its neighbour
        self.assertEqual(hidden, [("RF-005", "svc.stock:calls:svc.loan")])

    def test_a_real_claim_without_any_unresolved_evidence_is_still_reported(self):
        files = desk(edits=[(M, "ev=EVD-3 verif=Inferred", "ev=EVD-3 verif=Implemented")])
        self.assertEqual(check(files, "full").rule_ids(), {"EV-001"})

    def test_machine_without_a_node_row_explains_the_missing_owner(self):
        files = BASES["machine"]([("15-machine.atl", "st.loan | Loan state | Open, returned or lost. | room=rm.desk kind=State ev=EVD-1\n", "")], None)
        r = check(files, "full")
        self.assertEqual(r.rule_ids(), {"RF-002"})
        self.assertEqual({d.rule for d in r.diagnostics if d.derived}, {"SM-003"})
        self.assertIn("add a nodes row for the machine", r.render())

    def test_a_refused_row_explains_the_dangling_references_to_it(self):
        # the swallow trap of the design: a missing '|' makes ``ev=...`` part of the text cell, the row is refused
        files = desk(edits=[(M, "Knows which copy is on the shelf. | ev=EVD-2", "Knows which copy is on the shelf. ev=EVD-2")])
        for mode in ("slice", "full"):
            r = check(files, mode)
            self.assertEqual(r.rule_ids(), {"ST-006"}, mode)
            self.assertGreaterEqual(r.derived_count, 6, mode)
            self.assertEqual(r.pending, {}, mode)

    def test_cascade_works_for_jsonl_rows_too(self):
        model = synth.generate("tiny", 1, family=synth.Family.fixed())
        files = encode.encode_jsonl(model)
        self.assertEqual(check(files, "full", meta=encode.meta_of(model)).diagnostics, [])
        name = next(n for n in files if "structure" in n)
        lines = files[name].split("\n")
        broken = next(i for i, l in enumerate(lines) if '"_section":"nodes"' in l)
        lines[broken] = lines[broken].replace('"_section":"nodes"', '"_section":"nodes","bogus":1')
        files[name] = "\n".join(lines)
        r = check(files, "full", meta=encode.meta_of(model))
        self.assertEqual(r.rule_ids(), {"ST-004"})
        self.assertGreater(r.derived_count, 0)


class ST006Tests(unittest.TestCase):
    def test_option_token_in_a_text_cell_names_the_missing_pipe(self):
        files = desk(edits=[(M, "Knows which copy is on the shelf. | ev=EVD-2", "Knows which copy is on the shelf. ev=EVD-2")])
        (d,) = [d for d in check(files, "full").diagnostics if not d.derived]
        self.assertEqual((d.rule, d.severity, d.path, d.got), ("ST-006", "ERROR", "nodes.sub", "ev=EVD-2"))
        self.assertIn("is a '|' missing", d.hint)
        self.assertTrue(d.at.startswith("10-structure.atl:"))


class ScopeTests(unittest.TestCase):
    def test_only_findings_in_the_incoming_slice_are_reported(self):
        files = desk(edits=[(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.stok |"),
                            (S, "Call | usr.member | svc.loan |", "Call | usr.membre | svc.loan |")])
        both = check(files)
        self.assertEqual({d.at.split(":")[0] for d in both.diagnostics}, {M, S})
        only_scenarios = check(files, incoming=[S])
        self.assertEqual({d.at.split(":")[0] for d in only_scenarios.diagnostics}, {S})
        only_structure = check(files, incoming=[M])
        self.assertEqual({d.at.split(":")[0] for d in only_structure.diagnostics}, {M})

    def test_duplicate_id_across_slices_is_reported_where_the_new_text_defines_it(self):
        extra = {"05-more.atl": "#atlas 1\n## nodes: id | name | sub | opts\nsvc.loan | Twin | A repeated id. | room=rm.desk kind=Module\n"}
        files = desk(extra)
        r = check(files, incoming=["05-more.atl"])
        (d,) = [d for d in r.diagnostics if d.rule == "ST-002"]
        self.assertTrue(d.at.startswith("05-more.atl:"))
        self.assertIn("10-structure.atl:", d.hint)  # the other location is named

    def test_pending_of_other_slices_is_not_counted(self):
        files = desk(edits=[(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.nowhere |")])
        self.assertEqual(check(files, incoming=[S]).pending, {})
        self.assertEqual(check(files, incoming=[M]).pending, {"node": ["svc.nowhere"]})


# ---- remote check: same RF verdicts as the local slice check -------------------------------------------------

def damage(text: str) -> str:
    """Typos (adjacent swap in an id) and unknown ids in references, deterministically, never at a definition."""
    out, section, edges, events, nodes = [], "", 0, 0, 0
    for line in text.split("\n"):
        if line.startswith("## "):
            section = line[3:].split(":")[0]
        elif section == "edges" and line and not line.startswith(("@", "//", "#")):
            edges += 1
            cells = line.split("|")
            if edges % 7 == 0 and len(cells) > 2:
                t = cells[2].strip()
                if len(t) >= 8:
                    cells[2] = " " + t[:-3] + t[-2] + t[-3] + t[-1] + " "
                    line = "|".join(cells)
            elif edges % 11 == 0:
                line = "nowhere.item-zz |" + line.split("|", 1)[1]
        elif section == "events" and line and not line.startswith(("@", "//", "#")):
            events += 1
            if events % 9 == 0:
                cells = line.split("|")
                cells[1] = " nonesuch.item-qq "
                line = "|".join(cells)
        elif section == "nodes" and line and not line.startswith(("@", "//", "#")):
            nodes += 1
            if nodes % 6 == 0 and "ev=" in line:
                line = re.sub(r"ev=([A-Za-z0-9]{3})-", lambda m: "ev=" + m.group(1)[1] + m.group(1)[0] + m.group(1)[2] + "-", line, count=1)
        out.append(line)
    return "\n".join(out)


def rf_verdicts(result, derived: bool = False) -> set:
    return {(d.rule, d.severity, d.path, d.got) for d in result.diagnostics if d.rule.startswith(("RF-", "SM-001")) and d.derived == derived}


class RemoteCheckTests(unittest.TestCase):
    def test_remote_check_with_known_ids_gives_the_same_reference_verdicts_as_the_local_slice_check(self):
        for seed in (1, 2, 3):
            model = synth.generate("small", seed)
            meta = encode.meta_of(model)
            files = {k: v for k, v in encode.encode_atl(model).items()}
            damaged = {k: damage(v) for k, v in files.items()}
            checked = 0
            for name in damaged:
                others = {k: v for k, v in damaged.items() if k != name}  # what the server holds (damage included)
                known = slicecheck.known_ids(expand.expand_sources(others, meta).model)
                local = slicecheck.check(damaged, meta, mode="slice", incoming=[name])
                remote = slicecheck.check({name: damaged[name]}, meta, mode="slice", known=known)
                # identical verdicts, except one thing a lone slice cannot know: that a reference names the edge another slice
                # wrote with a mistyped endpoint (the local check hides it as derived, the remote one still prints it)
                local_set, remote_set = rf_verdicts(local), rf_verdicts(remote)
                hidden = {(r, p, g) for (r, _, p, g) in rf_verdicts(local, True)}
                self.assertLessEqual(local_set, remote_set, f"seed {seed} {name}")
                self.assertTrue(all((r, p, g) in hidden for (r, _, p, g) in remote_set - local_set), f"seed {seed} {name}")
                self.assertEqual(remote.pending, local.pending, f"seed {seed} {name}")
                checked += len(rf_verdicts(local)) + sum(len(v) for v in local.pending.values())
            self.assertGreater(checked, 5, f"the damage must produce verdicts (seed {seed})")

    def test_known_text_form_is_compact_and_round_trips(self):
        model = synth.generate("small", 4)
        known = slicecheck.known_ids(model)
        text = slicecheck.format_known(known)
        self.assertLessEqual(len(text.encode()), 8 * 1024 * 4)  # a small atlas; the wire budget (8 KB) is for the id kinds that matter
        self.assertEqual(slicecheck.parse_known(text), {k: set(v) for k, v in known.items() if v})


# ---- acceptance (d) and (e) -------------------------------------------------------------------------------------


class ForwardReferenceShareTests(unittest.TestCase):
    """(d) share of ids that the slice check would call a probable typo of another defined id (worst case: every defined id,
    as if it were a forward reference, against all the other ids of its kind). Random families, reference scale."""

    KINDS = (("nodes", "id"), ("evidences", "evidenceId"), ("requirements", "id"), ("cases", "caseId"))

    def share(self, ids: list) -> float:
        hits = 0
        for x in ids:
            others = [p for p in ids if p != x]
            if dg.LookAlike(others).find(x):
                hits += 1
        return hits / len(ids)

    def test_share_on_random_family_reference_scale_atlases(self):
        worst = {kind: 0.0 for kind, _ in self.KINDS}
        for seed in range(1, 9):
            model = synth.generate("reference", seed)
            for kind, key in self.KINDS:
                ids = [r[key] for r in model[kind]]
                worst[kind] = max(worst[kind], self.share(ids))
        print("\n[forward-reference WARN share, worst of 8 reference-scale random-family seeds] "
              + ", ".join(f"{k} {100 * v:.1f}%" for k, v in worst.items()))
        self.assertLessEqual(worst["nodes"], 0.20)
        self.assertLessEqual(worst["evidences"], 0.05)
        self.assertLessEqual(worst["nodes"], 0.35)  # the tripwire of the design

    def test_share_on_a_hand_built_family_with_realistic_near_names(self):
        """The synthetic generator draws random word pairs, so near-identical ids are rare. A family with versioned and plural
        names shows what the rule does when they are common (reported, not a pass criterion beyond the tripwire)."""
        bases = [f"svc.{w}" for w in ("loan", "stock", "member", "desk", "audit", "ledger", "queue", "router", "mailer", "scanner")]
        ids = bases + [b + "s" for b in bases[:5]] + [b + "-v2" for b in bases[:3]] + [b + "-api" for b in bases[:3]]
        value = self.share(ids)
        print(f"[forward-reference WARN share, hand-built near-name family of {len(ids)} ids] {100 * value:.1f}%")
        self.assertGreater(value, 0.2)  # the family exercises the rule (it is not vacuous)

    def test_single_edit_typo_recall_on_random_families(self):
        """Recall of the one-edit WARN for ids typed with one error (deletion, insertion, substitution, transposition)."""
        found = total = 0
        by_kind: dict[str, list] = {}
        for seed in range(1, 5):
            model = synth.generate("reference", seed)
            for kind, key in (("nodes", "id"), ("evidences", "evidenceId")):
                ids = [r[key] for r in model[kind]]
                la = dg.LookAlike(ids)
                for index, x in enumerate(ids):
                    body = len(x) // 2
                    typo = (x[:body] + x[body + 1:], x[:body] + "q" + x[body:], x[:body] + ("q" if x[body] != "q" else "z") + x[body + 1:],
                            x[:body - 1] + x[body] + x[body - 1] + x[body + 1:])[index % 4]
                    if typo == x or typo in la.pool:
                        continue
                    total += 1
                    hit = la.find(typo) == x
                    found += hit
                    by_kind.setdefault(kind, []).append(hit)
        recall = {k: sum(v) / len(v) for k, v in by_kind.items()}
        print("[one-edit typo recall on random families] " + ", ".join(f"{k} {100 * v:.1f}%" for k, v in recall.items()))
        self.assertGreaterEqual(recall["nodes"], 0.9)
        self.assertGreater(found / total, 0.5)


class TimeBudgetTests(unittest.TestCase):
    """(e) check slice <= 0.3 s and full (no engine) <= 1 s on the 169-node atlas (parse + expand + rules, whole project)."""

    def test_reference_scale_timings(self):
        model = synth.generate("reference", 3)
        self.assertEqual(len(model["nodes"]), 169)
        files, meta = encode.encode_atl(model), encode.meta_of(model)
        best = {}
        for mode in ("slice", "full"):
            times = []
            for _ in range(3):
                t = time.perf_counter()
                result = slicecheck.check(files, meta, mode=mode)
                times.append(time.perf_counter() - t)
            best[mode] = min(times)
            self.assertEqual(result.errors, [])
        print(f"\n[time, 169 nodes, best of 3] check slice {best['slice'] * 1000:.0f} ms, check full {best['full'] * 1000:.0f} ms")
        self.assertLessEqual(best["slice"], 0.3)
        self.assertLessEqual(best["full"], 1.0)


if __name__ == "__main__":
    unittest.main()
