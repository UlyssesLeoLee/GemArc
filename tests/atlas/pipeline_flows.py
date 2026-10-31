"""The two authored flows of milestone M8: a greenfield project from a requirement brief (to-be) and an as-is project over a
generated repository. Both drive ``pipeline.Session`` (in-process) or ``pipeline.StdioSession`` (real MCP stdio) through the REAL tools
and stop at ``status done``; they write what an AI would write, from the brief or from the repository, never from an engine result.

* ``BRIEF`` is a synthetic requirement text (an invented community garden booking desk, not a real system). ``run_greenfield`` follows
  the elicitation order of design 8.3: the text is saved under ``atlas/user/`` first, then requirements, tests and TBDs (each
  ``source`` a verbatim sentence of the brief), spatial rows, nodes with ``user:`` evidence (each anchor a verbatim sentence), edges,
  cases derived from the acceptance statements, expectations that restate the requirements, the user's answers as ``by=user``
  policies that quote the saved answers. Nothing is invented: ``REQUIREMENTS`` lists exactly the numbered sentences of the brief.
* ``make_repository`` generates a repository of about 25 modules (``MODULES``), each file with a class, a handler function and the
  call sites to the modules it depends on, plus two documents. ``run_asis`` reads each module through the ``outline`` tool, takes the
  anchors from it (the call-site anchors of the edges come from reading the file, as an AI would), and authors nodes, edges,
  evidence, cases, expectations and one nfr policy. ``evidence`` then stamps every Implemented claim (EV-003).

Standard library only; synthetic names only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LANG = "en-US"

# ---------------------------------------------------------------------------------------------------------------
# Greenfield: the requirement brief and the facts an AI derives from it
# ---------------------------------------------------------------------------------------------------------------

REQUIREMENTS = (
    "Members of the garden club can reserve one free plot for one growing season.",
    "A reservation is confirmed when the member pays the season fee at the club desk.",
    "Plots that stay unplanted for sixty days are released back to the waiting list.",
    "The coordinator can see which plots are free, reserved or released.",
    "Members on the waiting list are told by message when a plot is released to them.",
    "The watering rota gives each member one weekly slot for the shared water tank.",
    "The booking page answers within two seconds for up to two hundred members.",
)
QUESTIONS = (
    "Does a member who moves away keep the plot until the season ends?",
    "May a guest book a plot on behalf of a member?",
    "How long may the waiting list grow before the club stops adding names?",
)
ANSWERS = (
    "A member who moves away keeps the plot until the season ends.",
    "Only a club member may book a plot; a guest asks a member to book.",
    "The waiting list holds at most fifty names.",
)

BRIEF = (
    "Community garden plot booking: requirement brief\n"
    "(an invented example for tests, not a real system)\n\n"
    "Purpose\nThe garden club shares a field of small plots. This brief says how members reserve them.\n\n"
    "Requirements\n" + "\n".join(f"{i}. {text}" for i, text in enumerate(REQUIREMENTS, start=1)) + "\n\n"
    "Open questions\n" + "\n".join(f"{chr(64 + i)}. {text}" for i, text in enumerate(QUESTIONS, start=1)) + "\n\n"
    "Acceptance\nA member reserves a free plot and pays the fee at the desk, and the plot shows as reserved to the coordinator. "
    "An unplanted plot is released and the first name on the waiting list is told. If the message cannot be sent the release still stands.\n"
)


def greenfield_slices() -> dict:
    """``{slice name: text}`` in the order of the elicitation (design 8.3), every fact a verbatim sentence of ``BRIEF`` or a node
    id that the structure slice defines. The cases restate the acceptance paragraph; the expectations follow the engine rules of the
    brief's oracle section (a call chain whose Returns come back completes every participant; Failure fails its receiver)."""
    header = "#atlas 1 mode=tobe\n"
    r = REQUIREMENTS
    requirements = (
        header + "## tests: id | title\n" + "\n".join(f"TS-{i} | Check requirement {i}" for i in range(1, 8)) + "\n"
        "## requirements: id | title | source | opts\n"
        f"RQ-1 | Reserve one plot per season | {r[0]} | nodes=usr.member,svc.booking,tbl.plot tests=TS-1\n"
        f"RQ-2 | Confirm by paying the fee | {r[1]} | nodes=usr.member,svc.booking,tbl.reservation tests=TS-2\n"
        f"RQ-3 | Release unplanted plots | {r[2]} | nodes=svc.booking,svc.waitlist,tbl.plot tests=TS-3\n"
        f"RQ-4 | Show plot status | {r[3]} | nodes=usr.coordinator,svc.booking,tbl.plot tests=TS-4\n"
        f"RQ-5 | Tell the waiting list | {r[4]} | nodes=svc.waitlist,svc.notify tests=TS-5\n"
        f"RQ-6 | Give a weekly water slot | {r[5]} | nodes=usr.member,svc.rota tests=TS-6\n"
        f"RQ-7 | Answer within two seconds | {r[6]} | nodes=svc.booking tests=TS-7\n"
        "## tbds: id | title | decision | opts\n"
        + "\n".join(f"TBD-{i} | {title} | {q}" for i, (title, q) in enumerate(zip(("Moving away", "Guest booking", "Waiting list length"), QUESTIONS), start=1)) + "\n")
    space = (
        header + "## buildings: id | name | opts\nzone.club | Club grounds | trust=internal\nzone.web | Public web | trust=external\n"
        "## floors: id | building | name | level\nlvl.desk | zone.club | Club desk | 1\nlvl.web | zone.web | Booking page | 0\n"
        "## rooms: id | floor | name | opts\nrm.booking | lvl.desk | Booking logic | tier=T2\nrm.store | lvl.desk | Plot records | tier=T3\nrm.visitors | lvl.web | People | tier=T1\n")
    structure = (
        header + "## nodes: id | name | sub | opts\n@set room=rm.booking kind=Module\n"
        "svc.booking | Booking service | Reserves a plot and confirms it when the fee is paid. | ev=EVD-1,EVD-2\n"
        "svc.waitlist | Waiting list | Keeps the names of members who wait for a plot. | ev=EVD-3\n"
        "svc.notify | Notification service | Tells a member by message that a plot is released. | ev=EVD-5\n"
        "svc.rota | Watering rota | Gives each member a weekly slot for the water tank. | ev=EVD-6\n"
        "@set room=rm.store kind=Table\n"
        'tbl.plot | Plot table | One row per plot with its state. | ev=EVD-1,EVD-4 props={"columns":[{"name":"id","pk":true},"state","member_id"]}\n'
        'tbl.reservation | Reservation table | One row per reservation and its fee state. | ev=EVD-2 props={"columns":[{"name":"id","pk":true},"plot_id","fee_paid"]}\n'
        "@set room=rm.visitors kind=Actor\n"
        "usr.member | Member | A garden club member who books a plot. | ev=EVD-1\n"
        "usr.coordinator | Coordinator | The club volunteer who watches the plots. | ev=EVD-4\n"
        "## edges: from | rel | to | label | opts\n"
        "usr.member | calls | svc.booking | reserve a plot | ev=EVD-1\n"
        "svc.booking | reads | tbl.plot | find a free plot | ev=EVD-1\n"
        "svc.booking | writes | tbl.reservation | record the reservation | ev=EVD-2\n"
        "usr.coordinator | calls | svc.booking | list the plots | ev=EVD-4\n"
        "svc.booking | calls | svc.waitlist | offer a released plot | ev=EVD-3\n"
        "svc.waitlist | calls | svc.notify | tell the first name | ev=EVD-5\n"
        "usr.member | calls | svc.rota | ask for a water slot | ev=EVD-6\n"
        "@final edges\n"
        "## evidences: id | file | anchor | topic | opts\n"
        f"EVD-1 | user:brief | {r[0]} | the reservation rule\n"
        f"EVD-2 | user:brief | {r[1]} | the fee rule\n"
        f"EVD-3 | user:brief | {r[2]} | the release rule\n"
        f"EVD-4 | user:brief | {r[3]} | the coordinator view\n"
        f"EVD-5 | user:brief | {r[4]} | the notice rule\n"
        f"EVD-6 | user:brief | {r[5]} | the rota rule\n")
    scenarios = (
        header + "## cases: id | cat | title | opts\n"
        "CS-RESERVE | normal | A member reserves a free plot and pays the fee | req=RQ-1,RQ-2 ev=EVD-1\n"
        "CS-OVERVIEW | normal | The coordinator lists the plots | req=RQ-4 ev=EVD-4\n"
        "CS-RELEASE | normal | An unplanted plot is released and the first name is told | req=RQ-3,RQ-5 ev=EVD-3\n"
        "CS-NOTICE-FAIL | abnormal | The message cannot be sent and the release still stands | req=RQ-5 ev=EVD-5\n"
        "CS-ROTA | normal | A member gets a weekly water slot | req=RQ-6 ev=EVD-6\n"
        "## events: type | from | to | opts\n"
        "@set case=CS-RESERVE\n"
        "Call | usr.member | svc.booking | ruleId=R-RESERVE\nCall | svc.booking | tbl.plot | ruleId=R-FIND\n" "Return | tbl.plot | svc.booking | ruleId=R-FOUND\n"
        "Call | svc.booking | tbl.reservation | ruleId=R-SAVE\n" "Return | tbl.reservation | svc.booking | ruleId=R-SAVED\n" "Return | svc.booking | usr.member | ruleId=R-DONE\n"
        "@set case=CS-OVERVIEW\n"
        "Call | usr.coordinator | svc.booking | ruleId=R-LIST\nCall | svc.booking | tbl.plot | ruleId=R-READ\n" "Return | tbl.plot | svc.booking | ruleId=R-ROWS\n"
        "Return | svc.booking | usr.coordinator | ruleId=R-SHOWN\n"
        "@set case=CS-RELEASE\n"
        "Call | svc.booking | svc.waitlist | ruleId=R-OFFER\nCall | svc.waitlist | svc.notify | ruleId=R-TELL\n" "Return | svc.notify | svc.waitlist | ruleId=R-TOLD\n"
        "Return | svc.waitlist | svc.booking | ruleId=R-OFFERED\n"
        "@set case=CS-NOTICE-FAIL\n"
        "Call | svc.booking | svc.waitlist | ruleId=R-OFFER2\nCall | svc.waitlist | svc.notify | ruleId=R-TELL2\nFailure | svc.waitlist | svc.notify | ruleId=R-NOSEND\n"
        "@set case=CS-ROTA\n"
        "Call | usr.member | svc.rota | ruleId=R-ASK\n" "Return | svc.rota | usr.member | ruleId=R-SLOT\n"
        "## expect: case | nodes | opts\n"
        "CS-RESERVE | usr.member,svc.booking,tbl.plot,tbl.reservation | by=author\n"
        "CS-OVERVIEW | usr.coordinator,svc.booking,tbl.plot | by=author\n"
        "CS-RELEASE | svc.booking,svc.waitlist,svc.notify | by=author\n"
        "CS-NOTICE-FAIL | svc.notify=Failed,svc.waitlist=Running,svc.booking=Running | by=author\n"
        "CS-ROTA | usr.member,svc.rota | by=author\n"
        "## interventions: id | type | description | opts\n"
        'IV-NOTIFY-DOWN | disable_module | The notification service is down while a plot is released | target=svc.notify applies=CS-RELEASE class=DIVERGED terminal={"svc.notify":"Blocked"}\n')
    policy = header + "## policies: id | text | opts\nPL-NFR-1 | The booking page answers within two seconds for up to two hundred members. | src=RQ-7 kind=nfr\n"
    return {"10-requirements": requirements, "20-space": space, "30-structure": structure, "40-scenarios": scenarios, "50-policy": policy}


