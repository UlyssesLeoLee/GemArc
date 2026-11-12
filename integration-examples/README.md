# GemArc MCP 接入示例

把 GemArc MCP 服务器接到 Claude（Claude Code、Claude Desktop、claude.ai）、OpenAI Codex、Hermes Agent、MiniMax Code。每个客户端一个子目录，里面的文件可以直接复制。

## 先看这三条标记

- **RAN**：我在这台机器上实际执行过（写明了版本）。
- **DOCS / SOURCE**：来自官方文档 / 开源客户端源码的调研结论，**没有**在本机运行。
- **UNVERIFIED**：没有运行过真实客户端，也没有可引用的结论。

本机装了 Claude Code 2.1.318 和 codex-cli 0.145.0；**没有** Claude Desktop、Hermes Agent、`uv`。远程端点（Vercel）已于 2026-10-30 部署，并用官方 `mcp` 客户端库实测了握手与工具调用（`scripts/verify_remote.py`，见 `docs/09_部署/01_Vercel部署.md` 第 12 节）；**Claude Code 连真实远程端点也已于 2026-11-10 实测**（`claude mcp add` → `√ Connected` → 真调 `mcp__gemarc__gemarc_brief`，见 `claude-code/README.md`）；但下面这些客户端产品里 claude.ai / Desktop / Hermes / Codex 仍没有连过它，客户端行为的结论继续按各自的标记（DOCS / SOURCE / UNVERIFIED）看待。

## 兼容矩阵

| 客户端 | stdio（本地） | 远程 + Bearer | 远程 无认证 / OAuth | tools | resources | prompts | skills |
|---|---|---|---|---|---|---|---|
| Claude Code | **RAN**：配置展开、启动服务器、调用成功 | **RAN**（`--header` + `claude mcp add` + 连上生产端点 + 真调 `mcp__gemarc__gemarc_brief`）；没令牌时客户端会把服务端的 `hint` 显示出来 | 无认证 DOCS；OAuth **v3.4 起本仓库实现**（ADR-020），客户端侧未验 | **RAN**（stdio 调 `gemarc_new_model`） | DOCS（`@gemarc:gemarc://registry`） | DOCS（`/mcp__gemarc__gemarc-media-doc`） | UNVERIFIED |
| Claude Desktop | UNVERIFIED（配置 JSON 语法 RAN） | UNVERIFIED（静态 header 仅 beta，DOCS） | 无认证 DOCS | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| claude.ai / API connector | 不适用 | UNVERIFIED（静态 header 仅 beta，DOCS） | 无认证 DOCS；API connector 仅 tools、token 须为 OAuth（DOCS） | UNVERIFIED | UNVERIFIED（API connector 不支持，DOCS） | UNVERIFIED（API connector 不支持，DOCS） | UNVERIFIED |
| OpenAI Codex | 配置被 `--strict-config` 接受、`codex mcp add/list/get/remove` **RAN**；启动服务器 UNVERIFIED | SOURCE（`bearer_token_env_var`）；配置解析 **RAN** | SOURCE | SOURCE | SOURCE（仅内置 list/read 工具） | SOURCE（被忽略） | UNVERIFIED |
| Hermes Agent | SOURCE | SOURCE | SOURCE | SOURCE | SOURCE | SOURCE | SOURCE（目录）；发现 UNVERIFIED |
| MiniMax Code | **RAN**：用户级 `~/.minimax/mcp.json` 里的 stdio 条目可用（本会话里 `gemarc_local` 的工具一直在用） | **RAN**：静态 `Authorization` 头 → 鉴权状态 `static`，生产 `POST /mcp` 的 `initialize` 返回 200；**不展开** `${变量名}` 引用，令牌只能写明文；仓库自带导入命令 `python -m gemarc_mcp.clients minimax install` | **RAN**（服务端元数据与端点，ADR-020）；客户端侧的动态注册 + PKCE **UNVERIFIED** | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| 官方 `mcp` 客户端库（协议层基线） | **RAN**：`scripts/verify_stdio.py`，17 个工具 / 4 个资源 / 1 个提示词全部通过 | 未测 | 未测 | **RAN** | **RAN** | **RAN** | 不适用 |

“协议层基线”那一行说明服务器本身符合协议、字段合法；它**不能**替代各客户端自己的行为（审批、名字改写、输出截断）。

## 我该用哪种模式

