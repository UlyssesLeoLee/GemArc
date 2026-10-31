# GemArc Atlas：用户指南

Atlas 让 AI 产出 5D 设计资料（1D 文档、2D 结构、3D 空间、4D 运行、5D 对比）又便宜又稳定：AI **只写事实**（紧凑、经过校验的 `.atl` 文本切片），固定且经过测试的代码负责校验、推导、模拟和渲染其余一切（边 id、端口、状态、三层文档、图、提示词、查看器页面）。

**现状（先说清楚）**：Atlas 只在本地使用，没有发布，没有部署。`.vercelignore` 把 `src/gemarc_mcp/atlas`、`atlas_tools.py` 和 `tools/` 挡在 Vercel 上传之外，远程默认工具集仍是 `stateless`；发布需要用户明确同意（`src/gemarc_mcp/atlas/provenance/authorisation.tsv` 里的 `publication` 行现在是 PENDING，测试 `test_atlas_publication_gate.py` 守着这一点）。

## 1. 这份目录里有什么

| 文件 | 内容 |
|---|---|
| `README.md`（本文） | 用户指南：安装、怎么说、输出、停止点、确认、限制 |
| `ATLAS_TOOLS.md` | 13 个工具、入口提示、完成契约（`status` 的门）的契约 |
| `TOKEN_LEDGER.md` | token 账本：脚本流水线的字节实测、与设计账本的对照，留着「待实测(LLM)」一节 |
| `examples/` | Claude Code、Claude Code 无头模式、Codex、Hermes 的配置示例和说明 |
| `spec/` | 模型、查看器数据包、oracle、文档骨架的规格 |

## 2. 安装服务器（一次）

先在仓库根目录建虚拟环境并安装（需要 Python 3.11 及以上；只做一次，`<仓库>` 是你克隆 GemArc 的目录）：

