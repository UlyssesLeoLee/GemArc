"""Official MCP Python SDK v2 adapter; all business logic lives outside the SDK.

Two tool sets share one implementation:

``full``       every tool; project tools read and write a workspace directory.
               The default for a local stdio server.
``stateless``  only the tools that take the model as an argument and keep no
               state. The default for a remote (Vercel) deployment, where there
               is no durable filesystem and no caller identity.
``atlas``      the GemArc Atlas authoring tools (``atlas_tools.py``), local stdio.
``atlas_remote``  the two read-only Atlas tools (work order, grammar lint) for a
               remote deployment. One tool set per server process.

Tool names use ``[a-z0-9_]`` only. Claude, OpenAI-style function calling (Codex)
and most agent frameworks restrict function names to ``[a-zA-Z0-9_-]{1,64}``;
a dot is legal in the MCP spec but is rejected or rewritten by those clients.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from .paths import Workspace, GemArcError, safe_response, digest
from . import service, simulation, stateless, __version__
from . import model as model_lib
from . import i18n
from . import userctx

TOOLSETS = ("full", "stateless", "atlas", "atlas_remote")

#: Tools that mutate the workspace. The flag is declared on the registration
#: itself, so the server and the web console can never disagree about which
#: tools need GEMARC_ALLOW_WRITE=1. Only tools of the active tool set are listed.
WRITE_TOOLS: set[str] = set()

#: Every tool registered by the last ``create_server`` call, in order.
REGISTERED_TOOLS: list[str] = []

INSTRUCTIONS = (
    "GemArc turns facts about a software system into a reviewable 0D-6D multimedia architecture document. "
    "This server ships the whole workflow: when the user asks to make a multimedia document "
    "(制作多媒体文档、创建多媒体文档、做多媒体架构站点、多媒体架构模型；旧说法同样受理：制作5D设计、5D架构模型) "
    "or to validate, simulate, diff or render "
    "such a model, call gemarc_media_doc_guide first and follow it; the same text is the resource "
    "gemarc://skill/gemarc-media-doc. No separate skill has to be installed. "
    "Start with gemarc_brief: it is the complete work order (slots, rules, allowed vocabulary). "
    "Never invent facts: every node and edge must point at evidence, otherwise mark it Unknown. "
    "Simulation output is only as strong as the evidence class of its scenario. "
    "只使用已授权工作区；事实不可臆造；模拟输出必须标注证据等级。"
)


#: Instructions of the Atlas tool sets (the client shows at most 4096 characters of them).
ATLAS_INSTRUCTIONS = (
    "GemArc Atlas: you write only FACTS in a compact validated text format; tested code derives and checks everything else. "
    "Start with gemarc_atlas_brief (the work order). To create a design end to end use the prompt gemarc-atlas-create, "
    "or: gemarc_atlas_init once, then repeat gemarc_atlas_status(view=next) -> gemarc_atlas_put one slice -> repair a red draft "
    "with gemarc_atlas_put(mode=patch), until status says done or needs-user. needs-user means ask the user: never invent a fact. "
    "gemarc_atlas_check never writes; writes need GEMARC_ALLOW_WRITE=1 in the server config. "
    "只写事实，不写文档；不确定的事实标 TBD 或降低证据等级；需要用户决定的事项必须询问用户。"
)


def default_workspace_root() -> Path:
    """Where a local server keeps projects when the user configured nothing."""
    return Path.home() / ".gemarc" / "workspace"


def workspace() -> Workspace:
    root = os.environ.get("GEMARC_WORKSPACE_ROOT", "").strip()
    if not root:
        fallback = default_workspace_root()
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise GemArcError("GEMARC_AUTH_000", "未配置 GEMARC_WORKSPACE_ROOT，且默认工作区无法创建") from ex
        root = str(fallback)
    return Workspace(root)


def active_toolset(requested: str | None = None) -> str:
    value = (requested or os.environ.get("GEMARC_TOOLSET") or "full").strip().lower()
    if value not in TOOLSETS:
        raise ValueError(f"GEMARC_TOOLSET must be one of {TOOLSETS}, got {value!r}")
    return value


def _source(project: str, inline: dict):
    """Resolve a model given either a workspace project or an inline model."""
    if bool(project) == bool(inline):
        raise GemArcError("GEMARC_INPUT_003",
                          "必须二选一：传 project（工作区内的项目）或 model（内联模型）")
    if project:
        path, data = service.load_model(workspace(), project)
        return data, digest(path)
    data = stateless.inline_model(inline)
    return data, stateless.model_digest(data)


# -- the per-caller view of the tools (DD-010 section 4) --------------------------------------------------------
#: The key under which the three languages of a tool live inside its ``_meta`` (DD-009).
DESCRIPTIONS_KEY = "gemarc.dev/descriptions"


def _tool_texts(tool) -> dict:
    """The three languages of one tool, read from its ``_meta``."""
    meta = getattr(tool, "meta", None)
    if not isinstance(meta, dict):
        return {}
    texts = meta.get(DESCRIPTIONS_KEY)
    return texts if isinstance(texts, dict) else {}


def _personalized_tool(tool, ctx):
    """The tool as this caller must see it: the title and the description in the caller's language.

    A tool the tables do not translate keeps the English of the code (DD-009), and a caller whose
    language is the English sees exactly the tool the SDK returns.
    """
    entry = _tool_texts(tool).get(ctx.lang)
    if not isinstance(entry, dict):
        return tool
    update: dict = {}
    description = entry.get("description")
    title = entry.get("title")
    if isinstance(description, str) and description:
        update["description"] = description
    if isinstance(title, str) and title:
        update["title"] = title
        annotations = getattr(tool, "annotations", None)
        if annotations is not None:
            update["annotations"] = annotations.model_copy(update={"title": title})
    if not update:
        return tool
    return tool.model_copy(update=update)


def _refused_tool(name: str, lang: str = ""):
    """The answer to a call of a tool the account does not keep (DD-010 section 4.3).

    The tool list of that account does not carry the tool, so a call to it is a mistake of the
    client, not a permission problem: the answer says which tool and why, in the language of the
    account and in the same JSON shape every other tool failure uses. An account that pins no
    language gets the English text, the formal one (DD-010 section 4.1).
    """
    from mcp.types import CallToolResult, TextContent

    message = i18n.text(lang or "en-US", "messages", "GEMARC_ACCT_036")
    payload = {"status": "error", "error": "tool_not_allowed", "code": "GEMARC_ACCT_036",
               "tool": name, "message": message}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False,
                                                                     separators=(",", ":")))],
                          is_error=True)


def create_server(toolset: str | None = None):
    # Import only when MCP is actually launched, allowing offline core unit tests.
    from mcp.server import MCPServer
    from mcp.types import CallToolResult, TextContent, ToolAnnotations

    mode = active_toolset(toolset)
    remote = mode in ("stateless", "atlas_remote")
    atlas = mode.startswith("atlas")
    # ``subscriptions=False``: nothing here changes at run time, and ``subscriptions/listen`` (protocol
    # 2026-07-28) answers with a never-ending SSE stream even when ``json_response=True``: one POST would
    # pin a serverless invocation, which the POST-only gate in api/index.py exists to prevent.
    app = MCPServer("GemArc MCP", instructions=ATLAS_INSTRUCTIONS if atlas else INSTRUCTIONS, version=__version__,
                    subscriptions=False)

    WRITE_TOOLS.clear()
    REGISTERED_TOOLS.clear()
    _app_tool = app.tool

    # -- the per-caller view of the tools (DD-010 section 4) --------------------------------
    # With no identity in the request (no login, or the shared environment token) the answer is
    # exactly the one of the SDK: the list, the descriptions and the defaults do not change.
    # With an identity, the list is the account's own list, every description is the translation
    # into the language of that account, and a tool outside the account's list is refused.
    # A server without those two methods (the SDK-free registration smoke test) has no list to
    # personalize, so it is left alone.
    _base_list_tools = getattr(app, "list_tools", None)
    _base_call_tool = getattr(app, "call_tool", None)
    if _base_list_tools is not None and _base_call_tool is not None:

        async def _list_tools():
            tools = await _base_list_tools()
            ctx = userctx.current()
            if ctx is None or not ctx.has_identity:
                return tools
            return [_personalized_tool(tool, ctx) for tool in tools if ctx.allows(tool.name)]

        async def _call_tool(name, arguments, context=None):
            ctx = userctx.current()
            if ctx is not None and ctx.has_identity and not ctx.allows(name):
                return _refused_tool(name, ctx.lang)
            return await _base_call_tool(name, arguments, context)

        app.list_tools = _list_tools
        app.call_tool = _call_tool

    def reply(payload: dict):
        """Same JSON text as before, but a failed call is flagged as an error.

        Clients (and the model) can then tell "the tool refused" from "the tool
        ran and the answer is X" without parsing the payload.
        """
        if atlas and isinstance(payload, dict):
            # safety net: every atlas tool already caps itself; nothing may exceed the hard cap (24 KiB client limit)
            from .atlas import tools as atlas_core
            payload = atlas_core.fit_result(payload, atlas_core.HARD_CAP)
        failed = isinstance(payload, dict) and payload.get("status") in ("invalid", "error")
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False,
                                                              separators=(",", ":")))],
            is_error=failed)

    def run(action):
        return reply(safe_response(action))

    def tool(name: str, title: str, *, write: bool = False, stateless_ok: bool = False,
             destructive: bool = False, idempotent: bool = False, sets: tuple | None = None):
        """Register a tool, declare its safety hints and whether it mutates the workspace.

        ``sets`` names the tool sets that contain the tool; by default it follows ``stateless_ok``
        (``full`` plus ``stateless`` when set), so every v1 registration is unchanged.
        """
        in_sets = tuple(sets) if sets is not None else (("full", "stateless") if stateless_ok else ("full",))

        def deco(fn):
            if mode not in in_sets:
                return fn  # not part of this tool set; the function stays importable
            if write:
                WRITE_TOOLS.add(name)
            REGISTERED_TOOLS.append(name)
            annotations = ToolAnnotations(
                title=title,
                readOnlyHint=not write,
                # destructive / idempotent only carry meaning for tools that write
                destructiveHint=destructive if write else None,
                idempotentHint=idempotent if write else None,
                openWorldHint=False)
            # The three languages of the tool (DD-009): the description field itself stays the
            # English of the docstring, so a client that reads nothing else still gets the
            # canonical text, and a client that wants another language reads the `_meta`.
            # A tool the tables do not translate carries no `_meta`: it would only repeat
            # that English, and the list of the tools would grow for nothing.
            texts = i18n.tool_texts(name, title, (fn.__doc__ or "").strip())
            extra = {"meta": {"gemarc.dev/descriptions": texts}} if texts else {}
            return _app_tool(name=name, title=title, annotations=annotations, **extra)(fn)
        return deco

    # ---- tools that work in every deployment ---------------------------------------

    @tool("gemarc_media_doc_guide", "Multimedia document workflow", stateless_ok=True)
    def guide(topic: str = ""):
        """The complete multimedia document workflow, served by this server. Call it first when the user asks to make a multimedia architecture document (制作多媒体文档、做多媒体文档、生成多媒体架构站点) or to validate, simulate, diff or render such a model: it states the procedure, the hard rules and the tool sequence to use. The older wording (制作5D设计、5D架构模型) is accepted too: the model covers 0D-6D and the command name is no longer bound to a dimension. `topic` is "" or "workflow" for the full workflow, "assemble" for the step-by-step commands that put the downloaded site together. The same text is the MCP resource gemarc://skill/gemarc-media-doc; no separate skill has to be installed."""
        return run(lambda: service.guide(topic))

    @tool("gemarc_brief", "Work order for the model", stateless_ok=True)
    def brief(project: str = "", section: str = ""):
        """Start here. The compact work order for a language model: purpose, procedure, the slots to fill with their rules, the allowed vocabulary (evidence levels, entity kinds, edge types, diagram types), and what is already built. `section` returns one part - purpose, procedure, rules, slots, vocabulary, commands, alreadyDone, vocabDigest - and an empty `section` returns everything."""
        return run(lambda: service.brief(project, section, stateless=remote))

    @tool("gemarc_contracts", "Contracts and vocabularies", stateless_ok=True)
    def contracts(query: str = ""):
        """List the fixed JSON Schemas, vocabularies and fill-in slots. Pass `query` as a file name from the listing (for example node.schema.json) to get that document."""
        return run(lambda: service.contracts(query))

    @tool("gemarc_assets", "Reusable components", stateless_ok=True)
    def assets(query: str = ""):
        """Search the registry of reusable components. Components marked as planned are not implemented."""
        return run(lambda: service.assets(query))

    @tool("gemarc_new_model", "Empty architecture model", stateless_ok=True)
    def new_model(system: str):
        """Return the empty canonical architecture model for a system, with every slot present and unfilled, plus the slot list. Fill it, then check it with gemarc_validate."""
        return run(lambda: stateless.new_model(system))

    @tool("gemarc_validate", "Validate a model", stateless_ok=True)
    def validate(project: str = "", model: dict = {}, strict: bool = False):
        """Validate node/edge/view references, scenario rules and evidence levels. Pass exactly one of `project` (a project in the workspace) or `model` (the architecture model as a JSON object). `strict` (default false) additionally requires implemented or observed evidence. The result lists diagnostics with entity ids and a modelSha256."""
        def action():
            data, sha = _source(project, model)
            output = model_lib.validate(data, strict)
            output["modelSha256"] = sha
            return output
        return run(action)

    @tool("gemarc_simulate", "Simulate a scenario", stateless_ok=True)
    def simulate(scenario_id: str, project: str = "", model: dict = {},
                 disabled_nodes: list[str] = [], compare_baseline: bool = False):
        """Run a bounded, deterministic event propagation for one scenario of a model (4D). Pass exactly one of `project` or `model`. With `compare_baseline` and `disabled_nodes` it also reruns with those modules switched off and reports the difference (5D); this is rule-based what-if, not causal identification."""
        def action():
            data, _ = _source(project, model)
            blocked = list(disabled_nodes)
            return (simulation.compare(data, scenario_id, blocked) if compare_baseline else
                    simulation.simulate(data, scenario_id, blocked))
        return run(action)

    @tool("gemarc_diff", "Diff two models", stateless_ok=True)
    def diff(base_project: str = "", target_project: str = "",
             base_model: dict = {}, target_model: dict = {}):
        """Show added, removed and modified nodes and edges between two models, plus the nodes adjacent to a change. Give the base as `base_project` or `base_model`, and the target as `target_project` or `target_model`."""
        def action():
            before, before_sha = _source(base_project, base_model)
            after, after_sha = _source(target_project, target_model)
            return service.diff_models(before, after, before_sha, after_sha)
        return run(action)

    @tool("gemarc_render", "Render site data from a model", stateless_ok=True)
    def render(model: dict, profile: str = ""):
        """Validate a model and return the files that depend on it: data/architecture.json, scenarios.json, capabilities.json, evidence-index.json, diagram-catalog.json, spatial-layout.json, three-d.json, workflow-ir.json and case-library.json, plus a description of the fixed site kit and where to download it. The kit carries the site shell, the shared runtime and data/workflow-schema.json (the GWF-IR schema, which follows the vocabulary and not the model). Kit + these files is the complete multimedia document site; nothing is stored on the server. `profile` is empty (the default), which means the render form of the caller: the account's own default when the caller has one, static otherwise; it is static (zero-build ESM) or vite (npm project)."""
        ctx = userctx.current()
        effective = ctx.render_form(profile) if ctx is not None else (profile or userctx.DEFAULT_RENDER_PROFILE)
        return run(lambda: stateless.render(model, effective))

    # ---- tools that need a workspace (local servers) ------------------------------

    @tool("gemarc_inspect", "Scan a local repository")
    def inspect(path: str):
        """Read-only scan of a repository inside the workspace. Returns file statistics and candidate modules; every candidate is unconfirmed and must be verified against the code before it becomes a fact."""
        return run(lambda: service.inspect_path(workspace(), path))

    @tool("gemarc_plan", "Plan dimensions and evidence gaps")
    def plan(project: str, goals: list[str] = []):
        """Report which dimensions (1D-5D) a project can support and which evidence is missing. Does not change the project."""
        return run(lambda: service.plan(workspace(), project, list(goals) or None))

    @tool("gemarc_scaffold", "Create the project scaffold", write=True)
    def scaffold(project: str, profile: str = "static"):
        """Materialise the fixed parts (contracts, vocabulary, shared runtime, site shell) plus the empty model to fill. `profile` is static (default) or vite. Refuses to overwrite an existing scaffold. Requires GEMARC_ALLOW_WRITE=1."""
        return run(lambda: service.scaffold_project(workspace(), project, profile))

    @tool("gemarc_init", "Create an example project", write=True)
    def init(project: str):
        """Create a new project seeded with an illustrative example model (not real facts). Refuses to overwrite. Requires GEMARC_ALLOW_WRITE=1."""
        return run(lambda: service.init_project(workspace(), project))

    @tool("gemarc_model", "Replace a project model", write=True, destructive=True)
    def update_model(project: str, replacement_model: dict, expected_sha256: str):
        """Replace a project's model. `expected_sha256` must be the modelSha256 from the latest gemarc_validate (optimistic concurrency); an invalid replacement is rejected and nothing is written. Requires GEMARC_ALLOW_WRITE=1."""
        return run(lambda: service.replace_model(workspace(), project, replacement_model, expected_sha256))

    @tool("gemarc_build", "Build the site", write=True, destructive=True)
    def build(project: str, legacy_dist: str = "", profile: str = "mvp"):
        """Compile a project into a static site under <project>/site, replacing a previous generated site. `profile`: mvp (default; small viewer), static (zero-build ESM) or vite (npm project). Requires GEMARC_ALLOW_WRITE=1."""
        return run(lambda: service.build(workspace(), project, legacy_dist or None, profile))

    @tool("gemarc_verify", "Verify the built site")
    def verify(project: str):
        """Check the generated site's file integrity and schema only. Browser, WebGL and performance behaviour are not tested."""
        return run(lambda: service.verify(workspace(), project))

    @tool("gemarc_export", "Export the site as a ZIP", write=True, idempotent=True)
    def export(project: str):
        """Pack a verified site into a ZIP inside the project directory and return its local path and sha256. Requires GEMARC_ALLOW_WRITE=1."""
        return run(lambda: service.export_zip(workspace(), project))

    if atlas:
        # lazy import: the atlas package and its binding are loaded only for the atlas tool sets
        try:
            from .atlas_tools import register_atlas
        except ImportError as ex:  # a deployment that does not ship the atlas (see .vercelignore)
            raise GemArcError("GEMARC_TOOLSET_001", f"the {mode} tool set is not part of this deployment") from ex
        register_atlas(tool, run, remote, prompt=app.prompt)
        return app

    # ---- resources and prompt -------------------------------------------------------

    @app.resource("gemarc://registry")
    def registry() -> str:
        """Versioned registry for reusable design components."""
        return json.dumps(service.assets(), ensure_ascii=False, indent=2)

    @app.resource("gemarc://schema/model")
    def schema() -> str:
        """The canonical architecture model JSON Schema; semantic rules beyond it are checked by gemarc_validate."""
        from .scaffold import builder
        path = builder.CONTRACTS_DIR / "canonical-model.schema.json"
        return path.read_text(encoding="utf-8")

    @app.resource("gemarc://skill/gemarc-media-doc")
    def skill_workflow() -> str:
        """The complete multimedia document workflow shipped with this server: the same text as the gemarc_media_doc_guide tool, so a client that can read resources needs no separate skill."""
        from .scaffold import skillpack
        return skillpack.read_text("SKILL.md")

    @app.resource("gemarc://skill/gemarc-media-doc/references/assemble-site.md")
    def skill_assemble() -> str:
        """Step-by-step commands that put the downloaded site kit together into a working site."""
        from .scaffold import skillpack
        return skillpack.read_text("references/assemble-site.md")

    @app.prompt(name="gemarc-media-doc")
    def design_prompt(project: str = "") -> str:
        """Generate verified multimedia architecture data while reusing the fixed scaffold."""
        target = f"针对 {project}：" if project else ""
        lead = ("先调用 gemarc_media_doc_guide 取得服务器自带的完整制作流程"
                "（也可读资源 gemarc://skill/gemarc-media-doc），按它执行；")
        if remote:
            return (f"{target}{lead}再调用 gemarc_brief 取得工作指令、槽位清单与词表，不要自行发明字段名、证据等级说法或引擎。"
                    "用 gemarc_new_model 取得空骨架，再用你自己对用户仓库的访问能力读取真实证据来填写；"
                    "未确定的事实标为 Unknown。反复调用 gemarc_validate(model=…) 直到 0 个 error；"
                    "只有要宣称“已确认”时才加 strict=true（strict 下 Unknown/Hypothetical 必然不过，不要为此改证据等级）。"
                    "4D 仅使用真实事件或显式假设场景；5D 只能根据确认的规则做计算（gemarc_simulate）；"
                    "6D 的执行图只写契约与门禁，不要声称会真的执行。"
                    "最后调用 gemarc_render 取得 data/*.json 与固定件下载地址，"
                    "做一次整体 UI/UX 优化（硬规则：风格基准是美漫卡通渲染的宝石主题，"
                    "固定件已预置这一版主题），并说明哪些浏览器能力未经测试。")
        return (f"{target}{lead}再调用 gemarc_brief 取得精简工作指令、槽位清单与词表，"
                "不要自行发明字段名、证据等级说法或引擎。"
                "然后调用 gemarc_inspect 与 gemarc_assets，读取原始设计证据，"
                "建立一个统一 Architecture Model，未确定事实标为 unknown/assumed；调用 gemarc_validate。"
                "禁止重写现成 Viewer 共通代码；4D 仅使用真实事件或显式假设场景；5D 只能根据确认的规则做计算。"
                "生成后运行 gemarc_build、gemarc_verify，做一次整体 UI/UX 优化"
                "（硬规则：风格基准是美漫卡通渲染的宝石主题，固定件已预置这一版主题），"
                "审查未测试的浏览器能力并说明限制。")

    return app


def main():
    server = create_server()
    server.run(transport="stdio")

if __name__ == "__main__":
    main()
