"""CLI counterpart to MCP tools, useful in CI and offline unit tests."""
import argparse
import json
import os
import sys
from .paths import Workspace, GemArcError, digest
from . import service, model, simulation


def main(argv=None):
    p = argparse.ArgumentParser(prog="gemarc", description="GemArc 5D scaffold CLI")
    p.add_argument("--workspace", default=os.environ.get("GEMARC_WORKSPACE_ROOT"), help="Authorized existing workspace directory")
    commands = p.add_subparsers(dest="command", required=True)
    for cmd in ("init", "plan", "validate", "build", "verify", "export"):
        sub = commands.add_parser(cmd)
        sub.add_argument("project")
        if cmd == "validate": sub.add_argument("--strict", action="store_true")
        if cmd == "build":
            sub.add_argument("--legacy-dist", default="")
            sub.add_argument("--profile", default="mvp", choices=list(service.BUILD_PROFILES),
                             help="mvp=小型查看器, static=零构建 ESM 站, vite=npm 工程")
    insp = commands.add_parser("inspect")
    insp.add_argument("path")
    sc = commands.add_parser("scaffold")
    sc.add_argument("project")
    sc.add_argument("--profile", default="static", choices=["static", "vite"])
    ct = commands.add_parser("contracts")
    ct.add_argument("--query", default="")
    sim = commands.add_parser("simulate")
    sim.add_argument("project")
    sim.add_argument("scenario")
    sim.add_argument("--disable", nargs="*", default=[])
    sim.add_argument("--compare", action="store_true")
    d = commands.add_parser("diff")
    d.add_argument("base_project")
    d.add_argument("target_project")
    a = commands.add_parser("assets")
    a.add_argument("--query", default="")
    ns = p.parse_args(argv)
    try:
        if ns.command == "assets":
            result = service.assets(ns.query)
        elif ns.command == "contracts":
            # Read-only static content; it needs no workspace.
            result = service.contracts(ns.query)
        else:
            if not ns.workspace: raise GemArcError("GEMARC_AUTH_000", "--workspace 或 GEMARC_WORKSPACE_ROOT 为必填")
            ws = Workspace(ns.workspace)
            if ns.command == "init": result = service.init_project(ws, ns.project)
            elif ns.command == "inspect": result = service.inspect_path(ws, ns.path)
            elif ns.command == "plan": result = service.plan(ws, ns.project)
            elif ns.command == "validate":
                path, m = service.load_model(ws, ns.project)
                result = model.validate(m, ns.strict)
                result["modelSha256"] = digest(path)
            elif ns.command == "build":
                result = service.build(ws, ns.project, ns.legacy_dist or None, ns.profile)
            elif ns.command == "scaffold": result = service.scaffold_project(ws, ns.project, ns.profile)
            elif ns.command == "contracts": result = service.contracts(ns.query)
            elif ns.command == "verify": result = service.verify(ws, ns.project)
            elif ns.command == "export": result = service.export_zip(ws, ns.project)
            elif ns.command == "diff": result = service.diff(ws, ns.base_project, ns.target_project)
            elif ns.command == "simulate":
                _, m = service.load_model(ws, ns.project)
                result = (simulation.compare(m, ns.scenario, ns.disable) if ns.compare else
                          simulation.simulate(m, ns.scenario, ns.disable))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "ok" else 1
    except (GemArcError, OSError) as ex:
        print(json.dumps({"status": "invalid", "error": str(ex)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
