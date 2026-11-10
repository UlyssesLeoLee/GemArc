# Claude Code 接入 GemArc MCP

标记：**[RAN]** 本机实际执行过（Claude Code 2.1.318，Windows 11）；**[DOCS]** 来自官方文档 / 源码的调研结论，没在本机运行；**[UNVERIFIED]** 没运行过，也没有可引用的结论。

本目录文件：

| 文件 | 用途 |
|---|---|
| `.mcp.json` | 项目级配置，含远程 `gemarc`（HTTP）和本地 `gemarc_local`（stdio）两个条目，复制到项目根目录 |
| `settings.example.json` | 只读工具的权限白名单示例，合并进 `.claude/settings.json` |

占位符（`<尖括号>`）：`<project>` Vercel 项目子域名；`<workspace>` 一个**已存在**的工作区目录；token 放在环境变量 `GEMARC_MCP_PAT` 里，**不要**写进文件。

## 令牌从哪来

| 部署形态 | 令牌从哪来 | 客户端环境变量 |
|---|---|---|
| 账号制（本仓库现在的生产形态） | 登录 `https://<project>.vercel.app/account` → 「创建令牌」→ **明文只显示这一次** | `GEMARC_MCP_PAT` = 你自己的个人访问令牌 |
| 未开账号 | 运营者给的共享令牌 | `GEMARC_MCP_PAT` = 那个共享令牌 |

服务端的环境变量 `GEMARC_MCP_TOKEN` 是**另一侧**的事（未开账号部署的共享令牌）；本目录的示例文件原来客户端侧也用它，现在统一改成 `GEMARC_MCP_PAT`（与账号页给出的配置一致）。**已经照旧示例配好的用户不用改**：服务端不校验客户端环境变量的名字。

**装完以后怎么改**：改环境变量里的值并**重启 Claude Code**（客户端只在启动时读一次）；`.mcp.json` 里写着令牌明文的，删掉那个条目再重新 `claude mcp add` 一次。也可以在**控制台**（浏览器里的 `https://<project>.vercel.app/`）粘贴令牌，它只存在那个标签页的 `sessionStorage` 里。

## 选哪个

- **远程**（`gemarc`）：9 个无状态工具，模型内联传入；没有工作区，不能写盘。部署见 `docs/09_部署/01_Vercel部署.md`。
- **本地**（`gemarc_local`）：17 个工具，项目存在 `<workspace>` 里；写工具默认关闭，`GEMARC_ALLOW_WRITE=1` 才开。
- 两个同时启用会看到两套同名工具（前缀不同），容易混淆，建议只留一个。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `GEMARC_URL` | 远程 MCP 地址（含 `/mcp`） | `.mcp.json` 里的默认值 |
| `GEMARC_MCP_PAT` | 远程 Bearer token（只放环境变量）。客户端缺这个变量时，`.mcp.json` 里的 `gemarc` 条目会报 `Missing environment variables` 警告 **[RAN]**；服务端没设 token（无认证）时，可以删掉 `headers` | 无 |
| `GEMARC_WORKSPACE_ROOT` | 本地工作区，**必须已存在**；`gemarc_inspect` 只能扫描它里面的目录 | `.mcp.json` 里的 `<workspace>` |
| `GEMARC_ALLOW_WRITE` | `1` 才允许 `gemarc_scaffold/init/model/build/export` | `0` |

不要用 `ANTHROPIC_*`、`AWS_BEARER_TOKEN_BEDROCK`、`HTTPS_PROXY`、`NPM_TOKEN` 传 token：这几个在远程 `url`/`headers` 里读作空值 **[DOCS]**。

## 用 `claude mcp add` 添加

**要点 1（[RAN]）**：`--scope project` 会把配置写进 `.mcp.json`。header 里的 token 要用**单引号**保留 `${GEMARC_MCP_PAT}` 引用；用双引号或不加引号，shell 会在添加时展开，token 明文就写进 `.mcp.json` 并随仓库提交了。
**要点 2（[RAN]）**：`--env`/`-e` 可重复，但紧挨服务器名之前的 `-e` 会把名字吞掉（`Invalid environment variable format: <name>`）。把服务器名放在最前，选项在后，`--` 后面才是启动命令。

