"""MCP binding of the GemArc Atlas tools (design 4.1) and of the one-sentence entry prompt (addendum A1, D13).

``server.create_server`` calls ``register_atlas(tool, run, remote, prompt=...)`` for the tool sets ``atlas`` (local stdio) and
``atlas_remote`` (Vercel, stateless, read-only). This module owns the REGISTRY: adding a tool of a later lane is one decorated
function here plus one row in ``docs/atlas/ATLAS_TOOLS.md``::

    @atlas_tool("gemarc_atlas_xyz", "Short title", write=False, sets=("atlas",))
    def xyz(project: str, ...) -> dict:
        \"\"\"Description for the model (at most 800 bytes).\"\"\"
        return core.xyz(_ws(), project, ...)

Rules every tool function obeys (the surface test enforces them for the live list):

* input hints are plain ``str = ""``, ``int = 0``, ``bool = False``, ``list[str] = []`` (no ``Optional``, no union: the schema
  must not contain ``anyOf`` or ``null``, Codex and Hermes sanitise them);
* the function returns the JSON-able dict of ``atlas.tools``; ``status`` in ``invalid`` or ``error`` becomes ``isError`` in the
  server's ``reply`` (the payload is kept); the function never resolves the workspace at registration time (``_ws()`` is called
  inside the body, only when a project is named);
* the docstring is the tool description.

The module imports only the standard library and the atlas package (so it works without the MCP SDK; the SDK is touched
only by ``server.py``). ``PENDING_TOOLS`` lists the tool names that the entry prompt cites although no lane registered them
yet; the lane that registers the last one empties the tuple (the prompt test enforces that every cited name is registered
or pending).
"""
from __future__ import annotations

import functools
import math
import re
from dataclasses import dataclass
from typing import Callable

from .atlas import project as pj
from .atlas import slicecheck
from .atlas import slices as sl
from .atlas import status as status_mod
from .atlas import tools as core
from .paths import GemArcError

LOCAL = "atlas"
REMOTE = "atlas_remote"
BOTH = (LOCAL, REMOTE)

PROMPT_NAME = "gemarc-atlas-create"
REVIEW_TOOL = "gemarc_atlas_review"
#: Tools the entry prompt cites that no lane has registered yet. The review lane empties this tuple.
PENDING_TOOLS: tuple = ()
#: Tools of the design table (4.1) that later lanes register. A lane that registers one removes it from this tuple and adds
#: its row to ``docs/atlas/ATLAS_TOOLS.md``; the surface test fails while a registered tool is still listed here. Empty since
#: batch 3: the 12 tools of design 4.1 plus the review tool of addendum A1 (13 in all) are registered.
PLANNED_TOOLS: tuple = ()

#: ``atlas_remote`` check limits (design 4.1): the text and the ``known`` ids are each at most 32 KB.
REMOTE_TEXT_BYTES = 32 * 1024
E_REMOTE_ARG = "GEMARC_ATLAS_TOOL_003"


@dataclass(frozen=True)
class AtlasTool:
    """One registered tool: the function (its docstring is the description) and its honest hints."""

    name: str
    title: str
    fn: Callable
    write: bool
    destructive: bool
    idempotent: bool
    sets: tuple


#: Every atlas tool in registration order. The same name may appear once per tool set (check has a remote variant).
REGISTRY: list = []


def atlas_tool(name: str, title: str, *, write: bool = False, destructive: bool = False, idempotent: bool = False,
               sets: tuple = (LOCAL,)):
    """Add the decorated function to ``REGISTRY`` (it is registered by ``register_atlas`` for the sets it names)."""
    def deco(fn):
        for other in REGISTRY:
            if other.name == name and set(other.sets) & set(sets):
                raise ValueError(f"atlas tool {name!r} is registered twice for the same tool set")
        REGISTRY.append(AtlasTool(name, title, fn, write, destructive, idempotent, tuple(sets)))
        return fn
    return deco


def tools_of(toolset: str) -> list:
    """The registry entries of one tool set (``atlas`` or ``atlas_remote``), in registration order."""
    return [spec for spec in REGISTRY if toolset in spec.sets]


def tool_names(toolset: str) -> tuple:
    return tuple(spec.name for spec in tools_of(toolset))


