"""Seeded synthetic generator of EXPANDED ``gemarc-atlas/1`` models (test harness; standard library only).

Every derived field comes from the ``build_*`` / ``derive_*`` helpers of ``gemarc_mcp.atlas.model``; nothing is
derived here a second time. All content is invented: ids use SYNTHETIC id families (the fixed family ``svc. usr.
tbl. rm. zone. lvl.`` or a random family per seed) and text comes from small generic word pools. No data of any
real system is used or imitated.

Public API
----------
``generate(preset, seed=0, family="random", mode="asis")``  preset ``tiny`` | ``small`` | ``reference``
``edge_case(name, seed=0, family="random")``               names in ``EDGE_CASES``
``lending_desk(family="fixed")``                           the 4-node worked example of design 3.4
``Family.fixed()`` / ``Family.random(seed)``               id families
``estimate_atl(model)``                                    estimated size of the ``.atl`` authoring text (see its doc)
``section_counts(model)``                                  record counts of a model

Determinism: a tiny SplitMix64 generator (``Rng``) replaces ``random`` so that the same seed gives the same
model on every platform and Python version; nothing depends on ``hash()`` or on set ordering.

CLI (from the repository root)::

    PYTHONPATH=src python tests/atlas/synth.py write-fixture      # rewrite fixtures/lending_desk.model.json
    PYTHONPATH=src python tests/atlas/synth.py stats reference 7  # counts, sections and estimated size
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import vocab

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "fixtures"
LENDING_FIXTURE = FIXTURE_DIR / "lending_desk.model.json"

_MASK = (1 << 64) - 1


# --------------------------------------------------------------------------------------------------------
# Deterministic random numbers
# --------------------------------------------------------------------------------------------------------


class Rng:
    """SplitMix64 stream, identical on every platform and Python version; ``fork`` gives independent sub-streams."""

    def __init__(self, seed: int | str, label: str = ""):
        self.seed, self.label = seed, label
        digest = hashlib.sha256(f"{seed}|{label}".encode("utf-8")).digest()
        self._state = int.from_bytes(digest[:8], "big")

    def fork(self, label: str) -> "Rng":
        return Rng(self.seed, f"{self.label}/{label}")

    def _next(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return z ^ (z >> 31)

    def random(self) -> float:
        return (self._next() >> 11) / float(1 << 53)

    def below(self, n: int) -> int:
        return self._next() % n

    def randint(self, low: int, high: int) -> int:
        return low + self.below(high - low + 1)

    def chance(self, p: float) -> bool:
        return self.random() < p

    def choice(self, seq: Sequence):
        return seq[self.below(len(seq))]

    def shuffle(self, items: list) -> list:
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]
        return items

    def sample(self, seq: Sequence, k: int) -> list:
        pool = list(seq)
        k = min(k, len(pool))
        for i in range(k):
            j = i + self.below(len(pool) - i)
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:k]

    def weighted(self, pairs: Sequence[tuple]):
        total = sum(w for _, w in pairs)
        point = self.random() * total
        for value, weight in pairs:
            point -= weight
            if point < 0:
                return value
        return pairs[-1][0]


# --------------------------------------------------------------------------------------------------------
# Generic word pools (no domain of any real system)
# --------------------------------------------------------------------------------------------------------

EN_NOUNS = (
    "loan", "stock", "shelf", "copy", "member", "branch", "catalog", "volume", "renewal", "fine", "notice", "hold", "queue",
    "desk", "reader", "ticket", "archive", "ledger", "index", "search", "export", "import", "backup", "restore", "audit",
    "schedule", "batch", "worker", "router", "gateway", "cache", "session", "token", "profile", "preference", "mail",
    "report", "dashboard", "metric", "alert", "journal", "trace", "rule", "quota", "limit", "lock", "lease", "snapshot",
    "replica", "shard", "bucket", "topic", "stream", "feed", "mirror", "plan", "route", "parcel", "pallet", "depot", "dock",
    "lane", "slot", "window", "badge", "shift", "roster", "timer", "counter", "filter", "mapper", "parser", "scanner",
    "printer", "label", "barcode", "fleet", "driver", "vehicle", "trip", "stop", "zone", "grid", "tile", "layer", "stack",
    "buffer", "pool", "pipe", "valve", "pump", "sensor", "gauge", "panel", "console", "widget", "adapter", "bridge",
    "relay", "broker", "agent", "scout", "keeper", "clerk", "porter", "usher", "guard", "warden", "steward", "courier",
)
EN_VERBS = (
    "create", "find", "load", "save", "check", "apply", "build", "parse", "render", "send", "fetch", "update", "remove",
    "close", "open", "merge", "split", "verify", "sign", "sync", "route", "queue", "match", "count", "scan", "pack",
    "ship", "hold", "release", "renew", "assign", "audit", "notify", "export", "import", "reserve", "cancel", "confirm",
)
EN_ADJECTIVES = ("daily", "pending", "active", "shared", "stale", "primary", "backup", "manual", "bulk", "partial", "open", "late")
EN_CLAUSES = (
    "and records every change", "so repeated calls stay safe", "before the next step starts", "using the latest stored value",
    "without blocking the caller", "and reports a clear error when it fails", "for the current {n}", "once per {n}",
    "and keeps the {n} in step", "unless the {n} is already closed", "and hands the result back", "after checking the {n}",
    "so no {n} is processed twice", "and writes one line to the {n} log", "within the agreed time limit",
    "and skips anything that has not changed", "while the {n} stays readable by others", "and retries once on a timeout",
)
ZH_NOUNS = ("借阅", "库存", "书架", "读者", "分馆", "目录", "续借", "通知", "队列", "缓存", "网关", "日志", "索引", "任务", "报表", "会话", "令牌", "存储", "调度", "检索", "审计", "导出", "导入", "备份")
ZH_CLAUSES = ("并记录每一次变更", "重复调用也保持安全", "在下一步开始之前完成", "使用最近保存的值", "不阻塞调用方", "失败时给出明确错误")
JA_NOUNS = ("貸出", "返却", "在庫", "会員", "通知", "検索", "予約", "延滞", "分館", "目録", "書棚", "更新", "履歴", "集計", "照合", "配信")
JA_CLAUSES = ("変更をすべて記録し", "繰り返し呼んでも安全で", "次の処理の前に完了し", "最新の保存値を使って", "呼び出し側を止めずに", "失敗時は明確なエラーを返す")
GROUPS = ("front", "core", "store", "edge", "batch", "ops")
AREAS = ("intake", "lending", "stockroom", "reports", "access", "support", "ledger", "outreach")
ROOM_NOUNS = ("hall", "annex", "wing", "office", "vault", "floorplan", "workshop", "lobby")
STATE_POOLS = (
    ("Open", "Held", "Overdue", "Returned", "Lost", "Closed"),
    ("Draft", "Review", "Approved", "Rejected", "Published", "Archived"),
    ("Queued", "Active", "Paused", "Done", "Failed", "Expired"),
    ("Created", "Packed", "Shipped", "Delivered", "Returned", "Cancelled"),
)
GUARD_PARAMS = ("fee", "reason", "level", "flag", "count")
COLUMN_NAMES = ("id", "created_at", "status", "owner_id", "label", "amount", "due_at", "note", "version", "kind")
METHOD_NAMES = ("open()", "close()", "save(record)", "load(id)", "validate(input)", "apply(change)", "notify(target)")
FILE_DIRS = ("src", "app", "lib", "core", "service", "web", "worker", "tools")
FILE_EXTS = (".py", ".ts", ".js", ".go", ".java", ".rb", ".rs")
DOC_DIRS = ("docs", "design", "notes", "spec")
DOC_EXTS = (".md", ".md", ".md", ".rst", ".adoc")

_CONSONANTS = "bcdfghjklmnpqrstvwz"
_VOWELS = "aeiou"


class Text:
    """Realistic-length text from the generic pools; ``lang`` is ``en`` or ``cjk`` (zh and ja mixed)."""

    def __init__(self, rng: Rng, lang: str = "en"):
        self.rng, self.lang = rng, lang

    def noun(self) -> str:
        if self.lang == "cjk":
            return self.rng.choice(ZH_NOUNS if self.rng.chance(0.5) else JA_NOUNS)
        return self.rng.choice(EN_NOUNS)

    def verb(self) -> str:
        return self.rng.choice(EN_VERBS)

    def slug(self, words: int = 2) -> str:
        """Lower-case ASCII slug (ids stay ASCII even in the CJK text mode)."""
        return "-".join(self.rng.choice(EN_NOUNS) for _ in range(words))

    def title(self, words: int = 2) -> str:
        if self.lang == "cjk":
            return "".join(self.noun() for _ in range(words))
        text = " ".join(self.rng.choice(EN_NOUNS) for _ in range(words))
        return text[:1].upper() + text[1:]

    def clause(self, used: Sequence[str] = ()) -> str:
        """A trailing clause; ``used`` keeps one sentence from repeating itself."""
        pool = EN_CLAUSES
        if self.lang == "cjk":
            pool = ZH_CLAUSES if self.rng.chance(0.5) else JA_CLAUSES
        for _ in range(6):
            text = self.rng.choice(pool).replace("{n}", self.noun())
            if text not in used:
                break
        return text

    def sentence(self, target: int) -> str:
        """A sentence of about ``target`` characters (at least a few words)."""
        used: list[str] = []
        if self.lang == "cjk":
            text = self.noun() + "と" + self.noun() + "を扱い、" + self.clause()
            while len(text) < target:
                used.append(self.clause(used))
                text += "，" + used[-1]
            return text + "。"
        text = f"{_third_person(self.verb()).capitalize()} the {self.noun()} {self.rng.choice(('of', 'for', 'from', 'with'))} the {self.noun()}"
        while len(text) < target - 12:
            used.append(self.clause(used))
            text += ", " + used[-1]
        return text + "."

    def trimmed(self, target: int, slack: int = 8) -> str:
        """A sentence without the final full stop, cut at a word boundary to at most ``target + slack`` characters."""
        text = self.sentence(target).rstrip(".。")
        limit = target + slack
        if len(text) <= limit:
            return text
        comma = text.rfind(",", 0, limit)
        cut = comma if comma >= target // 2 else text.rfind(" ", 0, limit)
        return text[: cut if cut > 0 else limit].rstrip(" ,")

    def label(self, target: int) -> str:
        """A short action phrase such as ``check copy status`` of about ``target`` characters."""
        if self.lang == "cjk":
            return "".join(self.noun() for _ in range(max(2, target // 2)))
        text = f"{self.verb()} {self.noun()}"
        while len(text) < target - 4:
            text += " " + self.rng.choice(EN_NOUNS)
        return text


def _third_person(verb: str) -> str:
    if verb.endswith(("s", "x", "ch", "sh")):
        return verb + "es"
    if verb.endswith("y") and verb[-2:-1] not in ("a", "e", "i", "o", "u"):
        return verb[:-1] + "ies"
    return verb + "s"


# --------------------------------------------------------------------------------------------------------
# Id families
# --------------------------------------------------------------------------------------------------------

#: Kind -> prefix group; a family has one id prefix per group.
KIND_GROUP = {
    "Actor": "actor", "API": "service", "Workflow": "service", "Module": "service", "Service": "service",
    "Component": "service", "Class": "service", "Interface": "service", "Entity": "storage", "Table": "storage",
    "Storage": "storage", "Event": "event", "State": "state",
}
FAMILY_FIELDS = (
    "building", "floor", "room", "actor", "service", "storage", "event", "state", "element", "evidence", "case",
    "requirement", "intervention", "test", "tbd", "policy", "rule", "alias", "transition",
)
UPPER_FIELDS = ("evidence", "case", "requirement", "intervention", "test", "tbd", "policy", "rule")
FIXED_PREFIXES = {
    "building": "zone", "floor": "lvl", "room": "rm", "actor": "usr", "service": "svc", "storage": "tbl", "event": "evt",
    "state": "st", "element": "el", "evidence": "EVD", "case": "CS", "requirement": "RQ", "intervention": "IV", "test": "TS",
    "tbd": "TBD", "policy": "PL", "rule": "R", "alias": "e", "transition": "tr",
}


#: Alphabet of a random prefix: two lower-case letters and one digit (``kb7``). The space is small (6,760 words) on
#: purpose: the token-list test lists every member and checks the tokens of each against the hashed token list, so no
#: seed can ever produce a prefix that a token list built from a real system contains. Tokens of fewer than three
#: letters never enter such a list, and the letters of ``kb7`` are tokenised as ``kb`` and ``7``.
PREFIX_LETTERS = "abcdefghijklmnopqrstuvwxyz"
PREFIX_DIGITS = "0123456789"


def random_prefix(rng: "Rng") -> str:
    return rng.choice(PREFIX_LETTERS) + rng.choice(PREFIX_LETTERS) + rng.choice(PREFIX_DIGITS)


def all_random_prefixes() -> list[str]:
    """Every word ``random_prefix`` can return (lower case; record classes use the upper-case spelling)."""
    return [a + b + d for a in PREFIX_LETTERS for b in PREFIX_LETTERS for d in PREFIX_DIGITS]


@dataclass(frozen=True)
class Family:
    """One id prefix per node group and record class (``svc.loan``, ``EVD-1``, ``zone.lib`` ...)."""

    name: str
    prefixes: tuple[tuple[str, str], ...]

    @property
    def p(self) -> dict[str, str]:
        return dict(self.prefixes)

    def __getitem__(self, key: str) -> str:
        return self.p[key]

    def values(self) -> tuple[str, ...]:
        return tuple(v for _, v in self.prefixes)

    def node_prefix(self, kind: str) -> str:
        return self[KIND_GROUP.get(kind, "service")]

    @classmethod
    def fixed(cls) -> "Family":
        return cls("fixed", tuple((f, FIXED_PREFIXES[f]) for f in FAMILY_FIELDS))

    @classmethod
    def random(cls, seed: int) -> "Family":
        """A family that is a pure function of ``seed`` (it never reads any file, so rebuilding a token list cannot shift it).

        Every prefix is two letters and a digit; see ``PREFIX_LETTERS`` for why that space is safe by construction."""
        rng = Rng(seed, "family")
        banned = {v.lower() for v in FIXED_PREFIXES.values()}
        chosen: list[tuple[str, str]] = []
        for field_name in FAMILY_FIELDS:
            while True:
                word = random_prefix(rng)
                if word in banned:
                    continue
                banned.add(word)
                chosen.append((field_name, word.upper() if field_name in UPPER_FIELDS else word))
                break
        return cls(f"random-{seed}", tuple(chosen))

    @classmethod
    def resolve(cls, family: "str | Family", seed: int) -> "Family":
        if isinstance(family, Family):
            return family
        if family == "fixed":
            return cls.fixed()
        if family == "random":
            return cls.random(seed)
        raise ValueError(f"family must be 'fixed', 'random' or a Family, got {family!r}")


# --------------------------------------------------------------------------------------------------------
# Scales
# --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scale:
    """How big a generated model is and how long its text runs (lengths are characters)."""

    name: str
    buildings: int = 2
    floors: int = 3
    rooms: int = 5
    nodes: int = 40
    machines: int = 1
    edges: int = 90
    evidences: int = 50
    doc_share: float = 0.25
    user_share: float = 0.04
    cases: int = 5
    events: int = 60
    interventions: int = 8
    requirements: int = 6
    tbds: int = 6
    tests: int = 8
    policies: int = 6
    override_share: float = 0.15
    inherit_share: float = 0.08
    alias_edges: int = 3
    props_share: float = 0.18
    group_share: float = 0.2
    # text length targets
    name_len: int = 14
    sub_len: int = 90
    label_len: int = 24
    topic_len: int = 42
    anchor_len: int = 22
    doc_anchor_len: int = 40
    case_title_len: int = 40
    description_len: int = 72
    note_len: int = 40
    requirement_len: int = 52
    source_len: int = 14
    tbd_len: int = 28
    decision_len: int = 90
    policy_len: int = 80
    slug_words: int = 2
    space_words: int = 2
    extra_transitions: int = 2
    note_share: float = 0.4
    why_share: float = 0.5
    # behaviour flags used by the edge-case models
    lang: str = "en"
    long_ids: int = 0          # nodes with ~58-character ids (their edges fall back to hash ids)
    odd_ids: bool = False      # dots / dashes / colons in ids where the grammar allows them
    empty_strings: bool = False
    unknown_values: bool = False
    node_evidence: bool = True
    # richer content (all default to off; the ``reference`` preset and the ``rich-fields`` edge case switch them on)
    event_spread: bool = False          # case lengths follow a heavy tail between 4 and 48 events (sum stays ``events``)
    machine_transitions: tuple[int, int] | None = None  # transitions per machine, inclusive range
    when_share: float = 0.0             # share of events with ``when``
    guard_share: float = 0.0            # share of events with ``guard`` (and an ``errorCode``)
    code_share: float = 0.0             # share of other events with an ``errorCode``
    compensation_share: float = 0.0     # abnormal/recovery cases that end with a Compensation event
    dedup_nodes: int = 0                # nodes with ``props.dedup``; events into them carry a ``dedupKey`` (some twice)
    link_share: float = 0.0             # share of interface/class/service nodes with ``props.owner`` / ``store`` / ``enums``
    reference_edges: int = 0            # ``references`` edges between table/entity nodes (they carry a cardinality)
    disposition_mix: float = 0.0        # share of insert_element interventions with an extra keep or disable rewrite
    combined_rich: float = 0.0          # share of combined interventions that hold an insert_element and a change_guard part
    stamp_share: float = 0.0            # share of evidences that already carry a verifier ``lineRange``
    event_target_share: float = 0.0     # share of delay_message interventions that name one event (``<caseId>#<seq>``)


PRESETS: dict[str, Scale] = {
    "tiny": Scale("tiny"),  # replaced by the hand-written lending desk (see ``generate``)
    "small": Scale(
        "small", buildings=2, floors=4, rooms=8, nodes=40, machines=1, edges=88, evidences=46, cases=5, events=62,
        interventions=8, requirements=6, tbds=6, tests=8, policies=6, alias_edges=3,
    ),
    # 169 nodes / 374 edges / 242 evidences / 23 cases with 322 events / 21 interventions / 16 requirements / 23 TBDs.
    # Text lengths are tuned so the estimated .atl text lands near 166 KB with the per-section proportions of design 3.5.
    # The per-section proportions (nodes 38.6K, edges 41.5K, evidences 32.0K, cases 29.5K, interventions 9.7K,
    # requirements 5.5K, TBDs 4.4K, spatial 3.1K, machines 1.7K) are reproduced within about 5% on six seeds.
    "reference": Scale(
        "reference", buildings=4, floors=10, rooms=31, nodes=169, machines=5, edges=374, evidences=242, doc_share=0.28,
        user_share=0.05, cases=23, events=322, interventions=21, requirements=16, tbds=23, tests=22, policies=12,
        alias_edges=12, name_len=20, sub_len=130, label_len=44, topic_len=64, anchor_len=24, doc_anchor_len=56,
        case_title_len=62, description_len=152, note_len=104, requirement_len=116, source_len=24, tbd_len=52, decision_len=120,
        policy_len=96, props_share=0.2, space_words=3, extra_transitions=3, note_share=0.6, why_share=0.8,
        # the reference has case lengths from 4 to 48 events, machines of 3 to 10 transitions, 19 events with ``when``,
        # 2 with ``guard`` and about 10 deduplicated ones (design 3.5 gives no more than the counts above)
        event_spread=True, machine_transitions=(3, 10), when_share=0.05, guard_share=0.01, code_share=0.02,
        compensation_share=0.3, dedup_nodes=4, link_share=0.4, reference_edges=6, disposition_mix=0.3, combined_rich=0.4,
        event_target_share=0.2,
    ),
}


# --------------------------------------------------------------------------------------------------------
# The generator
# --------------------------------------------------------------------------------------------------------


def _spread_lengths(rng: Rng, count: int, total: int, low: int = 4, high: int = 48) -> list[int]:
    """``count`` case lengths in ``[low, high]`` that sum to ``total``: many short cases, a few long ones, and always
    one case of exactly ``low`` and one of exactly ``high`` events when the total allows it."""
    if count <= 0:
        return []
    if not count * low <= total <= count * high:
        return [total // count + (1 if k < total % count else 0) for k in range(count)]
    lengths = [low + int((high - low) * rng.random() ** 2.4) for _ in range(count)]
    if count >= 2 and low + high + (count - 2) * low <= total <= low + high + (count - 2) * high:
        first = rng.below(count)
        second = (first + 1 + rng.below(count - 1)) % count
        lengths[first], lengths[second] = low, high
        pinned = {first, second}
    else:
        pinned = set()
    free = [k for k in range(count) if k not in pinned] or list(range(count))
    current = sum(lengths)
    while current != total:
        k = free[rng.below(len(free))]
        if current < total and lengths[k] < high:
            lengths[k] += 1
            current += 1
        elif current > total and lengths[k] > low:
            lengths[k] -= 1
            current -= 1
    return lengths

KIND_WEIGHTS = (
    ("Actor", 6), ("API", 5), ("Workflow", 4), ("Module", 14), ("Service", 14), ("Component", 12), ("Class", 6),
    ("Interface", 5), ("Entity", 4), ("Table", 8), ("Storage", 4), ("Event", 8),
)
RUNTIME_FLOW = ("calls", "sends", "reads", "writes", "publishes", "subscribes", "triggers", "receives")
CASE_CATEGORY_WEIGHTS = (("normal", 9), ("abnormal", 6), ("boundary", 3), ("concurrency", 2), ("recovery", 2), ("combination", 1))
INTERVENTION_CYCLE = (
    "disable_module", "disable_edge", "resource_failure", "change_timeout", "delay_message", "insert_element",
    "change_retry", "disable_module", "combined_intervention", "change_guard", "disable_edge", "identity_failure",
    "disable_module", "delay_message", "combined_intervention", "change_timeout", "insert_element", "disable_edge",
    "change_retry", "resource_failure", "disable_module",
)
ELEMENT_KIND_HANDLER = {"Cache": ("receive", "forward"), "Proxy": ("receive", "forward"), "Validator": ("receive", "respond")}


class _Generator:
    def __init__(self, scale: Scale, seed: int, family: Family, mode: str):
        self.s, self.seed, self.fam, self.mode = scale, seed, family, mode
        self.profile = vocab.default_profile()
        self.base = Rng(seed, scale.name)
        self.used: set[str] = set()
        self.codes: dict[str, int] = {}
        # filled by the steps
        self.buildings: list[dict] = []
        self.floors: list[dict] = []
        self.rooms: list[dict] = []
        self.evidences: list[dict] = []
        self.ev_index: dict[str, dict] = {}
        self.nodes: list[dict] = []
        self.node_by_id: dict[str, dict] = {}
        self.machines: list[dict] = []
        self.machine_ids: list[str] = []
        self.edges: list[dict] = []
        self.requirements: list[dict] = []
        self.tests: list[dict] = []
        self.tbds: list[dict] = []
        self.cases: list[dict] = []
        self.interventions: list[dict] = []
        self.policies: list[dict] = []
        self.repos: list[str] = []
        self.rules: list[str] = []
        self.alias_edge_ids: list[str] = []

    # ---- helpers --------------------------------------------------------------------------------------

    def rng(self, label: str) -> Rng:
        return self.base.fork(label)

    def text(self, label: str) -> Text:
        return Text(self.rng(label), self.s.lang)

    def claim(self, identifier: str) -> str:
        """Reserve a globally unique authored id (the generator never reuses an id across record classes)."""
        candidate, n = identifier, 1
        while candidate in self.used:
            n += 1
            candidate = f"{identifier}-{n}"
        self.used.add(candidate)
        return candidate

    def code(self, kind: str) -> int:
        self.codes[kind] = self.codes.get(kind, 0) + 1
        return self.codes[kind]

    def building_of_room(self, room: Mapping) -> dict:
        floor = next(f for f in self.floors if f["id"] == room["floorId"])
        return next(b for b in self.buildings if b["id"] == floor["buildingId"])

    def room_by_id(self, room_id: str) -> dict:
        return next(r for r in self.rooms if r["id"] == room_id)

    def ev_kinds(self, ids: Iterable[str]) -> list[str]:
        return am.evidence_kinds_of(ids, self.ev_index)

    # ---- space ----------------------------------------------------------------------------------------

    def space(self) -> None:
        s, t, rng = self.s, self.text("space"), self.rng("space")
        trusts = ["internal"] if s.buildings == 1 else ["external", "perimeter", "internal", "restricted"]
        for i in range(s.buildings):
            bid = self.claim(f"{self.fam['building']}.{t.slug(1)}")
            self.buildings.append(am.build_building(bid, t.title(s.space_words) + " system" if i else "Outside", trusts[i % len(trusts)]))
        per_building: dict[str, int] = {}
        for j in range(s.floors):
            b = self.buildings[j % s.buildings]
            level = per_building.get(b["id"], 0)
            per_building[b["id"]] = level + 1
            fid = self.claim(f"{self.fam['floor']}.{t.slug(1)}")
            self.floors.append(am.build_floor(fid, b["id"], t.title(s.space_words), level))
        tier_by_trust = {"external": ("T1",), "perimeter": ("T2", "T3"), "internal": ("T2", "T3", "T4"), "restricted": ("T4", "T5")}
        for k in range(s.rooms):
            floor = self.floors[k % s.floors]
            building = next(b for b in self.buildings if b["id"] == floor["buildingId"])
            rid = self.claim(f"{self.fam['room']}.{t.slug(1)}")
            area = rng.choice(AREAS) if rng.chance(0.6) else None
            layer = "business" if (s.machines and k == s.rooms - 1) else None
            self.rooms.append(am.build_room(rid, floor["id"], f"{t.title(s.space_words)} {rng.choice(ROOM_NOUNS)}",
                                            layer=layer, tier=rng.choice(tier_by_trust[building["trust"]]), area=area))

    # ---- evidences ------------------------------------------------------------------------------------

    def evidence(self) -> None:
        s, t, rng = self.s, self.text("evidence"), self.rng("evidence")
        word = t.slug(1).replace("-", "")
        self.repos = ["main"] if self.fam.name == "fixed" else [f"{word}{rng.randint(2, 9)}"]
        if s.evidences > 120:
            self.repos.append(f"{t.slug(1)}-app" if self.fam.name != "fixed" else "web")
        files: list[str] = []
        for i in range(s.evidences):
            roll = rng.random()
            repo = self.repos[0] if rng.chance(0.8) else self.repos[-1]
            if roll < s.user_share:
                path = f"user:{t.slug(1)}"
                anchor, repo = t.sentence(s.doc_anchor_len + 16), ""
            elif roll < s.user_share + s.doc_share:
                if files and rng.chance(0.4) and files[-1].startswith(DOC_DIRS):
                    path = files[-1]
                else:
                    path = f"{rng.choice(DOC_DIRS)}/{t.slug(1)}{rng.choice(DOC_EXTS)}"
                anchor = t.trimmed(s.doc_anchor_len + 6)
                anchor = anchor[:1].lower() + anchor[1:]
            else:
                if files and rng.chance(0.35) and files[-1].startswith(FILE_DIRS):
                    path = files[-1]
                else:
                    path = f"{rng.choice(FILE_DIRS)}/{t.slug(1)}{rng.choice(FILE_EXTS)}"
                style = rng.below(3)
                noun = t.noun() if s.lang == "en" else t.slug(1)
                anchor = (f"def {t.verb()}_{noun}(" if style == 0 else f"class {noun.capitalize()}{rng.choice(('Service', 'Store', 'Job'))}"
                          if style == 1 else f"function {t.verb()}{noun.capitalize()}(")
                while len(anchor) < s.anchor_len:
                    anchor = anchor.replace("(", f"_{t.noun()}(", 1) if "(" in anchor else anchor + t.noun().capitalize()
            files.append(path)
            if s.odd_ids:
                eid = self.claim(f"{self.fam['evidence']}:{2026 + i % 3}:{i + 1:03d}-{t.slug(1)}.v{i % 4}")
            else:
                eid = self.claim(f"{self.fam['evidence']}-{i + 1}")
            line_range = None
            if s.stamp_share:
                stamp = self.rng(f"stamp:{i}")
                if stamp.chance(s.stamp_share):
                    first = stamp.randint(1, 400)
                    line_range = [first, first + stamp.randint(0, 25)]
            record = am.build_evidence(eid, path, anchor, t.trimmed(s.topic_len), repo, mode=self.mode, line_range=line_range, profile=self.profile)
            self.evidences.append(record)
            self.ev_index[eid] = record

    # ---- nodes ----------------------------------------------------------------------------------------

    def _evidence_for(self, rng: Rng, index: int, total: int, weights: Sequence[tuple[int, int]]) -> list[str]:
        if not self.evidences or not self.s.node_evidence:
            return []
        count = rng.weighted(weights)
        out: list[str] = []
        base = (index * len(self.evidences)) // max(1, total)
        for j in range(count):
            pick = self.evidences[(base + j * 7 + rng.below(3)) % len(self.evidences)]["evidenceId"]
            if pick not in out:
                out.append(pick)
        return out

    def _verification_override(self, rng: Rng, default: str) -> str | None:
        if not rng.chance(self.s.override_share):
            return None
        if default == "Implemented":
            return rng.choice(("Designed", "Inferred"))
        if default == "Designed":
            return "Inferred"
        return None

    def nodes_step(self) -> None:
        s, t, rng = self.s, self.text("nodes"), self.rng("nodes")
        regular = s.nodes - s.machines
        actor_rooms = [r for r in self.rooms if self.building_of_room(r)["trust"] == "external"] or self.rooms
        plain_rooms = [r for r in self.rooms if r.get("layer") != "business"] or self.rooms
        business = next((r for r in self.rooms if r.get("layer") == "business"), self.rooms[-1])
        for i in range(regular):
            kind = rng.weighted(KIND_WEIGHTS)
            long_id = i < s.long_ids
            slug = t.slug(s.slug_words)
            if long_id:
                slug = "-".join(t.slug(2) for _ in range(5))[:56]
            node_id = self.claim(f"{self.fam.node_prefix(kind)}.{slug}")
            if s.odd_ids and not long_id and i % 5 == 0:
                node_id = self.claim(f"{self.fam.node_prefix(kind)}.{t.slug(1)}_{i}.v{i % 3}-x")
            room = rng.choice(actor_rooms if kind == "Actor" else plain_rooms)
            building = self.building_of_room(room)
            evidence_ids = self._evidence_for(rng, i, regular, ((0, 6), (1, 54), (2, 30), (3, 10)))
            default = am.default_verification_for_node(self.ev_kinds(evidence_ids), self.mode, profile=self.profile)
            props = self._props(rng, t, kind)
            name = t.title(max(2, s.name_len // 8))[: s.name_len + 6]
            sub = "" if (s.empty_strings and i % 2 == 0) else t.sentence(s.sub_len)
            if s.empty_strings and i % 3 == 0:
                evidence_ids = []
                default = am.default_verification_for_node([], self.mode, profile=self.profile)
            node = am.build_node(
                node_id, kind, name, room["id"], sub, evidence_ids, self._verification_override(rng, default), props,
                rng.choice(GROUPS) if rng.chance(s.group_share) else None, "DD" if (kind == "Module" and rng.chance(0.02)) else None,
                evidence_kinds=self.ev_kinds(evidence_ids), mode=self.mode, room=room, building=building, profile=self.profile)
            self._add_node(node)
        for k in range(s.machines):
            slug = t.slug(2)
            node_id = self.claim(f"{self.fam['state']}.{slug}")
            evidence_ids = self._evidence_for(rng, k, s.machines, ((1, 70), (2, 30)))
            node = am.build_node(
                node_id, "State", f"{t.title(2)} status", business["id"], t.sentence(s.sub_len), evidence_ids, None, None,
                None, None, evidence_kinds=self.ev_kinds(evidence_ids), is_machine=True, mode=self.mode, room=business,
                building=self.building_of_room(business), profile=self.profile)
            self._add_node(node)
            self.machine_ids.append(node_id)

    def _add_node(self, node: dict) -> None:
        self.nodes.append(node)
        self.node_by_id[node["id"]] = node

    def dedup_step(self) -> None:
        """``props.dedup`` on a few nodes that receive events (the events into them carry a ``dedupKey``)."""
        s = self.s
        if not s.dedup_nodes:
            return
        rng, t = self.rng("dedup"), self.text("dedup")
        candidates = [n for n in self.nodes if n["kind"] in ("Module", "Service", "Component", "API") and n["id"] not in self.machine_ids]
        for node in rng.sample(candidates, min(s.dedup_nodes, len(candidates))):
            dedup: dict = {"mode": rng.choice(("reject", "ignore")), "key": rng.choice(("request-id", "idempotency-key", "message-id"))}
            if dedup["mode"] == "reject":
                dedup.update(errorCode="E_" + t.slug(1).upper().replace("-", "_"), httpStatus=409)
            node["props"]["dedup"] = dedup

    def links_step(self) -> None:
        """``props.owner`` / ``store`` / ``enums``: the data the class diagram, ER view and documents read."""
        s = self.s
        if not s.link_share:
            return
        rng = self.rng("links")
        regular = [n for n in self.nodes if n["id"] not in self.machine_ids]
        owners = [n for n in regular if n["kind"] in ("Module", "Service", "Component")]
        stores = [n for n in regular if n["kind"] in ("Table", "Entity", "Storage")]
        machine_states = {m["id"]: m["states"] for m in self.machines}
        for node in regular:
            if not rng.chance(s.link_share):
                continue
            if node["kind"] in ("Interface", "Class") and owners:
                node["props"]["owner"] = rng.choice(owners)["id"]
                if node["kind"] == "Interface" and machine_states:
                    node["props"]["enums"] = {"Status": list(rng.choice(sorted(machine_states.values(), key=len)))}
            elif node["kind"] in ("Module", "Service", "Component") and stores:
                node["props"]["store"] = rng.choice(stores)["id"]

    def _props(self, rng: Rng, t: Text, kind: str) -> dict | None:
        if not rng.chance(self.s.props_share):
            return None
        if kind in ("Table", "Entity", "Storage"):
            cols = rng.sample(COLUMN_NAMES, rng.randint(3, 5))
            return {"columns": [{"name": c, "pk": True} if c == "id" else c for c in cols]}
        if kind == "Class":
            return {"methods": rng.sample(METHOD_NAMES, rng.randint(2, 3))}
        if kind == "API":
            return {"route": "/v1/" + t.slug(1)}
        if kind in ("Module", "Service", "Component"):
            return {"aggregator": True} if rng.chance(0.25) else {"stack": rng.choice(("python", "node", "go", "jvm"))}
        return {"role": t.noun()}

    # ---- machines -------------------------------------------------------------------------------------

    def machines_step(self) -> None:
        s, t, rng = self.s, self.text("machines"), self.rng("machines")
        owners = [n for n in self.nodes if n["kind"] in ("Module", "Service", "Component") and n["id"] not in self.machine_ids]
        self.transition_ids_to_reference: list[tuple[str, str]] = []
        for k, machine_id in enumerate(self.machine_ids):
            node = self.node_by_id[machine_id]
            pool = list(rng.choice(STATE_POOLS))[: rng.randint(4, 6)]
            transitions = []
            for i in range(len(pool) - 1):
                transitions.append({"from": pool[i], "to": pool[i + 1]})
            for _ in range(rng.randint(s.extra_transitions, s.extra_transitions + 2)):
                a, b = rng.randint(0, len(pool) - 2), rng.randint(1, len(pool) - 1)
                if a != b and not any(x["from"] == pool[a] and x["to"] == pool[b] for x in transitions):
                    transitions.append({"from": pool[a], "to": pool[b]})
            if s.machine_transitions:
                transitions = self._fit_transitions(self.rng(f"machine-size:{k}"), pool, transitions, *s.machine_transitions)
            rng.shuffle(transitions)
            guarded = rng.sample(range(len(transitions)), min(2, len(transitions)))
            for g_index, position in enumerate(guarded):
                tr = transitions[position]
                tr["id"] = self.claim(f"{self.fam['transition']}.{t.slug(1)}")
                tr["guard"] = {"param": rng.choice(GUARD_PARAMS), "op": rng.choice(("present", "absent"))}
                tr["errorCode"] = "E_" + t.slug(1).upper().replace("-", "_")
                self.transition_ids_to_reference.append((machine_id, tr["id"]))
            machine = am.build_machine(machine_id, pool[0], transitions, "E_" + t.slug(1).upper().replace("-", "_") if rng.chance(0.5) else None,
                                       node["evidenceIds"])
            self.machines.append(machine)
        self._owners = owners

    @staticmethod
    def _fit_transitions(rng: Rng, pool: list[str], transitions: list[dict], low: int, high: int) -> list[dict]:
        """Trim or grow ``transitions`` to a random size in ``[low, high]`` (the pool limits the upper end)."""
        wanted = rng.randint(low, high)
        pairs = [(a, b) for a in pool for b in pool if a != b]
        taken = {(x["from"], x["to"]) for x in transitions}
        spare = rng.shuffle([pair for pair in pairs if pair not in taken])
        while len(transitions) < wanted and spare:
            a, b = spare.pop()
            transitions.append({"from": a, "to": b})
        return transitions[:wanted]

    # ---- edges ----------------------------------------------------------------------------------------

    def _relation(self, rng: Rng, source: dict, target: dict) -> str:
        sk, tk = source["kind"], target["kind"]
        if tk in ("Table", "Entity", "Storage"):
            if sk in ("Table", "Entity"):
                return "references"
            return rng.weighted((("reads", 5), ("writes", 5), ("dependsOn", 1)))
        if tk == "Event":
            return rng.weighted((("publishes", 5), ("subscribes", 4), ("triggers", 1)))
        if tk == "Interface" and sk == "Class":
            return "implements"
        if sk == "Event":
            return rng.weighted((("triggers", 3), ("calls", 1)))
        if tk == "Actor":
            return rng.weighted((("sends", 3), ("receives", 1)))
        return rng.weighted((("calls", 55), ("sends", 9), ("dependsOn", 9), ("triggers", 4), ("contains", 4), ("constrains", 3),
                             ("receives", 3), ("belongsTo", 4), ("implements", 2)))

    def edges_step(self) -> None:
        s, t, rng = self.s, self.text("edges"), self.rng("edges")
        regular = [n for n in self.nodes if n["id"] not in self.machine_ids]
        sources = [n for n in regular if n["kind"] not in ("Table", "Entity", "Storage", "Event")] or regular
        source_ids = {n["id"] for n in sources}
        owns_count = len(self.machines)
        data_nodes = [n for n in regular if n["kind"] in ("Table", "Entity")]
        reference_count = min(s.reference_edges, len(data_nodes) * (len(data_nodes) - 1)) if len(data_nodes) > 1 else 0
        authored_target = max(0, s.edges - owns_count - reference_count)
        seen: set[tuple[str, str, str]] = set()
        authored: list[dict] = []

        def add(source: dict, target: dict, relation: str, alias: str | None = None) -> bool:
            if source["id"] == target["id"] and relation != "calls":
                return False
            key = (source["id"], relation, target["id"])
            if key in seen and alias is None:
                return False
            seen.add(key)
            inherited = False
            if rng.chance(s.inherit_share) and (source["evidenceIds"] or target["evidenceIds"]) and alias is None:
                evidence_ids, inherited = am.inherit_edge_evidence(source["evidenceIds"], target["evidenceIds"]), True
            else:
                evidence_ids = self._edge_evidence(rng, source, target)
            default = am.default_verification_for_edge(self.ev_kinds(evidence_ids), self.mode, inherited, profile=self.profile)
            card = rng.choice(("N:1", "1:N", "N:N", "1:0..1")) if relation == "references" else None
            edge = am.build_edge(
                source["id"], relation, target["id"], t.label(s.label_len), evidence_ids, self._verification_override(rng, default), card,
                rng.choice(GROUPS) if rng.chance(s.group_share * 0.5) else None, alias, evidence_kinds=self.ev_kinds(evidence_ids),
                inherited=inherited, machine_ids=self.machine_ids, mode=self.mode, profile=self.profile)
            authored.append(edge)
            return True

        # spanning structure so that every node is reachable from an earlier one
        for index, node in enumerate(regular):
            if index == 0:
                continue
            earlier = [n for n in regular[:index] if n["id"] in source_ids] or regular[:index]
            parent = rng.choice(earlier)
            add(parent, node, self._relation(rng, parent, node))
        guard = 0
        while len(authored) < authored_target - s.alias_edges and guard < authored_target * 30:
            guard += 1
            source, target = rng.choice(sources), rng.choice(regular)
            add(source, target, self._relation(rng, source, target))
        del authored[max(0, authored_target - s.alias_edges):]
        # parallel edges that need an alias (a repeated triple derives the same id)
        for _ in range(min(s.alias_edges, len(authored))):
            base = rng.choice([e for e in authored if "alias" not in e and e["id"] == am.edge_id(e["sourceId"], e["relation"], e["targetId"])])
            alias = self.claim(f"{self.fam['alias']}.{t.slug(1)}")
            add(self.node_by_id[base["sourceId"]], self.node_by_id[base["targetId"]], base["relation"], alias)
            self.alias_edge_ids.append(alias)
        # logical foreign keys between data nodes: the only edges that carry a cardinality
        links = self.rng("reference-edges")
        added = 0
        for _ in range(reference_count * 20):
            if added >= reference_count:
                break
            source, target = links.choice(data_nodes), links.choice(data_nodes)
            if source is not target and (source["id"], "references", target["id"]) not in seen:
                added += add(source, target, "references")
        for edge in authored:
            self.used.add(edge["id"])
        self.edges = authored
        # generated owner edges come last, in machine order (build_owns_edge is the single source of their shape)
        for machine in self.machines:
            owner = rng.choice(self._owners) if self._owners else regular[0]
            node = self.node_by_id[machine["nodeId"]]
            owns = am.build_owns_edge(owner["id"], machine, node, evidence_kinds=self.ev_kinds(machine["evidenceIds"]),
                                      machine_ids=self.machine_ids, mode=self.mode, profile=self.profile)
            self.used.add(owns["id"])
            self.edges.append(owns)

    def _edge_evidence(self, rng: Rng, source: dict, target: dict) -> list[str]:
        if not self.evidences or not self.s.node_evidence:
            return []
        count = rng.weighted(((0, 8), (1, 56), (2, 28), (3, 8)))
        pool = list(dict.fromkeys(source["evidenceIds"] + target["evidenceIds"]))
        out: list[str] = []
        for _ in range(count):
            pick = rng.choice(pool) if pool and rng.chance(0.8) else rng.choice(self.evidences)["evidenceId"]
            if pick not in out:
                out.append(pick)
        return out

    # ---- tests, requirements, tbds ---------------------------------------------------------------------

    def requirements_step(self) -> None:
        s, t, rng = self.s, self.text("requirements"), self.rng("requirements")
        for i in range(s.tests):
            tid = self.claim(f"{self.fam['test']}:{t.slug(1)}.{i + 1}" if s.odd_ids else f"{self.fam['test']}-{i + 1}")
            self.tests.append(am.build_test(tid, t.trimmed(30)))
        for i in range(s.tbds):
            tid = self.claim(f"{self.fam['tbd']}-{i + 1}")
            ev = rng.sample([e["evidenceId"] for e in self.evidences], rng.randint(0, 2)) if self.evidences else []
            self.tbds.append(am.build_tbd(tid, t.trimmed(s.tbd_len), t.sentence(s.decision_len), ev))
        regular = [n for n in self.nodes if n["id"] not in self.machine_ids]
        adjacency: dict[str, list[str]] = {}
        for e in self.edges:
            adjacency.setdefault(e["sourceId"], []).append(e["targetId"])
            adjacency.setdefault(e["targetId"], []).append(e["sourceId"])
        for i in range(s.requirements):
            rid = self.claim(f"{self.fam['requirement']}-{i + 1}")
            seed_node = rng.choice(regular)["id"]
            members, frontier = [seed_node], [seed_node]
            goal = rng.randint(3, 12)
            while frontier and len(members) < goal:
                current = frontier.pop(0)
                for other in adjacency.get(current, []):
                    if other not in members and other not in self.machine_ids and len(members) < goal:
                        members.append(other)
                        frontier.append(other)
            tests = rng.sample([x["id"] for x in self.tests], rng.randint(1, 2)) if self.tests else []
            ev = rng.sample([e["evidenceId"] for e in self.evidences], rng.randint(1, 3)) if self.evidences else []
            tbd = rng.sample([x["id"] for x in self.tbds], rng.randint(0, 2)) if self.tbds and rng.chance(0.5) else []
            design = [f"{rng.choice(('BD', 'DD'))}-{rng.randint(1, 9)}.{rng.randint(1, 6)}"] if rng.chance(0.6) else []
            self.requirements.append(am.build_requirement(
                rid, t.trimmed(s.requirement_len), t.trimmed(s.source_len) if rng.chance(0.7) else f"interview {i + 1}",
                members, None, tests, ev, tbd, design, edges=self.edges))

    # ---- cases and events ----------------------------------------------------------------------------

    def cases_step(self) -> None:
        s, t, rng = self.s, self.text("cases"), self.rng("cases")
        if not s.cases:
            return
        runtime = [e for e in self.edges if not e["static"] and e["relation"] != "owns"
                   and e["sourceId"] not in self.machine_ids and e["targetId"] not in self.machine_ids]
        out_edges: dict[str, list[dict]] = {}
        for e in runtime:
            out_edges.setdefault(e["sourceId"], []).append(e)
        entries = [n["id"] for n in self.nodes if n["kind"] in ("Actor", "API") and out_edges.get(n["id"])] or list(out_edges)
        owners = {m["id"]: next((e for e in self.edges if e["relation"] == "owns" and e["targetId"] == m["id"]), None) for m in self.machines}
        per_case = (_spread_lengths(self.rng("case-lengths"), s.cases, s.events) if s.event_spread
                    else [s.events // s.cases + (1 if k < s.events % s.cases else 0) for k in range(s.cases)])
        categories = [rng.weighted(CASE_CATEGORY_WEIGHTS) for _ in range(s.cases)]
        if s.compensation_share >= 1.0:  # the coverage model must have cases that can end in Failure/Recovery and Compensation
            categories[:2] = ["abnormal", "recovery"][: len(categories)]
        for k in range(s.cases):
            category = categories[k]
            events = self._events(rng, t, per_case[k], category, entries, out_edges, owners, self.rng(f"event-extras:{k}"))
            participants: list[str] = []
            for ev in events:
                for node_id in (ev[1], ev[2]):
                    if node_id not in participants:
                        participants.append(node_id)
            cid = self.claim(f"{self.fam['case']}:{t.slug(1)}.{k + 1}" if s.odd_ids else f"{self.fam['case']}-{t.slug(1).upper()}-{k + 1}")
            reqs = [r["id"] for r in rng.sample(self.requirements, rng.randint(1, 2))] if self.requirements else []
            first_ev = [self.node_by_id[p]["evidenceIds"][0] for p in participants[:3] if p in self.node_by_id and self.node_by_id[p]["evidenceIds"]]
            evidence_ids = list(dict.fromkeys(first_ev))[:2]
            partial = rng.choice(({"timeoutMs": 500}, {"retryMax": 2, "retryBackoffMs": 100}, {"timeoutMs": 800, "retryMax": 1})) if rng.chance(0.2) else None
            context = rng.choice(({"region": "eu"}, {"retries": 1}, {"locale": "en", "tier": "basic"})) if rng.chance(0.3) else None
            if any("$ctx.region" in json.dumps(ev[3].get("guard", "")) for ev in events) and not (context and "region" in context):
                context = dict(context or {}, region="eu")  # a guard that reads $ctx.region needs the key
            machine_ids_in_case = [p for p in participants if p in self.machine_ids]
            init = {m: next(x for x in self.machines if x["id"] == m)["initial"] for m in machine_ids_in_case} or None
            terminal = {p: "Completed" for p in participants if p not in self.machine_ids}
            for ev in events:
                if ev[0] == "Transition" and ev[2] in self.machine_ids:
                    terminal[ev[2]] = ev[3]["params"]["target"]
            outcomes = None
            if category in ("abnormal", "boundary", "recovery"):
                rule_ids = [ev[3]["ruleId"] for ev in events if ev[3].get("ruleId")]
                if rule_ids:
                    outcomes = [{"ruleId": rng.choice(rule_ids), "outcome": rng.choice(("Failed", "Timeout", "Rejected", "Denied", "Blocked"))}]
                    victim = next((ev[2] for ev in events if ev[3].get("ruleId") == outcomes[0]["ruleId"]), None)
                    if victim in terminal and victim not in self.machine_ids:
                        terminal[victim] = rng.choice(("Failed", "Blocked", "Degraded"))
            by = "user" if rng.chance(0.1) else "author"
            self.cases.append(am.build_case(
                cid, category, t.trimmed(s.case_title_len), events, reqs, evidence_ids, partial, context,
                init, terminal, outcomes, by, None, self.requirements))
        self._ensure_explicit_edge(runtime)

    def _ensure_explicit_edge(self, runtime: Sequence[Mapping]) -> None:
        """Every generated model with cases has at least one event that names its edge explicitly (``edge`` option)."""
        if any("edge" in ev for c in self.cases for ev in c["events"]):
            return
        by_endpoints = {(e["sourceId"], e["targetId"]): e["id"] for e in runtime}
        for case in self.cases:
            for ev in case["events"]:
                if ev["type"] in ("Call", "Message") and (ev["from"], ev["to"]) in by_endpoints:
                    ev["edge"] = by_endpoints[(ev["from"], ev["to"])]
                    return

    def _when(self, rng: Rng, source: str, target: str) -> dict:
        states = self.profile.ids("nodeStates")
        node = rng.choice((source, target))
        base = ({"node": node, "is": rng.sample(states, rng.randint(1, 2))} if rng.chance(0.6)
                else {"node": node, "not": rng.sample(("Failed", "Blocked", "Idle"), rng.randint(1, 2))})
        form = rng.below(4)
        if form == 0:
            return {"all": [base, {"param": rng.choice(GUARD_PARAMS), "op": "present"}]}
        if form == 1:
            return {"not": base}
        if form == 2:
            return {"any": [base, {"param": rng.choice(GUARD_PARAMS), "op": "absent"}]}
        return base

    @staticmethod
    def _guard(rng: Rng) -> dict:
        param = rng.choice(GUARD_PARAMS)
        form = rng.below(4)
        if form == 0:
            return {"param": param, "op": rng.choice(("present", "absent"))}
        if form == 1:
            return {"param": param, "op": "eq", "value": "$ctx.region"}
        if form == 2:
            return {"param": param, "op": "in", "value": ["basic", "pro"]}
        return {"all": [{"param": param, "op": "neq", "value": "0"}, {"param": rng.choice(GUARD_PARAMS), "op": "present"}]}

    def _extra_options(self, rng: Rng, edge: Mapping, opts: dict, t: Text) -> None:
        """``when``, ``guard``, ``errorCode`` and ``dedupKey`` (drawn from the case's own fork, so they never shift other streams)."""
        s = self.s
        if s.when_share and rng.chance(s.when_share):
            opts["when"] = self._when(rng, edge["sourceId"], edge["targetId"])
        if s.guard_share and rng.chance(s.guard_share):
            opts["guard"] = self._guard(rng)
            opts["errorCode"] = "E_" + t.slug(1).upper().replace("-", "_")
        elif s.code_share and rng.chance(s.code_share):
            opts["errorCode"] = "E_" + t.slug(1).upper().replace("-", "_")
        node = self.node_by_id.get(edge["targetId"])
        if node is not None and "dedup" in node["props"] and rng.chance(0.7):
            opts["dedupKey"] = "k" + str(rng.randint(1, 3))

    def _events(self, rng: Rng, t: Text, target: int, category: str, entries, out_edges, owners, extra: Rng | None = None):
        events: list[tuple] = []
        alias_by_triple = {(e["sourceId"], e["relation"], e["targetId"]): e["id"] for e in self.edges if e["id"] in self.alias_edge_ids}
        rule_pool = self.rules

        def new_rule() -> str:
            word = f"{self.fam['rule']}-{t.slug(1).upper().replace('-', '')}"
            if word in rule_pool and rng.chance(0.5):
                return word
            rule_pool.append(word)
            return word

        def options(edge: Mapping, kind: str) -> dict:
            opts: dict = {}
            if rng.chance(0.8):
                opts["ruleId"] = new_rule()
            if rng.chance(0.2):
                opts["latencyMs"] = rng.randint(5, 200) if rng.chance(0.85) else [rng.randint(5, 80), rng.randint(5, 80)]
            if rng.chance(0.04):
                opts["ack"] = True
            if category == "concurrency" and rng.chance(0.4):
                opts["group"] = rng.choice(("a", "b"))
            if category in ("abnormal", "recovery") and kind == "Call" and rng.chance(0.15):
                opts["fault"] = {"mode": rng.choice(("fail", "deny")), "errorCode": "E_" + t.slug(1).upper().replace("-", "_"), "httpStatus": 503}
            if category == "recovery" and rng.chance(0.2):
                opts["role"] = "recovery"
            if rng.chance(0.02):
                opts["dedupKey"] = "k" + str(rng.randint(1, 9))
            if rng.chance(0.03):
                opts["section"] = True
            alias = alias_by_triple.get((edge["sourceId"], edge["relation"], edge["targetId"]))
            if alias and rng.chance(0.5):
                opts["edge"] = alias
            if extra is not None:
                self._extra_options(extra, edge, opts, Text(extra, self.s.lang))
            return opts

        # slots reserved for the machine transitions and for one Failure/Recovery event, filled after the walk
        chosen = [m for m in self.machines if owners.get(m["id"]) and m["transitions"] and rng.chance(0.35)][:2]
        closing = 1 if category in ("abnormal", "recovery") else 0
        compensate = bool(closing and extra is not None and self.s.compensation_share and extra.chance(self.s.compensation_share))
        closing += 1 if compensate else 0
        limit = max(1, target - len(chosen) - closing)

        def walk(node: str, depth: int, visited: set[str]) -> None:
            children = list(out_edges.get(node, []))
            rng.shuffle(children)
            fanout = rng.randint(1, 2)
            for edge in children[: fanout + (1 if depth == 0 else 0)]:
                if len(events) >= limit or edge["targetId"] in visited or depth >= 4:
                    continue
                kind = "Call" if edge["relation"] in ("calls", "reads", "writes", "triggers") else "Message"
                opts = options(edge, kind)
                events.append((kind, node, edge["targetId"], opts))
                if extra is not None and "dedupKey" in opts and len(events) < limit and extra.chance(0.5):
                    events.append((kind, node, edge["targetId"], dict(opts)))  # the same key twice: the second one is a duplicate
                visited.add(edge["targetId"])
                walk(edge["targetId"], depth + 1, visited)
                if kind == "Call" and len(events) < limit:
                    events.append(("Return", edge["targetId"], node, {}))

        guard = 0
        while len(events) < limit and guard < 40 and entries:
            guard += 1
            entry = rng.choice(entries)
            before = len(events)
            walk(entry, 0, {entry})
            if rng.chance(0.2) and len(events) < limit:
                events.append(("Wait", entry, entry, {"ms": rng.choice((50, 100, 500))}))
            if before == len(events):
                break
        while len(events) < limit and entries:
            entry = rng.choice(entries)
            edge = rng.choice(out_edges[entry])
            events.append(("Call", entry, edge["targetId"], options(edge, "Call")))
            if len(events) < limit:
                events.append(("Return", edge["targetId"], entry, {}))
        if closing and events:
            first = events[0]
            events.append(("Failure" if category == "abnormal" else "Recovery", first[1], first[2], {}))
            if compensate:
                events.append(("Compensation", first[2], first[1], {}))
        # machine transition events run through the generated owner edge
        for machine in chosen:
            tr = rng.choice(machine["transitions"])
            opts = {"params": {"target": tr["to"]}}
            if rng.chance(0.6):
                opts["ruleId"] = new_rule()
            events.append(("Transition", owners[machine["id"]]["sourceId"], machine["id"], opts))
        del events[target:]
        return [(e[0], e[1], e[2], e[3]) if len(e) > 3 else (e[0], e[1], e[2], {}) for e in events]

    # ---- interventions ---------------------------------------------------------------------------------

    def interventions_step(self) -> None:
        s, t, rng = self.s, self.text("interventions"), self.rng("interventions")
        if not s.interventions or not self.cases:
            return
        regular = [n for n in self.nodes if n["id"] not in self.machine_ids]
        runtime_edges = [e for e in self.edges if not e["static"] and e["relation"] != "owns"]
        rewritable = [e for e in runtime_edges if e["sourcePortId"] and e["targetPortId"]]
        guard_targets = list(self.transition_ids_to_reference)
        ports = am.derive_ports(self.nodes, self.machine_ids, self.profile.record_kinds, profile=self.profile)
        kinds = [k for k in INTERVENTION_CYCLE]
        if not guard_targets:
            kinds = [k for k in kinds if k != "change_guard"]
        if not rewritable:
            kinds = [k for k in kinds if k != "insert_element"]
        if not runtime_edges:
            kinds = [k for k in kinds if k not in ("disable_edge", "delay_message")]
        order = (kinds * (s.interventions // len(kinds) + 1))[: s.interventions]
        extra_rewrites = 0
        for i, kind in enumerate(order):
            iid = self.claim(f"{self.fam['intervention']}-{t.slug(1).upper()}-{i + 1}")
            applies = [c["caseId"] for c in rng.sample(self.cases, rng.randint(1, min(3, len(self.cases))))]
            # a rule or event can only be delayed when an applied case has it
            rule_ids = list(dict.fromkeys(e["ruleId"] for c in self.cases if c["caseId"] in applies for e in c["events"] if "ruleId" in e))
            extra = self.rng(f"iv-extra:{i}")
            params: dict = {}
            target_node = rng.choice(regular)
            expect: dict = {"outcomeClass": rng.weighted((("DIVERGED", 5), ("CONVERGED", 2), ("NO_DIFFERENCE", 3)))}
            if kind in ("disable_module", "resource_failure", "identity_failure"):
                params["targetNodeId"] = target_node["id"]
                if expect["outcomeClass"] == "DIVERGED":
                    expect["terminal"] = {target_node["id"]: rng.choice(("Blocked", "Failed"))}
            elif kind == "disable_edge":
                params["targetEdgeId"] = rng.choice(runtime_edges)["id"]
            elif kind == "change_timeout":
                params["timeoutMs"] = rng.choice((100, 300, 500, 1500))
            elif kind == "change_retry":
                params["retryMax"] = rng.randint(0, 4)
                if rng.chance(0.5):
                    params["retryBackoffMs"] = rng.choice((50, 100, 250))
            elif kind == "delay_message":
                params["addLatencyMs"] = rng.choice((100, 500, 2500))
                if rule_ids and rng.chance(0.5):
                    params["targetRuleId"] = rng.choice(rule_ids)
                else:
                    params["targetEdgeId"] = rng.choice(runtime_edges)["id"]
                if s.event_target_share and extra.chance(s.event_target_share):
                    case = next(c for c in self.cases if c["caseId"] == applies[0])
                    params["targetEventId"] = am.event_id(case["caseId"], extra.randint(1, len(case["events"])))
            elif kind == "change_guard":
                machine_id, transition_id = rng.choice(guard_targets)
                params.update(machineId=machine_id, transitionId=transition_id,
                              guard=None if rng.chance(0.5) else {"param": rng.choice(GUARD_PARAMS), "op": "present"})
            elif kind == "insert_element":
                element_id = self.claim(f"{self.fam['element']}.{t.slug(1)}")
                element_kind = rng.choice(self.profile.ids("elementKinds"))
                picked = rng.sample(rewritable, 2 if rng.chance(0.2) and len(rewritable) > 1 else 1)
                params["element"] = make_element(element_id, element_kind, t.sentence(40), rng, am.element_port_types(picked, ports))
                params["rewrite"] = [am.build_rewrite_entry(element_id, n, edge, "replace") for n, edge in enumerate(picked)]
                spare = [e for e in rewritable if e not in picked]
                if s.disposition_mix and spare and extra.chance(s.disposition_mix):
                    # alternate keep and disable so that a model with two inserted elements shows both dispositions
                    params["rewrite"].append(am.build_rewrite_entry(element_id, len(picked), extra.choice(spare), ("keep", "disable")[extra_rewrites % 2]))
                    extra_rewrites += 1
                expect = {"outcomeClass": rng.choice(("CONVERGED", "DIVERGED", "NO_DIFFERENCE")), "status": rng.choice(("OK", "DRAFT"))}
            elif kind == "combined_intervention":
                second = {"type": "change_timeout", "timeoutMs": 500} if rng.chance(0.5) else {"type": "change_retry", "retryMax": 2}
                params["parts"] = [{"type": "disable_module", "targetNodeId": target_node["id"]}, second]
                if s.combined_rich and rewritable and extra.chance(s.combined_rich):
                    # an inserted element and a relaxed guard inside one combined intervention
                    ex_text = self.text(f"iv-text:{i}")
                    element_id = self.claim(f"{self.fam['element']}.{ex_text.slug(1)}")
                    edge = extra.choice(rewritable)
                    element = make_element(element_id, extra.choice(self.profile.ids("elementKinds")), ex_text.sentence(40), extra,
                                           am.element_port_types([edge], ports))
                    parts = [{"type": "insert_element", "element": element, "rewrite": [am.build_rewrite_entry(element_id, 0, edge, "replace")]}]
                    if guard_targets:
                        machine_id, transition_id = extra.choice(guard_targets)
                        parts.append({"type": "change_guard", "machineId": machine_id, "transitionId": transition_id,
                                      "guard": None if extra.chance(0.5) else self._guard(extra)})
                    params["parts"] = parts + params["parts"]
                    expect["terminal"] = {element_id: extra.choice(("Completed", "Waiting", "Idle"))}
            note = t.sentence(s.note_len) if rng.chance(s.note_share) else ""
            why = t.sentence(s.note_len + 10) if rng.chance(s.why_share) else None
            self.interventions.append(am.build_intervention(iid, kind, t.trimmed(s.description_len), applies, expect, note, why, **params))

    # ---- policies --------------------------------------------------------------------------------------

    def policies_step(self) -> None:
        s, t, rng = self.s, self.text("policies"), self.rng("policies")
        sources = [r["id"] for r in self.requirements] + [x["id"] for x in self.tbds]
        if not s.policies or not sources:
            return
        regular = [n for n in self.nodes if n["id"] not in self.machine_ids]
        for i in range(s.policies):
            pid = self.claim(f"{self.fam['policy']}-{i + 1}")
            scope = rng.weighted((("all", 5), ("kind", 2), ("node", 2), ("req", 1)))
            if scope == "kind":
                scope = "kind:" + rng.choice([k for k, _ in KIND_WEIGHTS])
            elif scope == "node":
                scope = "node:" + rng.choice(regular)["id"]
            elif scope == "req" and self.requirements:
                scope = "req:" + rng.choice(self.requirements)["id"]
            else:
                scope = "all"
            self.policies.append(am.build_policy(
                pid, t.sentence(s.policy_len), rng.choice(sources), scope, rng.choice(("REQ", "BD", "DD")),
                rng.randint(1, 20) if rng.chance(0.4) else None, rng.choice(("constraint", "nfr", "data")), "user" if rng.chance(0.2) else "ai"))

    # ---- assembly --------------------------------------------------------------------------------------

    def assemble(self) -> dict:
        s = self.s
        t = self.text("meta")
        self.space()
        self.evidence()
        self.nodes_step()
        self.dedup_step()
        self.machines_step()
        self.links_step()
        self.edges_step()
        self.requirements_step()
        self.cases_step()
        self.interventions_step()
        self.policies_step()
        title = "" if s.empty_strings else t.title(3) + " system"
        model = am.empty_model(f"synth-{self.fam.name}-{s.name}", title, self.mode, ("en-US",) if s.lang == "en" else ("zh-CN", "ja-JP"),
                               repos=self.repos)
        model["space"] = {"buildings": self.buildings, "floors": self.floors, "rooms": self.rooms}
        model["nodes"], model["edges"] = self.nodes, self.edges
        model["ports"] = am.derive_ports(self.nodes, self.machine_ids, self.profile.record_kinds, profile=self.profile)
        model["evidences"], model["machines"], model["cases"] = self.evidences, self.machines, self.cases
        model["interventions"], model["requirements"] = self.interventions, self.requirements
        model["tests"], model["tbds"], model["policies"] = self.tests, self.tbds, self.policies
        return model


def make_element(element_id: str, kind: str, role: str, rng: Rng, port_types: tuple[str, str] = ("Message", "Message")) -> dict:
    """An ``insert_element`` body with ports ``<id>:in`` / ``<id>:out`` (the chain edges are derived elsewhere)."""
    trigger, action = ELEMENT_KIND_HANDLER.get(kind, ("receive", rng.choice(("forward", "respond", "drop"))))
    inputs, outputs = am.derive_element_ports(element_id, port_types[0], port_types[1])
    return {
        "elementId": element_id, "kind": kind, "role": role.rstrip("."),
        "inputPorts": inputs, "outputPorts": outputs,
        "stateMachine": {"states": ["Cold", "Warm"], "initial": "Cold", "transitions": [{"from": "Cold", "to": "Warm", "on": trigger}]},
        "eventHandlers": [{"on": trigger, "do": action, "setState": "Warm"}], "guardRules": [],
        "errorRules": [{"on": "timeout", "do": "fail"}], "resourceLimits": {"maxEvents": 20, "maxHops": 4},
        "terminationConditions": ["maxEvents"], "latencyMs": rng.choice((2, 5, 10)),
    }


# --------------------------------------------------------------------------------------------------------
# The worked example of design 3.4 (hand written; family-parameterised)
# --------------------------------------------------------------------------------------------------------


def lending_desk(family: "str | Family" = "fixed", seed: int = 0, mode: str = "asis") -> dict:
    """The 4-node library lending desk of design 3.4 (3 edges, 3 evidences, 1 case of 6 events, 1 intervention).

    With the fixed family the ids are exactly those of the design (``svc.loan``, ``EVD-1``, ``CS-BORROW-OK`` ...).
    Meta, as an ``atlas init`` would write it: projectId ``lending-desk``, title ``Library lending desk``, languages
    ``["en-US"]``, repos ``["main"]``. The ``tests`` registry holds ``TS-1`` because ``RQ-1`` cites it.
    """
    fam = Family.resolve(family, seed)
    p, profile = fam.p, vocab.default_profile()
    b_lib = am.build_building(f"{p['building']}.lib", "Library system", "internal")
    b_ext = am.build_building(f"{p['building']}.ext", "Outside", "external")
    f_core = am.build_floor(f"{p['floor']}.core", b_lib["id"], "Core services", 1)
    f_out = am.build_floor(f"{p['floor']}.out", b_ext["id"], "Visitors", 0)
    r_desk = am.build_room(f"{p['room']}.desk", f_core["id"], "Lending desk", tier="T2")
    r_vis = am.build_room(f"{p['room']}.vis", f_out["id"], "Members", tier="T1")
    model = am.empty_model("lending-desk", "Library lending desk", mode, ("en-US",), repos=("main",))
    model["space"] = {"buildings": [b_lib, b_ext], "floors": [f_core, f_out], "rooms": [r_desk, r_vis]}

    ev = [
        am.build_evidence(f"{p['evidence']}-1", "src/loan.py", "def create_loan(", "creates the loan row", "main", mode=mode),
        am.build_evidence(f"{p['evidence']}-2", "src/stock.py", "def find_copy(", "shelf lookup", "main", mode=mode),
        am.build_evidence(f"{p['evidence']}-3", "docs/lending.md", "borrower opens the page", "the borrower actor", "main", mode=mode),
    ]
    ev_index = {e["evidenceId"]: e for e in ev}
    model["evidences"] = ev

    def kinds(ids):
        return am.evidence_kinds_of(ids, ev_index)

    ids = {"loan": f"{p['service']}.loan", "stock": f"{p['service']}.stock", "table": f"{p['storage']}.loan", "member": f"{p['actor']}.member"}
    spec = [
        (ids["loan"], "Module", "Loan service", r_desk, b_lib, "Creates a loan and takes returns.", [ev[0]["evidenceId"], ev[1]["evidenceId"]], None),
        (ids["stock"], "Module", "Stock service", r_desk, b_lib, "Knows which copy is on the shelf.", [ev[1]["evidenceId"]], None),
        (ids["table"], "Storage", "Loan table", r_desk, b_lib, "One row per open loan.", [ev[0]["evidenceId"]], None),
        (ids["member"], "Actor", "Member", r_vis, b_ext, "Borrower using the web page.", [ev[2]["evidenceId"]], "Inferred"),
    ]
    nodes = [am.build_node(node_id, kind, name, room["id"], sub, evidence_ids, verif, None, None, None, evidence_kinds=kinds(evidence_ids),
                           mode=mode, room=room, building=building, profile=profile)
             for node_id, kind, name, room, building, sub, evidence_ids, verif in spec]
    model["nodes"] = nodes
    model["ports"] = am.derive_ports(nodes, (), profile.record_kinds, profile=profile)

    def edge(src, rel, tgt, label, evidence_ids):
        return am.build_edge(src, rel, tgt, label, evidence_ids, evidence_kinds=kinds(evidence_ids), mode=mode, profile=profile)

    model["edges"] = [
        edge(ids["member"], "calls", ids["loan"], "borrow a copy", [ev[2]["evidenceId"]]),
        edge(ids["loan"], "calls", ids["stock"], "check copy status", [ev[1]["evidenceId"]]),
        edge(ids["loan"], "writes", ids["table"], "write loan row", [ev[0]["evidenceId"]]),
    ]
    model["tests"] = [am.build_test(f"{p['test']}-1", "A member can borrow a copy")]
    requirement = am.build_requirement(
        f"{p['requirement']}-1", "A member can borrow an available copy", "interview 1",
        [ids["member"], ids["loan"], ids["stock"], ids["table"]], None, [f"{p['test']}-1"], (), (), (), edges=model["edges"])
    model["requirements"] = [requirement]
    rule = p["rule"]
    case_id = f"{p['case']}-BORROW-OK"
    model["cases"] = [am.build_case(
        case_id, "normal", "Member borrows an available copy",
        [("Call", ids["member"], ids["loan"], {"ruleId": f"{rule}-BORROW"}),
         ("Call", ids["loan"], ids["stock"], {"ruleId": f"{rule}-CHECK", "latencyMs": 20}),
         ("Return", ids["stock"], ids["loan"]),
         ("Call", ids["loan"], ids["table"], {"ruleId": f"{rule}-WRITE"}),
         ("Return", ids["table"], ids["loan"]),
         ("Return", ids["loan"], ids["member"])],
        [requirement["id"]], [ev[0]["evidenceId"]], None, None, None,
        {ids["member"]: "Completed", ids["loan"]: "Completed", ids["stock"]: "Completed", ids["table"]: "Completed"}, None, "author", None,
        model["requirements"])]
    model["interventions"] = [am.build_intervention(
        f"{p['intervention']}-STOCK-DOWN", "disable_module", "Stock service is down while borrowing", [case_id],
        {"outcomeClass": "DIVERGED", "terminal": {ids["stock"]: "Blocked"}}, "", None, targetNodeId=ids["stock"])]
    return model


# --------------------------------------------------------------------------------------------------------
# Presets and edge cases
# --------------------------------------------------------------------------------------------------------


def _finish(model: dict, check: bool = True) -> dict:
    if check:
        issues = am.contract_issues(model)
        if issues:
            raise AssertionError("synthetic model breaks the contract: " + "; ".join(f"{i['path']}: {i['message']}" for i in issues[:5]))
    return model


def generate(preset: "str | Scale" = "small", seed: int = 0, family: "str | Family" = "random", mode: str = "asis", check: bool = True) -> dict:
    """A fresh expanded model. ``tiny`` is the hand-written lending desk (any family); the others are generated."""
    scale = preset if isinstance(preset, Scale) else PRESETS[preset]
    fam = Family.resolve(family, seed)
    if scale.name == "tiny":
        return _finish(lending_desk(fam, seed, mode), check)
    return _finish(_Generator(scale, seed, fam, mode).assemble(), check)


EDGE_CASES = (
    "big400", "unknown-values", "no-cases", "no-interventions", "empty-strings", "cjk", "odd-ids", "machine",
    "insert-element", "element-ports", "rich-fields",
)


def _scale(name: str, **changes) -> Scale:
    return replace(PRESETS["small"], name=name, **changes)


def edge_case(name: str, seed: int = 0, family: "str | Family" = "random", mode: str = "asis", check: bool = True) -> dict:
    """Awkward but contract-conformant models (``unknown-values`` breaks only vocabulary membership, on purpose)."""
    fam = Family.resolve(family, seed)
    if name == "big400":
        scale = _scale(name, buildings=4, floors=9, rooms=24, nodes=400, machines=3, edges=880, evidences=260, cases=12, events=150,
                       interventions=10, requirements=10, tbds=8, tests=10, policies=6, alias_edges=8)
        return _finish(_Generator(scale, seed, fam, mode).assemble(), check)
    if name == "rich-fields":
        # every optional field of the contract at a high rate: event when/guard/errorCode, Compensation, dedup, owner/store/enums,
        # cardinality, keep and disable rewrites, combined parts with an element and a guard change, stamped evidence
        scale = _scale(name, nodes=70, edges=150, evidences=60, machines=3, cases=9, events=160, interventions=18, event_spread=True,
                       machine_transitions=(3, 10), when_share=0.25, guard_share=0.12, code_share=0.1, compensation_share=1.0,
                       dedup_nodes=4, link_share=0.8, reference_edges=8, disposition_mix=1.0, combined_rich=1.0, event_target_share=1.0,
                       stamp_share=0.5)
        return _finish(_Generator(scale, seed, fam, mode).assemble(), check)
    if name == "no-cases":
        scale = _scale(name, cases=0, events=0, interventions=0)
        return _finish(_Generator(scale, seed, fam, mode).assemble(), check)
    if name == "no-interventions":
        return _finish(_Generator(_scale(name, interventions=0), seed, fam, mode).assemble(), check)
    if name == "empty-strings":
        return _finish(_Generator(_scale(name, empty_strings=True, props_share=0.0), seed, fam, mode).assemble(), check)
    if name == "cjk":
        return _finish(_Generator(_scale(name, lang="cjk", sub_len=30, topic_len=24, doc_anchor_len=20), seed, fam, mode).assemble(), check)
    if name == "odd-ids":
        return _finish(_Generator(_scale(name, odd_ids=True, long_ids=3, alias_edges=4), seed, fam, mode).assemble(), check)
    if name == "machine":
        scale = _scale(name, machines=3, cases=6, events=90, interventions=10)  # the 10th slot is a change_guard
        return _finish(_Generator(scale, seed, fam, mode).assemble(), check)
    if name in ("insert-element", "element-ports"):
        model = _Generator(_scale(name, interventions=0, cases=4, events=40), seed, fam, mode).assemble()
        _add_insert_element(model, fam, seed, many_ports=name == "element-ports")
        return _finish(model, check)
    if name == "unknown-values":
        model = _Generator(_scale(name, interventions=4, props_share=0.0), seed, fam, mode).assemble()
        _inject_unknown_values(model)
        return _finish(model, False)
    raise ValueError(f"unknown edge case {name!r}; known: {', '.join(EDGE_CASES)}")


def _add_insert_element(model: dict, fam: Family, seed: int, many_ports: bool) -> None:
    rng = Rng(seed, "insert-element")
    edges = [e for e in model["edges"] if not e["static"] and e["relation"] != "owns" and e["sourcePortId"] and e["targetPortId"]]
    picked = rng.sample(edges, 2 if many_ports else 1)
    element_id = f"{fam['element']}.{Text(rng).slug(1)}"
    types = ("Record", "Any") if many_ports else am.element_port_types(picked, model["ports"])
    element = make_element(element_id, "Cache" if not many_ports else "MessageQueue", "buffers repeated reads", rng, types)
    case_ids = [c["caseId"] for c in model["cases"][:2]]
    model["interventions"].append(am.build_intervention(
        f"{fam['intervention']}-ELEMENT", "insert_element", "Put an element in front of the called service", case_ids,
        {"outcomeClass": "CONVERGED", "status": "OK"}, "", "the element answers locally", element=element,
        rewrite=[am.build_rewrite_entry(element_id, n, edge, "replace") for n, edge in enumerate(picked)]))
    if many_ports:  # one keep and one disable entry exercise the empty-chain branch
        spare = [e for e in edges if e not in picked]
        for offset, disposition in enumerate(("keep", "disable")):
            if offset < len(spare):
                model["interventions"][-1]["rewrite"].append(am.build_rewrite_entry(element_id, len(picked) + offset, spare[offset], disposition))


def _inject_unknown_values(model: dict) -> None:
    """Unknown trust, layer, tier, node kinds and one unknown relation (vocabulary membership is the only thing broken)."""
    model["space"]["buildings"][-1]["trust"] = "unlisted-zone"
    model["space"]["rooms"][0]["layer"] = "attic"
    model["space"]["rooms"][1]["tier"] = "T9"
    machine_ids = {m["id"] for m in model["machines"]}
    kinds = iter(("Gizmo", "Widget", "Contraption"))
    seen = 0
    for node in model["nodes"]:
        if node["id"] in machine_ids:
            continue
        node["kind"] = next(kinds)
        seen += 1
        if seen == 3:
            break
    # an unknown kind has no record flag: its ports are Message ports, so the stored ports follow
    model["ports"] = am.derive_ports(model["nodes"], machine_ids, vocab.default_profile().record_kinds)
    edge = next(e for e in model["edges"] if e["relation"] == "calls")
    old_id = edge["id"]
    edge["relation"] = "orchestrates"  # unknown relation: static is not derivable, keep the generic default
    edge["static"] = False
    # the id of an edge is derived from its relation, so re-derive it and follow every reference to it
    edge["id"] = am.edge_id(edge["sourceId"], "orchestrates", edge["targetId"])
    for requirement in model["requirements"]:
        requirement["edgeIds"] = [edge["id"] if x == old_id else x for x in requirement["edgeIds"]]
    for case in model["cases"]:
        for event in case["events"]:
            if event.get("edge") == old_id:
                event["edge"] = edge["id"]
    for intervention in model["interventions"]:
        if intervention.get("targetEdgeId") == old_id:
            intervention["targetEdgeId"] = edge["id"]
        for rewrite in intervention.get("rewrite", []):
            if rewrite["replaceEdgeId"] == old_id:
                rewrite["replaceEdgeId"] = edge["id"]


# --------------------------------------------------------------------------------------------------------
# Counts and an estimator for the .atl authoring text
# --------------------------------------------------------------------------------------------------------


def section_counts(model: Mapping) -> dict[str, int]:
    return {
        "buildings": len(model["space"]["buildings"]), "floors": len(model["space"]["floors"]), "rooms": len(model["space"]["rooms"]),
        "nodes": len(model["nodes"]), "edges": len(model["edges"]), "ports": len(model["ports"]), "evidences": len(model["evidences"]),
        "machines": len(model["machines"]), "transitions": sum(len(m["transitions"]) for m in model["machines"]),
        "cases": len(model["cases"]), "events": sum(len(c["events"]) for c in model["cases"]),
        "interventions": len(model["interventions"]), "requirements": len(model["requirements"]), "tests": len(model["tests"]),
        "tbds": len(model["tbds"]), "policies": len(model["policies"]),
    }


_BARE = re.compile(r"^[A-Za-z0-9_.:\-/+,^]+$")


def _val(value) -> str:
    """Option value: bare token, quoted string, or compact JSON."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value if value and _BARE.match(value) else json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _row(cells: Sequence[str], options: Sequence[tuple[str, object]]) -> str:
    opts = " ".join(f"{k}={_val(v)}" for k, v in options if v is not None)
    return " | ".join(list(cells) + ([opts] if opts else [])) + "\n"


def atl_sections_text(model: Mapping) -> dict[str, str]:
    """Approximate ``.atl`` text per section (design 3.1/3.2) an encoder would write for ``model``.

    This is an ESTIMATE: the real encoder lives in the format lane (``format/atl.py``). Rules used: positional text
    cells joined by `` | `` and one options cell; ``@set`` lines on change of ``room``/``kind`` (nodes), ``repo``
    (evidences) and ``case`` (events); ``verif=`` only when it differs from the derived default; ``ev=^`` for edges
    whose evidence is exactly the endpoint union; ``alias=`` only when the id is not the derived one; ``owner=`` for
    the generated owner edges instead of an edge row; transition ``id=`` only when not derived; the case ``tests=``
    list only when it differs from the default; ``use=<kind>`` instead of an element body; requirement ``edges=``
    only when not the default. Section headers and the three file headers are counted. Calibrated against the
    2,175-byte worked example of the design (see ``tests/atlas/test_synth.py``).
    """
    p = vocab.default_profile()
    mode = model["meta"]["mode"]
    ev_index = {e["evidenceId"]: e for e in model["evidences"]}
    node_by_id = {n["id"]: n for n in model["nodes"]}
    machine_ids = {m["id"] for m in model["machines"]}
    room_by_id = {r["id"]: r for r in model["space"]["rooms"]}
    floor_by_id = {f["id"]: f for f in model["space"]["floors"]}
    building_by_id = {b["id"]: b for b in model["space"]["buildings"]}
    sections: dict[str, str] = {}

    sections["spatial"] = "## buildings: id | name | opts\n"
    for b in model["space"]["buildings"]:
        sections["spatial"] += _row([b["id"], b["name"]], [("trust", b["trust"])])
    sections["spatial"] += "## floors: id | building | name | level\n"
    for f in model["space"]["floors"]:
        sections["spatial"] += _row([f["id"], f["buildingId"], f["name"], str(f["level"])], [])
    sections["spatial"] += "## rooms: id | floor | name | opts\n"
    for r in model["space"]["rooms"]:
        sections["spatial"] += _row([r["id"], r["floorId"], r["name"]], [("layer", r.get("layer")), ("tier", r.get("tier")), ("area", r.get("area"))])

    text = "## nodes: id | name | sub | opts\n"
    last_set: tuple | None = None
    for n in model["nodes"]:
        pair = (n["roomId"], n["kind"])
        if pair != last_set:
            changed = [f"{key}={value}" for key, value, old in (("room", pair[0], last_set[0] if last_set else None),
                                                               ("kind", pair[1], last_set[1] if last_set else None)) if value != old]
            text += "@set " + " ".join(changed) + "\n"
            last_set = pair
        room = room_by_id.get(n["roomId"])
        building = building_by_id.get(floor_by_id[room["floorId"]]["buildingId"]) if room and room["floorId"] in floor_by_id else None
        inherited = am.inherit_props(None, room, building)
        authored = {k: v for k, v in n["props"].items() if k not in inherited or inherited[k] != v}
        default = am.default_verification_for_node(am.evidence_kinds_of(n["evidenceIds"], ev_index), mode, profile=p)
        text += _row([n["id"], n["name"], n["sub"] or "~"], [
            ("ev", ",".join(n["evidenceIds"]) or None), ("verif", n["verification"] if n["verification"] != default else None),
            ("props", authored or None), ("group", n.get("group")), ("level", n.get("level"))])
    sections["nodes"] = text

    text = "## edges: from | rel | to | label | opts\n"
    owns_generated = {m["id"] for m in model["machines"]}
    for e in model["edges"]:
        if e["relation"] == "owns" and e["targetId"] in owns_generated:
            continue  # written as owner= on the machine row
        src, tgt = node_by_id.get(e["sourceId"], {}), node_by_id.get(e["targetId"], {})
        union = am.inherit_edge_evidence(src.get("evidenceIds", []), tgt.get("evidenceIds", []))
        inherited = bool(e["evidenceIds"]) and e["evidenceIds"] == union
        default = am.default_verification_for_edge(am.evidence_kinds_of(e["evidenceIds"], ev_index), mode, inherited, profile=p)
        try:
            derived_id = am.edge_id(e["sourceId"], e["relation"], e["targetId"])
        except am.AtlasModelError:
            derived_id = None
        text += _row([e["sourceId"], e["relation"], e["targetId"], e["label"]], [
            ("ev", "^" if inherited else (",".join(e["evidenceIds"]) or None)), ("verif", e["verification"] if e["verification"] != default else None),
            ("card", e.get("cardinality")), ("alias", e["id"] if e["id"] != derived_id else None), ("group", e.get("group"))])
    sections["edges"] = text

    text = "## evidences: id | file | anchor | topic | opts\n"
    last_repo: str | None = None
    for ev in model["evidences"]:
        if ev["repository"] and ev["repository"] != last_repo:
            text += f"@set repo={ev['repository']}\n"
            last_repo = ev["repository"]
        text += _row([ev["evidenceId"], ev["filePath"], ev["anchor"], ev["topic"]], [])
    sections["evidences"] = text

    text = "## machines: id | initial | opts\n"
    for m in model["machines"]:
        owner = next((e["sourceId"] for e in model["edges"] if e["relation"] == "owns" and e["targetId"] == m["id"]), None)
        text += _row([m["id"], m["initial"]], [("owner", owner), ("err", m["errorCode"]), ("ev", ",".join(m["evidenceIds"]) or None)])
    text += "## transitions: from | to | opts\n"
    for m in model["machines"]:
        text += f"@set machine={m['id']}\n"
        for tr in m["transitions"]:
            derived = am.transition_id(m["id"], tr["from"], tr["to"])
            text += _row([tr["from"], tr["to"]], [("guard", tr.get("guard")), ("err", tr.get("errorCode")), ("id", tr["id"] if tr["id"] != derived else None)])
    sections["machines"] = text

    text = "## tests: id | title\n" + "".join(_row([t["id"], t["title"]], []) for t in model["tests"])
    sections["tests"] = text

    text = "## cases: id | cat | title | opts\n"
    for c in model["cases"]:
        default_tests = am.default_linked_tests(c["requirementIds"], model["requirements"])
        policy = {k: v for k, v in c["policy"].items() if v != am.DEFAULT_POLICY[k]}
        text += _row([c["caseId"], c["category"], c["title"]], [
            ("req", ",".join(c["requirementIds"]) or None), ("ev", ",".join(c["evidenceIds"]) or None), ("policy", policy or None),
            ("ctx", c["context"] or None), ("init", c.get("initialMachines")), ("tests", ",".join(c["linkedTestIds"]) if c["linkedTestIds"] != default_tests else None)])
    text += "## events: type | from | to | opts\n"
    for c in model["cases"]:
        text += f"@set case={c['caseId']}\n"
        for ev in c["events"]:
            opts = [(k, v) for k, v in ev.items() if k not in ("seq", "type", "from", "to")]
            text += _row([ev["type"], ev["from"], ev["to"]], opts)
    text += "## expect: case | nodes | opts\n"
    for c in model["cases"]:
        parts = [k if v == "Completed" else f"{k}={v}" for k, v in c["expectedTerminalState"].items()]
        text += _row([c["caseId"], ",".join(parts) or "~"], [("by", c.get("expectedBy")), ("outcomes", c.get("expectedOutcomes"))])
    sections["cases"] = text

    text = "## interventions: id | type | description | opts\n"
    for iv in model["interventions"]:
        opts: list[tuple[str, object]] = []
        target = iv.get("targetNodeId") or iv.get("targetEdgeId")
        opts.append(("target", target))
        opts.append(("applies", ",".join(iv["appliesTo"]) or None))
        opts.append(("class", iv["expect"].get("outcomeClass")))
        opts.append(("terminal", iv["expect"].get("terminal")))
        opts.append(("status", iv["expect"].get("status")))
        opts.append(("why", iv.get("why")))
        opts.append(("note", iv.get("note") or None))
        params = {k: v for k, v in iv.items() if k in ("timeoutMs", "retryMax", "retryBackoffMs", "addLatencyMs", "targetEventId",
                                                         "targetRuleId", "machineId", "transitionId", "guard") and (v is not None or k == "guard")}
        if params:
            opts.append(("params", params))
        if "element" in iv:
            opts.append(("element", iv["element"]["elementId"]))
            opts.append(("use", iv["element"]["kind"]))
            opts.append(("rewrite", ",".join(r["replaceEdgeId"] for r in iv.get("rewrite", []))))
        if "parts" in iv:
            opts.append(("parts", iv["parts"]))
        text += _row([iv["interventionId"], iv["type"], iv["description"]], opts)
    sections["interventions"] = text

    text = "## requirements: id | title | source | opts\n"
    for r in model["requirements"]:
        default_edges = am.default_requirement_edges(r["nodeIds"], model["edges"])
        text += _row([r["id"], r["title"], r["source"]], [
            ("nodes", ",".join(r["nodeIds"]) or None), ("edges", ",".join(r["edgeIds"]) if r["edgeIds"] != default_edges else None),
            ("tests", ",".join(r["tstIds"]) or None), ("ev", ",".join(r["evidenceIds"]) or None), ("tbd", ",".join(r["tbdIds"]) or None),
            ("design", ",".join(r["designIds"]) or None)])
    sections["requirements"] = text

    text = "## tbds: id | title | decision | opts\n"
    for t in model["tbds"]:
        text += _row([t["id"], t["title"], t["decision"]], [("ev", ",".join(t["evidenceIds"]) or None)])
    sections["tbds"] = text

    text = "## policies: id | text | opts\n"
    for pol in model["policies"]:
        text += _row([pol["id"], pol["text"]], [
            ("src", pol["src"]), ("scope", pol["scope"] if pol["scope"] != "all" else None), ("level", pol["level"] if pol["level"] != "REQ" else None),
            ("chapter", pol["chapter"]), ("kind", pol["kind"] if pol["kind"] != "constraint" else None), ("by", pol["by"] if pol["by"] != "ai" else None)])
    sections["policies"] = text

    return {name: _drop_empty(body) for name, body in sections.items()}


def estimate_atl_sections(model: Mapping) -> dict[str, int]:
    """UTF-8 bytes per section of ``atl_sections_text`` plus the three ``#atlas 1`` file headers."""
    sizes = {name: len(body.encode("utf-8")) for name, body in atl_sections_text(model).items()}
    sizes["headers"] = 3 * len("#atlas 1\n")
    return sizes


def _drop_empty(text: str) -> str:
    """Remove ``## name:`` header lines that have no data row below them (an encoder writes no empty section)."""
    lines = text.split("\n")
    kept: list[str] = []
    for index, line in enumerate(lines):
        if line.startswith("## "):
            rows = []
            for follower in lines[index + 1:]:
                if follower.startswith("## "):
                    break
                if follower and not follower.startswith("@set"):
                    rows.append(follower)
            if not rows:
                continue
        elif line.startswith("@set"):
            # a @set line is only useful in front of a data row of the same section
            rest = lines[index + 1:]
            nxt = next((x for x in rest if x and not x.startswith("@set")), "")
            if not nxt or nxt.startswith("## "):
                continue
        kept.append(line)
    return "\n".join(kept)


#: Sections counted in design 3.5's 165,987 B (it excludes the ``tests`` registry and ``policies`` it adds itself).
CORE_SECTIONS = ("spatial", "nodes", "edges", "evidences", "machines", "cases", "interventions", "requirements", "tbds", "headers")


def estimate_atl(model: Mapping) -> dict[str, int]:
    """Section sizes plus ``core`` (comparable with design 3.5) and ``total``."""
    sizes = estimate_atl_sections(model)
    sizes["core"] = sum(sizes[name] for name in CORE_SECTIONS)
    sizes["total"] = sizes["core"] + sizes["tests"] + sizes["policies"]
    return sizes


def write_lending_fixture(path: Path = LENDING_FIXTURE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(am.canonical_bytes(lending_desk("fixed")))
    return path


def _main(argv: Sequence[str]) -> int:
    if len(argv) >= 2 and argv[1] == "write-fixture":
        print("wrote", write_lending_fixture())
        return 0
    if len(argv) >= 3 and argv[1] == "stats":
        preset = argv[2]
        seed = int(argv[3]) if len(argv) > 3 else 0
        model = edge_case(preset, seed) if preset in EDGE_CASES else generate(preset, seed)
        print(json.dumps({"counts": section_counts(model), "estimate": estimate_atl(model), "sha8": am.model_sha8(model),
                          "compactBytes": len(am.canonical_bytes(model))}, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
