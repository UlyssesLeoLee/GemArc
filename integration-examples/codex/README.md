# OpenAI Codex 接入 GemArc MCP

标记：**[RAN]** 本机实际执行过（codex-cli 0.145.0，Windows 11）；**[SOURCE]** 来自 `openai/codex` rust-v0.166.0 源码的调研结论，没在本机运行；**[UNVERIFIED]** 没运行过。注意本机版本（0.145.0）比调研读的源码（0.166.0）旧。

| 文件 | 用途 |
|---|---|
| `config.stdio.toml` | 本地 stdio（16 个工具），追加到 `~/.codex/config.toml` |
| `config.remote.toml` | 远程 Streamable HTTP（8 个只读工具），追加到 `~/.codex/config.toml` |

两个文件都用服务器名 `gemarc`，**只启用一个**。占位符：`<workspace>`（已存在的目录）、`<project>`（Vercel 子域名）。token 放在环境变量 `GEMARC_MCP_TOKEN`，不写进文件。也可以放项目里的 `.codex/config.toml`，但只有项目被信任时才生效 **[SOURCE]**。

## 配置键

- stdio：`command`、`args`、`env`、`env_vars`、`cwd`。HTTP：`url`、`bearer_token_env_var`、`http_headers`、`env_http_headers`。两组键混用会报错；内联 `bearer_token` 会被拒绝 **[SOURCE]**。
- 共用：`startup_timeout_sec`、`tool_timeout_sec`、`enabled`、`required`、`enabled_tools`、`disabled_tools`（**原始**工具名，即下划线名）、`default_tools_approval_mode`（`auto|prompt|writes|approve`）、`tools.<原始名>.approval_mode` **[SOURCE]**。
- **[RAN]** 在 0.145.0 上用 `codex exec --strict-config` 加载本目录两个文件，以及打开 `default_tools_approval_mode`、`tools.<名>.approval_mode` 后的变体，均无「未知字段」错误；`codex mcp get gemarc --json` 能读回 `enabled_tools`、`startup_timeout_sec`、`tool_timeout_sec`、`env`。
- **[RAN]** `tools.<名>.output_token_limit` 在 0.145.0 上被 `--strict-config` 判为未知字段（非严格模式下被忽略），所以两个文件里是注释状态；更新的 Codex（源码里有这个键）上遇到 `gemarc_render` 输出被截断再打开。
- 超时：源码默认 30 秒启动 / 300 秒单次调用，官方文档写 10 秒 / 60 秒，两者不一致，所以文件里显式设为 30 / 120 **[SOURCE]**。
- 服务器名必须匹配 `^[a-zA-Z0-9_:@/.-]+$` **[SOURCE]**。

## 用 `codex mcp` 添加

```powershell
# 本地 stdio（PowerShell）
codex mcp add gemarc --env GEMARC_TOOLSET=full --env GEMARC_WORKSPACE_ROOT='<workspace>' --env GEMARC_ALLOW_WRITE=0 --env PYTHONUTF8=1 '--' D:\GemArc\.venv\Scripts\python.exe -m gemarc_mcp

# 远程（先在启动 Codex 的环境里 $env:GEMARC_MCP_TOKEN = '<token>'）
codex mcp add gemarc --url 'https://<project>.vercel.app/mcp' --bearer-token-env-var GEMARC_MCP_TOKEN
```

```bash
# bash
codex mcp add gemarc --env GEMARC_TOOLSET=full --env GEMARC_WORKSPACE_ROOT='<workspace>' --env GEMARC_ALLOW_WRITE=0 --env PYTHONUTF8=1 -- D:/GemArc/.venv/Scripts/python.exe -m gemarc_mcp
codex mcp add gemarc --url 'https://<project>.vercel.app/mcp' --bearer-token-env-var GEMARC_MCP_TOKEN
```

- 以上形式在隔离的 `CODEX_HOME` 里执行过，生成的 `[mcp_servers.gemarc]` 与本目录文件同构 **[RAN]**。没有 `--header` 之类的开关；要加固定 header 请直接改 `config.toml`。`codex mcp list|get|remove` 也 **[RAN]**；`login|logout` **[SOURCE]**，本仓库不实现 OAuth，用不到。
- 在 PowerShell 里，npm 安装的 `codex.ps1` 垫片会吞掉**不加引号**的 `--`（用回显脚本验证 **[RAN]**）；在 0.145.0 上不加引号得到的 `command`/`args` 恰好相同，但写成 `'--'` 更稳。也可以改用 `codex.cmd`。
- `bearer_token_env_var` 指向的变量必须在 Codex 进程的环境里**存在且非空**，否则该 HTTP 服务器启动失败 **[SOURCE]**。服务端若没设 `GEMARC_MCP_TOKEN`（无认证），删掉 `bearer_token_env_var` 这一行即可。