def _ws(create: bool = False):
    """The workspace, resolved at call time (never at registration: ``create_server`` must not touch the disk).

    Only a call that writes (``create=True``) may create the default workspace directory; a read finds none and says so."""
    return pj.get_workspace(create=create)


# ---------------------------------------------------------------------------------------------------------------
# Tools of both tool sets
# ---------------------------------------------------------------------------------------------------------------


@atlas_tool("gemarc_atlas_brief", "Work order", sets=BOTH)
def brief(section: str = "", lang: str = "en-US") -> dict:
    """Start here: the work order for authoring an atlas. `section` empty = all, or format, vocab, derived, slices, oracle, docs, economics. `lang` zh-CN or en-US."""
    return core.brief(section, lang)


@atlas_tool("gemarc_atlas_check", "Check slice text", sets=(REMOTE,))
def check_remote(text: str, known: str = "", format: str = "text") -> dict:
    """Lint ONE slice text (at most 32 KB), nothing is stored. Advisory: grammar, vocabulary, references inside the slice. `known`: ids defined elsewhere, one `<kind> <id>` per line. `format` text or json."""
    return _remote_check(text, known, format)


@atlas_tool("gemarc_atlas_init", "Create project", write=True)
def init(project: str, title: str = "", mode: str = "asis", languages: list[str] = ["zh-CN"], profile: str = "software",
         repos: list[str] = []) -> dict:
    """Create the project. `mode` asis (code/docs) or tobe (from a requirement text). `repos`: evidence root aliases the operator set. Never overwrites. Needs GEMARC_ALLOW_WRITE=1."""
    return core.init(_ws(True), project, title, mode, list(languages), profile, list(repos))


@atlas_tool("gemarc_atlas_put", "Store one slice", write=True, destructive=True, idempotent=True)
def put(project: str, slice: str, text: str, mode: str = "replace", format: str = "atl", base: str = "") -> dict:
    """Validate and store one slice ([a-z0-9-]{1,40}). No ERROR: committed. ERROR: not committed, kept as a red draft. `mode=patch`: only changed rows (same identity replaces, `-id` deletes). `base`=sha8 to replace. Needs GEMARC_ALLOW_WRITE=1."""
    return core.put(_ws(True), project, slice, text, mode, format, base)


@atlas_tool("gemarc_atlas_check", "Check project or text", sets=(LOCAL,))
def check(project: str = "", text: str = "", slice: str = "", mode: str = "slice", rule: str = "", offset: int = 0,
          format: str = "text") -> dict:
    """Validate, never writes. One of `project` or `text` (advisory). `slice` narrows. `mode` slice or full. `rule` filters (RF), `offset` pages, `format` text or json."""
    return core.check(_ws() if project else None, project, text, slice, mode, rule, int(offset), format)


@atlas_tool("gemarc_atlas_status", "Project status")
def status(project: str, view: str = "next", scope: str = "") -> dict:
    """What to do next; resumes after lost context. `view=next`: {done, phase, next (<=10 lines), blockers, needsUser, waived}. Others: report (`scope` filters), dims, oracle, evidence."""
    return core.status(_ws(), project, view, scope)


@atlas_tool("gemarc_atlas_index", "List model contents")
def index(project: str, scope: str = "nodes", with_edges: bool = False, ids: list[str] = [], detail: str = "line",
          offset: int = 0) -> dict:
    """Compact lines about the committed model (instead of re-reading slices). `scope` nodes, room:<id>, req:<id>, case:<id>, ids:<prefix>; `with_edges`, `detail=expanded`, `offset`."""
    return core.index(_ws(), project, scope, bool(with_edges), list(ids), detail, int(offset))


@atlas_tool("gemarc_atlas_simulate", "Blind verdicts", write=True, idempotent=True)
def simulate(project: str, case: str = "", intervention: str = "", detail: str = "count") -> dict:
    """Blind verdicts for your expectations (seals first). `detail=count`: pass/fail and HOW MANY asserted nodes differ, never which; `ids`/`trace` reveal, are logged, and later edits make the item `revised`. Needs GEMARC_ALLOW_WRITE=1."""
    return core.simulate(_ws(), project, case, intervention, detail)


