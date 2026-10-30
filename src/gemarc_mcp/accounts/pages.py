"""Minimal server-rendered pages (zh-CN) and the ONE function that applies the security headers (design 3.2, 3.11).

Rules enforced here:

* every account response, whatever its status (200, 3xx, 4xx, 5xx, 421, 429, 503, redirects, JSON) goes through
  :func:`respond`, which applies :func:`security_headers`;
* the CSP is ``default-src 'none'`` with a per-response nonce for the single ``<style>`` block; there is no script,
  no ``style=`` attribute, no inline event handler and no ``unsafe-inline``;
* every dynamic value is escaped with :func:`html.escape`;
* the plaintext of a new personal access token appears on the one response that creates it, nowhere else.
"""
from __future__ import annotations

import html
import json
import secrets
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from starlette.responses import Response

CSP_BASE = "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
HSTS = "max-age=31536000"
PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
HTML_TYPE = "text/html; charset=utf-8"
#: The word the user must type to confirm deletion (kept in a variable: SQL-looking words stay out of string builds).
CONFIRM_WORD = "DELETE"


def new_nonce() -> str:
    return secrets.token_urlsafe(18)


def security_headers(*, nonce: Optional[str] = None, hsts: bool = False) -> "dict[str, str]":
    """The full header set of design 3.2 (the CSP only has ``style-src`` when the response carries a styled page)."""
    csp = CSP_BASE if nonce is None else f"default-src 'none'; style-src 'nonce-{nonce}'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": PERMISSIONS,
        "Content-Security-Policy": csp,
    }
    if hsts:
        headers["Strict-Transport-Security"] = HSTS
    return headers


def respond(status: int, body: "str | bytes" = b"", *, content_type: Optional[str] = HTML_TYPE,
            nonce: Optional[str] = None, hsts: bool = False, headers: Optional[dict] = None,
            cookies: Iterable[str] = ()) -> Response:
    """Build a response with the security headers; ``cookies`` are complete ``Set-Cookie`` values."""
    merged = security_headers(nonce=nonce, hsts=hsts)
    if headers:
        merged.update(headers)
    response = Response(body, status_code=status, media_type=None, headers=merged)
    if content_type:
        response.headers["Content-Type"] = content_type
    for cookie in cookies:
        response.headers.append("Set-Cookie", cookie)
    return response


def json_response(status: int, payload: dict, *, hsts: bool = False, headers: Optional[dict] = None,
                  cookies: Iterable[str] = ()) -> Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return respond(status, body, content_type="application/json", hsts=hsts, headers=headers, cookies=cookies)


def redirect(location: str, status: int = 303, *, hsts: bool = False, cookies: Iterable[str] = ()) -> Response:
    return respond(status, b"", content_type=None, hsts=hsts, headers={"Location": location}, cookies=cookies)


def unavailable(code: str = "GEMARC_ACCT_001", *, hsts: bool = False, retry_after: Optional[int] = None) -> Response:
    """Plain 503 for the account routes when the feature is misconfigured (no secrets, no variable names)."""
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return json_response(503, {"error": "accounts_unavailable", "code": code}, hsts=hsts, headers=headers)


# -- HTML -----------------------------------------------------------------------------------------------------------
esc = html.escape

CSS = """
:root{color-scheme:light dark;--bg:#fafafa;--fg:#1c1c1e;--mute:#5f6368;--line:#d0d3d9;--card:#fff;--acc:#1a56db;--bad:#b3261e;--ok:#146c2e}
@media (prefers-color-scheme:dark){:root{--bg:#121316;--fg:#ececf0;--mute:#a0a4ad;--line:#33363d;--card:#1b1d22;--acc:#8ab4ff;--bad:#ff8a80;--ok:#7fd99a}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:46rem;margin:0 auto;padding:1.5rem 16px 3rem}
h1{font-size:1.4rem;margin:.2rem 0 1rem}h2{font-size:1.1rem;margin:2rem 0 .6rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem 1.1rem;margin:1rem 0}
.mute{color:var(--mute);font-size:.9rem}.bad{color:var(--bad)}.ok{color:var(--ok)}
a{color:var(--acc)}
.btn,button{display:inline-block;background:var(--acc);color:#fff;border:0;border-radius:8px;padding:.5rem 1rem;font:inherit;cursor:pointer;text-decoration:none}
button.danger{background:var(--bad)}button.plain{background:transparent;color:var(--acc);border:1px solid var(--line)}
input,select{font:inherit;padding:.4rem .5rem;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);max-width:100%}
label{display:block;margin:.5rem 0 .2rem}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{border-bottom:1px solid var(--line);padding:.35rem .4rem;text-align:left;vertical-align:top}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:.7rem;overflow-x:auto;white-space:pre-wrap;word-break:break-all;user-select:all}
.tok{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.95rem}
form{margin:.4rem 0}.row{display:flex;flex-wrap:wrap;gap:.6rem;align-items:end}
"""


def page(title: str, body: str, nonce: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>{esc(title)}</title><style nonce=\"{esc(nonce)}\">{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>\n"
    )


