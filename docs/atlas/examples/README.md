# 接入 GemArc Atlas 工具集

本目录的配置把 GemArc 的 `atlas` 工具集（本地 stdio，撰写 5D 设计资料用的 6 个工具加一个入口提示）接到 Claude Code、Codex 和 Hermes。工具和提示的契约见 `../ATLAS_TOOLS.md`，使用流程见 `skills/gemarc-atlas/SKILL.md`。

这些示例故意放在 `docs/atlas/examples/`，不放在 `integration-examples/`：后者是 v1 工具集的示例，由 v1 的测试按 16 个工具逐一核对。

## 1. 占位符

示例里只出现下面两个占位符，用你机器上的真实路径替换：

| 占位符 | 含义 |
|---|---|
| `<repo>` | 本仓库的目录（里面有 `.venv`）。Windows 示例：`D:\GemArc` |
| `<workspace>` | 一个已存在的空目录，Atlas 项目写在这里面。不要选在你要分析的仓库里面 |

## 2. 环境变量（四个客户端相同）

| 变量 | 含义 |
|---|---|
| `GEMARC_TOOLSET` | 必须是 `atlas`。一个服务器进程只带一个工具集；要同时用 v1 工具，另外再注册一个 `gemarc` 条目 |
| `GEMARC_WORKSPACE_ROOT` | 项目目录所在的根（`<workspace>`）。写入只会发生在这里面 |
| `GEMARC_ALLOW_WRITE` | `1` 允许 `gemarc_atlas_init` 和 `gemarc_atlas_put` 写项目；`0` 只读（这两个工具返回 `GEMARC_AUTH_002`，其余工具照常） |
| `GEMARC_ATLAS_ROOTS` | 被分析源码所在的位置，`别名=路径;别名=路径`。模型只能写别名，不能写路径 |
| `PYTHONUTF8` | 保持 `1`，中文才不会乱码 |

客户端启动 stdio 服务器时只传过滤后的环境，所以服务器需要的每个变量都要写在配置里，不能指望继承你的 shell。

## 3. Claude Code

配置文件：`claude-code.mcp.json`。两种用法：

```
# 无头运行或临时试用：用 --mcp-config 指向文件（先把里面的占位符改成真实路径）
claude --mcp-config docs/atlas/examples/claude-code.mcp.json

# 长期使用：加到自己的用户配置里
claude mcp add gemarc_atlas -e GEMARC_TOOLSET=atlas -e GEMARC_WORKSPACE_ROOT=<workspace> -e GEMARC_ALLOW_WRITE=1 -e GEMARC_ATLAS_ROOTS=main=<repo> -e PYTHONUTF8=1 -- <repo>\.venv\Scripts\python.exe -m gemarc_mcp
```

接入后工具显示为 `mcp__gemarc_atlas__gemarc_atlas_put` 之类，入口提示是斜杠命令 `/mcp__gemarc_atlas__gemarc-atlas-create`。（这里的命令格式按 Claude Code 的文档写出；本目录的自动测试只验证 JSON 能解析、变量齐全，没有拉起 Claude Code，真实接入由最终验收阶段实测。）

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
2. 驱动文本让模型：读工作指令、`gemarc_atlas_init`、保存需求原文、循环 `gemarc_atlas_status` 与 `gemarc_atlas_put`，红稿用 `mode=patch` 修复，到 `review` 阶段恰好自审一次，最后写报告。
3. 随时可以用 `gemarc_atlas_status` 看进度；说"继续"就从 `status` 续上，状态都在 `<workspace>/<项目>/atlas/` 里。
4. 模型遇到只有你能决定的事项会停下来问你（`needs-user`），不会自己编造；同一个切片修复超过 3 轮或 `put` 次数用完，会报告 `blocked` 和最后一次诊断。