def decisions_slice() -> str:
    """The user's answers as policies: ``by=user``, each text quoting the answer the user gave (saved verbatim in ``atlas/user/answers.txt``)."""
    return ("#atlas 1 mode=tobe\n## policies: id | text | opts\n"
            + "".join(f"PL-DEC-{i} | The user decided: '{a}' | src=TBD-{i} by=user kind=constraint\n" for i, a in enumerate(ANSWERS, start=1)))


ANSWERS_TEXT = "".join(f"{chr(64 + i)}. {a}\n" for i, a in enumerate(ANSWERS, start=1))


def run_greenfield(s, *, answers: bool = True, finish: bool = True) -> dict:
    """Drive a greenfield project to ``done`` through ``s`` (a ``pipeline.Session`` or ``StdioSession`` on an empty workspace).

    Returns the replies by step name. ``answers=False`` stops before the user answers the TBDs (the project then waits for the user);
    ``finish=False`` stops after the structure (no gates)."""
    out: dict = {}
    s.call("brief", "orient", section="all", lang=LANG)
    out["init"] = s.call("init", "orient", title="Community garden plot booking", mode="tobe", languages=[LANG])
    out["status-empty"] = s.status("author")                       # collect: it asks for the requirement text first
    s.write_user_text("brief", BRIEF)                               # step 1 of design 8.3: the text is saved verbatim before any fact
    out["status-saved"] = s.status("author")
    slices = greenfield_slices()
    for name, text in slices.items():
        out[f"put-{name}"] = s.put(name, text)
        if out[f"put-{name}"]["status"] != "committed":
            raise AssertionError(f"put {name}: {json.dumps(out[f'put-{name}'])[:500]}")
    out["status-needs-user"] = s.status("author")                  # the three TBDs are the user's to decide: the driver stops and asks
    if answers:
        s.write_user_text("answers", ANSWERS_TEXT)                  # the user's own words, saved verbatim, THEN the policy rows that quote them
        out["put-60-decisions"] = s.put("60-decisions", decisions_slice())
        if out["put-60-decisions"]["status"] != "committed":
            raise AssertionError(f"put decisions: {json.dumps(out['put-60-decisions'])[:500]}")
    out["status-structure"] = s.status("author")
    if not finish:
        return out
    out["simulate"] = s.call("simulate", "gates")
    out["evidence"] = s.call("evidence", "gates")
    out["build"] = s.call("build", "gates")
    out["doc"] = s.call("doc", "gates", unit="system", level="REQ", lang=LANG)
    out["verify"] = s.call("verify", "gates")
    if out["verify"].get("code") == "GEMARC_ATLAS_BUILD_031":
        s.waive_browser("this machine has no browser; the user opens the page by hand")
    out["status-gates"] = s.status("gates")
    out["report"] = s.call("review", "review", action="report")
    out["record"] = s.call("review", "review", action="record", notes="fixed=; open=")
    out["final"] = s.status("final")
    return out


