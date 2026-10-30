# Hermes Agent (Nous Research) 接入 GemArc MCP

标记：**[SOURCE]** 来自 `NousResearch/hermes-agent` main 的源码与文档的调研结论；**[RAN]** 本机执行过；**[UNVERIFIED]** 没运行过。**本机没有安装 Hermes，这个目录里所有关于 Hermes 行为的陈述都是 [SOURCE] 或 [UNVERIFIED]，没有任何一条是真机运行结果。**配置文件的语法已用 PyYAML 6.0.3 解析通过 **[RAN]**，但解析通过不等于 Hermes 接受每个键。

| 文件 | 用途 |
|---|---|
| `config.stdio.yaml` | 本地 stdio（16 个工具；默认隐藏 5 个写工具），合并进 `~/.hermes/config.yaml` |
| `config.remote.yaml` | 远程 Streamable HTTP（8 个只读工具） |
| `env.example` | 放 token 的 `.env` 片段 |

两个 YAML 都用服务器名 `gemarc`，**只启用一个**。占位符：`<workspace>`（已存在的目录）、`<project>`（Vercel 子域名）、`<token>`。

## 配置位置与变量

- 配置目录是 `~/.hermes`（Windows 原生安装为 `%LOCALAPPDATA%\hermes`），`HERMES_HOME` 可覆盖；文件有 `config.yaml`、`.env`、`skills/` **[SOURCE]**。PowerShell 下查看：`$env:HERMES_HOME`、`Get-ChildItem $env:LOCALAPPDATA\hermes`。
- 配置里 `${VAR}` / `${env:VAR}` 先读 `~/.hermes/.env` 再读进程环境；`url` 或 `headers` 里引用了未设置的变量会 fail closed（该服务器不连接）**[SOURCE]**。
- **stdio 子进程拿到的是过滤后的环境**：服务器需要的每个变量都必须写在 `env:` 里 **[SOURCE]**。所以 `GEMARC_WORKSPACE_ROOT`、`GEMARC_ALLOW_WRITE`、`PYTHONUTF8` 都在 `config.stdio.yaml` 的 `env` 里显式列出。（官方 `mcp` 客户端库的 stdio 同样只继承 `PATH` 等少数变量再叠加 `env`；`scripts/verify_stdio.py` 就是这样启动服务器的 **[RAN]**。）

## 添加与测试

```bash
# 远程：交互式询问 token，写入 .env 的 MCP_<NAME>_API_KEY，并把 Authorization: Bearer ${MCP_<NAME>_API_KEY} 写进 config.yaml
hermes mcp add gemarc --url https://<project>.vercel.app/mcp

hermes mcp list
hermes mcp test gemarc        # 先 HEAD，405/501 再 GET，最后 POST initialize
hermes mcp configure gemarc   # 交互式选择要暴露的工具
hermes mcp remove gemarc
# 会话里改完配置：/reload-mcp
```

- `hermes mcp add NAME --command CMD --args ...` 也存在 **[SOURCE]**，但 `-m` 这类以 `-` 开头的参数能否经由 `--args` 传入我没验证 **[UNVERIFIED]**；本地 stdio 建议直接编辑 `config.yaml`。
- `config.remote.yaml` 用 `GEMARC_MCP_TOKEN` 而不是 `hermes mcp add` 生成的 `MCP_<NAME>_API_KEY`：两者等价，只是少一个变量名要记。
- 预检会请求你配置的**确切** URL：必须是 `/mcp`。一个返回 HTML 的 200（比如把站点根 `/` 当 MCP 地址）会预检失败；401、405 能通过 **[SOURCE]**。

## 过滤工具

`tools.include` / `tools.exclude` 是对**原始工具名**的 fnmatch 通配，`include` 优先 **[SOURCE]**：

```yaml
    tools:
      include: ["gemarc_brief", "gemarc_new_model", "gemarc_valid*", "gemarc_sim*", "gemarc_render"]
```

按这个例子只暴露 5 个无状态工具。`config.stdio.yaml` 里用的是 `exclude` 隐藏 5 个写工具。

## 必须注意

- **不要用 `cmd` / `powershell` / `bash` 包一层再去启动服务器**，更不要在内联脚本里放 `curl` / `wget`：Hermes 会拒绝这类 `command` **[SOURCE]**。`command` 直接写 Python 可执行文件，`args` 写 `["-m", "gemarc_mcp"]`。
- **不要给同一个工具暴露两个别名**。Hermes 把工具名里不属于 `[A-Za-z0-9_]` 的字符改成 `_`，规范化后重名的工具**两个都会被丢弃**，超过 64 字符会截断加哈希 **[SOURCE]**。本服务器的工具名本来就是下划线名（最长 `mcp__gemarc__gemarc_new_model`，29 字符），不会冲突；但**不要**同时注册两个规范化后同名的服务器（例如 `gemarc.local` 与 `gemarc_local`），也不要在本服务器前再套一层改名代理。这也是工具名从点号改成下划线、且不保留旧别名的原因（见 `docs/05_项目管理/04_配置变更与ADR.md` 的 ADR-008）。
- 工具报错（`isError`）会变成 `tool_error`，形似凭证的文本（`token=…`、`Bearer …`）会被打码 **[SOURCE]**；本服务器的错误 JSON 里不放这类文本。
- 服务器同时返回 `content` 与 `structuredContent` 时 Hermes 会去重，并忽略 `outputSchema` **[SOURCE]**；本服务器不返回 `structuredContent`，也没有 `outputSchema`。
- 单次结果超过 50 000 字符会被写入文件 **[SOURCE]**。`gemarc_render` 对示例模型约 10 KB；内联上限是 24 KiB，按此上限转义后通常仍低于 50 000 字符，所以一般不会触发写文件 **[UNVERIFIED]**（没装 Hermes，未实测）。
- Hermes 会对可空 `anyOf` 做清理 **[SOURCE]**；本服务器的输入 schema 没有 `null` 分支（`verify_stdio.py` 检查 **[RAN]**）。
- `hermes-agent[mcp]` 固定 `mcp==2.3.0` **[SOURCE]**，与本仓库的 SDK 版本一致（`.venv` 里是 2.3.0 **[RAN]**）。

## 安装 Skill

`skills/gemarc-5d-design/` 符合 agentskills.io 规则（`name` 与目录名一致、`description` ≤ 1024 字符、`SKILL.md` < 500 行；`tests/test_tool_surface_docs.py` 检查 **[RAN]**）。Hermes 的 skill 目录是 `$HERMES_HOME/skills/<name>/SKILL.md` **[SOURCE]**：

```powershell
Copy-Item -Recurse D:\GemArc\skills\gemarc-5d-design "$env:LOCALAPPDATA\hermes\skills\"
```

或者仓库放到 GitHub 后：`hermes skills install <owner>/<repo>/skills/gemarc-5d-design` **[SOURCE]**。Hermes 是否真的发现并使用这个 skill **[UNVERIFIED]**。

## 还没验证的

- **[UNVERIFIED]** Hermes 能否连接、列出并调用本服务器的任何工具（没装 Hermes）。
- **[UNVERIFIED]** Hermes 的 HEAD / GET / POST 预检序列对 `/mcp` 的实际表现：GET / HEAD 返回 405 并带 `Allow: POST` 已在部署上实测（`docs/09_部署/01_Vercel部署.md` 第 12 节），Hermes 怎样对待它没有验证。