```
cd <仓库>
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

下面所有命令里的 Python 都是**虚拟环境里的** `<仓库>\.venv\Scripts\python.exe`：系统里裸的 `python` 通常没有装 `mcp`，会报 `The official mcp package is required: pip install -e .`。

服务器就是 `<仓库>\.venv\Scripts\python.exe -m gemarc_mcp`，用环境变量选工具集。**客户端启动服务器时只传过滤后的环境，所以下面每个变量都要写进客户端的配置里，不能指望继承你的 shell。**

| 变量 | 含义 |
|---|---|
| `GEMARC_TOOLSET=atlas` | 选 Atlas 工具集（13 个工具加一个入口提示）。一个服务器进程只带一个工具集；要同时用 v1 工具，另外注册一个 `gemarc` 条目 |
| `GEMARC_WORKSPACE_ROOT=<工作区>` | 项目写在这里。选一个已存在的空目录，**不要选在你要分析的仓库里面** |
| `GEMARC_ALLOW_WRITE=1` | 允许写项目的工具工作（`init`、`put`、`simulate`、`evidence`、`build`、`verify`、`review` 的 `record`）；不设时它们返回 `GEMARC_AUTH_002` |
| `GEMARC_ATLAS_ROOTS=main=<被分析的仓库>` | 被分析源码的位置，`别名=路径;别名=路径`。模型只能写别名；没有它 as-is 里 Implemented 的声明无法核验 |
| `GEMARC_ATLAS_BROWSER` | 可选：Edge 或 Chrome 的完整路径（装在标准目录以外时） |
| `PYTHONUTF8=1` | 中文不乱码 |

三个客户端（配置文件和逐步说明在 `examples/`）：

- **Claude Code**：`claude mcp add gemarc_atlas -e GEMARC_TOOLSET=atlas -e GEMARC_WORKSPACE_ROOT=<工作区> -e GEMARC_ALLOW_WRITE=1 -e GEMARC_ATLAS_ROOTS=main=<仓库> -e PYTHONUTF8=1 -- <仓库>\.venv\Scripts\python.exe -m gemarc_mcp`，或 `claude --mcp-config docs/atlas/examples/claude-code.mcp.json`；无头模式见 `examples/README.md` 3.1。
- **Codex**：把 `examples/codex.config.toml` 追加到 `~/.codex/config.toml`。
- **Hermes**：把 `examples/hermes.config.yaml` 合并到 `~/.hermes/config.yaml`。

**上面的命令和 `examples/` 里的文件都带占位符，必须先换成真实路径，原样用会启动失败**：`<仓库>`（`<repo>`）是 GemArc 仓库的绝对路径（也是 `GEMARC_ATLAS_ROOTS` 里被分析的仓库，如果你分析的就是它），`<工作区>`（`<workspace>`）是一个已存在的空目录，在被分析的仓库**之外**。`init` 不会拒绝一个放在被分析仓库里面的工作区，但会在回复 `next` 的第一行警告。

不接任何客户端也能试：`<仓库>\.venv\Scripts\python.exe scripts/atlas_mcp.py list-tools`（经真实 stdio 与服务器通话，输出紧凑 JSON；`call`、`get-prompt` 同理；先设好 `GEMARC_WORKSPACE_ROOT`、`GEMARC_ALLOW_WRITE=1`）。没有 MCP 接入但有 shell 的客户端用工具包：`<仓库>\.venv\Scripts\python.exe tools/make_kit.py` 生成 `dist-kit/atlas-kit.zip`（纯标准库，解压即用，不提交），见第 8 节。

## 3. 怎么说「创建5D设计」

一句话就够：**「为这个库创建5D设计」**（根据现有代码还原现状，as-is），或 **「根据这份需求创建5D设计」** 并给出需求原文（设计新系统，to-be）。

MCP 服务器不能主动推动客户端，所以入口是一个 MCP 提示 `gemarc-atlas-create`（参数 `requirement`、`mode`、`project`、`lang`）：

- Claude Code 里它是斜杠命令 `/mcp__gemarc_atlas__gemarc-atlas-create`；
- Codex 和 Hermes：`<仓库>\.venv\Scripts\python.exe scripts/atlas_mcp.py get-prompt gemarc-atlas-create mode=asis project=demo` 取出文本，粘贴给它；
- 仓库里的技能 `skills/gemarc-atlas/SKILL.md` 装好之后，在你说「创建5D设计」「做5D设计资料」「create 5D design」时触发同一条流程。**它不会自己装**：把整个 `skills/gemarc-atlas/` 目录复制到 `~/.claude/skills/gemarc-atlas/`（Claude Code 的用户技能目录）才有效；不装也行，入口提示是同一条流程。

提示的文本就是驱动脚本：读工作指令，建项目，保存需求原文，循环「`status` 说下一步 -> 写一个切片 -> `put`」，红稿用补丁修复，然后按顺序过各道门（盲测判定、证据核验、构建、浏览器门），**恰好一次**自审，最后写报告。状态都在项目目录里，所以随时可以说「继续」，它从 `gemarc_atlas_status` 续上。

## 4. 输出目录里有什么

项目在 `<工作区>/<项目名>/atlas/`：

```
atlas.toml                      项目标识、模式、语言、用到的证据根别名（只有别名，从不写路径）
slices/<名字>.atl               已提交的切片（状态就是这个目录）
user/<名字>.txt                 需求原文和你对 TBD 的回答（原话；引用会对着它核对）
.atlas/                         红稿、历史版本、oracle 账本、证据印记、自审记录、锁（不用管，也不要手改）
out/model.json                  check --emit-model 写出的完整模型（可选）
dist/index.html                 查看器：一个自包含的文件，双击打开（1D 文档、2D、3D、4D、5D、QA 六个页签）
dist/build-info.json            这次构建的大小、各部分、检查结果、发布状态
dist/docs/<语言>/REQ.md BD.md DD.md   三层文档（需求、基本设计、详细设计）
dist/diagrams/*.mmd             Mermaid 图（结构图、已封存用例的序列图与状态图）
dist/prompts/*.md               派生的提示词
dist/oracle-sheet.md            交给你确认的期望清单（见第 6 节）
dist/.verify/                   浏览器门的报告和印记
```

页面在参考规模（169 个节点、374 条边、23 个用例）下约 1.9 MB。2 MiB 是测试对参考规模的预算，不是构建的上限：构建在 3 MiB 时警告（BD-005），8 MiB 时拒绝；页面随模型增大（验证者对 400 个节点的合成模型实测约 2.4 MB，没有警告）。同一个项目构建两次，字节完全一致。

## 5. 怎么读 needs-user 停止点

`gemarc_atlas_status` 的 `phase` 是 `needs-user` 时，模型会停下，把 `needsUser` 的每一行**原样**问给你，绝不替你决定、绝不编造。常见的几种：

| 它说 | 意思 | 你做什么 |
|---|---|---|
| `decide TBD <id>: ...` | 需求里有只有你能回答的歧义 | 回答；模型把你的**原话**存进 `atlas/user/<名字>.txt`，再写一行 `by=user` 的策略并引用这句原话。引用不在你保存的文字里，就不算你决定了 |
| `approve the oracle sheet atlas/dist/oracle-sheet.md (N edited expectation(s))` | 一条期望在看到过结果之后被改过（`revised`） | 看 `dist/oracle-sheet.md`，勾选或保留 `APPROVE-ALL` 行，写进 `atlas/oracle-confirm.txt`（第 6 节） |
| `no evidence root for main` | as-is 的证据要对着源码核验，但服务器没配证据根 | 在服务器配置里设置 `GEMARC_ATLAS_ROOTS`（操作者的事），或同意把这些声明降为较低的 `verif=` |
| `browser SKIPPED` | 找不到 Edge 或 Chrome，页面没有被验证 | 装一个；或把理由写进 `atlas/waive-browser.txt`（只有你能写；页面会被标成「未验证」，FAIL 永远不被豁免覆盖） |
| `blocked`（不是 needs-user，是报告） | 同一个切片修了 3 轮还是红，或 `put` 次数用完 | 看它附的最后一次诊断，决定怎么办 |

停下不是失败：说明下一步需要你。你回答之后说「继续」。

## 6. 怎么确认 oracle 表

「期望」是意图：AI 要在看到引擎结果**之前**写好，引擎再盲测（只说通过与否和不符的个数，不说是哪些）。`dist/oracle-sheet.md` 每一条还没经你确认的期望一行，用你的语言写（例如「库存服务宕机时，借阅仍应以会员界面 Completed 结束」），带 id、`hash8` 和状态，外加一行 `APPROVE-ALL <digest>`。

- 逐条确认：把想批准的行复制进 `atlas/oracle-confirm.txt`，勾成 `- [x]`。
- 一次全批：把 `APPROVE-ALL <digest>` 那一行写进 `atlas/oracle-confirm.txt`。
- **这个文件只有你写，工具从不写它。** 一个有 shell 的 AI 可以伪造它或改账本，那时徽章会写「来源未验证」。这是声明，不是认证。
- 在 to-be 里，这张表同时就是验收标准（期望复述的是你的需求）。

没有你的确认也能到 `done`（sealed 的期望不挡路，它们写在表里、写进最终报告）；被改过的（`revised`）和账本被篡改的需要你确认才能发布。

发布构建（`gemarc_atlas_build` 的 `release=true`，需要 `gate=smoke`）要求：oracle 门（先跑判定和变异体）、证据对源码重新核验且没有过期的印记、文档里没有 `[NOT-AUTHORED]` 空位、浏览器门通过。它先在暂存目录里组装，**全部通过才换进 `dist/`**；被拒绝时 `dist/` 里此前的页面和文档一个字节也不动（只刷新交给你的 `oracle-sheet.md`），回复列出每一类阻挡项（详见 `ATLAS_TOOLS.md` 的 `gemarc_atlas_build`）。发布必须带文档：`docs=false` 的发布会被拒绝，因为换入会删掉此前的文档、图和提示词。

## 7. 限制

- 只在本地：没有部署、没有公开路由；默认工具集和远程行为没变。
- 事实的可信度取决于证据：证据核验器只检查锚点**在声称的位置存在**，不检查它是否真的支持那条论断（所以 `topic` 必须写论断本身）；as-is 的 Implemented 声明需要源码锚点且盖过印记，Designed、Hypothetical、Unknown 不需要。
- 盲测判定只能保证「期望先于结果」这条记录；它**不能**发现一个照抄引擎输出的期望。真正的防线是封存、账本、`revised` 标记、发布门和你的确认。
- 有写权限的人能改的东西不在防线内：账本、确认文件，还有 `atlas.toml` 的 `mode`。把 as-is 项目手工改成 `tobe` 之后，每个声明的 `verification` 会被重新推导成 `Designed`（模型里不再有 Implemented 声明，证据门自然没有对象），页面和文档按 to-be 发布，`status` 则会停在 `needs-user`（要保存的需求原文）；发布构建不看 `status`。这是对模型诚实的降级，不是伪造，但它是一次手工编辑，工具拦不住。
- 浏览器门需要本机装有 Edge 或 Chrome；没有就是醒目的 SKIPPED（不算通过）。三个宽度（1024、1366、1600）都要过。
- 切片文本最多 64 KiB（超过 64 KiB 存为红稿，超过 128 KiB 拒绝）；每个切片约 11 个节点，`put` 总次数的预算是 8 + 2 x ceil(节点数/11)。
- 只有 **仅聊天**（没有文件、没有 shell）的客户端能用的是 `atlas_remote` 的两个只读工具（`gemarc_atlas_brief`、`gemarc_atlas_check`）：只能检查单个切片，没有持久化、没有引擎、没有 oracle，只适合很小的图册（约 40 个节点）；这条路径没有部署。
- Python 3.11 及以上（在 3.12 上测试）；核心只用标准库，产品路径不需要 Node 和网络。
- 真实客户端（Claude Code、Codex、Hermes）还没有端到端验收；自动测试用官方 MCP SDK 经真实 stdio 跑完整流程，配置示例只验证语法和服务器能按它们启动。

## 8. 工具包和 `/kit/atlas.zip`

`<仓库>\.venv\Scripts\python.exe tools/make_kit.py` 生成 `dist-kit/atlas-kit.zip`（已加入 `.gitignore`，不提交）：纯标准库的 atlas 包、它用到的两个 v1 模块、启动器 `atlas.py`、README（中文为主，带英文和日文各一行）和 `docs/atlas/spec`。解压后 `python -I atlas.py <命令>`（`brief`、`init`、`put`、`check`、`status`、`simulate`、`evidence`、`build`、`review` ……）；没有 `mcp`、没有 Node、不联网。自动测试把它解压到临时目录，用不带 site-packages 的 Python 跑 `check`、`status`、`evidence`、`build`，并核对 `build` 写出的页面与仓库构建的逐字节相同。

ASGI 应用里的路由 `/kit/atlas.zip` 是**本地的可选项**，默认关：只有环境变量恰好是 `GEMARC_ATLAS_KIT=1`（别的值都不行）时才提供下载，否则它和任何未知的 kit 一样回 404。Vercel 的上传里没有 atlas，所以即使设了这个变量，那里也只会回 503 和错误码 `GEMARC_ATLAS_KIT_001`（fail closed）。「是否把 Atlas 放到托管端」的决定是 **否**（默认），要改需要你明确授权发布。`verify_remote.py --atlas` 可以对一个本地启动的 `GEMARC_TOOLSET=atlas_remote` 应用检查这两个工具和这条路由。在仓库根目录（PowerShell）：

```
$env:GEMARC_TOOLSET = "atlas_remote"; $env:GEMARC_ATLAS_KIT = "1"
.\.venv\Scripts\python.exe -m uvicorn api.index:app --port 8000        # 另开一个窗口跑下面这条
.\.venv\Scripts\python.exe scripts\verify_remote.py http://127.0.0.1:8000 --atlas --atlas-kit on
```

`--atlas-kit on` 要求路由真的给出 zip（服务器有标志但没有 atlas 时的 503 在 `on` 下算失败，只有 `auto` 接受）；`off` 要求路由不存在。

## 9. 想看更多

- 工具逐个的契约和回复形状：`ATLAS_TOOLS.md`；完成契约（哪些门、什么算 `done`）：同一文件第 6 节。
- 一次完整运行要花多少字节和 token：`TOKEN_LEDGER.md`。
- 设计里「不要编造」「先写期望再看结果」「恰好一次自审」的理由：各工具说明里的 `brief` 一节（`gemarc_atlas_brief`，`section=oracle`）。
- 测试怎么跑：`tests/atlas/README.md`。