# ---------------------------------------------------------------------------------------------------------------
# As-is: a generated repository of about 25 modules
# ---------------------------------------------------------------------------------------------------------------

MODULES = ("intake", "parse", "route", "render", "cache", "audit", "queue", "notify", "export", "billing", "search", "session", "token",
           "profile", "report", "schedule", "archive", "backup", "filter", "gateway", "limiter", "mailer", "metrics", "planner", "worker")
PACKAGE = "pkg"


def _depends(index: int) -> list:
    """The modules that module ``index`` calls: the next one and, for some, the one three further on (a deterministic DAG)."""
    deps = []
    if index + 1 < len(MODULES):
        deps.append(index + 1)
    if index % 2 == 0 and index + 3 < len(MODULES):
        deps.append(index + 3)
    return deps


def call_line(index: int, dep: int) -> str:
    """The call site of module ``dep`` inside module ``index`` (one line, unique in its file)."""
    return f"        result_{MODULES[dep]} = {MODULES[dep]}.handle_{MODULES[dep]}(request)"


def module_source(index: int) -> str:
    name = MODULES[index]
    title = name.capitalize()
    deps = _depends(index)
    imports = "".join(f"from {PACKAGE} import {MODULES[d]}\n" for d in deps)
    calls = "".join(call_line(index, d) + "\n" for d in deps)
    body = calls + f"        return self.store.save_{name}(request)\n"
    second = ""
    if index % 5 == 0:                  # a second class with the same constructor line: that line has no unique anchor in this file
        second = (f"\n\nclass {title}Settings:\n    def __init__(self):\n        self.limit = {10 + index}\n"
                  f"\n    def read_limit_{name}(self):\n        return self.limit\n")
    return (f'"""Module {name} of the invented example repository."""\n{imports}\n'
            f"LIMIT_{name.upper()} = {100 + index}\n\n\n"
            f"class {title}Store:\n    def __init__(self):\n        self.rows = {{}}\n\n"
            f"    def save_{name}(self, request):\n        self.rows[len(self.rows)] = request\n        return len(self.rows)\n\n\n"
            f"class {title}Service:\n    def __init__(self, store):\n        self.store = store\n\n"
            f"    def handle_{name}(self, request):\n{body}{second}"
            f"\n\ndef handle_{name}(request):\n    return {title}Service({title}Store()).handle_{name}(request)\n")


