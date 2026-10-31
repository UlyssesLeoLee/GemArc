# 接入 GemArc Atlas 工具集

本目录的配置把 GemArc 的 `atlas` 工具集（本地 stdio，13 个工具加一个入口提示：写事实、校验、盲测判定、证据核验、构建、浏览器门、自审）接到 Claude Code、Codex 和 Hermes。工具和提示的契约见 `../ATLAS_TOOLS.md`，使用流程见 `skills/gemarc-atlas/SKILL.md`。

这些示例故意放在 `docs/atlas/examples/`，不放在 `integration-examples/`：后者是 v1 工具集的示例，由 v1 的测试按 16 个工具逐一核对。

## 1. 占位符

示例里只出现下面两个占位符，用你机器上的真实路径替换：

| 占位符 | 含义 |
|---|---|
| `<repo>` | 本仓库的目录（里面有 `.venv`）。Windows 示例：`C:\src\GemArc` |
| `<workspace>` | 一个已存在的空目录，Atlas 项目写在这里面。不要选在你要分析的仓库里面 |

## 2. 环境变量（四个客户端相同）

| 变量 | 含义 |
|---|---|
| `GEMARC_TOOLSET` | 必须是 `atlas`。一个服务器进程只带一个工具集；要同时用 v1 工具，另外再注册一个 `gemarc` 条目 |
| `GEMARC_WORKSPACE_ROOT` | 项目目录所在的根（`<workspace>`）。写入只会发生在这里面 |
| `GEMARC_ALLOW_WRITE` | `1` 允许写项目的工具工作（`init`、`put`、`simulate`、`evidence`、`build`、`verify` 和 `review` 的 `record`）；`0` 只读（这些工具返回 `GEMARC_AUTH_002`，`brief`、`check`、`status`、`index`、`doc`、`outline` 和 `review` 的 `report` 照常） |
| `GEMARC_ATLAS_ROOTS` | 被分析源码所在的位置，`别名=路径;别名=路径`。模型只能写别名，不能写路径；`gemarc_atlas_outline` 和 `gemarc_atlas_evidence` 用它读源码，没有它 as-is 的 Implemented 声明无法核验 |
| `GEMARC_ATLAS_BROWSER` | 可选：Edge 或 Chrome 可执行文件的完整路径，供浏览器门（`gemarc_atlas_verify`）用。指向的文件不存在时门是醒目的 SKIPPED，不会改用别的浏览器 |
| `PYTHONUTF8` | 保持 `1`，中文才不会乱码 |

客户端启动 stdio 服务器时只传过滤后的环境，所以服务器需要的每个变量都要写在配置里，不能指望继承你的 shell。浏览器门在 Windows 上靠 `ProgramFiles(x86)`、`ProgramFiles`、`LOCALAPPDATA` 找到 Edge；这几个变量不在被过滤的环境里时，门禁自己按标准安装目录（系统盘上的 `Program Files (x86)`、`Program Files`）找（`gate/smoke.py` 的 `candidate_paths`），不需要在配置里转发它们（转发无害）；Edge 装在别处时，设置 `GEMARC_ATLAS_BROWSER`。找不到浏览器时 `gemarc_atlas_verify` 回复 SKIPPED（不算通过），`gemarc_atlas_status` 停在 `needs-user`，由用户装浏览器，或把理由写进 `<workspace>/<项目>/atlas/waive-browser.txt`（只有用户写这个文件）。

## 3. Claude Code

配置文件：`claude-code.mcp.json`。两种用法：

```
# 无头运行或临时试用：用 --mcp-config 指向文件（先把里面的占位符改成真实路径）
claude --mcp-config docs/atlas/examples/claude-code.mcp.json

# 长期使用：加到自己的用户配置里
claude mcp add gemarc_atlas -e GEMARC_TOOLSET=atlas -e GEMARC_WORKSPACE_ROOT=<workspace> -e GEMARC_ALLOW_WRITE=1 -e GEMARC_ATLAS_ROOTS=main=<repo> -e PYTHONUTF8=1 -- <repo>\.venv\Scripts\python.exe -m gemarc_mcp
```