### PowerShell

```powershell
# 远程（需要先 $env:GEMARC_MCP_PAT = '<token>'；服务端未设 token 时可去掉 --header）
claude mcp add --transport http --scope project gemarc 'https://<project>.vercel.app/mcp' --header 'Authorization: Bearer ${GEMARC_MCP_PAT}'

# 本地 stdio（用户级；先 New-Item -ItemType Directory <workspace>）
claude mcp add gemarc --scope user --transport stdio -e GEMARC_WORKSPACE_ROOT='<workspace>' -e GEMARC_ALLOW_WRITE=0 -e PYTHONUTF8=1 -- D:\GemArc\.venv\Scripts\python.exe -m gemarc_mcp
```

PowerShell 5.1 对原生 exe（`claude.exe`）原样传递 `--` **[RAN]**。

### bash（含 Git Bash）

```bash
export GEMARC_MCP_PAT='<token>'
claude mcp add --transport http --scope project gemarc 'https://<project>.vercel.app/mcp' --header 'Authorization: Bearer ${GEMARC_MCP_PAT}'

mkdir -p '<workspace>'
claude mcp add gemarc --scope user --transport stdio -e GEMARC_WORKSPACE_ROOT='<workspace>' -e GEMARC_ALLOW_WRITE=0 -e PYTHONUTF8=1 -- D:/GemArc/.venv/Scripts/python.exe -m gemarc_mcp
```

要开启写入，把 `GEMARC_ALLOW_WRITE=0` 改成 `1`。macOS/Linux 把命令换成 `<repo>/.venv/bin/python`。

管理：`claude mcp list`、`claude mcp get gemarc`、`claude mcp remove gemarc --scope project`；会话里用 `/mcp`。

## 批准与权限

- 项目里的 `.mcp.json` 服务器第一次要用户批准：`claude mcp list` 显示 `Pending approval (run claude to approve)` **[RAN]**。
- 工具名是 `mcp__<服务器名>__<工具名>` **[DOCS]**。服务器名用 `gemarc`（没有点、连字符、`__`），工具名即 `mcp__gemarc__gemarc_validate`。
- `settings.example.json` 只放行**只读**工具，按 `.mcp.json` 里的两个服务器名分开写：远程 `gemarc`（`mcp__gemarc__` 下 9 个无状态工具，全是只读）和本地 `gemarc_local`（`mcp__gemarc_local__` 下 12 个只读工具）。如果你只用其中一个，删掉另一组；如果用 `claude mcp add` 把本地服务器也命名成 `gemarc`，就只保留 `mcp__gemarc__` 下的 12 个只读名。**不要**写 `mcp__gemarc__*` 或 `mcp__gemarc_local__*`，那会把 `gemarc_scaffold/init/model/build/export` 也一并放行。
- 权限规则的写法：**[RAN]** 用 `claude -p ... --allowedTools mcp__gemarc_local__gemarc_new_model` 调用该工具，没有权限拒绝（`permission_denials` 为空）；这证明了命令行 `--allowedTools` 的精确名形式。写进 `settings.json` 的 `permissions.allow` 后的匹配 **[UNVERIFIED]**，我没跑。
- 本地工作区目录不存在（或 `<workspace>` 占位符没改）时，所有项目类工具返回 `GEMARC_AUTH_001`（「GEMARC_WORKSPACE_ROOT 必须是现有目录」），先创建目录。
- 这些工具都声明了 `readOnlyHint`，只读工具允许并行调用 **[DOCS]**。

## 提示与资源

- 提示词 `gemarc-media-doc` 在会话里显示为 `/mcp__gemarc__gemarc-media-doc` **[DOCS]**。
- 资源 `gemarc://registry`、`gemarc://schema/model`：输入 `@gemarc:gemarc://registry` 引用 **[DOCS]**。
- 输出上限：超过 10k token 告警，`MAX_MCP_OUTPUT_TOKENS` 默认 25k；服务端 instructions 与工具描述在 4096 字符处截断 **[DOCS]**。实测 `gemarc_render` 对示例模型返回 40,865 字节（11,732 token），`gemarc_brief()` 约 10 KB（见 `scripts/verify_stdio.py` 输出）。

