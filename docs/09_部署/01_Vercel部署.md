# Vercel 部署说明（MCP 远程端点 + 5D 静态站）

> 文档编号：OPS-001 ｜版本：1.0 ｜日期：2026-10-25 ｜状态：已实现并本地实测；Vercel 侧构建待凭据验证  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文区分两类结论——**已实测**（本机真实运行得到）与**待验证**（需要 Vercel 账号凭据才能确认）。未验证的部分不写成已完成。

## 1. 部署形态

单个 Vercel 项目、单个 Python Function，同时承载 MCP 端点与 5D 静态站：

| 路径 | 内容 | 实现位置 |
|---|---|---|
| `/mcp` | MCP Streamable HTTP 端点 | `api/index.py` → `MCPServer.streamable_http_app()` |
| `/healthz` | 健康检查与部署状态 | `api/index.py` |
| `/` | **MCP 功能微调控制台**（Web 面板） | `api/index.py` → `templates/console/` |
| `/site/` | 脚手架产出的 5D 架构站 | `api/index.py` → Starlette `StaticFiles`（冷启动物化） |

**主页是控制台，不是设计图**。远程端点的主角是 MCP 本身：主页是一个「功能微调面板」，
列出服务器上**真实注册**的工具，让人在浏览器里改参数、直接调用、看真实返回。
它不是一个架构可视化页面——那类页面是脚手架的*产物*，放在 `/site/`。

控制台的三条硬约束：

| 约束 | 做法 |
|---|---|
| 不许声称服务器没有的能力 | 工具列表、参数表单、输入契约全部来自 `tools/list` 的真实返回 |
| 写工具的名单不能手抄 | `server.py` 的装饰器自带 `write=True` 标记，`/healthz` 暴露该名单，页面据此上锁 |
| 路径不能写死 | 服务端渲染时注入 `mcpPath` / `healthPath` / `sitePath` / `writeTools` |

浏览器直连 `/mcp` 走的是**无状态 JSON-RPC**（`stateless_http=True` + `json_response=True`）：
POST 一次拿一个 JSON 响应，不需要 session id，也就不需要跨域许可。


配置文件：

| 文件 | 作用 |
|---|---|
| `api/index.py` | ASGI 入口：MCP、健康检查、静态站、路由分发 |
| `vercel.json` | 单一 `@vercel/python` 构建 + 单一 catch-all 路由 |
| `requirements.txt` | Vercel Python 构建器安装的依赖 |
| `.python-version` | Python 3.12 |

**静态站为什么是「物化」出来的**：仓库里的 `examples/demo-site` 是 **MVP 档**的随附预览，
它与脚手架站（`static` 档）不是同一个应用。若把某一份提交进仓库的快照直接上线，
它会与包内源码**静默漂移**——仓库声称有 3D 适配器，线上站点却没有。

因此入口在**冷启动时**用包内源码（`src/gemarc_mcp/scaffold/` 的模板与运行时），
配合 `examples/demo-site/data/architecture.json` 这份**已发布的示例模型**，
在临时目录里现搭一个 `static` 档站点再对外提供：

```
冷启动 → builder.build_site(tmp, 已发布示例模型, "static") → StaticFiles(tmp)
```

- 线上站点**永远等于**当前源码，不可能漂移；
- 不需要在仓库里提交第二份运行时副本；
- 物化失败时**回退**到 `examples/demo-site`（并在 `/healthz` 的 `staticSiteSource` 里
  如实写明回退原因），而不是让站点整个消失。

**已实测**：`/healthz` 返回 `staticSiteSource: "scaffold-static"`；
`/` 返回的 HTML 含 `importmap` 与 `"three"`；
`/runtime/space/space-viewer-three.js` 返回 200 且含 `createThreeSpaceRenderer`。
这三条已固化为回归测试 `tests/test_http_transport.py`。

**为什么静态站也由函数提供**：这样整个部署只有一条路由规则和一个构建产物，
不依赖 Vercel 静态构建的 `dest` 路径约定。若后续要让静态资源走 CDN，
把静态站拆成独立的静态项目，并删除 `api/index.py` 中的
`static_app` 分支即可，其余部分不依赖它。

## 2. 三个必须的适配（及其实测依据）

### 2.1 传输改为 Streamable HTTP，无状态 + JSON 响应

Vercel 函数不在调用之间保存 MCP 会话状态，长连接的 SSE 响应会占住函数直到超时。
因此使用 `streamable_http_app(json_response=True, stateless_http=True)`。

**已实测**：`initialize` → `list_tools` → `list_resources` → `list_prompts` → `call_tool`
全链路跑通，协商协议版本 `2025-11-25`，14 个 tool / 2 个 resource / 1 个 prompt 全部可见。

### 2.2 关闭 DNS rebinding 防护