@atlas_tool("gemarc_atlas_doc", "Document outline")
def doc(project: str, unit: str = "system", level: str = "BD", lang: str = "", chapter: str = "", page: int = 0) -> dict:
    """Structure of the derived documents (never engine output): outline of a `unit` (system, req:<id>, case:<id>, node:<id>) at `level` REQ|BD|DD, a digit per chapter (2 = NOT-AUTHORED slot). `chapter` 1-12 (+`page`): its text."""
    return core.doc(_ws(), project, unit, level, lang, chapter, int(page))


@atlas_tool("gemarc_atlas_outline", "Source outline")
def outline(root: str, path: str, offset: int = 0) -> dict:
    """Reading aid for as-is evidence: def/class/heading lines of ONE file with line numbers and a unique anchor snippet. `root` = alias the operator set, `path` relative to it, `offset` continues. Secrets redacted."""
    return core.outline(root, path, int(offset))


@atlas_tool("gemarc_atlas_evidence", "Verify evidence", write=True, idempotent=True)
def evidence(project: str, ids: list[str] = []) -> dict:
    """Check evidence rows against the real files (roots from the operator's env), stamp the passes, list each row not green with a repair hint (never applied). `ids` limits. As-is Implemented claims need a stamped anchor. Needs GEMARC_ALLOW_WRITE=1."""
    return core.evidence(_ws(), project, list(ids))


@atlas_tool("gemarc_atlas_build", "Build the atlas", write=True, destructive=True, idempotent=True)
def build(project: str, release: bool = False, docs: bool = True, gate: str = "none") -> dict:
    """Write dist/ (viewer page, documents, diagrams, prompts) and oracle-sheet.md for the user. Only sealed or confirmed expectations show results. `gate=smoke` runs the browser gate; `release=true` needs the full gate, and a refusal leaves the page unverified. Needs GEMARC_ALLOW_WRITE=1."""
    return _no_host_paths(core.build_all(_ws(), project, bool(release), bool(docs), gate))


@atlas_tool("gemarc_atlas_verify", "Browser gate", write=True, idempotent=True)
def verify(project: str, widths: list[int] = [1024, 1366, 1600]) -> dict:
    """Browser gate on the built page at `widths`, verdict bound to this page. FAIL, SKIPPED (no browser: NOT passed) and INCONCLUSIVE reply invalid. Needs a current build and GEMARC_ALLOW_WRITE=1."""
    return _no_host_paths(core.verify(_ws(), project, [int(w) for w in widths]))


@atlas_tool("gemarc_atlas_review", "Self-review", write=True)
def review(project: str, action: str = "report", notes: str = "") -> dict:
    """The ONE self-review, only at phase review. `action=report` (never writes): ranked findings and 8 sample rows to check against the real source. `action=record` (after a report): `notes` required (fixed=<ids>; open=<ids>); needs GEMARC_ALLOW_WRITE=1."""
    return core.review(_ws(), project, action, notes)


def _no_host_paths(reply: dict) -> dict:
    """The MCP reply names the files of the project relative to it (``atlas/dist/...``): the host path carries the user name and the workspace
    location, which a client does not need and must not leak into its transcript (the shell entry still prints the full path)."""
    for key in ("dist", "report"):
        value = reply.get(key)
        if isinstance(value, str) and value:
            norm = value.replace("\\", "/")
            at = norm.rfind("/atlas/")
            reply[key] = norm[at + 1:] if at >= 0 else norm.rsplit("/", 1)[-1]
    return reply


def _remote_check(text: str, known: str, fmt: str) -> dict:
    """The remote lint: one inline slice plus the optional ``known`` ids; never a workspace (``tools.check`` has no ``known``)."""
    def body() -> dict:
        if not text or not text.strip():
            raise GemArcError(E_REMOTE_ARG, "text is required")
        if fmt not in ("text", "json"):
            raise GemArcError(core.E_ARG, "format is 'text' or 'json'")
        if len(text.encode("utf-8", errors="replace")) > REMOTE_TEXT_BYTES:
            raise GemArcError(sl.E_LIMIT, f"text is limited to {REMOTE_TEXT_BYTES // 1024} KB on the remote check")
        if len((known or "").encode("utf-8", errors="replace")) > REMOTE_TEXT_BYTES:
            raise GemArcError(sl.E_LIMIT, f"known is limited to {REMOTE_TEXT_BYTES // 1024} KB")
        name = core.INLINE_NAME + (".jsonl" if text.lstrip().startswith("{") else ".atl")
        parsed_known = slicecheck.parse_known(known)
        result = slicecheck.check([(name, text)], None, mode="slice", incoming=[name], known=parsed_known,
                                  element_templates=status_mod.element_templates())
        reply = core._check_reply(
            result, scope="grammar+intra-slice", authority="advisory", mode="slice", rule="", offset=0, fmt=fmt,
            extra={"known": sum(len(v) for v in parsed_known.values()),
                   "next": ["advisory: ids defined in other slices resolve only through `known`; the local server's check decides"]})
        return core.fit_result(reply, core.DEFAULT_CAP)
    return core._guard(body)


