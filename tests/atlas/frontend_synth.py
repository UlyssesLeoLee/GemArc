"""A synthetic static frontend for the frontend-check tests (design A3 T-S3): a six-screen prototype, a generator that turns the expanded prototype into
plain HTML and script files, and ten seeded defects (single changes of the generated tree).

Test helper, standard library only; every id and text is invented. The frontend is the plain kind the scan is meant for: one page per screen,
labels as text or accessible names (``aria-label`` for containers, whose label the prototype does not draw), field names in ``name=``, navigation as
``data-goto`` / ``href`` literals, a router file with the routes. ``tag_ids=True`` adds the recommended ``data-atlas-id`` attribute to every element.

``prototype_text()``      the six-screen prototype (``#atlas-ui 1``)
``prototype_model()``     its expanded model
``render(model, ...)``    ``{relative path: text}`` of the frontend (clean, or with one defect of ``DEFECTS`` applied)
``write(files, root)``    write a tree to a directory
``DEFECTS``               the ten seeded defects, each with the check points it makes false (``direct``) and the ones that only change as a consequence
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gemarc_mcp.atlas.protoui import atl, expand as ex

SCREENS = ("SCR-LOGIN", "SCR-HOME", "SCR-CATALOG", "SCR-BOOK", "SCR-RETURN", "SCR-PROFILE")


def prototype_text() -> str:
    return """#atlas-ui 1