1. **在自己的机器上、仓库就在磁盘上、需要把模型写进工作区** → 本地 stdio，`full` 工具集（17 个）。Claude Code / Codex / Hermes 都行。写工具默认关，`GEMARC_ALLOW_WRITE=1` 才开。
2. **不想在每台机器装环境、只做「读仓库 → 填模型 → 校验 → 模拟 → 取回站点数据」** → 远程 `stateless` 工具集（9 个，全只读，没有工作区）。Claude Code / Codex / Hermes 用 Bearer token；claude.ai / Desktop 需要部署**不设** token（见 `claude-desktop/README.md` 的取舍）。
3. **模型很大**（`gemarc_render` 派生文件合计超过约 35 KiB，或单次输出超过客户端上限）→ 走本地：`gemarc_build`，或命令行 `gemarc build <project> --profile static`。
4. **只想试一下** → 本地 stdio，`GEMARC_ALLOW_WRITE=0`，只用只读工具。
5. **不想手工保管令牌**（而且客户端只会说 OAuth）→ 用 **OAuth 通道**：配置里不写任何头，客户端自己走「发现 → 动态注册 → Google 登录 → 同意」。**只对开了账号模式的部署有效**（v3.4 起，ADR-020）；令牌通道永远可用，是默认值。

## 占位符（全部在这里）

| 占位符 | 含义 |
|---|---|
| `<project>` | 你的 Vercel 项目子域名，远程地址是 `https://<project>.vercel.app/mcp` |
| `<workspace>` | 本地工作区，必须是**已存在**的目录；`gemarc_inspect` 只能扫描它里面的目录，写入也落在这里 |
| `<token>` | 远程 Bearer token。**客户端**放环境变量 `GEMARC_MCP_PAT`（账号制部署里是你自己的个人访问令牌），**服务端**的环境变量叫 `GEMARC_MCP_TOKEN`（未开账号部署的共享令牌）；令牌只放环境变量或 `.env`，不要写进配置文件 |
| `<python>` | 跑本地 stdio 服务器的解释器（`python -m gemarc_mcp`）；MiniMax Code 的示例里用它，因为那个客户端没有自己的添加命令 |
| `D:\GemArc` | 本仓库的检出路径（不是尖括号，但示例里写死了，换成你的） |

## 令牌：从哪来、装的时候怎么设、装完以后怎么改

远程端点有两种形态，**令牌的名字与来源不同**，不要混：

| | 未开账号的部署 | 账号制部署（本仓库现在的生产形态） |
|---|---|---|
| 服务端认什么 | 环境变量 `GEMARC_MCP_TOKEN`（可逗号分隔多个，便于轮换） | 账号页签发的个人访问令牌（PAT）；`pat-or-env` 模式仍兼容环境令牌 |
| 令牌从哪来 | 运营者给的共享令牌 | **登录账号页 → 创建令牌**（明文只显示一次，离开页面就看不到） |
| 客户端怎么填 | 客户端环境变量 `GEMARC_MCP_PAT` 里放那个共享令牌 | 客户端环境变量 `GEMARC_MCP_PAT` 里放**你自己**的 PAT，或写进客户端的 `Authorization: Bearer` 头 |

**同名不同侧**：`GEMARC_MCP_TOKEN` 是**服务端**的变量名，`GEMARC_MCP_PAT` 是**客户端**的变量名。本目录的示例原来两边都用 `GEMARC_MCP_TOKEN`，现在客户端侧统一改成 `GEMARC_MCP_PAT`（与账号页给出的配置一致）；**已经照旧示例配好的用户不用改**——服务端不校验客户端环境变量的名字。

**装的时候怎么设**：令牌放进客户端的**环境变量**，配置文件里只写变量名（令牌不落进文件）：

| 客户端 | 安装（令牌先放进环境变量，再执行添加命令） |
|---|---|
| Claude Code | `$env:GEMARC_MCP_PAT = '<token>'` → `claude mcp add --transport http --scope project gemarc 'https://<project>.vercel.app/mcp' --header 'Authorization: Bearer ${GEMARC_MCP_PAT}'` |
| Codex | `$env:GEMARC_MCP_PAT = '<token>'` → `codex mcp add gemarc --url 'https://<project>.vercel.app/mcp' --bearer-token-env-var GEMARC_MCP_PAT` |
| Hermes | `GEMARC_MCP_PAT=<token>` 写进 `~/.hermes/.env` → YAML 里写 `Authorization: "Bearer ${GEMARC_MCP_PAT}"` |
| MiniMax Code | 不支持环境变量引用：令牌**直接写进**用户级 `~/.minimax/mcp.json` 的 `Authorization` 头，步骤见 `minimax-code/README.md`；也可以用仓库自带的导入命令 `python -m gemarc_mcp.clients minimax install --url https://<project>.vercel.app/mcp --token-env GEMARC_MCP_PAT`（令牌不经过终端） |

**装完以后怎么改**：改环境变量里的值并**重启客户端**（客户端只在启动时读一次）；配置里写着令牌明文的，删掉那个条目再重新添加一次。也可以在**控制台**里粘贴令牌：它只存在那个标签页的 `sessionStorage` 里，关掉标签页就没了。

**连不上时看哪里**：账号制部署的 `POST /mcp` 在 401 响应体里带一句 `hint`，写明令牌在哪拿、填到哪。**所有失败原因同一句**（没带、带错、过期、被撤销一律同形），它不会告诉你令牌是过期还是写错。