# ---------------------------------------------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------------------------------------------


def _bind(fn: Callable, run: Callable) -> Callable:
    """The function the SDK sees: same signature and docstring, body run through the server's ``run``."""
    @functools.wraps(fn)
    def bound(*args, **kwargs):
        def call():
            with status_mod.product_gates():     # the real completion gates (oracle, evidence, docs, build, browser) apply to MCP calls
                return fn(*args, **kwargs)
        return run(call)
    return bound


def register_atlas(tool: Callable, run: Callable, remote: bool, prompt: "Callable | None" = None) -> list:
    """Register the tools of this tool set through the server's ``tool`` decorator; returns the registered names.

    ``tool`` is ``create_server``'s decorator factory, ``run`` its error-flagging runner, ``remote`` true for ``atlas_remote``;
    ``prompt`` is the SDK's prompt decorator (``app.prompt``), omitted by callers that only need the tools.
    """
    toolset = REMOTE if remote else LOCAL
    names = []
    for spec in tools_of(toolset):
        tool(spec.name, spec.title, write=spec.write, destructive=spec.destructive, idempotent=spec.idempotent,
             sets=spec.sets)(_bind(spec.fn, run))
        names.append(spec.name)
    if prompt is not None:
        text_fn = create_prompt_remote if remote else create_prompt_local
        prompt(name=PROMPT_NAME)(text_fn)
    return names


# ---------------------------------------------------------------------------------------------------------------
# The entry prompt "gemarc-atlas-create" (addendum A1, D13: the driver text)
# ---------------------------------------------------------------------------------------------------------------

#: Puts the driver may spend before it must report ``blocked``: ``BUDGET_FIXED + BUDGET_PER_GROUP * ceil(nodes / GROUP)``
#: (the brief's sizing: about two puts per 11 nodes; the fixed part covers space, scenarios, requirements and policies).
BUDGET_FIXED = 8
BUDGET_PER_GROUP = 2
BUDGET_GROUP = 11
BUDGET_DEFAULT_NODES = 40
MAX_REPAIRS_PER_SLICE = 3


#: Step 6 of the driver text (after the structure is authored: the gates of the completion contract, in the order status names them).
FINISH = (
    "6. 收尾（status 的 phase 为 build：它的 next 每次点名还红的那道门，按下面的顺序做，不要跳）：\n"
    "   a. gemarc_atlas_simulate(project)（detail=count）：先封存期望再出盲测判定，只告诉你通过与否和不符的节点个数，不说是哪些。"
    "ids、trace 是被记账的揭示：不要为了凑通过去看，更不要照着引擎结果改期望（改过的期望会变成 revised，要用户确认）；不符时先检查用例事件有没有写错。\n"
    "   b. as-is 才做：gemarc_atlas_evidence(project)：逐条修复 FAIL 行（put mode=\"patch\" 改文件或锚点，修复建议绝不会被自动应用），"
    "读不到源码就降低 verif= 或标 TBD，绝不猜锚点；SKIP / REPO_UNMAPPED 说明证据根没有配置（只有操作者能在服务器环境设置 GEMARC_ATLAS_ROOTS）："
    "这时 status 会给出 needs-user，停下，把 needsUser 原样告诉用户（由操作者设置证据根，或由用户同意降低这些事实的 verif=），不要反复调用 evidence。\n"
    "   c. gemarc_atlas_build(project)：写 dist/（页面、文档、图、提示词）和 oracle-sheet.md；报 BLOCK 或 ERROR 时回到第 4 步修复。\n"
    "   d. gemarc_atlas_verify(project)（用默认 widths，只传一个宽度不算通过）：浏览器门，FAIL 照它列出的问题修复后重新 build、verify。回复 SKIPPED 表示没有找到浏览器，页面没有被验证，不算通过："
    "停下（needs-user），告诉用户装 Edge 或 Chrome 后再 verify，或由用户自己把豁免理由写进 atlas/waive-browser.txt（你绝不写这个文件）。\n"
    "   e. 可选：gemarc_atlas_doc(project) 看文档大纲，markers 大于 0 或某章是 2 说明有 [NOT-AUTHORED] 槽位，补写它提示的行。")