@dataclass(frozen=True)
class View:
    """What the pages need to know about the deployment (nothing secret)."""

    base_url: str
    privacy_url: Optional[str] = None
    write_scope: bool = False  # the toolset has write tools AND the operator allows write: show the scope choice
    default_days: int = 30
    max_days: int = 90
    secure: bool = True


def _fmt_time(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(value)))


def privacy_notice(view: View) -> str:
    link = ""
    if view.privacy_url:
        link = f" 运营者的隐私声明：<a href=\"{esc(view.privacy_url)}\">查看</a>。"
    return (
        "<div class=\"card\"><h2>隐私告知</h2>"
        "<p>登录用 Google 账号（只请求 <code>openid</code> 与 <code>email</code> 两项，不请求姓名、头像）。</p>"
        "<ul><li>存什么：你的邮箱与 Google 账号标识，字段加密后存入数据库；不存密码，也不存 Google 的任何令牌。</li>"
        "<li>为什么存：识别你的账号、签发和撤销个人访问令牌、留下最小的安全审计记录。</li>"
        "<li>存多久：账号存在期间保存；审计记录按运营者设定的天数清理；删除账号后数据库中的账号数据一并删除"
        "（数据库服务商的备份可能在其保留窗口内仍含旧数据）。</li>"
        "<li>你的权利：登录后可随时导出全部数据或删除账号。</li></ul>"
        f"<p class=\"mute\">{link}</p></div>"
    )


def signed_out_page(view: View, nonce: str, *, notice: Optional[str] = None) -> str:
    banner = f"<p class=\"bad\">{esc(notice)}</p>" if notice else ""
    body = (
        "<h1>GemArc 账号</h1>" + banner + privacy_notice(view)
        + "<p><a class=\"btn\" href=\"/auth/google/start\">使用 Google 登录</a></p>"
        "<p class=\"mute\">首次成功登录即完成注册。登录后可创建个人访问令牌，供 Claude Code、Codex、Hermes 调用 MCP。</p>"
    )
    return page("GemArc 账号", body, nonce)


def error_page(nonce: str, code: str, message: str, *, email: Optional[str] = None, relogin: bool = True,
               title: str = "出错了") -> str:
    shown = ""
    if email:
        shown = (f"<p>你登录的邮箱是 <strong>{esc(email)}</strong>。如果你确定应当被允许，"
                 "请把这个地址发给运营者。</p>")
    link = "<p><a class=\"btn\" href=\"/account\">返回账号页</a></p>"
    if relogin:
        link = "<p><a class=\"btn\" href=\"/auth/google/start\">使用 Google 登录</a> <a href=\"/account\">返回账号页</a></p>"
    body = (f"<h1>{esc(title)}</h1><div class=\"card\"><p class=\"bad\">{esc(message)}</p>"
            f"<p class=\"mute\">错误编号：{esc(code)}</p></div>{shown}{link}")
    return page(f"{title} {code}", body, nonce)


def deleted_page(nonce: str) -> str:
    return page("账号已删除", "<h1>账号已删除</h1><div class=\"card\"><p>你的账号、会话与令牌已从数据库删除。"
                "审计记录已匿名化。数据库服务商的备份在其保留窗口内可能仍含旧数据。</p>"
                "<p class=\"mute\">你也可以在自己的 Google 账号设置里移除对本应用的授权。</p></div>"
                "<p><a href=\"/account\">返回</a></p>", nonce)


def _csrf(csrf: str) -> str:
    return f"<input type=\"hidden\" name=\"csrf\" value=\"{esc(csrf)}\">"


def snippets(view: View, token: str) -> str:
    """Exact client configuration for a freshly created token (token only inside the one-time box)."""
    url = esc(view.base_url + "/mcp")
    env_ps = esc(f"$env:GEMARC_MCP_PAT = '{token}'")
    env_sh = esc(f"export GEMARC_MCP_PAT='{token}'")
    claude = esc(f"claude mcp add --transport http --scope user gemarc '{view.base_url}/mcp' "
                 "--header 'Authorization: Bearer ${GEMARC_MCP_PAT}'")
    codex = esc(f"[mcp_servers.gemarc]\nurl = \"{view.base_url}/mcp\"\nbearer_token_env_var = \"GEMARC_MCP_PAT\"\n")
    hermes = esc(f"mcp_servers:\n  gemarc:\n    url: \"{view.base_url}/mcp\"\n    headers:\n"
                 "      Authorization: \"Bearer ${GEMARC_MCP_PAT}\"\n")
    return (
        "<h2>怎么用这个令牌</h2><p class=\"mute\">先把令牌放进环境变量 <code>GEMARC_MCP_PAT</code>，"
        "配置文件里只写变量名，不写令牌本身。</p>"
        f"<p>Windows PowerShell：</p><pre>{env_ps}</pre><p>macOS / Linux：</p><pre>{env_sh}</pre>"
        f"<p>Claude Code：</p><pre>{claude}</pre>"
        f"<p>Codex（<code>~/.codex/config.toml</code>）：</p><pre>{codex}</pre>"
        f"<p>Hermes（<code>~/.hermes/config.yaml</code>）：</p><pre>{hermes}</pre>"
        f"<p class=\"mute\">服务地址：<code>{url}</code>。claude.ai 的自定义连接器不能使用这种令牌。</p>"
    )