## 已验证到哪一步

- **[RAN]** `claude -p ... --mcp-config integration-examples/claude-code/.mcp.json --strict-mcp-config` 在干净目录里：Claude Code 展开了 `${GEMARC_WORKSPACE_ROOT:-…}`，启动了 `gemarc_local`（stdio），并成功调用 `mcp__gemarc_local__gemarc_new_model`（`status` = `ok`，无权限拒绝）。
- **[RAN]** 缺少令牌变量时 `claude mcp list` 只对 `gemarc` 条目报 `Missing environment variables: <变量名>` 警告，`gemarc_local` 不受影响（实测当时那个变量名是 `GEMARC_MCP_TOKEN`；本目录现在的示例用 `GEMARC_MCP_PAT`，警告里出现的就是配置里写的那个名字）。
- **[RAN]（2026-11-10 实测，生产端点 + 账号制的个人访问令牌）** Claude Code 连真实的 Vercel 远程端点成功：
  - `claude mcp add --transport http --scope user gemarc 'https://gem-arc.vercel.app/mcp' --header 'Authorization: Bearer ${GEMARC_MCP_PAT}'` → 写入 `~/.claude.json`（**user 作用域**，不会碰仓库里的 `.mcp.json`）；
  - `claude mcp list` → `gemarc: https://gem-arc.vercel.app/mcp (HTTP) - √ Connected`；
  - `claude -p "…mcp__gemarc__gemarc_brief…" --allowedTools mcp__gemarc__gemarc_brief` → **真的调到了生产服务器并拿回工作指令正文**（工具名带服务器前缀 `mcp__gemarc__`；服务器名用 `gemarc`，不要用点号）。
- **[RAN]（2026-11-10 实测）没设令牌变量时，Claude Code 自己就把服务端的提示显示出来**（这是账号制部署的 401 `hint`，ADR-018）：

  ```
  gemarc: https://gem-arc.vercel.app/mcp (HTTP) - × Failed to connect — Server rejected the configured
  Authorization header (HTTP 401). … Error detail: {"error":"unauthorized","hint":"访问令牌在账号页签发:打开
  https://gem-arc.vercel.app/account 创建个人访问令牌,再放进客户端的环境变量 GEMARC_MCP_PAT…"}
   └ [Warning] [gemarc] mcpServers.gemarc: Missing environment variables: GEMARC_MCP_PAT
  ```

  ⇒ **不用去翻文档**：连不上时，客户端的报错里就写着令牌从哪拿、填到哪。（未开账号的部署没有这句 `hint`，那是另一回事。）
- **[UNVERIFIED]** Skill 安装位置：本机存在 `~/.claude/skills/` 目录，但我没验证 Claude Code 如何发现 `skills/gemarc-media-doc`。

## claude.ai 与 Claude Desktop（远程）

claude.ai / Desktop 的远程 connector 在 **Settings > Connectors** 里添加，认证只有 OAuth、无认证、或（部分组织的 beta）静态 header **[DOCS]**。本仓库未实现 OAuth；用 claude.ai 连接需要服务端不设 `GEMARC_MCP_TOKEN`（无认证）。取舍见 `../claude-desktop/README.md`。API 的 MCP connector 只支持 tools，且 `authorization_token` 文档说明是 OAuth access token **[DOCS]**。

## 插件注意

仓库里的 `.claude-plugin/` 是旧的 `ipa-architecture-3d` skill 插件，**不包含**本 MCP 服务器。想把 MCP 打进插件，需要自己在插件根目录加 `.mcp.json`（或 `plugin.json` 的 `mcpServers`），插件里的工具名会变成 `mcp__plugin_<插件名>_<服务器名>__<工具名>` **[DOCS]**，白名单规则要相应改写。本目录没有做这件事，也没有验证。

## 自检

```bash
python scripts/verify_stdio.py --toolset full --allow-write   # 在任意目录运行，不依赖仓库为当前目录
```