接入后工具显示为 `mcp__gemarc_atlas__gemarc_atlas_put` 之类，入口提示是斜杠命令 `/mcp__gemarc_atlas__gemarc-atlas-create`。（这里的命令格式按 Claude Code 的文档写出；本目录的自动测试只验证 JSON 能解析、变量齐全，没有拉起 Claude Code，真实接入由最终验收阶段实测。）

### 3.1 无头运行：`claude -p`（只给 atlas 工具加 Read、Grep、Glob）

最终验收用的是无头模式：一句话进去，只允许 atlas 的 13 个工具和只读的 `Read`、`Grep`、`Glob`，工作区放在仓库之外，证据根指向被分析的仓库。三份文件配合使用：

| 文件 | 作用 |
|---|---|
| `claude-headless.mcp.json` | 给 `--mcp-config` 的服务器配置（只有 `mcpServers`，和 `claude-code.mcp.json` 同一个条目，`GEMARC_ATLAS_ROOTS` 指向 `<repo>`） |
| `claude-headless.settings.json` | 给 `--settings` 的权限：`allow` 恰好是 13 个 `mcp__gemarc_atlas__gemarc_atlas_*` 加 `Read`、`Grep`、`Glob`；`deny` 禁掉 `Bash`、`Edit`、`Write`、`NotebookEdit`、`WebFetch`、`WebSearch` |
| `claude-headless-tobe.settings.json` | to-be（从需求短文设计新系统）用的 `--settings`：清单与上一份相同，另外只允许 `Write` 写 `<workspace>/*/atlas/user/**`（保存需求原文；`deny` 里没有整体的 `Write`，因为 `deny` 的优先级高于 `allow`，整体禁掉会让这条允许失效）。路径规则写成 `//` 加小写盘符再加路径，例如 `//c/work/atlas-ws/*/atlas/user/**`，按你的 Claude Code 版本核对写法；**这条路径规则没有在真实客户端上试过** |
| 命令行里的 `--allowedTools` | 同一份清单，写在命令行上（`allowedTools` 不是 `--mcp-config` 文件里的键，所以单独列出） |

在仓库根目录执行（先把两份 JSON 里的 `<repo>` 和 `<workspace>` 换成真实路径，`<workspace>` 在仓库之外）：

```
claude -p "为这个库创建5D设计" \
  --mcp-config docs/atlas/examples/claude-headless.mcp.json --strict-mcp-config \
  --settings docs/atlas/examples/claude-headless.settings.json \
  --allowedTools "mcp__gemarc_atlas__gemarc_atlas_brief,mcp__gemarc_atlas__gemarc_atlas_init,mcp__gemarc_atlas__gemarc_atlas_put,mcp__gemarc_atlas__gemarc_atlas_check,mcp__gemarc_atlas__gemarc_atlas_status,mcp__gemarc_atlas__gemarc_atlas_index,mcp__gemarc_atlas__gemarc_atlas_simulate,mcp__gemarc_atlas__gemarc_atlas_doc,mcp__gemarc_atlas__gemarc_atlas_outline,mcp__gemarc_atlas__gemarc_atlas_evidence,mcp__gemarc_atlas__gemarc_atlas_build,mcp__gemarc_atlas__gemarc_atlas_verify,mcp__gemarc_atlas__gemarc_atlas_review,Read,Grep,Glob" \
  --output-format stream-json --verbose
```

`--strict-mcp-config` 让这次运行只用上面这一份 MCP 配置。`claude-headless.settings.json` 没有 `Write`，模型不能自己把需求原文存进 `atlas/user/`（as-is 不强制）。**to-be 的无头运行**要么把 `--settings` 换成 `claude-headless-tobe.settings.json`，要么由你或操作者先把需求原文写进 `<工作区>/<项目名>/atlas/user/<名字>.txt`（`init` 先创建目录；工具集里没有保存用户文字的工具）。**这条命令没有在真实的 `claude` 上跑过**：自动测试只验证两份 JSON 能解析、清单与注册的工具逐个一致、服务器按这份配置能起来并列出 13 个工具；真实运行由最终验收阶段做。

## 4. Codex