SDK 默认只接受 `Host: localhost` / `127.0.0.1`。部署到真实域名后，每个请求都会被拒。

**已实测**（同一个 `initialize` 请求，只改 `Host` 头）：

| 配置 | `Host: 127.0.0.1:8934` | `Host: gemarc.vercel.app` |
|---|---|---|
| 防护开启（默认） | HTTP 421 `Invalid Host header` | HTTP 421 `Invalid Host header` |
| 防护关闭 | HTTP 200 正常 `initialize` 结果 | HTTP 200 正常 `initialize` 结果 |

因此入口显式设置 `TransportSecuritySettings(enable_dns_rebinding_protection=False)`。
**代价**：不再校验 Host 头，访问控制必须由外部机制承担（见第 5 节）。

### 2.3 session manager 用后台线程启动，不依赖 ASGI lifespan

Serverless 宿主不保证执行 ASGI lifespan 事件（官方 issue vercel/vercel#11677）。

**已实测**：去掉 lifespan 后，SDK 抛出
`RuntimeError: Task group is not initialized. Make sure to use run().`，所有工具调用失败；
改为在 import 时用守护线程启动 `session_manager.run()` 后，11 个工具全部恢复正常。

另有一个顺序约束：`session_manager` 只能在 `streamable_http_app()` 调用**之后**访问，
否则抛 `RuntimeError: Session manager can only be accessed after calling streamable_http_app()`。

### 2.4 路径归一化

catch-all 路由下，函数收到的路径可能是 `/mcp`，也可能是重写产生的 `/api/index`。
入口把这两者（以及 `/index.py`）都归一化为 MCP 端点。

**已实测**：`/mcp` 与 `/api/index` 两条路径都返回 14 个 tool。

## 3. 工具可用性矩阵

| 工具 | 远程端点状态 | 说明 |
|---|---|---|
| `gemarc.assets` | ✅ 可用 | 读取包内 `registry.json`，不依赖工作区 |
| `gemarc.plan` | ✅ 可用 | 针对冷启动时植入的 `demo` 种子项目 |
| `gemarc.validate` | ✅ 可用 | 同上 |
| `gemarc.simulate` | ✅ 可用 | 同上（**已实测** `normal` 场景 delivered=4、blocked=0） |
| `gemarc.inspect` | ⚠️ 语义受限 | 只能扫描工作区内的路径；云端没有用户本地的仓库 |
| `gemarc.init` / `model` / `build` / `verify` / `export` / `diff` | ⛔ 默认关闭 | 写操作需 `GEMARC_ALLOW_WRITE=1`，默认关闭 |

种子项目来自 `examples/demo-site/data/architecture.json`，是**已发布的示例数据**
（`evidenceState: illustrative`），不含任何真实工程事实，因此只读工具对外暴露不构成数据泄露。

## 4. 状态持久化边界（务必读）

Vercel 函数的文件系统是临时的，只有 `/tmp` 可写，实例销毁后内容消失。

**已实测**：新起一个容器 + 空工作区后，对先前创建的项目调用 `gemarc.validate`，返回
`GEMARC_IO_001 文件不存在或超过 4MiB 限额`。

因此：

- `gemarc.export` 返回的是**本地文件路径**而不是下载 URL，远程调用方拿不到产物；
- `gemarc.build` 生成的站点无法被远程用户访问；
- 跨调用的状态（乐观并发的 SHA、模型修改）在实例回收后丢失。

**结论**：远程端点适合**只读演示与工具契约验证**，不适合承载真实工程的架构编辑工作流。
真实工程场景请继续使用本地 stdio 模式，或改用有持久卷的常驻容器宿主。

## 5. 暴露到公网前的强制要求

入口默认把写操作关掉：

```python
if os.environ.get("GEMARC_ALLOW_WRITE") != "1":
    os.environ["GEMARC_ALLOW_WRITE"] = "0"
```

**已实测**：写工具调用返回 `GEMARC_AUTH_002`，且被拒绝的调用**没有留下任何副作用**；
工作区越界访问仍返回 `GEMARC_AUTH_001`。

如果要在公网端点开启写操作，必须先解决以下问题，否则不同调用方会共享同一个临时工作区、
互相覆盖项目并读取到对方的架构数据：

1. **租户隔离**：工作区根必须从「环境变量单根目录」改为按请求身份派生的子目录，
   并从请求上下文取租户身份，而不是从进程环境取。
2. **鉴权**：SDK 的 DNS rebinding 防护已关闭，必须在 Vercel 侧另行加访问控制
   （Vercel Authentication / 自建 token 校验）。
3. **关闭 Deployment Protection**：开启 SSO/密码保护时，MCP 客户端拿到的是 HTML 挑战页
   而不是 JSON-RPC，协议直接不可用。

## 6. 部署步骤

