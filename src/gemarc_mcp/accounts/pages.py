"""Minimal server-rendered pages (three languages) and the ONE function that applies the security headers.

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

from .. import i18n

CSP_BASE = "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
HSTS = "max-age=31536000"
PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
HTML_TYPE = "text/html; charset=utf-8"
#: The word the user must type to confirm deletion (kept in a variable: SQL-looking words stay out of string builds).
CONFIRM_WORD = "DELETE"
#: The language of a page when nothing else says which one to use.
DEFAULT_LANG = i18n.DEFAULT_LANG


def t(lang: str, key: str, **fmt) -> str:
    """One string of the account pages in one language."""
    return i18n.text(lang or DEFAULT_LANG, "accounts", key, **fmt)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def new_nonce() -> str:
    return secrets.token_urlsafe(18)


def security_headers(*, nonce: Optional[str] = None, hsts: bool = False) -> "dict[str, str]":
    """The full header set of design 3.2 (the CSP only has ``style-src`` when the response carries a styled page)."""
    csp = CSP_BASE if nonce is None else (
        f"default-src 'none'; style-src 'nonce-{nonce}'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
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


CSS = """
/* The gem theme of BD-010 (UI style policy). The account page and the operator
   area are the same product as the site and the console: one palette, the
   tokens of section 3. The backend stands on the same sheet of washi as the
   site (section 12.2) - a warm paper, an ink line on every edge, one hard
   offset shadow, and no translucent shell and no halo. The theme is light and it does not
   follow the system preference: a second dark palette would be a second set
   of near-duplicate colours. */
:root {
  color-scheme:light;
  /* The paper and the ink (section 3.1). */
  --gem-void:#d6c9ae;--gem-bg:#ebe1ce;--gem-panel:#f4eddf;--gem-facet:#e6dac6;--gem-rim:#a89a7c;
  --gem-ink:#2e2a24;--gem-muted:#6e6354;
  /* The pigments (section 3.2). */
  --gem-ai:#3d5a72;--gem-fuji:#5f5573;--gem-kaki:#96501f;--gem-koke:#56663a;--gem-sabi:#8a3a2c}
body{margin:0;min-height:100vh;color:var(--gem-ink);font:16px/1.65 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
background-color:var(--gem-bg);
background-image:repeating-linear-gradient(45deg,color-mix(in srgb,var(--gem-rim) 12%,transparent) 0 1px,transparent 1px 15px),
repeating-linear-gradient(-45deg,color-mix(in srgb,var(--gem-rim) 8%,transparent) 0 1px,transparent 1px 23px);
background-attachment:fixed}
main{max-width:46rem;margin:0 auto;padding:2.2rem 16px 3.4rem}
h1{font-size:1.45rem;margin:.2rem 0 1rem;letter-spacing:-.01em}
/* The brand mark: a crystal, and only in the title (section 12.3). */
h1::before{content:"◆";color:var(--gem-ai);font-size:.72em;margin-right:.5rem;vertical-align:.1em}
h2{font-size:1.08rem;margin:2.2rem 0 .7rem;color:var(--gem-ink)}
ul{margin:.5rem 0 .3rem;padding-left:1.15rem}
li{margin:.3rem 0}
.card{background:var(--gem-panel);border:1px solid var(--gem-rim);
border-radius:8px;padding:1.1rem 1.2rem;margin:1rem 0;
box-shadow:4px 4px 0 var(--gem-void)}
.mute{color:var(--gem-muted);font-size:.9rem}.bad{color:var(--gem-sabi)}.ok{color:var(--gem-koke)}
a{color:var(--gem-ai)}
.btn,button{display:inline-block;background:var(--gem-panel);color:var(--gem-ink);
border:1px solid var(--gem-muted);border-radius:6px;padding:.5rem 1rem;font:inherit;cursor:pointer;text-decoration:none;
box-shadow:3px 3px 0 var(--gem-void);transition:border-color .12s ease,color .12s ease}
.btn:hover,button:hover{border-color:var(--gem-ai);color:var(--gem-ai)}
.btn:active,button:active{background:var(--gem-facet)}
button:focus-visible,.btn:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--gem-ai);outline-offset:1px}
button.danger{border-color:var(--gem-sabi);color:var(--gem-sabi)}button.danger:hover{background:var(--gem-facet)}
button.plain{color:var(--gem-ai)}button.plain:hover{color:var(--gem-ai)}
input,select{font:inherit;padding:.45rem .6rem;border:1px solid var(--gem-muted);
border-radius:6px;background:var(--gem-panel);color:var(--gem-ink);max-width:100%}
label{display:block;margin:.6rem 0 .25rem;color:var(--gem-ink)}
table{border-collapse:collapse;width:100%;font-size:.9rem;background:var(--gem-panel);border-radius:8px;overflow:hidden}
th,td{border-bottom:1px solid var(--gem-rim);padding:.45rem .55rem;text-align:left;vertical-align:top}
th{color:var(--gem-muted);font-weight:600}
pre{background:var(--gem-panel);border:1px solid var(--gem-muted);border-radius:8px;padding:.8rem;
overflow-x:auto;white-space:pre-wrap;word-break:break-all;user-select:all}
.tok{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.95rem}
form{margin:.4rem 0}.row{display:flex;flex-wrap:wrap;gap:.6rem;align-items:end}
/* The language switcher: three links, each written in its own language
   (section 12.7). The pages have no script, so a link is the only control. */
