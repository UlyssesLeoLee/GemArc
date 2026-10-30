# Claude Desktop 与 claude.ai 接入 GemArc MCP

标记：**[DOCS]** 来自官方文档的调研结论；**[RAN]** 本机执行过；**[UNVERIFIED]** 没运行过。**本机没有安装 Claude Desktop（`%APPDATA%\Claude` 不存在），本目录对 Desktop / claude.ai 的陈述没有一条是真机运行结果。**

## 两条完全不同的路

| | 本地 stdio | 远程 connector |
|---|---|---|
| 在哪里配置 | `claude_desktop_config.json` | **Settings > Connectors**（不是 `claude_desktop_config.json`）**[DOCS]** |
| 工具集 | 16 个（`GEMARC_TOOLSET=full`） | 8 个无状态工具（部署默认） |
| 认证 | 无（本机进程） | OAuth、无认证，或静态 header（beta，部分组织才有）**[DOCS]** |
| 本仓库支持 | 是 | 仅「无认证」；**OAuth 没有实现** |

## 本地 stdio

1. 把本目录的 `claude_desktop_config.json` 合并进 Desktop 的配置文件（Windows 一般在 `%APPDATA%\Claude\claude_desktop_config.json`，位置 **[UNVERIFIED]**）。
2. 把 `<workspace>` 换成**已存在**的目录，它必须包含要扫描的仓库；`D:\\GemArc` 换成你的仓库路径（JSON 里反斜杠要写两遍）。
3. 默认 `GEMARC_ALLOW_WRITE` 是 `"0"`：写工具会返回 `GEMARC_AUTH_002`。要让模型写入工作区改成 `"1"`。
4. 完全退出并重启 Desktop。
5. `env` 里的四个变量要全部保留：`GEMARC_WORKSPACE_ROOT`、`GEMARC_ALLOW_WRITE`、`PYTHONUTF8`、`GEMARC_TOOLSET`。Desktop 对子进程环境的处理方式没有验证 **[UNVERIFIED]**，显式写全最稳。

这个启动方式（绝对路径的 venv Python、`-m gemarc_mcp`、显式 env、非仓库的工作目录）我用官方 `mcp` 客户端库跑通了 **[RAN]**：`scripts/verify_stdio.py --toolset full --allow-write` 47/47 通过。Desktop 本身启动它是否成功 **[UNVERIFIED]**。

## 远程：claude.ai 与 Desktop 的 Settings > Connectors

claude.ai 和 Desktop 的远程 connector 只支持三种认证 **[DOCS]**：OAuth、无认证、静态 header（beta，部分组织）。本仓库的远程端点只有「可选 Bearer token」：

- `GEMARC_MCP_TOKEN` **未设置** → 端点无认证。claude.ai / Desktop 可以直接填 `https://<project>.vercel.app/mcp` 使用。
- `GEMARC_MCP_TOKEN` **已设置** → 端点要求 `Authorization: Bearer ...`。claude.ai / Desktop 除非你的组织开了静态 header 的 beta，否则**连不上**；而这个静态 header 路径我没有验证 **[UNVERIFIED]**。

所以要在 claude.ai / Desktop 里用远程端点，就必须让部署跑在无认证模式（Vercel 项目里不要设 `GEMARC_MCP_TOKEN`）。这是一个真实的取舍，请你自己决定：

- 代价：知道 URL 的任何人都能调用这 8 个工具。它们是纯计算（`stateless` 工具集：没有工作区、不读写文件、服务端不保存任何东西），没有数据可泄露，但会消耗你的 Vercel 函数额度，可能被滥用或刷量。URL 隐蔽不算访问控制。
- 缓解（都没有在本仓库验证）：给 Vercel 项目加用量告警；如果只给自己的 Claude Code / Codex / Hermes 用，就设 token，不要给 claude.ai 用。
- 彻底的办法是在服务端实现 OAuth，**本仓库没有做**，也没有计划内的实现。

另外，Claude API 的 MCP connector 只支持 tools（不含 resources、prompts），其 `authorization_token` 文档说明是 OAuth access token，所以静态 Bearer token 不能直接填进去 **[DOCS]**。

## prompt、资源与 skill

- 提示词 `gemarc-design`、资源 `gemarc://registry`、`gemarc://schema/model` 在 Desktop 里的呈现方式没有验证 **[UNVERIFIED]**。让模型先调用 `gemarc_brief` 是不依赖它们的做法。
- `skills/gemarc-5d-design` 在 Desktop / claude.ai 里的安装方式没有验证 **[UNVERIFIED]**。