配置文件：`codex.config.toml`，追加到 `~/.codex/config.toml`。Codex 默认对破坏性工具先询问：`gemarc_atlas_put` 会询问，`gemarc_atlas_init` 不会（文件里有一段注释教你让它也询问）。

## 5. Hermes

配置文件：`hermes.config.yaml`，合并到 `~/.hermes/config.yaml`（Windows 原生：`%LOCALAPPDATA%\hermes\config.yaml`）。命令必须是真实的可执行文件，不要用 `cmd` 或 `bash -c` 包一层。

## 6. 手工测试：`scripts/atlas_mcp.py`

不接任何客户端也能验证服务器：`scripts/atlas_mcp.py` 通过真实的 stdio 和官方 MCP SDK 与服务器通话，输出紧凑 JSON，回复为 `isError` 时退出码非零。它转发当前 shell 里的 `GEMARC_*` 变量，`GEMARC_TOOLSET` 默认 `atlas`。在仓库根目录执行：

```
export GEMARC_WORKSPACE_ROOT=<workspace>
export GEMARC_ALLOW_WRITE=1
.venv/Scripts/python.exe scripts/atlas_mcp.py list-tools
.venv/Scripts/python.exe scripts/atlas_mcp.py list-prompts
.venv/Scripts/python.exe scripts/atlas_mcp.py get-prompt gemarc-atlas-create mode=asis project=demo
.venv/Scripts/python.exe scripts/atlas_mcp.py call gemarc_atlas_init '{"project":"demo","title":"Demo"}'
.venv/Scripts/python.exe scripts/atlas_mcp.py call gemarc_atlas_status '{"project":"demo"}'
.venv/Scripts/python.exe scripts/atlas_mcp.py call gemarc_atlas_put @put-args.json
```

要点：

- `call` 的第二个参数是 JSON 对象，也可以写成 `@文件名` 从文件读（长切片文本用这种方式更方便）。
- 不设 `GEMARC_ALLOW_WRITE=1` 时，`gemarc_atlas_init` 和 `gemarc_atlas_put` 返回 `GEMARC_AUTH_002`，`gemarc_atlas_check`、`gemarc_atlas_status`、`gemarc_atlas_index`、`gemarc_atlas_brief` 照常工作。
- 选远程工具集用 `--toolset atlas_remote`：只有 `gemarc_atlas_brief` 和只读的 `gemarc_atlas_check`。
- 没有 MCP 接入的无头代理可以只用这个脚本：`get-prompt` 取出驱动文本，再逐个 `call`。

## 7. 一次完整的会话

1. `get-prompt gemarc-atlas-create requirement=...` 取出驱动文本（或在 Claude Code 里直接运行斜杠命令）。
2. 驱动文本让模型：读工作指令、`gemarc_atlas_init`、保存需求原文、循环 `gemarc_atlas_status` 与 `gemarc_atlas_put`，红稿用 `mode=patch` 修复；结构写完后（phase `build`）按 `status` 点名的顺序过 `gemarc_atlas_simulate`、`gemarc_atlas_evidence`（as-is）、`gemarc_atlas_build`、`gemarc_atlas_verify`；phase `review` 时恰好自审一次；最后写报告，并把 `atlas/dist/oracle-sheet.md` 交给你。
3. 随时可以用 `gemarc_atlas_status` 看进度；说"继续"就从 `status` 续上，状态都在 `<workspace>/<项目>/atlas/` 里。
4. 交到你手里的有两样：`<workspace>/<项目>/atlas/dist/index.html`（查看器）和 `oracle-sheet.md`（每条还没经你确认的期望一行，外加一行 `APPROVE-ALL`；勾选行或保留 `APPROVE-ALL` 行，写进 `atlas/oracle-confirm.txt`，这个文件只有你写）。
5. 模型遇到只有你能决定的事项会停下来问你（`needs-user`：TBD、改过的期望要你批准的 oracle 表、没有浏览器、as-is 没有配置证据根 `GEMARC_ATLAS_ROOTS`），不会自己编造；同一个切片修复超过 3 轮或 `put` 次数用完，会报告 `blocked` 和最后一次诊断。