.langbar{display:flex;align-items:baseline;gap:.55rem;margin:0 0 1.4rem;font-size:.85rem}
.langbar a{text-decoration:none;padding:.1rem .35rem;border-radius:4px}
.langbar a:hover{background:var(--gem-facet)}
.langbar a.on{color:var(--gem-ink);border:1px solid var(--gem-ai)}
"""


def page(title: str, body: str, nonce: str, lang: str = "") -> str:
    language = lang or DEFAULT_LANG
    return (
        "<!doctype html>\n"
        f"<html lang=\"{esc(language)}\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>{esc(title)}</title><style nonce=\"{esc(nonce)}\">{CSS}</style></head>"
        f"<body><main>{language_switcher(language)}{body}</main></body></html>\n"
    )


def language_switcher(lang: str) -> str:
    """The language switcher: three links, because the pages have no script.

    Every option is written in its own language, so the switcher reads the same
    in every language (BD-010 section 12.7).
    """
    parts = [f"<p class=\"langbar\"><span class=\"mute\">{esc(t(lang, 'lang.label'))}</span>"]
    for code in i18n.LANGUAGES:
        label = {"zh-CN": "lang.zh", "ja-JP": "lang.ja", "en-US": "lang.en"}[code]
        current = " class=\"on\"" if code == lang else ""
        parts.append(f"<a{current} href=\"?lang={code}\" hreflang=\"{code}\">{esc(t(lang, label))}</a>")
    parts.append("</p>")
    return "".join(parts)


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


def privacy_notice(view: View, lang: str) -> str:
    link = ""
    if view.privacy_url:
        link = (f" {t(lang, 'privacy.operator')}<a href=\"{esc(view.privacy_url)}\">{esc(t(lang, 'privacy.view'))}</a>"
                + esc(t(lang, "common.period")))
    return (
        "<div class=\"card\"><h2>" + esc(t(lang, "privacy.h2")) + "</h2>"
        "<p>" + esc(t(lang, "privacy.login")) + "</p>"
        "<ul><li>" + esc(t(lang, "privacy.what")) + "</li>"
        "<li>" + esc(t(lang, "privacy.why")) + "</li>"
        "<li>" + esc(t(lang, "privacy.howlong")) + "</li>"
        "<li>" + esc(t(lang, "privacy.rights")) + "</li></ul>"
        f"<p class=\"mute\">{link}</p></div>"
    )


def signed_out_page(view: View, nonce: str, lang: str = "", notice: Optional[str] = None) -> str:
    banner = f"<p class=\"bad\">{esc(notice)}</p>" if notice else ""
    body = (
        "<h1>" + esc(t(lang, "title.account")) + "</h1>" + banner + privacy_notice(view, lang)
        + "<p><a class=\"btn\" href=\"/auth/google/start\">" + esc(t(lang, "signedout.google")) + "</a></p>"
        "<p class=\"mute\">" + esc(t(lang, "signedout.note")) + "</p>"
    )
    return page(t(lang, "title.account"), body, nonce, lang)


def error_page(nonce: str, code: str, message: str, *, lang: str = "", email: Optional[str] = None,
               relogin: bool = True, title: str = "") -> str:
    shown = ""
    if email:
        shown = "<p>" + esc(t(lang, "error.email", email=email)) + "</p>"
    heading = title or t(lang, "error.title")
    link = "<p><a class=\"btn\" href=\"/account\">" + esc(t(lang, "error.back")) + "</a></p>"
    if relogin:
        link = ("<p><a class=\"btn\" href=\"/auth/google/start\">" + esc(t(lang, "error.relogin"))
                + "</a> <a href=\"/account\">" + esc(t(lang, "error.back")) + "</a></p>")
    body = ("<h1>" + esc(heading) + "</h1><div class=\"card\"><p class=\"bad\">" + esc(message) + "</p>"
            "<p class=\"mute\">" + esc(t(lang, "error.code", code=code)) + "</p></div>" + shown + link)
    return page(f"{heading} {code}", body, nonce, lang)


def deleted_page(nonce: str, lang: str = "") -> str:
    body = ("<h1>" + esc(t(lang, "deleted.title")) + "</h1><div class=\"card\"><p>"
            + esc(t(lang, "deleted.body")) + "</p>"
            "<p class=\"mute\">" + esc(t(lang, "deleted.revoke")) + "</p></div>"
            "<p><a href=\"/account\">" + esc(t(lang, "deleted.back")) + "</a></p>")
    return page(t(lang, "deleted.title"), body, nonce, lang)


def oauth_consent(nonce: str, *, lang: str = "", client_name: str, scope: str, resource: str,
                   redirect_uri: str, state: str, code_challenge: str, client_id: str, csrf: str,
                   expires_in: int, refresh_in: int) -> str:
    """The consent page of the OAuth authorisation (design 3.16.3).

    Two buttons and nothing else: the reader either allows this client or denies it. The request
    travels in hidden fields, so the page cannot be turned into a different request by hand.
    """
    hidden = (
        f"<input type=\"hidden\" name=\"csrf\" value=\"{esc(csrf)}\">"
        f"<input type=\"hidden\" name=\"response_type\" value=\"code\">"
        f"<input type=\"hidden\" name=\"client_id\" value=\"{esc(client_id)}\">"
        f"<input type=\"hidden\" name=\"redirect_uri\" value=\"{esc(redirect_uri)}\">"
        f"<input type=\"hidden\" name=\"scope\" value=\"{esc(scope)}\">"
        f"<input type=\"hidden\" name=\"state\" value=\"{esc(state)}\">"
        f"<input type=\"hidden\" name=\"code_challenge\" value=\"{esc(code_challenge)}\">"
        f"<input type=\"hidden\" name=\"code_challenge_method\" value=\"S256\">"
        f"<input type=\"hidden\" name=\"resource\" value=\"{esc(resource)}\">"
    )
    body = (
        "<h1>" + esc(t(lang, "oauth.consent.title")) + "</h1>"
        "<div class=\"card\">"
        "<p>" + esc(t(lang, "oauth.consent.intro")) + "</p>"
        "<ul>"
        "<li><b>" + esc(t(lang, "oauth.consent.client")) + "</b>: " + esc(client_name) + "</li>"
        "<li><b>" + esc(t(lang, "oauth.consent.scope")) + "</b>: " + esc(scope) + "</li>"
        "<li><b>" + esc(t(lang, "oauth.consent.audience")) + "</b>: " + esc(resource) + "</li>"
        "<li><b>" + esc(t(lang, "oauth.consent.lifetime")) + "</b>: "
        + esc(t(lang, "oauth.consent.access")) + " " + str(int(expires_in) // 60) + " "
        + esc(t(lang, "oauth.consent.minutes")) + "; " + esc(t(lang, "oauth.consent.refresh")) + " "
        + str(int(refresh_in) // 86400) + " " + esc(t(lang, "oauth.consent.days")) + "</li>"
        "</ul>"
        "<p class=\"mute\">" + esc(t(lang, "oauth.consent.redirect")) + ": " + esc(redirect_uri) + "</p>"
        "</div>"
        "<form method=\"post\" action=\"/authorize\">" + hidden
        + "<button class=\"btn\" type=\"submit\" name=\"decision\" value=\"allow\">"
        + esc(t(lang, "oauth.consent.allow")) + "</button> "
        + "<button class=\"btn\" type=\"submit\" name=\"decision\" value=\"deny\">"
        + esc(t(lang, "oauth.consent.deny")) + "</button>"
        "</form>"
    )
    return page(t(lang, "oauth.consent.title"), body, nonce, lang)


def _csrf(csrf: str) -> str:
    return f"<input type=\"hidden\" name=\"csrf\" value=\"{esc(csrf)}\">"


def snippets(view: View, token: str, lang: str) -> str:
    """The client configuration of a freshly created token.

    The token only lives inside the one-time box of the page that created it.
    The three blocks carry the token already, so they can be copied into the
    agent as they are; the environment-variable way is the alternative for a
    reader who does not want a credential in a file (OPS-002 section 3.15).
    """
    url = esc(view.base_url + "/mcp")
    env_ps = esc(f"$env:GEMARC_MCP_PAT = '{token}'")
    env_sh = esc(f"export GEMARC_MCP_PAT='{token}'")
    claude = esc(f"claude mcp add --transport http --scope user gemarc '{view.base_url}/mcp' "
                 f"--header 'Authorization: Bearer {token}'")
    codex = esc(f"[mcp_servers.gemarc]\nurl = \"{view.base_url}/mcp\"\nbearer_token_env_var = \"GEMARC_MCP_PAT\"\n")
    hermes = esc(f"mcp_servers:\n  gemarc:\n    url: \"{view.base_url}/mcp\"\n    headers:\n"
                 f"      Authorization: \"Bearer {token}\"\n")
    return (
        "<h2>" + esc(t(lang, "account.snipfilledh2")) + "</h2>"
        "<p class=\"mute\">" + esc(t(lang, "account.snipfillednote")) + "</p>"
        "<p>" + esc(t(lang, "account.snipclaude")) + "</p><pre>" + claude + "</pre>"
        "<p>" + esc(t(lang, "account.snipcodex")) + "</p><pre>" + codex + "</pre>"
        "<p>" + esc(t(lang, "account.sniphermes")) + "</p><pre>" + hermes + "</pre>"
        "<h2>" + esc(t(lang, "account.sniph2")) + "</h2>"
        "<p class=\"mute\">" + esc(t(lang, "account.snipintro")) + "</p>"
        "<p>" + esc(t(lang, "account.snipps")) + "</p><pre>" + env_ps + "</pre>"
        "<p>" + esc(t(lang, "account.snipsh")) + "</p><pre>" + env_sh + "</pre>"
        "<p class=\"mute\">" + esc(t(lang, "account.snipservice", url=url)) + "</p>"
    )


def _settings_form(csrf: str, lang: str, settings, tools: Sequence[str]) -> str:
    """The settings of the account as a plain form: it works without any script (DD-010).

    ``settings`` is the stored settings (or None for an account that never chose anything) and
    ``tools`` are the tools the server really registers. The form posts to ``/account/settings``.
    """
    current_lang = getattr(settings, "lang", "") or ""
    current_profile = getattr(settings, "render_profile", "") or ""
    kept = set(getattr(settings, "tools", ()) or ())
    mode = "some" if kept else "all"

    def option(value: str, label: str, selected: bool) -> str:
        mark = " selected" if selected else ""
        return "<option value=\"" + esc(value) + "\"" + mark + ">" + esc(label) + "</option>"

    # The first option is "follow the reader": the account does not pin a language. It is the one
    # marked when the account never chose one, so saving another field never pins the language of
    # the reader by accident (DD-010 section 4.1).
    lang_options = option("", t(lang, "account.settingslangdefault"), current_lang == "")
    lang_options += "".join(
        option(code, t(lang, key), code == current_lang)
        for code, key in (("zh-CN", "lang.zh"), ("ja-JP", "lang.ja"), ("en-US", "lang.en")))
    profile_options = option("", t(lang, "account.settingsprofiledefault"), current_profile == "")
    profile_options += "".join(option(code, code, code == current_profile) for code in ("mvp", "static", "vite"))

    boxes = []
    for name in tools:
        mark = " checked" if mode == "all" or name in kept else ""
        boxes.append("<label><input type=\"checkbox\" name=\"tools\" value=\"" + esc(name) + "\"" + mark + ">"
                     + " <code>" + esc(name) + "</code></label>")
    box_list = "".join(boxes) or "<p class=\"mute\">-</p>"

    def radio(value: str, label: str, checked: bool) -> str:
        mark = " checked" if checked else ""
        return ("<label><input type=\"radio\" name=\"tools_mode\" value=\"" + value + "\"" + mark + ">"
                + esc(label) + "</label>")

    return (
        "<form method=\"post\" action=\"/account/settings\" class=\"card\">" + _csrf(csrf) +
        "<div class=\"row\">"
        "<div><label for=\"set-lang\">" + esc(t(lang, "account.settingslang")) + "</label>"
        "<select id=\"set-lang\" name=\"lang\">" + lang_options + "</select></div>"
        "<div><label for=\"set-profile\">" + esc(t(lang, "account.settingsprofile")) + "</label>"
        "<select id=\"set-profile\" name=\"render_profile\">" + profile_options + "</select></div>"
        "</div>"
        "<p class=\"mute\">" + esc(t(lang, "account.settingstoolsnote")) + "</p>"
        "<div class=\"row\">"
        + radio("all", t(lang, "account.settingstoolsall"), mode == "all")
        + radio("some", t(lang, "account.settingstoolssome"), mode == "some")
        + "</div>"
        "<div class=\"checks\">" + box_list + "</div>"
        "<button type=\"submit\">" + esc(t(lang, "account.settingssave")) + "</button>"
        "</form>"
    )


def account_page(view: View, nonce: str, *, lang: str = "", email: str, csrf: str, tokens: Sequence, now: int,
                 recent: bool, created: Optional[dict] = None, message: Optional[str] = None,
                 default_days: Optional[int] = None, max_days: Optional[int] = None,
                 is_admin: bool = False, settings=None, tools: Sequence[str] = ()) -> str:
    """The signed-in page. ``tokens`` are TokenMeta-like objects; ``created`` is ``{"token": ..., "id": ...}``.

    ``default_days`` and ``max_days`` are the token policy in force; without them the deployment
    defaults from the environment are used. ``is_admin`` shows the link to the operator area.
    ``settings`` and ``tools`` are the settings of the account and the tools the server registers.
    """
    days_default = int(view.default_days if default_days is None else default_days)
    days_max = int(view.max_days if max_days is None else max_days)
    parts = ["<h1>" + esc(t(lang, "title.account")) + "</h1>"]
    parts.append("<p>" + esc(t(lang, "account.signedin", email=email)) + "</p>")
    links = "<p><a class=\"btn\" href=\"/\">" + esc(t(lang, "account.console")) + "</a>"
    if is_admin:
        links += " <a class=\"btn\" href=\"/account/admin\">" + esc(t(lang, "account.admin")) + "</a>"
    links += "</p>"
    parts.append(links)
    if message:
        parts.append(f"<p class=\"ok\">{esc(message)}</p>")
    if not recent:
        parts.append("<p class=\"bad\">" + esc(t(lang, "account.stale"))
                     + " <a href=\"/auth/google/start\">" + esc(t(lang, "account.stalelink")) + "</a>"
                     + esc(t(lang, "common.period")) + "</p>")
    if created:
        parts.append(
            "<div class=\"card\"><h2>" + esc(t(lang, "account.tokennewh2")) + "</h2>"
            "<p>" + esc(t(lang, "account.tokennewnote")) + "</p>"
            f"<pre class=\"tok\">{esc(created['token'])}</pre>"
            f"{snippets(view, created['token'], lang)}</div>")
    rows = []
    for tok in tokens:
        state = tok.state(now)
        label = {"active": "account.tokenactive", "expired": "account.tokenexpired",
                 "revoked": "account.tokenrevoked"}[state]
        revoke = ""
        if state == "active":
            revoke = (f"<form method=\"post\" action=\"/account/tokens/{esc(tok.id)}/revoke\">{_csrf(csrf)}"
                      "<button class=\"plain\" type=\"submit\">" + esc(t(lang, "account.tokenrevoke")) + "</button></form>")
        scope = "" if not view.write_scope else f"<td>{esc(tok.scope)}</td>"
        rows.append(f"<tr><td>{esc(tok.name)}<br><span class=\"mute tok\">{esc(tok.id)}</span></td>{scope}"
                    f"<td>{esc(t(lang, label))}</td><td>{_fmt_time(tok.expires_at)}</td>"
                    f"<td>{_fmt_time(tok.last_used_at)}</td><td>{revoke}</td></tr>")
    scope_head = "<th>" + esc(t(lang, "account.tokenscope")) + "</th>" if view.write_scope else ""
    table = ("<p class=\"mute\">" + esc(t(lang, "account.tokennone")) + "</p>" if not rows else
             "<table><tr><th>" + esc(t(lang, "account.tokenname")) + "</th>" + scope_head
             + "<th>" + esc(t(lang, "account.tokenstate")) + "</th>"
             + "<th>" + esc(t(lang, "account.tokenexpires")) + "</th>"
             + "<th>" + esc(t(lang, "account.tokenused")) + "</th><th></th></tr>" + "".join(rows) + "</table>")
    scope_field = ""
    if view.write_scope:
        scope_field = ("<div><label for=\"scope\">" + esc(t(lang, "account.tokenscopelabel")) + "</label>"
                       "<select id=\"scope\" name=\"scope\">"
                       "<option value=\"read\">" + esc(t(lang, "account.tokenscoperead")) + "</option>"
                       "<option value=\"write\">" + esc(t(lang, "account.tokenscopewrite")) + "</option></select></div>")
    parts.append(
        "<h2>" + esc(t(lang, "account.tokenh2")) + "</h2>" + table +
        "<form method=\"post\" action=\"/account/tokens\" class=\"card\">" + _csrf(csrf) +
        "<div class=\"row\"><div><label for=\"name\">" + esc(t(lang, "account.tokennamelabel")) + "</label>"
        "<input id=\"name\" name=\"name\" maxlength=\"40\" required></div>"
        "<div><label for=\"days\">" + esc(t(lang, "account.tokendayslabel", max=days_max)) + "</label>"
        f"<input id=\"days\" name=\"days\" type=\"number\" min=\"1\" max=\"{days_max}\" value=\"{days_default}\"></div>"
        + scope_field +
        "<div><button type=\"submit\">" + esc(t(lang, "account.tokencreate")) + "</button></div></div></form>"
        "<form method=\"post\" action=\"/account/tokens/revoke-all\">" + _csrf(csrf) +
        "<button class=\"plain\" type=\"submit\">" + esc(t(lang, "account.tokenrevokeall")) + "</button></form>"
    )
    parts.append(
        "<h2>" + esc(t(lang, "account.settingsh2")) + "</h2>" + _settings_form(csrf, lang, settings, tools)
    )
    parts.append(
        "<h2>" + esc(t(lang, "account.sessionh2")) + "</h2>"
        "<form method=\"post\" action=\"/auth/logout\">" + _csrf(csrf) + "<button class=\"plain\" type=\"submit\">"
        + esc(t(lang, "account.logout")) + "</button></form>"
        "<form method=\"post\" action=\"/auth/logout-all\">" + _csrf(csrf) + "<button class=\"plain\" type=\"submit\">"
        + esc(t(lang, "account.logoutall")) + "</button></form>"
        "<form method=\"post\" action=\"/account/export\">" + _csrf(csrf) + "<button class=\"plain\" type=\"submit\">"
        + esc(t(lang, "account.export")) + "</button></form>"
    )
    parts.append(
        "<h2>" + esc(t(lang, "account.deleteh2")) + "</h2><form method=\"post\" action=\"/account/delete\" class=\"card\">"
        + _csrf(csrf) +
        "<p>" + esc(t(lang, "account.deletewarn", email=email)) + "</p>"
        "<label for=\"confirm\">" + esc(t(lang, "account.deleteconfirm", word=CONFIRM_WORD)) + "</label>"
        "<input id=\"confirm\" name=\"confirm\" autocomplete=\"off\" required> "
        "<button class=\"danger\" type=\"submit\">" + esc(t(lang, "account.deletebutton")) + "</button></form>"
    )
    if view.privacy_url:
        parts.append("<p class=\"mute\"><a href=\"" + esc(view.privacy_url) + "\">"
                     + esc(t(lang, "privacy.link")) + "</a></p>")
    return page(t(lang, "title.account"), "".join(parts), nonce, lang)


# -- the operator area -------------------------------------------------------------------------------------------

def _admin_user_rows(users: Sequence, csrf: str, lang: str) -> str:
    """One row per user with the five actions. Every action is a POST with the CSRF token."""
    rows = []
    for u in users:
        email = u.email or t(lang, "admin.unreadable")
        admin = t(lang, "admin.yes") if u.granted else t(lang, "admin.no")
        if u.status != "active":
            admin = admin + " · " + t(lang, "admin.disabledmark")
        buttons = []
        if u.status == "active":
            buttons.append(f"<form method=\"post\" action=\"/account/admin/users/{esc(u.id)}/disable\">"
                           f"{_csrf(csrf)}<button class=\"danger\" type=\"submit\">{esc(t(lang, 'admin.disable'))}</button></form>")
        else:
            buttons.append(f"<form method=\"post\" action=\"/account/admin/users/{esc(u.id)}/enable\">"
                           f"{_csrf(csrf)}<button type=\"submit\">{esc(t(lang, 'admin.enable'))}</button></form>")
        if u.granted:
            buttons.append(f"<form method=\"post\" action=\"/account/admin/users/{esc(u.id)}/revoke-admin\">"
                           f"{_csrf(csrf)}<button class=\"plain\" type=\"submit\">{esc(t(lang, 'admin.revokeadmin'))}</button></form>")
        else:
            buttons.append(f"<form method=\"post\" action=\"/account/admin/users/{esc(u.id)}/grant-admin\">"
                           f"{_csrf(csrf)}<button type=\"submit\">{esc(t(lang, 'admin.grantadmin'))}</button></form>")
        buttons.append(f"<form method=\"post\" action=\"/account/admin/users/{esc(u.id)}/revoke-tokens\">"
                       f"{_csrf(csrf)}<button class=\"plain\" type=\"submit\">{esc(t(lang, 'admin.revoketokens'))}</button></form>")
        rows.append(
            f"<tr><td>{esc(email)}<br><span class=\"mute tok\">{esc(u.id)}</span></td>"
            f"<td>{esc(u.status)}</td><td>{esc(admin)}</td><td>{esc(t(lang, 'admin.yes') if u.can_write else t(lang, 'admin.no'))}</td>"
            f"<td>{_fmt_time(u.created_at)}</td><td>{_fmt_time(u.last_login_at)}</td><td>{''.join(buttons)}</td></tr>")
    if not rows:
        return "<p class=\"mute\">" + esc(t(lang, "admin.usersnone")) + "</p>"
    return ("<table><tr><th>" + esc(t(lang, "admin.useremail")) + "</th>"
            "<th>" + esc(t(lang, "admin.userstatus")) + "</th>"
            "<th>" + esc(t(lang, "admin.useradmin")) + "</th>"
            "<th>" + esc(t(lang, "admin.userwrite")) + "</th>"
            "<th>" + esc(t(lang, "admin.usercreated")) + "</th>"
            "<th>" + esc(t(lang, "admin.userlastlogin")) + "</th>"
            "<th>" + esc(t(lang, "admin.useractions")) + "</th></tr>" + "".join(rows) + "</table>")


def _admin_audit_rows(entries: Sequence, lang: str) -> str:
    rows = []
    for a in entries:
        extra = " · " + a["code"] if a["code"] else ""
        rows.append(f"<tr><td>{_fmt_time(a['ts'])}</td><td>{esc(a['event'])}{esc(extra)}</td>"
                    f"<td class=\"tok\">{esc(a['userId'])}</td><td class=\"tok\">{esc(a['ref'])}</td></tr>")
    return ("<table><tr><th>" + esc(t(lang, "admin.audittime")) + "</th>"
            "<th>" + esc(t(lang, "admin.auditevent")) + "</th>"
            "<th>" + esc(t(lang, "admin.audituser")) + "</th>"
            "<th>" + esc(t(lang, "admin.auditref")) + "</th></tr>" + "".join(rows) + "</table>")


def admin_page(view: View, nonce: str, *, lang: str = "", email: str, csrf: str, now: int, users: Sequence,
               audit: Sequence, settings: dict, deployment: dict) -> str:
    """The operator area. Every value on this page comes from the server; nothing here is a script."""
    yes, no = t(lang, "admin.yes"), t(lang, "admin.no")
    parts = ["<h1>" + esc(t(lang, "title.admin")) + "</h1>"]
    parts.append("<p class=\"mute\">" + esc(t(lang, "admin.current", email=email)) + " · "
                 "<a href=\"/account\">" + esc(t(lang, "account.myaccount")) + "</a> · "
                 "<a href=\"/\">" + esc(t(lang, "account.console")) + "</a></p>")

    parts.append(
        "<h2>" + esc(t(lang, "admin.deployh2")) + "</h2><div class=\"card\"><table>"
        f"<tr><th>{esc(t(lang, 'admin.toolset'))}</th><td>{esc(str(deployment.get('toolset', '')))}</td></tr>"
        f"<tr><th>{esc(t(lang, 'admin.mcpmode'))}</th><td>{esc(str(deployment.get('mcpMode', '')))}</td></tr>"
        f"<tr><th>{esc(t(lang, 'admin.envadmins'))}</th><td>{esc(', '.join(deployment.get('adminEmails') or []) or t(lang, 'admin.none'))}</td></tr>"
        f"<tr><th>{esc(t(lang, 'admin.alloweddomains'))}</th><td>{esc(', '.join(deployment.get('allowedDomains') or []) or t(lang, 'admin.none'))}</td></tr>"
        f"<tr><th>{esc(t(lang, 'admin.allowedemails'))}</th><td>{esc(', '.join(deployment.get('allowedEmails') or []) or t(lang, 'admin.bydomain'))}</td></tr>"
        f"<tr><th>{esc(t(lang, 'admin.maxusers'))}</th><td>{esc(str(deployment.get('maxUsers', '')))}</td></tr>"
        f"<tr><th>{esc(t(lang, 'admin.openregistration'))}</th><td>{esc(yes if deployment.get('openRegistration') else no)}</td></tr>"
        "</table></div>")

    parts.append("<h2>" + esc(t(lang, "admin.usersh2")) + "</h2>" + _admin_user_rows(users, csrf, lang))

    site = deployment.get("site")
    if site:
        parts.append(
            "<h2>" + esc(t(lang, "admin.siteh2")) + "</h2><div class=\"card\"><table>"
            f"<tr><th>{esc(t(lang, 'admin.siteavailable'))}</th><td>{esc(yes if site.get('available') else no)}</td></tr>"
            f"<tr><th>{esc(t(lang, 'admin.sitebuilt'))}</th><td>{esc(yes if site.get('built') else no)}</td></tr>"
            f"<tr><th>{esc(t(lang, 'admin.sitesource'))}</th><td>{esc(str(site.get('source', '')) or '—')}</td></tr>"
            "</table>"
            "<form method=\"post\" action=\"/account/admin/site\">" + _csrf(csrf) +
            "<button type=\"submit\">" + esc(t(lang, "admin.siterebuild")) + "</button></form>"
            "<p class=\"mute\">" + esc(t(lang, "admin.siterebuildnote")) + "</p>"
            "</div>")

    def number_row(key: str, label: str, low: int, high: int) -> str:
        current = settings.get(key, "")
        shown = esc(current) if current else t(lang, "admin.settingsfromenv")
        return (f"<tr><td>{esc(label)}</td><td>{shown}</td>"
                f"<td><form method=\"post\" action=\"/account/admin/settings\" class=\"row\">{_csrf(csrf)}"
                f"<input type=\"hidden\" name=\"key\" value=\"{esc(key)}\">"
                f"<input name=\"value\" type=\"number\" min=\"{low}\" max=\"{high}\" required "
                "style=\"width:6rem\">"
                f"<button type=\"submit\">{esc(t(lang, 'admin.settingssave'))}</button></form></td></tr>")

    toolset_value = settings.get("toolset", "")
    toolset_row = (
        "<tr><td>" + esc(t(lang, "admin.toolset")) + "</td><td>"
        + (esc(toolset_value) if toolset_value else t(lang, "admin.settingsfromenv")) + "</td>"
        "<td><form method=\"post\" action=\"/account/admin/settings\" class=\"row\">" + _csrf(csrf) +
        "<input type=\"hidden\" name=\"key\" value=\"toolset\">"
        "<input name=\"value\" list=\"gemarc-toolsets\" required style=\"width:10rem\">"
        "<datalist id=\"gemarc-toolsets\"><option value=\"stateless\"><option value=\"full\"></datalist>"
        "<button type=\"submit\">" + esc(t(lang, "admin.settingssave")) + "</button></form></td></tr>")
    parts.append(
        "<h2>" + esc(t(lang, "admin.settingsh2")) + "</h2><div class=\"card\"><table>"
        "<tr><th>" + esc(t(lang, "admin.settingsitem")) + "</th>"
        "<th>" + esc(t(lang, "admin.settingscurrent")) + "</th>"
        "<th>" + esc(t(lang, "admin.settingschange")) + "</th></tr>"
        + number_row("pat_default_days", t(lang, "admin.settingpatdefault"), 1, 365)
        + number_row("pat_max_days", t(lang, "admin.settingpatmax"), 1, 365)
        + number_row("pat_max_per_user", t(lang, "admin.settingpatcount"), 1, 50)
        + toolset_row +
        "</table><p class=\"mute\">" + esc(t(lang, "admin.settingsnote")) + "</p></div>")

    parts.append("<h2>" + esc(t(lang, "admin.audith2")) + "</h2>" + _admin_audit_rows(audit, lang))
    return page(t(lang, "title.admin"), "".join(parts), nonce, lang)
