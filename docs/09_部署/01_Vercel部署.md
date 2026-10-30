# Vercel 部署说明（MCP 远程端点 + 5D 静态站）

> 文档编号：OPS-001 ｜版本：2.0 ｜日期：2026-10-30 ｜状态：新版入口已本地实测，并已于 2026-10-30 部署到 Vercel 公网实测（见第 12 节）；仍未验证的项目列在第 12 节末尾  
> 产品：GemArc MCP — 5D Design Scaffolding & Architecture Compiler  
> 说明：本文把结论分成四类，每条都标明属于哪一类，**未验证的部分不写成已完成**：
>
> | 标记 | 含义 |
> |---|---|
> | **本地实测** | 在本机真实运行并得到结果（命令与结果见各节） |
> | **主线实测** | 协调者（lead）对用户真实 Vercel 项目做只读 GET 得到的观测；本文作者没有复跑，仅转述 |
> | **UNVERIFIED** | 来自文档或源码调研，或需要 Vercel 凭据才能确认；**没有人运行过** |
> | 设计决定 | 取舍与理由，不是事实断言 |
>
> 起草本文时本机没有 Vercel 凭据、没有部署过；2026-10-30 已完成暂存部署、冒烟与 promote（第 12 节）。凡标 UNVERIFIED 的，以第 12 节末尾的清单为准。

## 1. 部署形态

单个 Vercel 项目、单个 Python Function，同时承载 MCP 端点、控制台与 5D 静态站：

| 路径 | 方法 | 内容 | 鉴权 |
|---|---|---|---|
| `/mcp` | **仅 POST** | MCP Streamable HTTP 端点（无状态 + JSON 响应） | 设置了 `GEMARC_MCP_TOKEN` 才要求 `Authorization: Bearer` |
| `/healthz` | GET/HEAD | 健康检查与部署状态（不含路径与令牌） | 公开 |
| `/` | GET/HEAD | **MCP 功能微调控制台**（Web 面板） | 公开（静态外壳，无机密） |
| `/site/*` | GET/HEAD | 脚手架产出的 5D 架构站（首次访问时才物化） | 公开 |
| `/kit/static.zip` `/kit/vite.zip` | GET/HEAD | 站点**固定件**，一份确定性 zip（带 ETag，支持 304） | 公开 |
| `/.well-known/*` | 任意 | 一律返回 404 JSON（OAuth 发现探测得到干净的否定答案，而不是控制台 HTML） | 公开 |
| 其他路径 | 任意 | 404 JSON（**不再**把站点挂在根路径下） | — |

**远程默认工具集是 `stateless`**（8 个工具，模型随调用内联传入，不需要工作区）。
运营者仍可设 `GEMARC_TOOLSET=full` 得到全部 16 个工具，但那是**每个实例各自一份临时工作区**，
见第 5、6 节。

**主页是控制台，不是设计图**。主页列出服务器上**真实注册**的工具，让人在浏览器里改参数、
直接调用、看真实返回；它不是架构可视化页面——那类页面是脚手架的*产物*，放在 `/site/`。

控制台的硬约束：

| 约束 | 做法 |
|---|---|
| 不许声称服务器没有的能力 | 工具列表、参数表单、输入契约全部来自 `tools/list` 的真实返回 |
| 写工具名单不能手抄 | `server.py` 装饰器自带 `write=True`；服务端渲染时注入，`/healthz.writeTools` 再核对；stateless 工具集里没有写工具 |
| 路径不能写死 | 服务端渲染时注入 `mcpPath` / `healthPath` / `sitePath` / `writeTools` |
| 令牌不落地 | 服务器要求令牌时才出现令牌栏；令牌只放在**本标签页的 `sessionStorage`**（读写都包在 try/catch 里），不进 `localStorage`、不进 URL，只作为 `Authorization` 头随 `/mcp` 调用发送；收到 401 时清除并提示 |

浏览器直连 `/mcp` 走的是**无状态 JSON-RPC**（`stateless_http=True` + `json_response=True`）：
POST 一次拿一个 JSON 响应，不需要 session id，控制台与 `/mcp` 同源，因此不需要 CORS 头。

## 2. 配置文件

| 文件 | 作用 | 说明 |
|---|---|---|
| `api/index.py` | ASGI 入口 | `build_app()` 工厂 + 模块级 `app`；MCP、鉴权、健康检查、kit、控制台、懒物化静态站 |
| `vercel.json` | **旧式** `builds` + `routes`（`@vercel/python` + catch-all） | **本次未改动**；主线已观测它在用户真实项目上工作（见 §10） |
| `pyproject.toml` | 包元数据与依赖下限 `mcp>=2.3.0,<3.0` | 版本 0.3.0；`package-data` 已核对完整（§2.1） |
| `requirements.txt` | 同一个依赖下限，给 pip 用户和 `pip install -r` 的宿主 | 用户项目的控制台显示安装命令为 `pip install -r requirements.txt`（主线观测），但在 `builds` 配置下它不生效：第 12 节的构建日志写明依赖按 `pyproject.toml` 安装（实测）。两个文件必须一致，没有测试把它们绑在一起 |
| `.python-version` | `3.12` | 本地与测试用的就是 3.12 |
| `.vercelignore` | 不上传 `/docs /tests /skills /scripts /integration-examples /.claude-plugin *.egg-info *.xlsx .env* install.ps1` | 运行时只读 `src/gemarc_mcp/**` 与 `examples/demo-site/**`；新增文件；第 12 节实测它没有妨碍构建与运行 |