def put_budget(nodes: int = BUDGET_DEFAULT_NODES) -> int:
    """Total ``put`` calls allowed for a system of about ``nodes`` nodes."""
    return BUDGET_FIXED + BUDGET_PER_GROUP * math.ceil(max(1, int(nodes)) / BUDGET_GROUP)


def _review_registered() -> bool:
    return any(spec.name == REVIEW_TOOL for spec in tools_of(LOCAL))


_LANG_ALIASES = {"zh": "zh-CN", "zh-cn": "zh-CN", "zh-hans": "zh-CN", "ja": "ja-JP", "ja-jp": "ja-JP", "en": "en-US", "en-us": "en-US"}
_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _mode_of(mode: str, requirement: str = "") -> str:
    """``tobe`` / ``asis``. An empty mode is to-be when a requirement text was given (a requirement describes a new system)
    and as-is otherwise; an unknown word is refused instead of silently becoming as-is."""
    word = (mode or "").strip().lower().replace("-", "").replace("_", "")
    if not word:
        return "tobe" if (requirement or "").strip() else "asis"
    if word in ("tobe", "to"):
        return "tobe"
    if word in ("asis", "as"):
        return "asis"
    raise ValueError(f"mode must be asis or tobe, got {mode[:20]!r}")


def _lang_of(lang: str) -> str:
    """The document language as ``init`` takes it (zh-CN, ja-JP, en-US); common short forms are mapped, anything else refused."""
    word = (lang or "").strip()
    if not word:
        return "zh-CN"
    hit = _LANG_ALIASES.get(word.lower())
    if hit is None:
        raise ValueError(f"lang must be one of zh-CN, ja-JP, en-US, got {word[:20]!r}")
    return hit


def _project_of(project: str) -> str:
    name = (project or "").strip()
    if name and not _PROJECT_RE.fullmatch(name):
        raise ValueError("project is a lowercase name of letters, digits and '-' (at most 40 characters)")
    return name


def _subject(project: str, mode: str) -> str:
    return project or ("新系统" if mode == "tobe" else "当前仓库/系统")


def _requirement_block(requirement: str) -> str:
    text = (requirement or "").strip()
    if not text:
        return "（未提供。先向用户要需求原文，或要他指明要分析的仓库与文档，拿到后再继续；不要凭空设想需求。）"
    return text + "\n（若上面是文件路径，先读取该文件。）"