## 工具名与输出

- 工具以命名空间 `mcp__gemarc` 加裸工具名暴露；名字里不属于 `[A-Za-z0-9_]` 的字符会被改成 `_`（冲突时加 sha1 后缀），合计上限 128 **[SOURCE]**。本服务器的名字已是纯下划线，不会被改写，也不会冲突。
- 结果里若带 `structuredContent`，模型看到的是它而不是 `content` **[SOURCE]**。本服务器**不返回** `structuredContent`（`scripts/verify_stdio.py` 逐次断言 **[RAN]**），所以模型看到的就是 JSON 文本。
- 单次输出约 10k token 上限（再乘 1.2）**[SOURCE]**。`gemarc_render` 对示例模型约 10 KB；内联上限 24 KiB（约 8k token）低于这个输出上限，更大的模型 `inline` 为 `false`、只返回清单（改用本地 `gemarc build`），所以通常不需要 `output_token_limit`。
- Codex 忽略 prompt（所以别指望 `gemarc-design` 提示词），只能通过内置的 list/read resource 工具访问资源；服务器的 `instructions` 作为命名空间描述 **[SOURCE]**。让模型先调 `gemarc_brief`。
- Codex 会丢掉 schema 里的 `title`/`default` **[SOURCE]**，不影响本服务器：所有参数都有默认值或必填，且没有 `null` 分支（`verify_stdio.py` 检查 **[RAN]**）。

## 审批（approval）

Codex 的 `auto` 模式：工具带 `readOnlyHint=true`，或同时 `destructiveHint=false` 且 `openWorldHint=false`，就不提示；其余提示；没有注解的工具总是提示 **[SOURCE]**。服务器当前声明（`server.create_server("full")` 读出 **[RAN]**）：

| 工具 | readOnly | destructive | 在 `auto` 下 |
|---|---|---|---|
| 11 个只读：`gemarc_brief` `gemarc_contracts` `gemarc_assets` `gemarc_new_model` `gemarc_validate` `gemarc_simulate` `gemarc_diff` `gemarc_render` `gemarc_inspect` `gemarc_plan` `gemarc_verify` | true | - | 不提示 |
| `gemarc_scaffold` `gemarc_init` `gemarc_export` | false | false | **不提示**（`openWorldHint` 也是 false） |
| `gemarc_model` `gemarc_build` | false | true | 提示 |

所以开启 `GEMARC_ALLOW_WRITE=1` 后，`scaffold/init/export` 在 `auto` 下会直接执行。想要每次写入都确认：给它们各加 `tools.<名>.approval_mode = "prompt"`（`config.stdio.toml` 里有注释好的写法），或设 `default_tools_approval_mode = "prompt"`。工作区边界（`GEMARC_WORKSPACE_ROOT`）和写开关由服务器自己强制，与 Codex 审批无关。

## Codex 插件的限制

仓库根的 `plugin.json` 带 `agent-plugins.org` 的 `$schema`，会被 Codex 当作 Agent Plugin 读取，它是旧的 `ipa-architecture-3d` skill 插件，**不含** MCP 服务器 **[SOURCE]**。Agent Plugin 的 MCP 只能来自插件内的 `./mcp.json`（`type: stdio | streamable-http`），并且 `Authorization` 一类的 header 会被**剥掉**，所以插件**无法携带静态 bearer token**：要么远程服务器无认证 / 走 OAuth，要么像本目录这样在 `config.toml` 里配置 **[SOURCE]**。Marketplace 只从 `.agents/plugins/marketplace.json` 或 `.claude-plugin/marketplace.json` 发现；`integration-examples/codex-marketplace.json` **不会被自动发现**，要用的话得复制到上述路径 **[SOURCE]**。

## Codex cloud

云端任务里能否使用自定义 MCP **[UNVERIFIED]**；调研结论是密钥会在 agent 阶段之前被清除，所以带 token 的远程配置在云端大概率不可用。别把云端当作已支持。

## 还没验证的

- **[UNVERIFIED]** 真实 Codex 会话里启动 `gemarc` 并调用工具：本机 Codex 已登录，但验证需要付费模型调用，我没有执行；`--strict-config` 只证明配置被接受。
- **[UNVERIFIED]** 用 Codex 连接真实 Vercel 端点。`GET /mcp` 返回 405、`/.well-known/oauth-*` 返回 404 JSON 已在部署上实测（`docs/09_部署/01_Vercel部署.md` 第 12 节）；Codex 怎样对待这些答复没有验证。
- **[UNVERIFIED]** Skill 安装位置：本机的 `CODEX_HOME` 下有 `skills/` 目录，我没验证 Codex 是否会发现 `skills/gemarc-5d-design`。
