"""Official MCP Python SDK v2 adapter; all business logic lives outside the SDK."""
from __future__ import annotations
import json
import os
from .paths import Workspace, GemArcError, safe_response, read_json, digest
from . import service, model, simulation


def workspace() -> Workspace:
    root = os.environ.get("GEMARC_WORKSPACE_ROOT", "")
    if not root:
        raise GemArcError("GEMARC_AUTH_000", "必须先配置 GEMARC_WORKSPACE_ROOT")
    return Workspace(root)


#: Tools that mutate the workspace. The flag is declared on the registration
#: itself, so the server and the web console can never disagree about which
#: tools need GEMARC_ALLOW_WRITE=1.
WRITE_TOOLS: set[str] = set()


def create_server():
    # Import only when MCP is actually launched, allowing offline core unit tests.
    from mcp.server import MCPServer
    app = MCPServer("GemArc MCP", instructions="只使用已授权工作区。事实不可臆造；模拟输出必须标注证据等级。")

    WRITE_TOOLS.clear()
    _app_tool = app.tool

    def tool(name: str, write: bool = False):
        """Register a tool and remember whether it mutates the workspace."""
        def deco(fn):
            if write:
                WRITE_TOOLS.add(name)
            return _app_tool(name=name)(fn)
        return deco

    @tool("gemarc.inspect")
    def inspect(path: str) -> dict:
        """Read-only scan of a local repository in GEMARC_WORKSPACE_ROOT; candidate facts are unconfirmed."""
        return safe_response(lambda: service.inspect_path(workspace(), path))

    @tool("gemarc.plan")
    def plan(project: str, goals: list[str] | None = None) -> dict:
        """Prepare dimension availability and evidence gaps without changing the project."""
        return safe_response(lambda: service.plan(workspace(), project, goals))

    @tool("gemarc.assets")
    def assets(query: str = "") -> dict:
        """Find reusable standardized components and clearly labeled future components."""
        return safe_response(lambda: service.assets(query))

    @tool("gemarc.contracts")
    def contracts(query: str = "") -> dict:
        """List the fixed contracts, vocabularies and fill-in slots; optionally return one document."""
        return safe_response(lambda: service.contracts(query))

    @tool("gemarc.brief")
    def brief(project: str = "", section: str = "") -> dict:
        """The compact work order for a language model: purpose, procedure, slots with their rules, vocabulary, commands. Use this instead of restating the specification."""
        return safe_response(lambda: service.brief(project, section))

    @tool("gemarc.scaffold", write=True)
    def scaffold(project: str, profile: str = "static") -> dict:
        """Materialise the fixed parts (contracts, vocabulary, shared runtime, site shell) plus the fill-in slots; requires GEMARC_ALLOW_WRITE=1."""
        return safe_response(lambda: service.scaffold_project(workspace(), project, profile))

    @tool("gemarc.init", write=True)
    def init(project: str) -> dict:
        """Create a new example-backed GemArc project; requires GEMARC_ALLOW_WRITE=1."""
        return safe_response(lambda: service.init_project(workspace(), project))

    @tool("gemarc.model", write=True)
    def update_model(project: str, replacement_model: dict, expected_sha256: str) -> dict:
        """Replace the model using optimistic concurrency, reject invalid model changes."""
        return safe_response(lambda: service.replace_model(workspace(), project, replacement_model, expected_sha256))

    @tool("gemarc.validate")
    def validate(project: str, strict: bool = False) -> dict:
        """Validate node/edge/view references, scenario rules, and evidence status."""
        def action():
            filename, data = service.load_model(workspace(), project)
            output = model.validate(data, strict)
            output["modelSha256"] = digest(filename)
            return output
        return safe_response(action)

    @tool("gemarc.build", write=True)
    def build(project: str, legacy_dist: str = "", profile: str = "mvp") -> dict:
        """Compile a static site; profile selects mvp (small viewer), static (zero-build ESM) or vite (npm project)."""
        return safe_response(lambda: service.build(workspace(), project, legacy_dist or None, profile))

    @tool("gemarc.simulate")
    def simulate(project: str, scenario_id: str, disabled_nodes: list[str] | None = None,
                 compare_baseline: bool = False) -> dict:
        """Run bounded deterministic event propagation; optional causal intervention comparison."""
        def action():
            _, data = service.load_model(workspace(), project)
            return (simulation.compare(data, scenario_id, disabled_nodes or []) if compare_baseline else
                    simulation.simulate(data, scenario_id, disabled_nodes or []))
        return safe_response(action)

    @tool("gemarc.verify")
    def verify(project: str) -> dict:
        """Verify generated file integrity and schema only; browser/WebGL tests are not claimed."""
        return safe_response(lambda: service.verify(workspace(), project))

    @tool("gemarc.diff")
    def diff(base_project: str, target_project: str) -> dict:
        """Show changed entities and adjacent impact in two project models."""
        return safe_response(lambda: service.diff(workspace(), base_project, target_project))

    @tool("gemarc.export", write=True)
    def export(project: str) -> dict:
        """Export a validated static site ZIP within the authorized project directory."""
        return safe_response(lambda: service.export_zip(workspace(), project))

    @app.resource("gemarc://registry")
    def registry() -> str:
        """Versioned registry for reusable design components."""
        return json.dumps(service.assets(), ensure_ascii=False, indent=2)

    @app.resource("gemarc://schema/model")
    def schema() -> str:
        """Essential architecture model schema; additional semantics are checked in gemarc.validate."""
        return json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            "required": ["meta", "nodes", "edges"],
            "properties": {
                "meta": {"type": "object", "required": ["system"], "properties": {"system": {"type": "string", "minLength": 1}}},
                "nodes": {"type": "array", "items": {"type": "object", "required": ["id", "name"]}},
                "edges": {"type": "array", "items": {"type": "object", "required": ["id", "source", "target"]}},
                "views": {"type": "array"}, "scenarios": {"type": "array"}}}, ensure_ascii=False, indent=2)

    @app.prompt(name="gemarc-design")
    def design_prompt(project: str) -> str:
        """Generate verified 5D architecture data while reusing standardized components."""
        return (f"针对授权项目 {project}：先调用 gemarc.brief 取得精简工作指令、槽位清单与词表，"
                "不要自行发明字段名、证据等级说法或引擎。"
                "然后调用 gemarc.inspect 与 gemarc.assets，读取原始设计证据，"
                "建立一个统一 Architecture Model，未确定事实标为 unknown/assumed；调用 gemarc.validate。"
                "禁止重写现成 Viewer 共通代码；4D 仅使用真实事件或显式假设场景；5D 只能根据确认的规则做计算。"
                "生成后运行 gemarc.build、gemarc.verify，审查未测试的浏览器能力并说明限制。")

    return app


def main():
    server = create_server()
    server.run(transport="stdio")

if __name__ == "__main__":
    main()