def create_prompt_local(requirement: str = "", mode: str = "", project: str = "", lang: str = "zh-CN") -> str:
    """Create a 5D design atlas for a system end to end: author validated facts slice by slice, repair, self-review once, report. `mode` asis or tobe (empty: tobe when a requirement is given). `lang` zh-CN, ja-JP or en-US."""
    m = _mode_of(mode, requirement)
    project = _project_of(project)
    label = "to-be（按用户需求设计新系统）" if m == "tobe" else "as-is（基于真实代码与文档还原现状）"
    name = project or "<自拟小写短名 [a-z0-9-]>"
    languages = _lang_of(lang)
    brief_lang = languages if languages in ("zh-CN", "en-US") else "en-US"
    budget = put_budget()
    if _review_registered():
        review = (
            "7. 自审，恰好一次（phase=review 时，即上一步的各道门都绿了）：先 gemarc_atlas_review(project, action=\"report\") 取机械报告与抽样行（record 之前必须有针对当前模型的 report：修复改了模型后要再 report 一次，它只读，否则 record 会被拒绝）；"
            "逐条用真实源码或需求原文核对抽样行与报告（客户端支持时交给一个全新的子代理，只给它报告和抽样）；"
            "然后只修复报告里列出的问题（put mode=patch）。修复改了模型，就按 status 点名的顺序重做 simulate、evidence、build、verify"
            "（门变绿之前 record 会被拒绝，这不是第二轮自审），最后 gemarc_atlas_review(action=\"record\", notes=\"fixed=<ids>; open=<ids>\")，notes 不能为空。"
            "自审只做这一次：record 之后只修已列出的 findings，不得再开第二轮自审；仍然存活的 BLOCK 发现写进最终报告交给用户，不要循环。")
    else:
        review = (
            f"7. 自审，恰好一次（phase=review 时）：自审工具 {REVIEW_TOOL} 本服务器尚未提供，"
            "改为你自己对照真实源码或需求原文抽查 8 行事实，只修复查出的错误（put mode=patch），然后直接写最终报告，"
            "并在报告里写明「自审由人工式抽查完成，未使用自审工具」。自审只做这一次，不得再开第二轮自审。")
    return (
        f"目标：为「{_subject(project, m)}」创建 5D 设计资料。模式 {label}，文档语言 {languages}。"
        "你只写事实（紧凑的 .atl 文本切片）；固定、已测试的代码负责推导、检查与渲染其余一切。一切以工具回复为准，不凭记忆，不改写工具的判定，不自己写 HTML 或文档。\n\n"
        f"用户需求：\n{_requirement_block(requirement)}\n\n"
        "流程（按顺序执行，不要跳步）：\n"
        f"1. gemarc_atlas_brief(section=\"all\", lang=\"{brief_lang}\") 读一次工作指令；需要细节再取单节（format、vocab、slices、oracle）。\n"
        f"2. gemarc_atlas_init(project=\"{name}\", title=..., mode=\"{m}\", languages=[\"{languages}\"])；项目已存在则不要重复 init，直接转第 4 步。\n"
        "3. 用你自己的文件写入能力，把需求原文原样保存到 init 回复 path 下的 user/requirement.txt（to-be 必须；as-is 也保存，供引用）。"
        "写不了文件时在最终报告里说明；to-be 且没有原文可引用时按 needs-user 处理。\n"
        "4. 循环（写事实，直到 phase 离开 author 和 repair）：gemarc_atlas_status(view=\"next\") -> 按 next 第 1 条点名的步骤或空位写下一个切片（切片名按 brief 的约定自取）-> gemarc_atlas_put。"
        "put 回复自带 next 的第一行：照它做，不必每次 put 后都调 status；只在红稿之后、续接（丢了上下文）、大约每 5 次 put、阶段切换时调 status view=next。"
        "put 返回 invalid 且 status 为 invalid 时有两种情形：语法错误或规则 ERROR 时你的文本被存为红稿（没有丢失），只用 mode=\"patch\" 发送出错的行，"
        "先改列出的第一条错误，不要整片重发、不要重读切片（替换一行时它沿用被替换行的 @set 默认值）；"
        "而语法坏到无法解析的 patch（GEMARC_ATLAS_SLICE_005）什么都不会写，改正后重发。需要确认已有内容时用 gemarc_atlas_index，不要重读切片。"
        "回复带 retry:true（例如 GEMARC_ATLAS_SLICE_004，别的写入占着锁）不算修复轮：几秒后原样重发。"
        "遇到 GEMARC_AUTH_002 停下，告诉用户需要在 MCP 配置里设置 GEMARC_ALLOW_WRITE=1，不要绕过。"
        "to-be 的步骤顺序见 status 的 tobe step N/7 行；phase 为 author 而 needsUser 已非空时，先把能写的写完，phase 变成 needs-user 再停。"
        "as-is 要先读真实代码与文档取证，每个已实现事实都引用真实锚点；读不到就标 TBD 或降低证据等级（verif=），绝不猜测。"
        "TBD 由用户回答后，把他的原话存进 atlas/user/<名字>.txt，再写 policies 行 by=user src=<tbd id>，text 里用引号引用那句原话。\n"
        f"5. 停止条件（任一满足就停，不要继续硬写）：\n"
        "   - status 的 phase 是 needs-user：停下，把 needsUser 列表原样问给用户并等待回答；绝不替用户决定，绝不编造事实。"
        "这包括 TBD 的回答、用户对被改动的期望（revised）所做的 oracle 表确认、以及没有浏览器时的安装或豁免、as-is 没有配置证据根时的设置或降低 verif=。\n"
        f"   - 循环保护：同一个切片最多修复 {MAX_REPAIRS_PER_SLICE} 轮；第 {MAX_REPAIRS_PER_SLICE + 1} 次仍红，停下并报告 blocked，附最后一次诊断。\n"
        f"   - put 总次数预算 = {BUDGET_FIXED} + {BUDGET_PER_GROUP} x ceil(节点数/{BUDGET_GROUP})（节点数先按需求或源码规模估计，估不出按 {BUDGET_DEFAULT_NODES}，"
        f"即 {budget} 次）；用完预算还没到 review，停下并报告 blocked，附最后一次 status 与诊断。\n"
        "   - status 的 done 为 true：转最终报告，并先读回复里的 openBlocks（仍然打开的 BLOCK）、waived（被用户豁免的门，页面未验证）和 next 里的 await 行，如实逐条写进报告。\n"
        f"{FINISH}\n"
        f"{review}\n"
        "8. 最终报告（只写工具确认过的事实）：done/phase；切片清单与 put 次数、修复轮数；各道门的结果（盲测判定、证据核验、文档、构建、浏览器门，"
        "浏览器门若是 SKIPPED 或被用户豁免（status 的 waived 字段）要如实写出，页面未验证）；自审发现及处理结果，以及 status 的 openBlocks 里每一条仍然打开的 BLOCK；needs-user 与 blocked 项；"
        "已就绪的维度（1D-5D）；所有 TBD 与低证据等级的项；"
        "oracle 表：把 atlas/dist/oracle-sheet.md 交给用户，写明有几条期望等他确认（他勾选行或保留 APPROVE-ALL 行，写进 atlas/oracle-confirm.txt；这个文件只有用户写，你绝不写）。\n")