def account_page(view: View, nonce: str, *, email: str, csrf: str, tokens: Sequence, now: int, recent: bool,
                 created: Optional[dict] = None, message: Optional[str] = None) -> str:
    """The signed-in page. ``tokens`` are TokenMeta-like objects; ``created`` is ``{"token": ..., "id": ...}``."""
    parts = ["<h1>GemArc 账号</h1>"]
    parts.append(f"<p>已登录：<strong>{esc(email)}</strong></p>")
    if message:
        parts.append(f"<p class=\"ok\">{esc(message)}</p>")
    if not recent:
        parts.append("<p class=\"bad\">为了安全，创建令牌、全部撤销、导出和删除需要在 15 分钟内登录过。"
                     "<a href=\"/auth/google/start\">请重新登录以确认</a>。</p>")
    if created:
        parts.append(
            "<div class=\"card\"><h2>你的新令牌（只显示这一次）</h2>"
            "<p>请现在复制并妥善保存。离开此页后无法再次查看；丢失了就撤销后重建。</p>"
            f"<pre class=\"tok\">{esc(created['token'])}</pre>"
            f"{snippets(view, created['token'])}</div>"
        )
    rows = []
    for t in tokens:
        state = t.state(now)
        label = {"active": "有效", "expired": "已过期", "revoked": "已撤销"}[state]
        revoke = ""
        if state == "active":
            revoke = (f"<form method=\"post\" action=\"/account/tokens/{esc(t.id)}/revoke\">{_csrf(csrf)}"
                      "<button class=\"plain\" type=\"submit\">撤销</button></form>")
        scope = "" if not view.write_scope else f"<td>{esc(t.scope)}</td>"
        rows.append(f"<tr><td>{esc(t.name)}<br><span class=\"mute tok\">{esc(t.id)}</span></td>{scope}"
                    f"<td>{label}</td><td>{_fmt_time(t.expires_at)}</td><td>{_fmt_time(t.last_used_at)}</td><td>{revoke}</td></tr>")
    scope_head = "<th>范围</th>" if view.write_scope else ""
    table = ("<p class=\"mute\">还没有令牌。</p>" if not rows else
             f"<table><tr><th>名称</th>{scope_head}<th>状态</th><th>到期</th><th>最近使用</th><th></th></tr>{''.join(rows)}</table>")
    scope_field = ""
    if view.write_scope:
        scope_field = ("<div><label for=\"scope\">范围</label><select id=\"scope\" name=\"scope\">"
                       "<option value=\"read\">read（只读）</option><option value=\"write\">write（需运营者授权）</option></select></div>")
    parts.append(
        "<h2>个人访问令牌</h2>" + table +
        "<form method=\"post\" action=\"/account/tokens\" class=\"card\">" + _csrf(csrf) +
        "<div class=\"row\"><div><label for=\"name\">名称（至多 40 字）</label>"
        "<input id=\"name\" name=\"name\" maxlength=\"40\" required></div>"
        f"<div><label for=\"days\">有效天数（至多 {int(view.max_days)}）</label>"
        f"<input id=\"days\" name=\"days\" type=\"number\" min=\"1\" max=\"{int(view.max_days)}\" value=\"{int(view.default_days)}\"></div>"
        + scope_field +
        "<div><button type=\"submit\">创建令牌</button></div></div></form>"
        "<form method=\"post\" action=\"/account/tokens/revoke-all\">" + _csrf(csrf) +
        "<button class=\"plain\" type=\"submit\">撤销全部令牌</button></form>"
    )
    parts.append(
        "<h2>会话与数据</h2>"
        "<form method=\"post\" action=\"/auth/logout\">" + _csrf(csrf) + "<button class=\"plain\" type=\"submit\">登出</button></form>"
        "<form method=\"post\" action=\"/auth/logout-all\">" + _csrf(csrf) + "<button class=\"plain\" type=\"submit\">在所有设备登出</button></form>"
        "<form method=\"post\" action=\"/account/export\">" + _csrf(csrf) + "<button class=\"plain\" type=\"submit\">导出我的数据（JSON）</button></form>"
    )
    parts.append(
        "<h2>删除账号</h2><form method=\"post\" action=\"/account/delete\" class=\"card\">" + _csrf(csrf) +
        f"<p>这会永久删除账号 <strong>{esc(email)}</strong>、会话与令牌，且无法撤销。</p>"
        f"<label for=\"confirm\">请输入 {CONFIRM_WORD} 确认</label><input id=\"confirm\" name=\"confirm\" autocomplete=\"off\" required> "
        "<button class=\"danger\" type=\"submit\">永久删除</button></form>"
    )
    if view.privacy_url:
        parts.append(f"<p class=\"mute\"><a href=\"{esc(view.privacy_url)}\">隐私声明</a></p>")
    return page("GemArc 账号", "".join(parts), nonce)