`vercel.json` 现状（未改动）：

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "version": 2,
  "builds": [
    { "src": "api/index.py", "use": "@vercel/python",
      "config": { "includeFiles": ["src/**", "examples/demo-site/**"] } }
  ],
  "routes": [ { "src": "/(.*)", "dest": "/api/index.py" } ]
}
```

### 2.1 依赖与打包（本地实测）

- 依赖下限：`pyproject.toml` 与 `requirements.txt` 都是 `mcp>=2.3.0,<3.0`。
  控制台里的 Install Command 显示为 `pip install -r requirements.txt`，但在 `builds` 配置下它不生效：
  第 12 节的构建日志写明依赖按 `pyproject.toml` 安装（实测）。`requirements.txt` 只服务 pip 用户与其它宿主；
  改依赖时两处都要改（没有测试把两者绑在一起）。
- **本地实测**：`pip wheel . --no-deps`（隔离构建，需联网）得到 `gemarc_mcp-0.3.0-py3-none-any.whl`；
  把轮子里的文件与 `src/gemarc_mcp` 下的文件逐个比对：磁盘 95 个、轮子 95 个，**缺 0 个**
  （含 `stateless.py`、`__main__.py`、`registry.json`、`scaffold/templates/console/*`、
  `scaffold/vocab/*.json`、`scaffold/runtime/**` 共 48 个文件）。`package-data` 不需要改。
- **本地实测**：在一个全新的临时 venv 里安装该轮子（解析出 `mcp 2.3.0`），再用 `python -I`
  从仓库**之外**的目录运行：`gemarc_mcp` 来自 `site-packages`，`stateless.site_kit("static")`
  得到 52 个文件，`create_server("stateless")` 注册 8 个工具，`builder.build_site` 正常。
  这证明「已安装的包」本身是完整的，而不是靠 `api/index.py` 往 `sys.path` 里塞了仓库的 `src`。
- 本机没有 `uv`（`uv --version` 不存在），所以**没有提交 `uv.lock`**。建议首次部署前在装有
  `uv` 的机器上执行 `uv lock` 并提交；Vercel 是否使用它 **UNVERIFIED**。

## 3. 入口的几个适配及其依据

### 3.1 传输：Streamable HTTP，无状态 + JSON，**只接受 POST**

Vercel 函数不在调用之间保存 MCP 会话状态；SSE 长连接会占住函数实例直到超时。
因此使用 `streamable_http_app(json_response=True, stateless_http=True)`。

**GET 必须立刻拒绝**。旧入口上 `GET /mcp` 返回 200 并一直挂着 SSE 流：

- **本地实测**（lead 对旧入口，本文作者未复跑）：curl 6 秒超时仍未结束；
- **主线实测**（用户的真实站点）：`GET /mcp` 同样 200 并挂起（curl 8 秒后被杀）。

在 Vercel 上，这样的请求会占住一个函数实例直到 `maxDuration`（**UNVERIFIED**），而部分客户端
（如 Hermes Agent）会先 HEAD、再 GET 探测。MCP 规范允许服务器对可选的 GET 流返回 405。

现在的顺序（**本地实测**，`tests/test_http_transport.py::test_only_post_reaches_the_sdk`）：

```
OPTIONS      -> 204, Allow: POST, OPTIONS（不需要令牌，不带 CORS 头）
非 POST      -> 405, Allow: POST          （先于鉴权，也先于 SDK）
POST         -> 鉴权 -> session manager 就绪检查 -> SDK
```

`GET/HEAD/DELETE/PUT/PATCH` 在 `/mcp`、`/mcp/`、`/api/index` 上都是 405，立刻返回
（`verify_remote.py` 实测 0.00–0.02 s；测试断言 < 2 s）。

### 3.2 关闭 DNS rebinding 防护

SDK 默认只接受 `Host: localhost` / `127.0.0.1`，部署到真实域名后每个请求都会被拒。

**本地实测**（同一个 `initialize` 请求，只改 `Host` 头；沿用上一版文档的实测记录，本次未复跑）：

| 配置 | `Host: 127.0.0.1:8934` | `Host: gemarc.vercel.app` |
|---|---|---|
| 防护开启（默认） | HTTP 421 `Invalid Host header` | HTTP 421 `Invalid Host header` |
| 防护关闭 | HTTP 200 正常 `initialize` 结果 | HTTP 200 正常 `initialize` 结果 |

因此显式设置 `TransportSecuritySettings(enable_dns_rebinding_protection=False)`。
**代价**：不再校验 Host 头，访问控制由第 4 节的可选 Bearer 令牌承担。

### 3.3 session manager：外层 lifespan + 懒启动兜底（**不再用后台线程**）

SDK 要求外层应用进入 `session_manager.run()`，且**同一个 manager 只能进入一次**；没有它会抛
`RuntimeError: Task group is not initialized. Make sure to use run().`。

旧入口在 import 时用守护线程启动它。主线观测到这个旧方案**在用户的真实部署上工作**
（`sessionManagerThreadAlive: true`），但线程里的 task group 属于另一个事件循环，
在并发调用下有竞态，因此改为在**服务事件循环**上启动，有两条入口：

| 模式 | 触发 | `/healthz.sessionManager` |
|---|---|---|
| `lifespan` | 宿主运行 ASGI lifespan（uvicorn 等）：外层 `Starlette(lifespan=…)` 进入 `run()` | `"lifespan"` |
| `lazy` | 宿主**不**运行 lifespan（旧版 `@vercel/python` 可能如此，vercel/vercel#11677）：第一个 MCP 请求在**当前事件循环**上 `create_task` 启动 `run()`，每个循环一把 `asyncio.Lock`，并等待就绪事件 | `"lazy"` |
| 未启动 / 失败 | 从未请求过（`status` 仍为 `ok`）；或启动失败（`status: "degraded"`） | `"not-running"` |

**循环不一致时重建**（超出「只进入一次」的字面要求，理由如下）：旧的线程方案与事件循环无关，
而懒启动的 manager 绑在启动它的循环上。如果宿主**每次调用都新建事件循环**，第一个请求之后
manager 就随旧循环消失，后续请求会全部 503——对一个已知可用的部署是退步。所以当拥有运行中
manager 的循环不是当前循环（已关闭、已关停或启动失败）时，下一个请求会用
`streamable_http_app()` 新建一个内层应用 + **全新的** manager 与当前循环配对
（SDK 的真正约束是「每个 manager 只进入一次」，这一点仍然成立）。
持久循环下（uvicorn、或主流 ASGI 宿主）不会发生重建。

请求**永远不会**撞上 SDK 的 `Task group is not initialized`：启动失败或超时（15 秒）时，
`POST /mcp` 返回 503 `{"error":"session manager not running"}`，每次失败都记一次日志；
之后 `run()` 恢复正常的话，下一个请求会用新的 manager 自愈。

**本地实测**：

| 场景 | 命令/测试 | 结果 |
|---|---|---|
| lifespan 开 | `uvicorn api.index:app`（默认 auto） | healthz `sessionManager: lifespan`；`initialize` 200 |
| lifespan 关 | `uvicorn api.index:app --lifespan off` | healthz 先 `not-running`（status 仍 ok）；首个 POST 200；之后 `lazy` |
| 并发首请求 | 6 个线程同时 `initialize`（`SessionManagerStartupTests`） | 全部 200，只启动一个 manager |
| 持久循环、无 lifespan | 同一循环上 5 个并发 + 3 个顺序请求 | 全部 200，**没有**重建 manager |
| **每次调用一个新循环** | 手写 ASGI 调用，无 lifespan，4 个请求，(A) 每次 `asyncio.run`（循环关闭）(B) 每次 `new_event_loop()` 且不关闭 | 加入重建之前：`[200, 503, 503]`（A、B 都是）；加入之后：A、B 都是 `[200, 200, 200, 200]` |
| 启动失败 | 把 `StreamableHTTPSessionManager.run` 换成抛异常 | 每个请求都是 503 `session manager not running`，healthz `degraded`；`run()` 恢复后下一个请求 200，healthz 回到 `ok` / `lazy` |

**这些路径在 Vercel 上各自是否会走到，UNVERIFIED。**

### 3.4 路径归一化

catch-all 路由下函数收到的路径可能是 `/mcp`，也可能是重写产生的 `/api/index`。
入口把 `/mcp`、`/mcp/`、`/api/index`、`/api/index.py`、`/index.py` 都归一化为 MCP 端点
（**本地实测**：五条路径都返回同一组 8 个工具）。

### 3.5 静态站：首次访问 `/site/*` 才物化

仓库里的 `examples/demo-site` 是 **MVP 档**的随附预览，与脚手架站（`static` 档）不是同一个应用。
把一份提交进仓库的快照直接上线，会与包内源码**静默漂移**。因此 `/site/*` 的**第一个请求**
在临时目录里用包内模板 + 已发布示例模型现搭一个 `static` 档站点（带锁、缓存、放进工作线程），
物化失败则回退到 `examples/demo-site`，并在 `staticSiteSource` 里写明失败的异常**类名**
（异常全文可能含路径，只进日志，不进 `/healthz`）。

- 这样只访问 `/mcp` 的冷启动**不付**物化的代价（旧入口在 import 时就物化）；
- **本地实测**：物化前 `/healthz.staticSiteSource == "not-built-yet"`；访问 `/mcp`、`/`、
  `/kit/*`、`/favicon.ico` 都不会触发物化；4 个并发的 `/site/` 请求只物化一次，之后是
  `"scaffold-static"`（`LazySiteTests`）；
- 主线观测旧入口（import 时物化）冷启动 `/healthz` 为 3.3 s（含物化）；新入口把物化挪到首次
  `/site/*`，冷启动应更快，但**没有在 Vercel 上测过（UNVERIFIED）**。

### 3.6 `/healthz` 不泄露服务器路径

旧入口返回了 `"workspace": "/tmp/gemarc-workspace"`（主线在真实站点上观测到）。现在的字段：

| 字段 | 含义 |
|---|---|
| `status` | `ok`；启动失败或已关闭时为 `degraded` |
| `server` `version` `transport` `protocolPath` | `gemarc-mcp`、包版本、`streamable-http`、`/mcp` |
| `toolset` `tools` | `stateless` / `full`；真实注册的工具名 |
| `writeEnabled` `writeTools` | `GEMARC_ALLOW_WRITE` 是否为 `1`；写工具名单（stateless 为空） |
| `authRequired` | **只有布尔值**，没有任何令牌信息 |
| `sessionManager` | `lifespan` / `lazy` / `not-running` |
| `seedProject` `seeded` | `demo`；本次是否新植入 |
| `staticSiteAvailable` `staticSiteSource` `staticSitePath` | 站点可用性与来源（`not-built-yet` / `scaffold-static` / `committed-preview (…)`） |
| `consoleAvailable` | 控制台外壳是否在包内 |

**本地实测**：测试递归扫描 `/healthz` 的每个字符串值，断言不含驱动器号、反斜杠、`/tmp`、`/var`、
`/home` 等文件系统形状，也不含工作区目录、系统临时目录、仓库根目录的字面值。
（`protocolPath` 与 `staticSitePath` 是 URL 路径，不是服务器路径，所以允许。）

## 4. 鉴权：可选的 Bearer 令牌

| 模式 | 配置 | 行为 | 适用 |
|---|---|---|---|
| **authless**（默认） | 不设或留空 `GEMARC_MCP_TOKEN` | `/mcp` 对任何知道 URL 的人开放；`/healthz.authRequired:false` | claude.ai 自定义连接器；纯计算、无机密的演示部署 |
| **Bearer 令牌** | `GEMARC_MCP_TOKEN=<token>`（可逗号分隔多个，便于轮换） | `POST /mcp` 必须带 `Authorization: Bearer <token>`，否则 401 | Claude Code、Codex、Hermes Agent（它们都能配静态请求头） |
| OAuth | **未实现** | 对 `/.well-known/*` 返回干净的 404 JSON | — |

**claude.ai 的限制（UNVERIFIED，来自调研，未在 claude.ai 上试过）**：自定义连接器只支持
OAuth 或「无认证」；静态请求头只是部分组织可用的 beta。所以要给 claude.ai 用，就只能 authless；
想设令牌就意味着 claude.ai 用不了，除非以后实现 OAuth。

令牌规则（**本地实测**，`tests/test_remote_auth.py`，29 个用例）：

- 只读 `Authorization` 头；**不读查询串**（`?token=`、`?access_token=` 一律 401，
  即使同时带了错误的头也不会被查询串“救回”）；方案名大小写不敏感，前后空白忽略；
- 用 `hmac.compare_digest` 对**字节**逐个令牌比较（不会因非 ASCII 输入抛 `TypeError`）；
- 失败：HTTP 401、`WWW-Authenticate: Bearer`、`Cache-Control: no-store`、
  `{"error":"unauthorized"}`；令牌不会出现在任何响应或 `/healthz` 里，也不写日志；
- 豁免（公开静态内容，无机密）：`/healthz`、`/`、`/console.*`、`/site/*`、`/kit/*`、
  `/.well-known/*`、`OPTIONS`；方法检查**先于**鉴权，所以 GET/HEAD/DELETE 无论有无令牌都是 405；
- 轮换：`GEMARC_MCP_TOKEN="old,new"` 两个都有效；空项与首尾空白被忽略。
  令牌里不能含逗号（十六进制令牌没有此问题）。
- 令牌是环境变量，**改了之后必须重新部署才生效**（UNVERIFIED：Vercel 对环境变量变更的生效时机以其文档为准）。

### 4.1 生成与轮换（PowerShell 5.1）

```powershell
# 生成 32 字节随机令牌（64 个十六进制字符），只显示一次，立刻存进密码管理器
$b = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
($b | % { $_.ToString('x2') }) -join ''
```

设置（**交互式输入，不要用 `--value`**，否则令牌会进 shell 历史）。**必须在第一次生产部署之前**完成：

```powershell
npx vercel@65.8.0 login
npx vercel@65.8.0 link
npx vercel@65.8.0 env add GEMARC_MCP_TOKEN production --sensitive
npx vercel@65.8.0 env add GEMARC_MCP_TOKEN preview --sensitive   # 要先在预览部署上验证时
npx vercel@65.8.0 deploy --prod --logs
```

以上 CLI 子命令与 `--sensitive` 的行为 **UNVERIFIED**（没有运行过）；先看 `npx vercel@65.8.0 env add --help`。
Sensitive 变量创建后无法再读出明文，所以**必须自己保存令牌**。

轮换（不中断客户端）：

1. 生成新令牌 `N`；把变量改为 `旧,N`（`env rm` 后重新 `env add`，值里带逗号）→ 重新部署；
2. 把所有客户端的头换成 `N`；
3. 把变量改为只含 `N` → 重新部署；
4. 用 `scripts/verify_remote.py --token N` 确认。

## 5. 工具可用性

### 5.1 远程默认：`stateless` 工具集（8 个）

| 工具 | 说明 |
|---|---|
| `gemarc_brief` `gemarc_contracts` `gemarc_assets` | 工作指令、契约/词表、组件注册表，读取包内文件，不依赖工作区 |
| `gemarc_new_model` | 返回空骨架模型与槽位清单 |
| `gemarc_validate` `gemarc_simulate` `gemarc_diff` | 模型通过 `model` 参数**内联**传入；也接受 `project="demo"`（冷启动时植入的示例项目），让第一次试用的人不必先准备模型 |
| `gemarc_render` | 校验模型，返回依赖模型的 `data/*.json` 与固定件清单；固定件由 `GET /kit/<profile>.zip` 下载，两者合起来就是完整 5D 站，服务器不保存任何东西 |

工具名只含 `[a-z0-9_]`（Claude、OpenAI 风格函数调用与多数 agent 框架要求 `^[a-zA-Z0-9_-]{1,64}$`；
点号在 MCP 规范里合法，但会被这些客户端拒绝或改写）。**没有任何写工具**，没有工作区依赖。

示例模型来自 `examples/demo-site/data/architecture.json`，是**已发布的示例数据**
（`evidenceState: illustrative`），不含真实工程事实。

### 5.2 `GEMARC_TOOLSET=full`（运营者主动选择，16 个）

额外含 `gemarc_inspect/plan/scaffold/init/model/build/verify/export`。它们读写**工作区**，
而函数实例的文件系统是临时的（第 6 节），写工具默认关闭（`GEMARC_ALLOW_WRITE`）。
**不建议**在公开部署里使用。

## 6. 状态持久化边界（务必读）

Vercel 函数的文件系统是临时的，只有 `/tmp` 可写，实例销毁后内容消失
（**本地实测**，沿用上一版文档的记录、本次未复跑：新起一个容器 + 空工作区后，对先前创建的项目调用
`gemarc_validate`，返回 `GEMARC_IO_001 文件不存在或超过 4MiB 限额`）。这正是远程默认改为 `stateless` 的原因：
模型随每次调用内联传入，产物一部分内联返回（`render`），固定件走下载链接，
**服务器不需要保存任何东西**。

## 7. 暴露到公网前的要求

- 写操作默认关闭：只有 `GEMARC_ALLOW_WRITE` **恰好等于** `1` 才开启（`true`/`yes` 不算）；
  stateless 工具集本身不含写工具。**本地实测**：`full` 工具集下 `gemarc_init`/`gemarc_build`
  返回 `GEMARC_AUTH_002` 且无副作用；越界路径返回 `GEMARC_AUTH_001`。
- authless 部署意味着任何人拿到 URL 都能调用这些纯计算工具（输入大小有 4 MiB 上限，
  Vercel 另有 4.5 MB 请求/响应体上限，UNVERIFIED）。如果不想要，就设令牌。
- 若要开写操作（`full` + `GEMARC_ALLOW_WRITE=1`），先解决租户隔离（工作区按请求身份派生）
  与鉴权，否则不同调用方会共享同一个临时工作区。
- **Deployment Protection**：开启后，MCP 客户端拿到的是 HTML 挑战页而不是 JSON-RPC，协议直接不可用。
  Standard Deployment Protection 覆盖**每次部署的独立 URL**（`<project>-<hash>-<team>.vercel.app`），
  生产别名的覆盖范围以项目设置为准（**UNVERIFIED**）。所以验证请用生产别名，或 `vercel curl`，
  或给探测脚本传绕过头（`--header`）。

## 8. 部署步骤

建议顺序（2026-10-30 的实际部署按这个顺序跑过，结果见第 12 节；带令牌的步骤仍 **UNVERIFIED**）：

```powershell
cd D:\GemArc
npx vercel@65.8.0 login
npx vercel@65.8.0 link
# 先设好环境变量（见 4.1），再部署
npx vercel@65.8.0 deploy --logs                 # 1. 预览部署
# 2. 用 `vercel curl` 对预览 URL 做冒烟（语法见 `npx vercel@65.8.0 curl --help`）
# 3. 没问题再提升为生产（控制台 Promote，或）：
npx vercel@65.8.0 deploy --prod --logs
python scripts/verify_remote.py https://<project>.vercel.app --token <T>   # 4. 完整验证；红了就回滚
```

环境变量：

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `GEMARC_MCP_TOKEN` | 否 | 空（authless） | Bearer 令牌，可逗号分隔多个；**不要**用 `ANTHROPIC_` 开头的名字 |
| `GEMARC_TOOLSET` | 否 | `stateless` | `full` 才开放 16 个工具 |
| `GEMARC_ALLOW_WRITE` | 否 | `0` | 置 `1` 才开放写工具；公开部署不建议 |
| `GEMARC_WORKSPACE_ROOT` | 否 | 系统临时目录下的 `gemarc-workspace`（Vercel 上即 `/tmp`） | 工作区根，建议保持默认 |
| `GEMARC_PUBLIC_URL` | 否 | 空 | 让 `gemarc_render` 返回绝对的 `kit.url`；未设时回退到 Vercel 的 `VERCEL_PROJECT_PRODUCTION_URL`（系统变量，第 12 节实测默认暴露）；它在预览部署里也是生产域名，所以仅当 `VERCEL_ENV` 为 `production`（或未设）时才使用；暂存生产部署（`--skip-domain`）在 promote 之前，这个域名仍指向上一个部署的 kit，用 `archiveSha256` 核对。都没有则 `kit.url` 为 `null`，调用方用 `kit.path` 拼接 |

`vercel.json` 用 `builds[].config.includeFiles`（**数组**）显式带上 `src/**` 与
`examples/demo-site/**`：前者是 MCP 运行时也是物化站点所用的模板与共享运行时，后者提供示例模型。

## 9. 部署后验证

推荐直接用探测脚本（只用标准库；有 `mcp` 2.x 时多做一次官方客户端握手）：

```powershell
python scripts/verify_remote.py https://<project>.vercel.app                       # authless
python scripts/verify_remote.py https://<project>.vercel.app --token <T>           # 或环境变量 GEMARC_MCP_TOKEN
python scripts/verify_remote.py <url> --header "x-vercel-protection-bypass: <secret>"   # 可重复
python scripts/verify_remote.py --local                                            # http://127.0.0.1:8000，不启动任何东西
```

逐项打印 `PASS` / `FAIL` / `INFO` 与原因，有 FAIL 时退出码为 1，令牌永远不会被打印：

1. `/healthz` 为 ok，且**不含路径形状的值**；`/` 是 HTML 控制台；`/site/` 200；
2. `GET /mcp` 在 5 秒内返回 405（`Allow: POST`），`HEAD`/`DELETE` 也是 405；
   `/.well-known/oauth-protected-resource` 是 404 JSON；
3. 无令牌 `POST /mcp`：要求令牌时 401（并检查错误令牌也是 401），否则 200；
4. `initialize` 回报服务器名与协议版本；`tools/list` 与 `/healthz.tools` 一致（stateless 时恰好是那 8 个名字），
   每个名字都匹配 `^[a-zA-Z0-9_-]{1,64}$`；
5. `gemarc_new_model` → `gemarc_validate`（空骨架、内联探测模型、`project="demo"`）→ `gemarc_simulate`
   → `gemarc_render`；
6. 下载 `kit.path`，核对 sha256 等于 `kit.archiveSha256`、`ETag` 等于它、`If-None-Match` 得 304；`kit.url` 非空时也核对；
7. 官方 MCP 客户端握手（可选）；业务调用之后再读一次 `/healthz`，`sessionManager` 必须是 `lifespan` 或 `lazy`；
8. **Deployment Protection 探测**：任何响应是 HTML 登录/SSO 挑战页，或 `/healthz` 返回带 HTML 的 401，
   就停止并提示「Deployment Protection is on for this URL - use the production alias or `vercel curl`」。

**本地实测**：对本机 uvicorn 上的 `api.index` 运行，要求令牌的实例 25 项 PASS、authless 实例 24 项 PASS，
退出码 0；不带令牌连要求令牌的实例、带错令牌、对一个返回 Vercel 式 HTML 挑战页的假主机、
对一个泄露 `/tmp/...` 的假 `/healthz`，都按预期 FAIL 并退出 1
（`tests/test_remote_auth.py::VerifyRemoteScriptTests`）。

手工探测：

```powershell
curl.exe -i https://<domain>/healthz
curl.exe -i --max-time 5 https://<domain>/mcp            # 期望：405，Allow: POST，立刻返回
curl.exe -I https://<domain>/kit/static.zip              # 期望：200，ETag、Content-Disposition
```

## 10. 备选：现代 `functions` 配置（UNVERIFIED，**不是**仓库配置）

仓库保持旧式 `builds` + `routes`：主线已观测它连同旧的线程方案在用户项目上工作
（**主线实测**，2026-10-30，只读 GET）：

- `https://gem-arc.vercel.app/healthz` → 200，冷启动 3.3 s，`staticSiteSource: "scaffold-static"`，
  `sessionManagerThreadAlive: true`；`/` 返回控制台 HTML；
- `GET /mcp` → 200 并挂起（curl 8 秒后被杀）——第 3.1 节要修的就是这个；
- `/healthz` 泄露 `"workspace": "/tmp/gemarc-workspace"`——第 3.6 节要修的就是这个。

**新入口（lifespan + 懒启动、POST-only、鉴权、kit 路由）当时还没有在 Vercel 上跑过；2026-10-30 已部署并实测，见第 12 节。**
旧式 `builds` 在文档里被标为弃用（不能与 `functions` 同用，控制台构建设置被忽略），但源码调研
显示它仍被接受（UNVERIFIED）。如果以后要换成预设模式，调研得出的写法如下（**没有人运行过**）：

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": "python",
  "functions": {
    "api/index.py": {
      "includeFiles": "{src/**,examples/demo-site/**}",
      "excludeFiles": "{tests/**,docs/**,skills/**,scripts/**,integration-examples/**}",
      "maxDuration": 60
    }
  }
}
```

注意（均 UNVERIFIED）：`functions` 下的 `includeFiles`/`excludeFiles` 是**单个字符串**（≤256 字符），
不是数组；预设模式下**不要**加到 `/api/index` 的 rewrite；**不要**排除 `**/tests/**`，
否则会剥掉 `src/gemarc_mcp/scaffold/runtime/tests`；Python 入口是顶层变量 `app`，
也可在 `pyproject.toml` 里写 `[tool.vercel] entrypoint = "api.index:app"`；
`.python-version`（3.12）优先于 `requires-python`，受支持的是 3.12/3.13/3.14，不含 3.11
（`requires-python = ">=3.11"` 本身没问题，但实际运行的是 3.12）。

## 11. 限制（UNVERIFIED，来自调研，请以 Vercel 当前文档为准）

| 项目 | 值 | 备注 |
|---|---|---|
| 函数最长执行时间 | Fluid 默认 300 s；Hobby 上限 300 s，Pro 上限 800 s | 旧文档的「10/60/900 秒」已过时；预设模式示例里写了 `maxDuration: 60`，旧式 `builds` 下未设 |
| Python 函数包体积 | 500 MB（未压缩） | 当前依赖远低于此 |
| 请求/响应体 | 4.5 MB | 与 `MAX_MODEL_BYTES`（4 MiB）接近：接近上限的模型可能先被平台拒绝 |
| 静态资源 | 由函数提供，不走 CDN 边缘缓存 | 需要 CDN 时把站点拆成独立静态项目 |

远程端点**没有**经过浏览器端 / WebGL 端到端验证——同一份 `static` 档站点在本机浏览器里
已实测通过（见设计文档 §7），但**真实域名下的那次验证还没做**；`gemarc_verify` 仍只做文件系统与 Schema 检查。

## 12. 验证范围

### 本地实测（本机真实运行；Windows，Python 3.12.10，MCP SDK 2.3.0）

- 全部 Python 测试 **192 个通过**，Node 测试 **125 个通过**（基线是 109 + 125，其中 1 个 HTTP 用例因工具改名而红，已按新现实重写）。
- Streamable HTTP 全链路：官方客户端握手，stateless 8 个工具 / 2 个 resource / 1 个 prompt；
  `full` 工具集 16 个；工具名全部匹配 `^[a-zA-Z0-9_-]{1,64}$`。
- `gemarc_validate(project="demo")`、内联模型调用、`gemarc_new_model → validate → render` 往返。
- GET/HEAD/DELETE `/mcp` → 405；OPTIONS → 204；鉴权全部规则（§4）；`/.well-known/*` → 404 JSON。
- session manager 的 lifespan / lazy / 每次调用新事件循环 / 失败各条路径（§3.3）。
- `/healthz` 无路径泄露；`/kit/*.zip` 的 ETag、304（含 `W/`、列表、`*`）与未知 profile 404。
- 懒物化（§3.5）；`full` 工具集下写工具默认关闭（`GEMARC_AUTH_002`）、越界路径被拒（`GEMARC_AUTH_001`）。
- 沿用上一版文档的实测记录（本次未复跑）：DNS rebinding 开/关对照（§3.2）；新容器丢状态（§6）。
- 打包：轮子文件 95/95，临时 venv 安装后在仓库之外导入通过（§2.1）。
- 控制台令牌流程（用假 DOM 跑真实 `console.js`）：服务器要求令牌时不发任何 MCP 调用；粘贴后以
  `Authorization` 头发送、只进 `sessionStorage`、不进 URL；401 会清除错误令牌；`sessionStorage` 被禁用时不崩。
  **没有在真实浏览器里点过**（UNVERIFIED）。
- `scripts/verify_remote.py` 对本机实例的运行结果见 §9。

### Vercel 实测（2026-10-30，账号 `lidian727-9708` / 团队 `SayAtelier`（Hobby）/ 项目 `gem-arc`）

部署前（只读 GET）：旧入口 + 旧式 `vercel.json` 在 `gem-arc.vercel.app` 上工作；`GET /mcp` 返回 200 后挂起（curl 8 秒超时仍未断）；
`/healthz` 公开了工作区路径 `/tmp/gemarc-workspace`；每个部署的专属 URL 返回 302 "Protected by Vercel Authentication"，生产别名 `gem-arc.vercel.app` 公开。
项目控制台显示 Install Command 为 `pip install -r requirements.txt`，Framework Preset 为 Python。

部署流程与结果（新入口 + 保持不变的旧式 `vercel.json` + 新增的 `.vercelignore`）：

1. `vercel deploy --prod --skip-domain --yes --logs`（**暂存生产部署**：拿到生产环境变量，但不切域名）。构建日志：
   `WARNING! Due to builds existing in your configuration file, the Build and Development Settings defined in your Project Settings will not apply`、
   `Using Python 3.12 from .python-version`、`Using uv 0.13.5`、`Installing required dependencies from pyproject.toml`，构建 8 秒，上传 113 个文件。
   结论：`builds` 配置仍被接受；**控制台的 Install Command 在 `builds` 配置下不生效，依赖按 `pyproject.toml` 安装**；`.vercelignore` 没有妨碍构建与运行。
2. 对新部署用 `vercel curl` 冒烟：`GET /mcp` → **405，0.76 秒**；连续两次 POST（`initialize`、`tools/list`）均 200，返回 stateless 8 个工具。
3. `vercel promote <deployment-url> --yes` → 1 秒完成。生产别名 `/healthz`：`version 0.3.0`、`toolset stateless`、**`sessionManager: "lifespan"`**
   （即该运行时**确实执行 ASGI lifespan**，与 vercel/vercel#11677 的描述不同）、无路径字段、`staticSiteSource: "not-built-yet"`（懒构建如预期）。
4. `scripts/verify_remote.py https://gem-arc.vercel.app`（无令牌，即无鉴权形态）：**25 项全部 PASS，退出码 0**。覆盖：
   `/healthz` 无路径与无令牌材料、控制台与 `/site/` 200、GET/HEAD/DELETE `/mcp` 均 405（约 0.5 秒）、OAuth 探测 404 JSON、
   `initialize`、8 个工具名合法、`new_model → validate → simulate → render` 往返、`kit` 下载 177630 字节且 SHA-256 与清单一致、
   `If-None-Match` → 304、`kit.url` 可达且相同（因此 **`VERCEL_PROJECT_PRODUCTION_URL` 默认暴露**，`kit.url` 为 `https://gem-arc.vercel.app/kit/static.zip`）、
   官方 MCP 客户端握手（协议 `2025-11-25`，8 个工具）、流量后 session manager 仍在运行。

副作用（须知）：`vercel curl` 首次运行时**自动在项目里创建了 "Protection Bypass for Automation" 密钥**；不需要时可在项目
Settings → Deployment Protection 撤销。`vercel link` 会生成含 `VERCEL_OIDC_TOKEN` 的 `.env.local`（已被 `.gitignore` 与 `.vercelignore` 排除，用完应删除）。

### UNVERIFIED（没有人运行过）

- **带令牌的部署**：`vercel env add GEMARC_MCP_TOKEN production --sensitive` 的写入、之后的新部署、`verify_remote.py --token` 的 401/200 分支在真实域名上的表现
  （写密钥的步骤被运行环境的权限分类器拦下，需由账号所有者自己执行；当前线上为**无鉴权**形态）；环境变量变更后的生效时机；
- 现代 `functions` / `framework` / `[tool.vercel]` 配置（§10）；`maxDuration` 与各项平台限额（§11）；
- 预览（非生产）环境的环境变量作用域；
- claude.ai 自定义连接器的认证限制；
- 真实浏览器中的控制台（含令牌流程）；
- 懒启动分支在真实 Vercel 上是否被走到（实测走的是 lifespan 分支）。

## 13. 什么时候不该用 Vercel

- 需要多用户同时编辑同一工程的架构模型 → 用常驻容器（Railway / Fly.io / Cloud Run）加持久卷。
- 需要导出产物供用户下载 → `stateless` 工具集已经把固定件做成了 `/kit/*.zip` 下载，
  其余产物由 `gemarc_render` 内联返回；更大的产物要加对象存储直传，而不是依赖函数本地磁盘。
- 只想要一个可分享的 5D 架构站 → 直接部署 `examples/demo-site`，不需要 MCP 端点。