def create_prompt_remote(requirement: str = "", mode: str = "", project: str = "", lang: str = "zh-CN") -> str:
    """Create a 5D design atlas through the remote lint: write slices in local files, lint each one, repair, review once, report. `mode` asis or tobe (empty: tobe when a requirement is given)."""
    m = _mode_of(mode, requirement)
    project = _project_of(project)
    label = "to-be（按用户需求设计新系统）" if m == "tobe" else "as-is（基于真实代码与文档还原现状）"
    languages = _lang_of(lang)
    brief_lang = languages if languages in ("zh-CN", "en-US") else "en-US"
    return (
        f"目标：为「{_subject(project, m)}」创建 5D 设计资料的事实切片。模式 {label}，文档语言 {languages}。"
        "本服务器是只读的远程服务，只有 gemarc_atlas_brief 与 gemarc_atlas_check 两个工具，不保存任何东西：切片文本保存在你自己的本地文件里。"
        "你只写事实（紧凑的 .atl 文本），不凭空设想，不自己写 HTML 或文档。\n\n"
        f"用户需求：\n{_requirement_block(requirement)}\n\n"
        "流程：\n"
        f"1. gemarc_atlas_brief(section=\"all\", lang=\"{brief_lang}\") 读一次工作指令。\n"
        "2. 按指令的切片划分（00-space、10-structure ...、20-scenarios ...）逐个写成本地文件，每个切片约 11 个节点。\n"
        "3. 每写完一个切片，gemarc_atlas_check(text=切片全文, known=其他切片已定义的 id，每行一个「kind id」) 检查；"
        "有 ERROR 就只改出错的行，先改第一条，再检查。check 的结论是 advisory：只覆盖语法与切片内部引用，权威检查在本地服务器。\n"
        "4. 停止条件：needs-user，即需要用户决定的事项（TBD、缺少的需求事实），必须停下来问用户，绝不编造事实；"
        f"同一个切片最多修复 {MAX_REPAIRS_PER_SLICE} 轮，仍红就报告 blocked 并附最后一次诊断；"
        f"check 总次数预算 = {BUDGET_FIXED} + {BUDGET_PER_GROUP} x ceil(节点数/{BUDGET_GROUP})，用完仍未全绿同样报告 blocked。\n"
        "5. 自审，恰好一次：全部切片通过 check 后，对照真实源码或需求原文抽查 8 行事实，只修复查出的错误；不得再开第二轮自审。\n"
        "6. 最终报告（只写工具确认过的事实）：切片清单、check 次数、自审发现及处理、needs-user 与 blocked 项、所有 TBD 与低证据等级的项，"
        "并说明这只是远程 advisory 检查，需要在本地 atlas 服务器上做权威的 full 检查与构建。\n")