// 借阅台:合成的六屏示例,不是真实系统。
## screens: id | title | purpose | opts
SCR-LOGIN | 登录 | 会员用邮箱和密码进入借阅台。 | kind=page frame=desktop route=/login start=true
SCR-HOME | 我的借阅 | 列出会员当前借出的书。 | kind=page frame=desktop route=/home
SCR-CATALOG | 馆藏目录 | 按关键词查找馆藏。 | kind=page frame=desktop route=/catalog
SCR-BOOK | 登记新书 | 管理员登记一本新书。 | kind=page frame=desktop route=/book/new
SCR-RETURN | 还书 | 会员办理还书。 | kind=page frame=desktop route=/return
SCR-PROFILE | 个人资料 | 会员查看并修改联系方式。 | kind=page frame=desktop route=/profile
## variants: screen | name | note | opts
SCR-LOGIN | error | 邮箱或密码不正确。 | kind=error
SCR-HOME | empty | 没有任何借出的书。 | kind=empty
SCR-CATALOG | empty | 没有找到馆藏。 | kind=empty
SCR-BOOK | error | 登记失败。 | kind=error
SCR-RETURN | success | 还书成功。 | kind=success
## widgets: local | label | opts
@set screen=SCR-LOGIN
form | 登录表单 | kind=form
email | 邮箱 | kind=input parent=form bind=email
pwd | 密码 | kind=input parent=form bind=password
err | 邮箱或密码不正确 | kind=text parent=form in=error emph=danger
submit | 登录 | kind=button parent=form emph=primary
@set screen=SCR-HOME
bar | 借阅台 | kind=header
logout | 退出 | kind=link parent=bar
me | 资料 | kind=link parent=bar
head | 我的借阅 | kind=heading
loans | 借出的书 | kind=table w=fill data={"cols":["书名","到期日"],"rows":[["示例一","明天"]]}
find | 找书 | kind=button emph=primary
give | 还书 | kind=button
@set screen=SCR-CATALOG
bar | 目录页眉 | kind=header
back | 返回 | kind=link parent=bar
head | 馆藏目录 | kind=heading
search | 检索表单 | kind=form
q | 关键词 | kind=input parent=search bind=keyword
go | 检索 | kind=button parent=search req=RQ-1
books | 馆藏列表 | kind=table w=fill
open | 登记新书 | kind=link
@set screen=SCR-BOOK
form | 登记表单 | kind=form
title | 书名 | kind=input parent=form bind=title
author | 作者 | kind=input parent=form bind=author
isbn | 书号 | kind=input parent=form bind=isbn
shelf | 书架 | kind=select parent=form bind=shelf
note | 备注 | kind=textarea parent=form bind=note
err | 登记失败,请检查书号 | kind=text parent=form in=error emph=danger
save | 保存 | kind=button parent=form emph=primary
cancel | 取消 | kind=link parent=form
@set screen=SCR-RETURN
head | 办理还书 | kind=heading
form | 还书表单 | kind=form
copy | 册号 | kind=input parent=form bind=copyId
hint | 请把书放在柜台上 | kind=text parent=form
confirm | 确认还书 | kind=button parent=form emph=primary
done | 还书成功 | kind=text in=success emph=muted
@set screen=SCR-PROFILE
head | 联系方式 | kind=heading
form | 资料表单 | kind=form
phone | 电话 | kind=input parent=form bind=phone
city | 城市 | kind=input parent=form bind=address.city
notify | 接收到期提醒 | kind=checkbox parent=form bind=notify
update | 保存修改 | kind=button parent=form emph=primary
return | 回到首页 | kind=link
## nav: from | to | label | opts
SCR-LOGIN.submit | SCR-HOME | 凭证正确,进入首页 | on=submit
SCR-HOME.logout | SCR-LOGIN | 退出登录 | on=tap
SCR-HOME.find | SCR-CATALOG | 去找书 | on=tap
SCR-HOME.give | SCR-RETURN | 去还书 | on=tap
SCR-HOME.me | SCR-PROFILE | 看资料 | on=tap
SCR-CATALOG.back | SCR-HOME | 回到首页 | on=tap
SCR-CATALOG.open | SCR-BOOK | 去登记 | on=tap
SCR-BOOK.save | SCR-CATALOG | 保存后回到目录 | on=submit
SCR-BOOK.cancel | SCR-CATALOG | 放弃登记 | on=tap
SCR-RETURN.confirm | SCR-HOME | 还书后回到首页 | on=submit
SCR-PROFILE.update | SCR-HOME | 保存后回到首页 | on=submit
SCR-PROFILE.return | SCR-HOME | 不保存 | on=tap
@final nav
"""


def prototype_model() -> dict:
    expansion = ex.expand_ui([atl.parse_atl(prototype_text(), "ui-10-synthetic.atl")])
    errors = [d for d in expansion.diagnostics if d.severity == "ERROR"]
    assert not errors, errors
    return expansion.model


# ---------------------------------------------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------------------------------------------


@dataclass(eq=False)
class Node:
    """One element of a page: a widget of the prototype, or an extra element a defect adds."""

    wid: str
    local: str
    kind: str
    label: str
    bind: str = ""
    routes: list = field(default_factory=list)
    states: list = field(default_factory=list)       # states in which the element shows (empty: always)
    cols: list = field(default_factory=list)
    emph: str = ""
    children: list = field(default_factory=list)
    parent: "Node | None" = None


def _tree(model: dict) -> dict:
    """``{screen id: root Node}`` built from the expanded model; the root holds the widgets without a parent in their order."""
    routes = {s["id"]: s.get("route", "") for s in model["screens"]}
    goto: dict = {}
    for n in model["nav"]:
        if "." in n["fromId"]:
            target = routes.get(n["toScreenId"], "")
            if target:
                goto.setdefault(n["fromId"], []).append(target)
    widgets = {w["id"]: w for w in model["widgets"]}
    out = {}
    for s in model["screens"]:
        root = Node(s["id"], "root", "screen", s["title"])
        nodes = {}
        for wid in s["widgetIds"]:
            w = widgets[wid]
            states = [v for v in (w.get("inVariants") or []) if v != "default"] if w.get("inVariants") and "default" not in w["inVariants"] else []
            cols = (w.get("data") or {}).get("cols", []) if w["kind"] == "table" else []
            nodes[wid] = Node(wid, w["local"], w["kind"], w["label"], w.get("bind", ""), list(goto.get(wid, [])), states, list(cols), w.get("emph", ""))
        for wid in s["widgetIds"]:
            w = widgets[wid]
            parent = nodes.get(w["parentId"]) if w.get("parentId") else root
            nodes[wid].parent = parent
            parent.children.append(nodes[wid])
        out[s["id"]] = root
    return out


_CONTAINER_TAG = {"stack": ("div", ' class="stack"'), "row": ("div", ' class="row"'), "grid": ("div", ' class="grid"'), "card": ("section", ' class="card"'),
                  "form": ("form", ""), "header": ("header", ""), "footer": ("footer", ""), "sidebar": ("aside", ""),
                  "toolbar": ("div", ' role="toolbar"'), "tabs": ("div", ' class="tabs"'), "list": ("ul", "")}


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _lines(node: Node, depth: int, tag_ids: bool) -> list:
    pad = "  " * depth
    ids = f' data-atlas-id="{node.wid}"' if tag_ids else ""
    hide = f' hidden data-show="{",".join(node.states)}"' if node.states else ""
    label = _esc(node.label)
    k = node.kind
    if k in _CONTAINER_TAG:
        tag, extra = _CONTAINER_TAG[k]
        inner = []
        for child in node.children:
            inner += _lines(child, depth + 1, tag_ids)
        if tag == "ul":
            inner = [f"{pad}  <li>{line.strip()}</li>" for line in inner]
        return [f'{pad}<{tag}{extra} aria-label="{label}"{ids}{hide}>'] + inner + [f"{pad}</{tag}>"]
    field_name = _esc(node.bind or node.local)
    if k == "heading":
        return [f"{pad}<h2{ids}{hide}>{label}</h2>"]
    if k == "text":
        return [f'{pad}<p class="{node.emph or "plain"}"{ids}{hide}>{label}</p>']
    if k == "badge":
        return [f'{pad}<span class="badge"{ids}>{label}</span>']
    if k == "image":
        return [f'{pad}<img alt="{label}" src="img/{node.local}.png"{ids}>']
    if k == "icon":
        return [f'{pad}<i class="icon" title="{label}"{ids}></i>']
    if k == "divider":
        return [f'{pad}<hr role="separator" aria-label="{label}"{ids}>']
    if k == "chart":
        return [f'{pad}<canvas aria-label="{label}"{ids}></canvas>']
    if k == "custom":
        return [f'{pad}<div class="custom" aria-label="{label}"{ids}></div>']
    if k == "pagination":
        return [f'{pad}<nav aria-label="{label}"{ids}></nav>']
    if k == "table":
        head = "".join(f"<th>{_esc(c)}</th>" for c in node.cols)
        return [f'{pad}<table aria-label="{label}"{ids}>', f"{pad}  <thead><tr>{head}</tr></thead>", f"{pad}  <tbody></tbody>", f"{pad}</table>"]
    if k in ("input", "textarea", "select"):
        control = {"input": f'<input id="{node.local}" name="{field_name}" type="text"{ids}>',
                   "textarea": f'<textarea id="{node.local}" name="{field_name}"{ids}></textarea>',
                   "select": f'<select id="{node.local}" name="{field_name}"{ids}><option></option></select>'}[k]
        return [f'{pad}<label for="{node.local}"{hide}>{label}</label>', f"{pad}{control}"]
    if k in ("checkbox", "radio", "switch"):
        typ = "radio" if k == "radio" else "checkbox"
        role = ' role="switch"' if k == "switch" else ""
        return [f'{pad}<label><input type="{typ}"{role} name="{field_name}"{ids}> {label}</label>']
    if k == "button":
        goto = f' data-goto="{",".join(node.routes)}" onclick="go(this.dataset.goto)"' if node.routes else ""
        return [f'{pad}<button type="button" class="btn {node.emph}"{goto}{ids}>{label}</button>']
    if k == "link":
        return [f'{pad}<a href="{node.routes[0] if node.routes else "#"}"{ids}>{label}</a>']
    if k == "tab":
        return [f'{pad}<button role="tab"{ids}>{label}</button>']
    if k == "menu":
        return [f'{pad}<ul role="menu" aria-label="{label}"{ids}></ul>']
    return [f"{pad}<div{ids}>{label}</div>"]


def _page(sid: str, title: str, root: Node, tag_ids: bool) -> str:
    body = []
    for child in root.children:
        body += _lines(child, 2, tag_ids)
    root_id = f' data-atlas-id="{sid}"' if tag_ids else ""
    return "\n".join([
        "<!doctype html>", '<html lang="zh-CN">', "<head>", '  <meta charset="utf-8">', f"  <title>{_esc(title)}</title>",
        '  <script src="../js/app.js"></script>', "</head>", "<body>", '  <nav class="site"><a href="/home">首页</a> <a href="/catalog">目录</a></nav>',
        f'  <main data-screen="{sid}"{root_id}>', *body, "  </main>", "</body>", "</html>", ""])


def _router(model: dict) -> str:
    rows = [f'  "{s["route"]}": "pages/{s["id"].lower()}.html",' for s in model["screens"] if s.get("route")]
    return "\n".join(["// routes of the synthetic frontend", "const routes = {", *rows, "};", ""])


def render(model: dict, *, tag_ids: bool = False, defect: "Defect | None" = None) -> dict:
    """The frontend files of ``model`` (clean, or with ``defect`` applied to the tree and the text)."""
    tree = _tree(model)
    if defect is not None and defect.on_tree is not None:
        defect.on_tree(tree)
    files = {"js/routes.js": _router(model), "js/app.js": "function go(path) {\n  location.href = path.split(\",\")[0];\n}\n"}
    for s in model["screens"]:
        files[f"pages/{s['id'].lower()}.html"] = _page(s["id"], s["title"], tree[s["id"]], tag_ids)
    if defect is not None and defect.on_text is not None:
        files = defect.on_text(files)
    return files


def write(files: dict, root: "Path | str") -> None:
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------------------------------------------
# The ten seeded defects
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Defect:
    """One single change of the frontend. ``direct``: the check points the change makes false; ``indirect``: check points that change as a
    consequence (a removed element also removes the text of its label from the source)."""

    key: str
    kind: str
    note: str
    screen: str
    direct: tuple
    indirect: tuple = ()
    on_tree: "Callable | None" = None
    on_text: "Callable | None" = None


def _find(tree: dict, wid: str) -> Node:
    screen = wid.split(".", 1)[0]
    stack = [tree[screen]]
    while stack:
        n = stack.pop()
        if n.wid == wid:
            return n
        stack.extend(n.children)
    raise KeyError(wid)


def _remove(wid: str) -> Callable:
    def go(tree: dict) -> None:
        n = _find(tree, wid)
        n.parent.children.remove(n)
    return go


def _relabel(wid: str, label: str) -> Callable:
    def go(tree: dict) -> None:
        _find(tree, wid).label = label
    return go


def _replace_everywhere(old: str, new: str) -> Callable:
    def go(files: dict) -> dict:
        return {rel: text.replace(f'"{old}"', f'"{new}"') for rel, text in files.items()}
    return go


def _lead_to(wid: str, new: str) -> Callable:
    def go(tree: dict) -> None:
        n = _find(tree, wid)
        n.routes = [new]
    return go


def _add_extra(parent: str, label: str) -> Callable:
    def go(tree: dict) -> None:
        p = _find(tree, parent)
        p.children.append(Node(parent.split(".", 1)[0] + ".export", "export", "button", label, parent=p))
    return go


def _move_out(wid: str) -> Callable:
    def go(tree: dict) -> None:
        n = _find(tree, wid)
        root = tree[wid.split(".", 1)[0]]
        n.parent.children.remove(n)
        n.parent = root
        root.children.append(n)
    return go


def _swap(first: str, second: str) -> Callable:
    def go(tree: dict) -> None:
        a, b = _find(tree, first), _find(tree, second)
        siblings = a.parent.children
        i, j = siblings.index(a), siblings.index(b)
        siblings[i], siblings[j] = siblings[j], siblings[i]
    return go


DEFECTS = (
    Defect("missing-field-1", "missing field", "the book form has no field for the book number", "SCR-BOOK", ("SCR-BOOK.isbn@label", "SCR-BOOK.isbn@bind"),
           on_tree=_remove("SCR-BOOK.isbn")),
    Defect("missing-field-2", "missing field", "the profile form has no phone field", "SCR-PROFILE", ("SCR-PROFILE.phone@label", "SCR-PROFILE.phone@bind"),
           on_tree=_remove("SCR-PROFILE.phone")),
    Defect("wrong-text-1", "wrong text", "the login button says another word", "SCR-LOGIN", ("SCR-LOGIN.submit@label",),
           on_tree=_relabel("SCR-LOGIN.submit", "登入")),
    Defect("wrong-text-2", "wrong text", "the heading of the catalog says another thing", "SCR-CATALOG", ("SCR-CATALOG.head@label",),
           on_tree=_relabel("SCR-CATALOG.head", "图书检索")),
    Defect("wrong-route", "wrong route", "the catalog is served at another path (every link follows)", "SCR-CATALOG", ("SCR-CATALOG@route",),
           indirect=("SCR-HOME.find@goto", "SCR-BOOK.save@goto", "SCR-BOOK.cancel@goto"),
           on_text=lambda files: {rel: text.replace("/catalog", "/books") for rel, text in files.items()}),
    Defect("missing-state", "missing state", "the login page has no branch for a wrong password", "SCR-LOGIN", ("SCR-LOGIN#error@state",),
           indirect=("SCR-LOGIN.err@label",), on_tree=_remove("SCR-LOGIN.err")),
    Defect("wrong-goto", "wrong goto", "the login button leads to the profile page", "SCR-LOGIN", ("SCR-LOGIN.submit@goto",),
           on_tree=_lead_to("SCR-LOGIN.submit", "/profile")),
    Defect("extra-control", "extra control", "the home header has an export button the prototype does not have", "SCR-HOME", (),
           on_tree=_add_extra("SCR-HOME.bar", "导出")),
    Defect("misplaced-container", "misplaced container", "the book number field sits outside the form", "SCR-BOOK", ("SCR-BOOK.isbn@parent", "SCR-BOOK.form@order"),
           on_tree=_move_out("SCR-BOOK.isbn")),
    Defect("wrong-order", "wrong order", "title and author are swapped in the book form", "SCR-BOOK", ("SCR-BOOK.form@order",),
           on_tree=_swap("SCR-BOOK.title", "SCR-BOOK.author")),
)
