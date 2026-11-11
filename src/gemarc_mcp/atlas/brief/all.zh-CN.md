# GemArc Atlas 工作指令

目标：把一个系统（as-is：它的代码和文档；to-be：用户给的需求文本）变成经过校验的事实。你只写紧凑格式的事实，其余全部由经过测试的代码推导和渲染（文档、图、提示词、仿真、HTML 查看器）。不要自己写 HTML 或文档，不要编造事实：不知道就写 TBD，或降低 `verif=`。

## 循环
1. 先 `init` 一次（project、title、mode asis|tobe；用户在服务器配置里设 `GEMARC_ALLOW_WRITE=1` 与 `GEMARC_WORKSPACE_ROOT`，遇到 `GEMARC_AUTH_002` 就停下告诉用户）。to-be 的需求文本原样存到 `atlas/user/<name>.txt`（目录已建好）。
2. 重复：`status view=next` -> 用 `put` 写它指出的那个切片 -> 读 `put` 回复里的 `next` 行并照做；只在红稿之后、续接、大约每 5 次 put、阶段切换时再调 `status view=next`。`put` 返回 invalid 就用 `put mode=patch` 修。回复带 `retry:true` 不算修复轮：几秒后原样重发。
3. 遇到 `done:true`、`phase:needs-user`（把 `needsUser` 原样交给用户，绝不替用户决定）或同一切片修了 3 轮仍不过（报告 `blocked` 并附最后的诊断）就停；`next` 里的 `0 STOP` 行是同一个意思。
4. `phase` 为 `build` 时，按 `next` 点名的顺序过各道门：`simulate`（盲测判定，先封存）、`evidence`（as-is：把引用的锚点对着真实文件核验；没有配置证据根就是 needs-user：由操作者设置 `GEMARC_ATLAS_ROOTS`，或用户同意降低 `verif=`）、`build`（写 `dist/` 和 `oracle-sheet.md`）、`verify`（浏览器门；SKIPPED 不算通过：停下，告诉用户装 Edge 或 Chrome，或由用户把理由写进 `atlas/waive-browser.txt`，这个文件只有用户写）。可选：`doc` 显示文档里哪里还有 NOT-AUTHORED 空位。
5. 然后做且只做一次自审（`phase:review` 时）：`gemarc_atlas_review action=report`，只修它列出的问题，`action=record` 并带 `notes="fixed=<ids>; open=<ids>"`（要先 report；命令行：`python -m gemarc_mcp.atlas review <project> --action report|record --human`）。修复改了模型，要把上面的门重新过一遍，`record` 才会被接受；这不是第二轮自审。再给最终报告（先读 status 回复里的 `openBlocks`、`waived` 和 `await` 行，逐条写进去），并把 `atlas/dist/oracle-sheet.md` 交给用户：这些期望等他确认。

## 写什么
- 切片名 `[a-z0-9-]{1,40}`，按阅读顺序编号：`00-space`（buildings、floors、rooms），`10-structure`、`11-structure` ...（nodes、edges、evidences、machines、transitions），`20-scenarios` ...（tests、cases、events、expect、interventions、requirements、tbds、policies）。
- 大小：每个切片约 11 个节点连同它们的边和证据（4-7 KB）；超过 8 KB 是警告，超过 64 KiB 被拒。总共大约每 11 个节点 put 两次；169 个节点约 25 次 put，每次不超过 3K token。
- 格式：`#atlas 1`，再是原样抄写的 `## name: col | col | opts` 表头，行 `cell | cell | key=value ...`，`@set key=value` 默认值（只能是标量），`~` 取默认值，边写完后用 `@final edges`。各段表头见 `brief section=format`；可用 id 见 `brief section=vocab`。to-be：按 `status` 的 `tobe step N/7` 行做；用户回答的 TBD 要原话存进 `atlas/user/<name>.txt`，`by=user` 的 policy 在 text 里引用那句话（'...'）。
- 证据行 `id | file | anchor | topic`（anchor 是源码里的一行原文；to-be 用 `user:<name>` 和需求原句）。Implemented 必须有代码证据；没把握就写 `verif=Designed|Inferred|Hypothetical`。

## 派生项：绝不要写（DV-001）
边 id、端口、verification 默认值、证据的 sourceKind 与 lineRange、状态机的 states 与 terminal、转移 id、owns 边、事件 seq、用例 policy 与 linked tests、元素链与端口、需求的 edges，以及所有下游产物（图、轨迹、文档、提示词、布局、标签）。完整清单：`brief section=derived`。

## 修复
`put` 遇到 ERROR 什么都不提交，并把你的全文存成该切片的红色草稿（不会丢）。用 `put mode=patch` 只发改动的行：同一标识替换（id；边 from|rel|to；expect 的 case；转移 machine+from|to），`-id` 删除一行，事件块 `@set case=X` 替换 X 的全部事件。不要重发或重读整个切片。先修列出的第一条错误：其余常常是它的后果。WARN 和 INFO 不阻断；还没定义的 id 先记为 pending，接着写定义它的切片。

## 读 `status view=next`
`{done, phase, next[], blockers[], needsUser[]}`。`next` 最多 10 行；行首数字是优先级（1 红色草稿或错误，2 BLOCK 空位，3 未定义的 id，4 需求缺口，5 没有期望的用例，6 不可达状态，7 覆盖）；最后一行是维度就绪度 1D-5D。phase 取值：collect、author、repair、build（有门是红的：simulate、evidence、build 或 verify）、review、done、needs-user。`index` 列出已有内容（nodes、room:、req:、case:、ids:前缀）；`check` 永不写入（mode=slice 检查单个切片，mode=full 检查整个项目）。

## 期望
`expect` 行写的是应该发生什么，依据需求或你读到的代码，在任何仿真之前写，绝不从引擎结果抄。终态由固定规则推出：节点从 `Idle` 开始；`Call` 让接收方 `Running`，直到有 `Return`（发送方随之 `Completed`）；`Failure` 让接收方 `Failed`。详见 `brief section=oracle`。

## 成本
不重发有效的行，不重读切片，引用唯一锚点而不是粘贴代码，sub、label、title 各写一短行。详见 `brief section=economics`；文档见 `brief section=docs`。