DOC_TEXT = ("# Overview of the invented example\n\nThe example repository moves a request from intake to the last worker.\n\n"
            "## Request flow\n\nEvery module hands the request to the next one and stores a row.\n\n"
            "## Operating limits\n\nEach module keeps its own limit constant.\n")


def make_repository(root: Path) -> dict:
    """Write the repository under ``root``; returns ``{"modules": [...], "calls": [(caller index, callee index)], "doc": rel path}``."""
    for index, name in enumerate(MODULES):
        target = root / PACKAGE / f"{name}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(module_source(index).encode("utf-8"))
    (root / PACKAGE / "__init__.py").write_bytes(b'"""Invented example package."""\n')
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "overview.md").write_bytes(DOC_TEXT.encode("utf-8"))
    calls = [(i, d) for i in range(len(MODULES)) for d in _depends(i)]
    return {"modules": list(MODULES), "calls": calls, "doc": "docs/overview.md"}


_OUTLINE_LINE = re.compile(r'^L(\d+) (\w+) (\S+) \| "(.*)"$')


def read_outline(s, root: str, path: str) -> list:
    """All entries ``(line, kind, name, anchor)`` of a file through the ``outline`` tool (pages followed until the file is done);
    entries the tool lists without an anchor (a duplicated line) are dropped, as an AI that needs a verified anchor would."""
    entries, offset = [], 0
    while True:
        reply = s.call("outline", "references", root=root, path=path, offset=offset)
        if reply["status"] != "ok":
            raise AssertionError(f"outline {path}: {json.dumps(reply)[:300]}")
        for line in reply["text"].split("\n"):
            m = _OUTLINE_LINE.match(line)
            if m:
                entries.append((int(m.group(1)), m.group(2), m.group(3), json.loads('"' + m.group(4) + '"')))
        nxt = reply.get("next")
        if not nxt or reply.get("shown", 0) >= reply.get("total", 0):
            return entries
        offset = int(nxt) if isinstance(nxt, int) or str(nxt).isdigit() else offset + int(reply.get("shown", 0))


