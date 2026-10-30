"""Model-in / result-out services that need no workspace and keep no state.

A remote deployment (Vercel) has no durable filesystem and no caller identity,
so every tool that reads or writes a *project* is useless there. The functions
in this module take the architecture model as an argument and return
everything as data, so they behave identically on a laptop over stdio and on a
serverless function over HTTP.

The fixed part of a site (shell + shared runtime, ~620 KB) is never put into a
tool response: no MCP client accepts a result that large. It is described by
``site_kit`` and served as one deterministic archive; only the few kilobytes
that depend on the model are returned inline by ``render``.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path

from .model import normalized_model, validate
from .paths import GemArcError, digest_bytes, encode_json
from .simulation import compare, simulate

#: Profiles that produce a standalone site (``mvp`` is the tiny local viewer).
SITE_PROFILES = ("static", "vite")

#: Largest ``render`` payload returned inline. Codex shows the model at most
#: ~12k tokens (10k x 1.2) and Claude Code warns above 10k; JSON runs about
#: 3 bytes per token, so keep the derived files under ~8k tokens.
RENDER_INLINE_LIMIT = 24 * 1024

#: Warnings that ``render`` echoes back. The data files are held to RENDER_INLINE_LIMIT, but one warning per
#: evidence-less node or edge would otherwise grow the rest of the result without bound.
RENDER_MAX_WARNINGS = 20

KIT_PATH_TEMPLATE = "/kit/{profile}.zip"
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def inline_model(value) -> dict:
    """Accept a model supplied as a tool argument."""
    if not isinstance(value, dict) or not value:
        raise GemArcError("GEMARC_INPUT_003", "model 必须是非空的 JSON 对象")
    try:
        encode_json(value)  # enforces the 4 MiB limit (GEMARC_MODEL_002)
    except ValueError as ex:  # NaN / Infinity: not JSON, and they would corrupt every derived data/*.json
        raise GemArcError("GEMARC_INPUT_004", "model 含 NaN 或 Infinity，JSON 不允许这样的数字") from ex
    return value


def model_digest(value: dict) -> str:
    """Digest of the canonical encoding; stable for equal models."""
    return digest_bytes(encode_json(value))


def precompute_scenarios(model: dict) -> dict:
    """Per-scenario baseline plus a single-module-disabled sample run.

    The model must already have passed ``validate`` (render and build both check first), so the runs skip the
    per-run validation, each baseline is computed once, and each run sees a shallow one-scenario view so that
    its set-up cost does not grow with the number of scenarios (a remote caller chooses that number).
    """
    scenarios, nodes = model.get("scenarios"), model.get("nodes")
    first_node = nodes[0]["id"] if isinstance(nodes, list) and nodes else None
    precomputed = {}
    for scenario in scenarios if isinstance(scenarios, list) else []:
        view = {**model, "scenarios": [scenario]}
        normal = simulate(view, scenario["id"], validated=True)
        precomputed[scenario["id"]] = {
            "normal": normal,
            "disabledSample": (compare(view, scenario["id"], [first_node], validated=True, baseline=normal)
                               if first_node is not None else None),
        }
    return precomputed


def site_capabilities(model: dict, profile: str, has_3d: bool = False) -> dict:
    return {
        "viewer2D": "implemented",
        "viewer3D": "supplied_compiled_legacy_viewer" if has_3d else "not_available",
        "dynamic4D": "model_driven_mvp" if model.get("scenarios") else "no_data",
        "causal5D": "explicit_rule_mvp" if model.get("scenarios") else "no_data",
        "prototype": "basic_tabs",
        "evidenceState": model.get("meta", {}).get("evidenceState", "unknown"),
        "profile": profile,
    }


def new_model(system: str) -> dict:
    """The empty canonical model with every slot present and unfilled."""
    from .scaffold import builder

    name = (system or "").strip()
    if not name or len(name) > 80:
        raise GemArcError("GEMARC_INPUT_001", "system 必填，且不超过 80 个字符")
    return {
        "status": "ok",
        "model": builder.model_template(name),
        "slots": [{"id": s["id"], "pointer": s["pointer"], "what": s["what"],
                   "required": s["required"], "rule": s["rule"]} for s in builder.SLOTS],
        "next": ["gemarc_brief 取得规则与词表", "按槽位填写 model（事实必须指回证据，缺失标 Unknown）",
                 "gemarc_validate(model=…) 直到 0 个 error（要宣称“已确认”时再加 strict=true；strict 下 Unknown 必然不过，不要为此改标签）",
                 "gemarc_simulate / gemarc_render"],
    }


def _capped_validation(result: dict) -> dict:
    """The validation result with its warning list capped (``summary.warnings`` still holds the total)."""
    warnings = result.get("warnings")
    if not isinstance(warnings, list) or len(warnings) <= RENDER_MAX_WARNINGS:
        return result
    return {**result, "warnings": warnings[:RENDER_MAX_WARNINGS],
            "warningsOmitted": len(warnings) - RENDER_MAX_WARNINGS,
            "notice": f"只列出前 {RENDER_MAX_WARNINGS} 条 warning（summary.warnings 是总数）；完整列表用 gemarc_validate 取得。"}


def render(model: dict, profile: str = "static") -> dict:
    """Validate a model and return the files that depend on it.

    The result plus the site kit equals what ``gemarc_build --profile <p>``
    writes, byte for byte (asserted by ``tests/test_stateless.py``).
    """
    if profile not in SITE_PROFILES:
        raise GemArcError("GEMARC_BUILD_005", f"未知的站点 profile: {profile}；可选 {list(SITE_PROFILES)}")
    model = inline_model(model)
    result = validate(model)
    if result["status"] != "ok":
        return result
    artefacts = {
        "data/architecture.json": normalized_model(model),
        "data/scenarios.json": precompute_scenarios(model),
        "data/capabilities.json": site_capabilities(model, profile),
    }
    encoded = {path: encode_json(value) for path, value in artefacts.items()}
    total = sum(len(raw) for raw in encoded.values())
    inline = total <= RENDER_INLINE_LIMIT
    files = []
    for path, raw in encoded.items():
        entry = {"path": path, "bytes": len(raw), "sha256": digest_bytes(raw)}
        if inline:
            entry["content"] = raw.decode("utf-8")
        files.append(entry)
    kit = site_kit(profile)
    payload = {
        "status": "ok",
        "profile": profile,
        "validation": _capped_validation(result),
        "inline": inline,
        "files": files,
        "kit": kit,
        "assemble": (f"把 kit 解压到站点目录，再把 files 里的每个文件按 path 写入同一目录；"
                     f"然后 {kit['run']}"),
    }
    if not inline:
        payload["notice"] = (f"派生文件合计 {total} 字节，超过内联上限 {RENDER_INLINE_LIMIT}；"
                             "此处只给出清单与摘要。请在本地运行 `gemarc build <project> --profile "
                             f"{profile}` 取得完整产物。")
    return payload


# --- site kit -----------------------------------------------------------------

@lru_cache(maxsize=None)
def _kit_entries(profile: str) -> tuple[tuple[str, bytes], ...]:
    """Every file of a site that does not depend on the model, sorted by path."""
    from .scaffold import builder

    if profile not in SITE_PROFILES:
        raise GemArcError("GEMARC_BUILD_005", f"未知的站点 profile: {profile}；可选 {list(SITE_PROFILES)}")
    with tempfile.TemporaryDirectory(prefix="gemarc-kit-") as tmp:
        target = Path(tmp)
        builder.build_site(target, builder.model_template("kit"), profile)
        entries = []
        for path in sorted(target.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(target).as_posix()
            if relative.startswith("data/"):
                continue
            entries.append((relative, path.read_bytes()))
    return tuple(entries)


@lru_cache(maxsize=None)
def site_kit_zip(profile: str) -> bytes:
    """Deterministic archive: same bytes on every instance and every call."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative, raw in _kit_entries(profile):
            info = zipfile.ZipInfo(relative, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3  # the default depends on the host OS; the archive bytes must not
            archive.writestr(info, raw)
    return buffer.getvalue()


def public_base_url() -> str:
    """Where this deployment is reachable, when the operator told us."""
    configured = os.environ.get("GEMARC_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    # VERCEL_PROJECT_PRODUCTION_URL is set on preview deployments too and names the PRODUCTION domain: a preview
    # would advertise another deployment's kit next to its own archiveSha256. Only a production deployment uses it.
    if os.environ.get("VERCEL_ENV", "production") != "production":
        return ""
    vercel_host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
    return f"https://{vercel_host}" if vercel_host else ""


def site_kit(profile: str) -> dict:
    """Manifest of the fixed part of a site and where to fetch it."""
    from .scaffold import builder

    archive = site_kit_zip(profile)
    path = KIT_PATH_TEMPLATE.format(profile=profile)
    base = public_base_url()
    return {
        "profile": profile,
        "fileCount": len(_kit_entries(profile)),
        "bytes": sum(len(raw) for _, raw in _kit_entries(profile)),
        "archiveBytes": len(archive),
        "archiveSha256": hashlib.sha256(archive).hexdigest(),
        "path": path,
        "url": f"{base}{path}" if base else None,
        "run": builder._commands(profile)["dev"],
        "note": "kit 只含固定件（站点外壳 + 共用运行时 + 词表）；与 render 返回的 data/*.json 合并即是完整站点。",
    }


def kit_listing(profile: str) -> list[dict]:
    return [{"path": relative, "bytes": len(raw), "sha256": digest_bytes(raw)}
            for relative, raw in _kit_entries(profile)]