## stdio 启动命令

| 形式 | 状态 |
|---|---|
| `D:\GemArc\.venv\Scripts\python.exe -m gemarc_mcp` | **RAN**，示例文件都用它；`pip install -e .` 之后在任意工作目录都能跑 |
| `D:\GemArc\.venv\Scripts\gemarc-mcp.exe`（控制台脚本） | **RAN**（`verify_stdio.py --toolset stateless --command …\gemarc-mcp.exe`；`--command` 吃掉它后面的所有参数，必须放在最后） |
| `uv run --directory D:\GemArc gemarc-mcp` | UNVERIFIED：本机没有 `uv`（`uv --version` 找不到命令）。`codex mcp add` 能把这种参数形式写进配置 **RAN**，但没有启动过 |
| macOS / Linux：`<repo>/.venv/bin/python -m gemarc_mcp` | UNVERIFIED（没在这些系统上跑过） |

### stdio 的 env 块

| 变量 | 取值 | 说明 |
|---|---|---|
| `GEMARC_TOOLSET` | `full` | 本地默认就是 `full`；远程部署默认 `stateless` |
| `GEMARC_WORKSPACE_ROOT` | `<workspace>` | 不设则用 `~/.gemarc/workspace`（自动创建）；设了就必须已存在 |
| `GEMARC_ALLOW_WRITE` | `0` 或 `1` | 服务器只认 `1` |
| `PYTHONUTF8` | `1` | 见下 |

**`PYTHONUTF8=1` 实测结论**：在本机（默认代码页 GBK / cp936）去掉它，`scripts/verify_stdio.py --toolset full --allow-write --no-utf8` 同样通过（含中文的 `gemarc_build` / `gemarc_export`），中文 instructions 与结果也完整往返 **RAN**。`src/` 里所有文本模式的文件读写都显式写了 `encoding=`（AST 扫描 **RAN**）。所以它**不是必需的**，只是防御性设置（保护 stderr 日志和将来任何漏写 `encoding=` 的地方）；我没有在别的代码页下复现过失败。保留它没有副作用。

**Hermes 要特别注意**：Hermes 给 stdio 子进程的是过滤后的环境 **SOURCE**，所以每个变量都必须显式写在 `env` 里；官方 `mcp` 客户端库同样只继承 `PATH` 等少数变量 **RAN**，示例文件因此一律显式列全。

## 排错

- `GEMARC_WORKSPACE_ROOT` 指向的目录**不存在**、是文件、或示例里的 `<workspace>` 没改：所有项目类工具（`gemarc_inspect`、`gemarc_plan`、`gemarc_scaffold`……）都返回 `status: "invalid"` 与 `GEMARC_AUTH_001`（「GEMARC_WORKSPACE_ROOT 必须是现有目录」）。先创建目录。**RAN**（进程内实测）。
- 不设 `GEMARC_WORKSPACE_ROOT` 时用 `~/.gemarc/workspace`，服务器会自动创建。
- 写工具返回 `GEMARC_AUTH_002`：`GEMARC_ALLOW_WRITE` 不是 `1`，这是故意的默认值。

## 目录

| 目录 | 文件 |
|---|---|
| `claude-code/` | `.mcp.json`、`settings.example.json`、`README.md` |
| `codex/` | `config.stdio.toml`、`config.remote.toml`、`README.md` |
| `hermes/` | `config.stdio.yaml`、`config.remote.yaml`、`env.example`、`README.md` |
| `minimax-code/` | `mcp.json.example`、`README.md` |
| `claude-desktop/` | `claude_desktop_config.json`、`README.md` |
| `codex-marketplace.json` | 旧的 skill 插件市场清单，Codex 不会自动发现，见 `codex/README.md` |

## 自检

```bash
# 在任意目录运行：用「客户端的方式」启动服务器（显式 env、filtered 环境、干净的工作目录）并逐项检查
python scripts/verify_stdio.py --toolset stateless
python scripts/verify_stdio.py --toolset full --allow-write
python scripts/verify_stdio.py --no-utf8 --allow-write          # 不带 PYTHONUTF8
python scripts/verify_stdio.py --prove-detector                 # 证明「stdout 里混入杂行」会被判 FAIL
```

## 工具名约定

工具名一律是 `gemarc_` 加小写下划线单词（如 `gemarc_validate`），符合 Claude API 与 OpenAI 函数名的字符集 `[a-zA-Z0-9_-]{1,64}`；加上客户端前缀后最长的 `mcp__gemarc__gemarc_new_model` 是 29 字符（`verify_stdio.py` 检查 **RAN**）。旧的点号形式工具名已不存在，也**没有保留别名**：Codex 与 Hermes 会改写点号，Hermes 对改写后重名的工具两个都丢掉。理由见 `docs/05_项目管理/04_配置变更与ADR.md` 的 ADR-008。