def asis_slices(s, repo: dict) -> dict:
    """Author the slices of the as-is project: the anchors of the nodes come from the ``outline`` tool, the anchors of the edges from the call
    sites in the files (what an AI reads). Every fact is something the generator wrote into the repository."""
    names = repo["modules"]
    anchors: dict = {}
    for index, name in enumerate(names):
        entries = read_outline(s, "main", f"{PACKAGE}/{name}.py")
        wanted = {f"handle_{name}": None, f"{name.capitalize()}Service": None}
        for _line, kind, entry, anchor in entries:
            for key in wanted:
                if entry.endswith(key) and wanted[key] is None:
                    wanted[key] = anchor
        if wanted[f"handle_{name}"] is None:
            raise AssertionError(f"the outline of {name}.py lists no anchor for handle_{name}")
        anchors[name] = wanted
    rooms = [names[i:i + 5] for i in range(0, len(names), 5)]
    space = ("#atlas 1\n## buildings: id | name | opts\nzone.app | Application | trust=internal\nzone.edge | Edge | trust=perimeter\n"
             "## floors: id | building | name | level\nlvl.core | zone.app | Core modules | 1\nlvl.edge | zone.edge | Edge modules | 0\n"
             "## rooms: id | floor | name | opts\n" + "".join(f"rm.g{i} | {'lvl.edge' if i == 0 else 'lvl.core'} | Group {i + 1} | tier=T{1 + i % 3}\n" for i in range(len(rooms))))
    evidence_rows, ev_of, edge_ev = [], {}, {}
    number = 0
    for name in names:
        for key, label in ((f"handle_{name}", "handler"), (f"{name.capitalize()}Service", "service class")):
            if anchors[name][key] is not None:
                number += 1
                ev_of.setdefault(name, []).append(f"EV-{number}")
                evidence_rows.append(f"EV-{number} | {PACKAGE}/{name}.py | {anchors[name][key]} | the {label} of {name}")
    for caller, callee in repo["calls"]:
        number += 1
        edge_ev[(caller, callee)] = f"EV-{number}"
        evidence_rows.append(f"EV-{number} | {PACKAGE}/{names[caller]}.py | {call_line(caller, callee).strip()} | {names[caller]} hands the request to {names[callee]}")
    number += 1
    doc_ev = f"EV-{number}"
    evidence_rows.append(f"{doc_ev} | {repo['doc']} | ## Request flow | the documented flow")
    slices: dict = {"00-space": space}
    groups = [list(range(i, min(i + 9, len(names)))) for i in range(0, len(names), 9)]
    for g, members in enumerate(groups):
        rows = ["#atlas 1", "## nodes: id | name | sub | opts"]
        for i in members:
            room = f"rm.g{i // 5}"
            rows.append(f"mod.{names[i]} | {names[i].capitalize()} module | Handles the {names[i]} step of the request flow. | room={room} kind=Module ev={','.join(ev_of[names[i]])}")
        rows.append("## evidences: id | file | anchor | topic | opts")
        rows.append("@set repo=main")
        wanted = {e for i in members for e in ev_of[names[i]]}
        rows += [row for row in evidence_rows if row.split(" | ")[0] in wanted]
        slices[f"10-nodes-{chr(97 + g)}"] = "\n".join(rows) + "\n"
    edge_rows = ["#atlas 1", "## edges: from | rel | to | label | opts"]
    edge_rows += [f"mod.{names[a]} | calls | mod.{names[b]} | hand over the request | ev={edge_ev[(a, b)]}" for a, b in repo["calls"]]
    edge_rows += ["@final edges", "## evidences: id | file | anchor | topic | opts", "@set repo=main"]
    edge_rows += [row for row in evidence_rows if row.split(" | ")[0] in set(edge_ev.values())]
    edge_rows += [row for row in evidence_rows if row.startswith(doc_ev + " ")]
    slices["20-edges"] = "\n".join(edge_rows) + "\n"
    # cases: three call chains along real call edges; a call chain with returns completes every participant, and a node the chain never reaches stays Idle
    def chain(start: int) -> list:
        path, here = [start], start
        while _depends(here) and len(path) < 5:
            here = _depends(here)[0]
            path.append(here)
        return path
    cases, events, expects = [], [], []
    for number, start in enumerate((0, 1, 2), start=1):
        path = chain(start)
        cid = f"CS-FLOW-{number}"
        cases.append(f"{cid} | normal | The request travels from {names[path[0]]} to {names[path[-1]]} | req=RQ-1 ev={doc_ev}")
        events.append(f"@set case={cid}")
        for a, b in zip(path, path[1:]):
            events.append(f"Call | mod.{names[a]} | mod.{names[b]} | ruleId=R-{number}-{names[a]}")
        for a, b in reversed(list(zip(path, path[1:]))):
            events.append(f"Return | mod.{names[b]} | mod.{names[a]} | ruleId=R-{number}-{names[b]}-back")
        expects.append(f"{cid} | " + ",".join(f"mod.{names[i]}" for i in path) + " | by=author")
    slices["30-scenarios"] = ("#atlas 1\n## tests: id | title\nTS-1 | A request travels through the modules\n## cases: id | cat | title | opts\n" + "\n".join(cases)
                              + "\n## events: type | from | to | opts\n" + "\n".join(events) + "\n## expect: case | nodes | opts\n" + "\n".join(expects)
                              + "\n## interventions: id | type | description | opts\n"
                              + f'IV-STALL | disable_module | The {names[1]} module is down while a request travels | target=mod.{names[1]} applies=CS-FLOW-1 class=DIVERGED terminal={{"mod.{names[1]}":"Blocked"}}'
                              + f"\n## requirements: id | title | source | opts\nRQ-1 | A request flows through every module | Every module hands the request to the next one and stores a row. | "
                              f"nodes={','.join('mod.' + names[i] for i in chain(0))} tests=TS-1\n")
    slices["40-policy"] = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Each module keeps its own limit constant. | src=RQ-1 kind=nfr\n"
    return slices


