# MiniMax Code 接入 GemArc MCP

标记：**[RAN]** 本机实际执行过（MiniMax Code 桌面版，Windows 11，2026-11-12）；**[SOURCE]** 结论来自本机安装包里的实现代码；**[UNVERIFIED]** 没有验证过。

本目录文件：

| 文件 | 用途 |
|---|---|
| `mcp.json.example` | 用户级配置示例（复制到 `~/.minimax/mcp.json` 后按需合并） |

占位符（`<尖括号>`）：`<project>` 是你的 Vercel 项目子域名；`<token>` 是你的个人访问令牌。

## 两条通道：令牌（稳）与 OAuth（新）

| 通道 | 配置里写什么 | 客户端做什么 | 需要什么 |
|---|---|---|---|
| **令牌**（推荐） | 静态 `Authorization: Bearer <token>` 头 | 跳过 OAuth，直接带凭据 | 一个个人访问令牌 |
| **OAuth** | **什么都不写**（只有 `type` 与 `url`） | 走自己的 OAuth 流程：发现 → 动态注册 → 跳转 Google 登录 → 同意 → 换令牌 | 部署开了账号模式（**v3.4 起**，ADR-020） |

两条通道互不通用，但都连同一个 `/mcp`。

**为什么以前点「授权」必然失败**：MiniMax Code 的 MCP「授权」按钮走的是 **OAuth 2.1 的发现与动态注册**。当时的 GemArc 只有**个人访问令牌**一条通道：服务器上没有 `/.well-known/oauth-protected-resource`、没有授权服务器信息、没有 `/authorize` 与 `/token`，所以那个按钮必然报「**未找到该服务器的授权配置**」。**这是协议不匹配，不是配置写错**。

**现在（v3.4 起）**：账号模式的部署会提供 OAuth 元数据与端点（设计第 3.16 节、ADR-020），那个按钮有可能直接走通；但它能否完成**动态客户端注册**与 **PKCE S256** 仍待实测。令牌通道不受这些影响，**任何时候都可用**，所以它是默认值。

## 配置

用户级配置文件：`~/.minimax/mcp.json`（Windows：`C:\Users\<你>\.minimax\mcp.json`）。它对这台机器上的所有项目生效。

```json
{
  "mcpServers": {
    "gemarc": {
      "type": "http",
      "url": "https://<project>.vercel.app/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      },
      "enabled": true
    }
  }
}
```

OAuth 通道把 `headers` 整块删掉即可：

```json
{
  "mcpServers": {
    "gemarc": {
      "type": "http",
      "url": "https://<project>.vercel.app/mcp",
      "enabled": true
    }
  }
}
```

要点：

- **令牌只能写明文**。这个客户端不像 Claude Code 那样展开 `${GEMARC_MCP_PAT}` 这样的引用：配置里的头是原样交给服务器的 **[SOURCE]**。所以令牌会落在配置文件里，文件本身只受你的账户权限保护。
- **令牌从哪来**：登录 `https://<project>.vercel.app/account` →「创建令牌」→ **明文只显示这一次**。令牌与账号绑定，签发点唯一（`docs/09_部署/02_账号与安全.md` 第 3.15 节）。
- **本地 stdio 服务器不需要令牌**：`gemarc_local` 走本机进程，配置形态与 `../claude-code/README.md` 的本地那一段相同（启动命令、`GEMARC_WORKSPACE_ROOT`、`GEMARC_ALLOW_WRITE` 通用）。

## 一条命令装好（不用手改 JSON）

仓库自带导入命令，它自己写 `~/.minimax/mcp.json`，**令牌不经过终端**：

```powershell
# 令牌通道：令牌优先从环境变量读
$env:GEMARC_MCP_PAT = "<token>"
python -m gemarc_mcp.clients minimax install --url https://<project>.vercel.app/mcp --check

# OAuth 通道：写不进令牌，命令会先确认这个部署确实提供 OAuth
python -m gemarc_mcp.clients minimax install --url https://<project>.vercel.app/mcp --oauth

# 看现状（只报服务器名、端点、头名与通道，不报令牌值）
python -m gemarc_mcp.clients minimax status

# 卸掉
python -m gemarc_mcp.clients minimax remove
```

`--oauth` 模式**先探测** `https://<project>.vercel.app/.well-known/oauth-protected-resource`：这个部署没有提供 OAuth 时命令会**拒绝并且不写文件**（否则配置里会留下一个「点了必失败」的条目）。

> 命令需要把仓库的 `src` 放进 `PYTHONPATH`（源码树里运行）：`$env:PYTHONPATH = "<仓库路径>\src"`。

## 装完以后

1. **令牌通道**：服务器条目的鉴权状态会变成 `static`（原来是没有凭据时的待授权状态）。
2. **OAuth 通道**：条目没有凭据，客户端第一次连接时会走发现与授权；授权成功后由客户端自己保存访问令牌。
3. **工具清单在会话启动时装载**：已经开着的会话看不到新接上的服务器，**新开一个会话**（或重启客户端）才会出现生产 GemArc 的 9 个工具。
4. 工具名与 Claude Code 同形：`mcp__gemarc__gemarc_brief`、`mcp__gemarc__gemarc_validate`……（服务器名用 `gemarc`，不要用点号或连字符）。

## 已验证到哪一步

- **[RAN]（2026-11-12）** 给 `gemarc` 条目加上 `Authorization` 头之后：
  - 该服务器的鉴权状态从「待授权」变成 `static`；
  - 用同一个令牌直接向生产 `POST /mcp` 发 `initialize`，返回 **200**，正文是正常的初始化结果；
  - 「授权」按钮不再是这条路上的必要步骤。
- **[RAN]（2026-11-12）** 不加头时点「授权」，报「未找到该服务器的授权配置」；这句错误文案来自客户端安装包里 OAuth 发现失败的那一条，与 GemArc 的令牌通道无关。
- **[RAN]（2026-11-12）** 导入命令 `python -m gemarc_mcp.clients minimax install|status|remove` 写入用户级配置成功；令牌不经过终端。
- **[UNVERIFIED]** OAuth 通道在 MiniMax Code 里点「授权」的真实效果（动态注册 + PKCE S256 + 同意页）尚未实机验证。
- **[UNVERIFIED]** 没有在新会话里真的调一次生产工具（这一轮只验到配置与鉴权状态这一层；工具调用留待实测）。
- **[UNVERIFIED]** 没有验证过令牌到期后、或在客户端里改掉令牌之后的行为。

## 与其他客户端的差别（一句话版）

| 客户端 | 令牌放哪 | 能否用 `${变量名}` 引用 | OAuth 通道 |
|---|---|---|---|
| Claude Code | 环境变量 `GEMARC_MCP_PAT` | 能 | 有（自带 MCP 授权） |
| Codex | 环境变量 `GEMARC_MCP_PAT` | 不能（它有自己的 `bearer_token_env_var`） | 未验 |
| Hermes | 环境变量 `GEMARC_MCP_PAT` | 能 | 未验 |
| MiniMax Code | **只能写在 `~/.minimax/mcp.json` 的头里** | **不能** | 有（客户端自己的「授权」按钮，v3.4 起可试） |

客户端这一侧的具体形态见 `../README.md` 的兼容矩阵。