```powershell
cd E:\GemArc
npx vercel@65.2.0 login
npx vercel@65.2.0 link
npx vercel@65.2.0 deploy --prod
```

环境变量：

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `GEMARC_WORKSPACE_ROOT` | 否 | `/tmp/gemarc-workspace` | 工作区根，建议保持默认 |
| `GEMARC_ALLOW_WRITE` | 否 | `0` | 置 `1` 才开放写工具，公开部署不建议 |

`vercel.json` 用 `config.includeFiles` 显式带上 `src/**` 与 `examples/demo-site/**`：
前者是 MCP 运行时**也是**冷启动物化站点所用的模板与共享运行时，后者提供已发布的示例模型
（以及物化失败时的回退预览）。因为构建器不保证把非 import 追踪到的目录打进函数包。

## 7. 部署后验证

```powershell
# 1. 健康检查
curl https://<domain>/healthz
# 期望：status=ok、writeEnabled=false、seedProject=demo、staticSiteAvailable=true

# 2. MCP 协议握手（官方客户端，不依赖任何本地 mock）
python -c "import asyncio; from mcp import ClientSession; from mcp.client.streamable_http import streamable_http_client
async def m():
    async with streamable_http_client('https://<domain>/mcp') as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); print(len((await s.list_tools()).tools), 'tools')
asyncio.run(m())"
# 期望输出：14 tools

# 3. 静态站与主页
curl -I https://<domain>/
# 期望：200，控制台页面
curl -I https://<domain>/site/
# 期望：200，脚手架 5D 站
```

## 8. 验证范围（已实测 vs 待验证）

### 已实测（本机真实运行）

- Streamable HTTP 全链路握手，14 tool / 2 resource / 1 prompt 可见可调
- DNS rebinding 防护开/关的 421 / 200 对照
- 去掉 lifespan 触发 `Task group is not initialized`，改用后台线程后恢复
- 写工具默认关闭（`GEMARC_AUTH_002`）且无副作用；越界路径被拒（`GEMARC_AUTH_001`）
- 新容器 + 空工作区导致项目状态丢失（`GEMARC_IO_001`）
- `/` 返回 MCP 控制台（占位符全部被服务端替换；写工具名单与 `server.py` 同源）
- `/site/` 返回脚手架 5D 站；`/site/runtime/space/space-viewer-three.js` 200
- 浏览器实测：控制台调 `gemarc.validate` 返回 `status: ok`、0 error；
  选写工具时调用按钮显示「写工具已关闭」并禁用；读 `gemarc://registry` 成功；控制台零报错
- `/mcp` 与 `/api/index` 两条路径都可用
- 真实域名 Host 头下 `/healthz` 返回 200
- 以上行为已固化为回归测试 `tests/test_http_transport.py`（真实启动应用 + 官方客户端握手），
  与 `vercel deploy` 无关，可直接在 CI 中运行

### 待验证（需要 Vercel 账号凭据，本次无法执行）

- `vercel build` 是否按预期打出函数产物
- `config.includeFiles` 的 glob 在真实构建中是否覆盖全部静态资源，
  以及 `src/gemarc_mcp/scaffold/` 下的模板与运行时是否完整进入函数包
  （冷启动物化站点依赖它们；缺任何一个都会让 `/` 回退到随附预览）
- 冷启动物化站点的耗时（每个新实例都要现搭一次站点）
- 真实域名下的完整 MCP 握手
- 静态资源由函数提供时的冷启动耗时与 CDN 缓存行为

本机 `vercel dev` / `vercel build` 均因缺少凭据而无法运行
（`Error: No existing credentials found`），因此以上待验证项**不声称已完成**。

## 9. 已知限制

- 函数超时：Hobby 10 秒 / Pro 60 秒 / Enterprise 900 秒。`vercel.json` 未写死 `maxDuration`，
  以免在 Hobby 计划上构建失败；Pro 用户可自行加上。
- 函数包体积上限 250MB（未压缩）/ 50MB（压缩），当前依赖远低于该限制。
- 静态资源由函数提供，不走 CDN 边缘缓存；需要 CDN 时按第 1 节拆分项目。
- 远程端点**没有**经过浏览器端 / WebGL 端到端验证——同一份 `static` 档站点在本机浏览器里
  已实测通过（见设计文档 §7），但**真实域名下的那次验证还没做**；
  `gemarc.verify` 仍只做文件系统与 Schema 检查。

## 10. 什么时候不该用 Vercel

- 需要多用户同时编辑同一工程的架构模型 → 用常驻容器（Railway / Fly.io / Cloud Run）加持久卷。
- 需要导出产物供用户下载 → 给 `gemarc.export` 增加对象存储直传，而不是依赖函数本地磁盘。
- 只想要一个可分享的 5D 架构站 → 直接部署 `examples/demo-site`，不需要 MCP 端点。