def run_asis(s, repo: dict, *, finish: bool = True) -> dict:
    """Drive the as-is project to ``done`` (the repository is the evidence root alias ``main`` of the session)."""
    out: dict = {}
    s.call("brief", "orient", section="all", lang=LANG)
    out["init"] = s.call("init", "orient", title="Invented example repository", mode="asis", languages=[LANG], repos=["main"])
    slices = asis_slices(s, repo)
    for name, text in slices.items():
        out[f"put-{name}"] = s.put(name, text)
        if out[f"put-{name}"]["status"] != "committed":
            raise AssertionError(f"put {name}: {json.dumps(out[f'put-{name}'])[:600]}")
    out["slices"] = slices
    out["status-structure"] = s.status("author")
    if not finish:
        return out
    out["simulate"] = s.call("simulate", "gates")
    out["evidence"] = s.call("evidence", "gates")
    out["build"] = s.call("build", "gates")
    out["doc"] = s.call("doc", "gates", unit="system", level="BD", lang=LANG)
    out["verify"] = s.call("verify", "gates")
    if out["verify"].get("code") == "GEMARC_ATLAS_BUILD_031":
        s.waive_browser("this machine has no browser; the user opens the page by hand")
    out["status-gates"] = s.status("gates")
    out["report"] = s.call("review", "review", action="report")
    out["record"] = s.call("review", "review", action="record", notes="fixed=; open=")
    out["final"] = s.status("final")
    return out
