# GemArc Atlas 查看器数据契约：`window.__ATLAS__`

状态：**规范性（normative）**，适用于构建（`assemble.py`）与查看器运行时之间。本文规定嵌入单文件 HTML 的 JSON 的精确形状，以及查看器的纯模块（`src/gemarc_mcp/atlas/viewer/src/*.js`）读取它的方式。展开模型本身的契约在 `model-schema.md`（`gemarc-atlas/1`），本文不重复。

标注约定：**〔引擎通道待确认〕** 表示形状取自参考引擎的结果形状并与引擎通道当前的 Python 实现（`engine/sim.py`、`engine/causal.py`）逐字段对照过，但最终以引擎通道的单元测试为准；**〔文档通道〕** 表示形状已按文档通道的实现（`derive/fragments.py`、`derive/diagrams.py`、`viewer/src/assemble.js`）改写，以那里的单元测试为准。标注都意味着：若实现与本文不同，改本文，不改查看器的读取方式之外的任何东西。

## 1. 总原则

1. **一个全局对象，只读。** 页面在任何脚本之前写入 `window.__ATLAS__ = {...}`（一个 `<script>` 内的 JSON 字面量），查看器只读它，不改它，不复制整个模型。
2. **页面里没有仿真器。** 4D、5D 只播放构建时预先算好的结果（第 4 节），浏览器不能运行未编写的“如果……会怎样”。
3. **只含封存项。** `runs` 只放已封存（oracle 判定为 `sealed`）的用例及其干预对；未封存项**不出现**，查看器对缺项显示空态（第 5 节）。验收：M5(c)、M6(d)。
4. **确定性。** 同一输入产生逐字节相同的 JSON：对象键按字典序，数组保持作者顺序（永不重排），无时间戳（构建时间只写 `build-info.json`），无 `NaN`、`Infinity`、`undefined`（构建时 `allow_nan=False`）。
5. **页面是自包含的。** 嵌入前转义 `</script` 与 U+2028/U+2029，用函数替换器填占位符；构建的 BD-001 在最终 HTML 上扫描 `fetch(`、`import(`、`@import`、`url(http`、远程 `src`/`href`，所以**查看器源码（含注释）不得出现这些字符串**。查看器纯模块由测试扫描保证。
6. **词表是数据，不是代码。** 形状、比例、域色、关系的视图归属、信任域顺序都来自 `profile`（第 3 节）。未知值得到中性默认值与一条警告，**永不抛错**。

## 2. 顶层结构

```
window.__ATLAS__ = {
  "schema": "gemarc-atlas-bundle/1",
  "meta": {...},
  "profile": {...},
  "model": {...},
  "i18n": {...},
  "fragments": {...},
  "closures": {...},
  "runs": {"baselines": {...}, "pairs": [...]},
  "diagrams": [...],
  "calibrationHooks": {...}
}
```

顶层键恰为这 10 个，不得多、不得少。**与任务书中的 9 个键相比多了 `schema` 与 `profile`**：查看器需要词表（形状、比例、域色、关系的视图归属、信任域顺序、三种语言的标签），而它们既不属于模型也不属于 `i18n` 的界面文字，所以 `profile` 单独成键；`schema` 供页面启动时核对版本。

| 键 | 类型 | 生产者 | 读取者 | 必需 |
|---|---|---|---|---|
| `schema` | 字符串，恒为 `"gemarc-atlas-bundle/1"` | `assemble.py` | 启动检查 | 是 |
| `meta` | 对象，见 2.1 | `assemble.py` | 外壳、偏好键 | 是 |
| `profile` | 对象：`profiles/software.json` 原样 | `assemble.py`（读 `meta.profile` 指向的词表） | `makeVocab`（`model-access.js`） | 是 |
| `model` | 对象：展开模型，`schema == "gemarc-atlas/1"` | 展开器 | 全部纯模块 | 是 |
| `i18n` | 对象，见 2.2 | `assemble.py` | 外壳 | 是 |
| `fragments` | 对象，见 2.3 | 文档通道（`fragments.py`） | 1D 文档 | 是（可为空列表） |
| `closures` | 对象，见 2.3 | 文档通道 | 1D 文档 | 是（可为空对象） |
| `runs` | 对象，见第 4 节 | 引擎通道 + `assemble.py` | `playback.js` | 是（可为空） |
| `diagrams` | 数组，见 2.4 | 文档通道（`diagrams.py`） | `search.js`、2D 图表窗口 | 是（可为空） |
| `calibrationHooks` | 对象，见 2.5 | `assemble.py` | 门禁模式（`?gate=1`） | 是 |

### 2.1 `meta`

| 字段 | 类型 | 说明 |
|---|---|---|
| `projectId` | 字符串 | `model.meta.projectId` |
| `title` | 字符串 | `model.meta.title`（可为空串） |
| `modelSha` | 字符串，64 位十六进制 | `model_sha(model)`（canonical 字节的 SHA-256） |
| `modelSha8` | 字符串，8 位 | `modelSha` 的前 8 位，界面抬头与偏好键用它 |
| `prefKey` | 字符串 | `gemarc-atlas:<projectId>:<modelSha8>`，本地偏好的键；不同项目互不串味 |
| `languages` | 字符串数组 | 与 `model.meta.languages` 相同，首项是主语言 |
| `primaryLanguage` | 字符串 | `languages[0]`；`fragments` 与预渲染的因果链文字使用它 |
| `profileId` | 字符串 | `profile.id` |
| `mode` | `asis` \| `tobe` | `model.meta.mode` |
| `counts` | 对象 | `{nodes, edges, cases, interventions, requirements, tbds, sealedCases, sealedPairs}`，整数；门禁与 QA 页的核对基准 |
| `warnings` | 数组 | 构建期词表警告，元素 `{code, subject, detail}`，形状与 `makeWarnings().items` 相同；上限见第 6 节 |

`meta` 不含时间、路径、构建机信息。

### 2.2 `i18n`

```
"i18n": {"languages": ["zh-CN", "ja-JP", "en-US"], "tables": {"zh-CN": {"tab.2d": "..."}, "ja-JP": {...}, "en-US": {...}}}
```

- 界面文字表，键是扁平的点分串，值是字符串。三种语言的键集合必须相同（规则 XP-001，CI 检查）。
- 运行时 JS 里没有任何中日文字面量，也没有面向用户的英文字面量：显示文字一律由键查表（门禁与 CI 双重检查）。**例外**：纯模块写入警告收集器的 `detail`（见第 6 节）是面向作者的诊断串，不是界面文字，不走 `i18n`；界面若要展示警告，按 `code` 查键。
- 词表标签（种类、关系、状态……）不放在这里，读 `profile` 里各条目的 `label[<locale>]`。
- `scene-buffers.js` 的 `markPrefix`（`{cause, effect}`）与 `search.js` 的 `kind` 键由界面通过本表翻译；这两个纯模块自己不产生显示文字。

### 2.3 `fragments` 与 `closures`〔文档通道〕

```
"fragments": {"language": "zh-CN", "modelSha8": "0a1b2c3d", "unselected": "...",
              "items": [{"fid": 0, "chapter": 3, "order": 12, "level": "BD", "grp": "g1", "md": "..."}],
              "frame": {"REQ": {"head": "...", "chapters": [{"n": 1, "h": "## 1. ...", "note": "", "empty": "", "groups": ["g1"]}]}, "BD": {...}, "DD": {...}},
              "groups": {"g1": {"head": "...", "empty": "", "join": "\n"}},
              "always": [7, 15], "elements": ["<elementId>", ...]}
"closures":  {"<elementId>": [fid * 8 + levelMask, ...]}
```

- `fragments.language` 等于 `meta.primaryLanguage`；`items` 只含主语言，其它语言以文件形式导出。`fid` 等于 `order` 也等于数组下标；`level` 是使用该片段的最浅详细度。
- 闭包是**每个元素一张表**，条目是 `fid * 8 + levelMask`，`levelMask` 的第 0、1、2 位依次表示 `REQ`、`BD`、`DD` 是否使用该片段；`always` 是每份文档都有的片段（同一编码）。元素 id 是节点 id、边 id、用例 id、需求 id；`fragments.elements` 给出规范顺序。
- 浏览器的自由选择文档 = `always` 与所选元素闭包的并集，取目标详细度的位，按 `(chapter, order)` 排序，按 `grp` 分桶后依 `frame` 连接；由 `viewer/src/assemble.js` 的 `assemble(ds, level, ids)` 完成，与 Python 的 `derive/assemble.py` 逐字节相同，`calibrationHooks.docSelections` 在每次构建中核对。`md` 里的四个占位符（本档文档号、相邻两档的文档号、目标行）由 `assemble` 填写，文档号的哈希是 `hash32Hex`（UTF-8 字节的 32 位 FNV-1a，8 位小写十六进制）。
- 预算：主语言原文 ≤ 400 KB，超过 700 KB 触发设计 9.1 的退路（预先算好的“需求 × 详细度”文档）。
- 查看器的其它纯模块**不读** `fragments` 与 `closures`，只有 `assemble.js` 读。

### 2.4 `diagrams`〔文档通道；形状取自 `derive/diagrams.py` 的 `UNIT_FIELDS`〕

数组，每个元素是一个图表单元：

```
{"id": "component-<building>", "type": "component|class|state|er|sequence", "title": "...",
 "covers": {"nodeIds": [], "edgeIds": [], "caseIds": []}, "requirementIds": [],
 "evidenceLevel": "Designed", "evidenceIds": [], "data": {...}, "mermaid": "...", "issues": []}
```

`search.js` 只读 `id`、`title`、`type`。`sequence` 单元只描述已封存的用例（由 oracle 通道登记）。

### 2.5 `calibrationHooks`

```
{"docSelections": [{"level": "BD", "elementIds": ["..."], "hash32": "0a1b2c3d"}],
 "traceLengths": [{"caseId": "...", "interventionId": null, "length": 12}],
 "scene": {"nodeCount": 169, "buildingIds": ["..."], "unassignedCount": 0}}
```

- `docSelections`：K 个规范选择，`hash32` 是 Python 对组装出的文字的 UTF-8 字节计算的 32 位 FNV 哈希（a 变体，小写 8 位十六进制）。门禁模式在页面里重新组装并比较，孪生实现漂移会让每次构建失败。
- `traceLengths`：门禁断言“4D/5D 的步数等于轨迹长度”的基准；`interventionId` 为 `null` 表示基线。
- `scene`：门禁与 QA 页的核对基准；`unassignedCount` 是 `arrangeView` 返回的 `unassigned` 数组长度，期望 0（非 0 时 QA 页显示警告）。

## 3. 查看器读取的词表字段（`profile`）

纯模块只读下表中的字段；`profile` 里的其它字段它们不看。缺字段或类型不对一律走中性默认值（`makeVocab` 对任何输入都不抛错，`null` 就是全中性场景）。

| 条目 | 读取的字段 | 用途 | 中性默认值（附警告） |
|---|---|---|---|
| `kinds[]` | `shape`、`scale`、`level`、`domain`、`lane`、`record` | 宝石形状、尺寸、最浅详细度、域、过程视图的行 | `box`、1、`DD`、无、1（`GEMARC_ATLAS_VIEW_001`） |
| `relations[]` | `views`、`level`、`static`、`runtime`、`color`、`trustBoundary` | 视图归属、详细度、静态淡化、空闲边颜色、跨信任域虚线 | 仅出现在 `logical-view`、`process-view`、`security-view`；`#5b7090`；非静态；实线（`GEMARC_ATLAS_VIEW_002`） |
| `views[]` | `kinds`（`null` 表示全部） | 视图的节点过滤 | 全部节点（未知视图 id 回退到逻辑视图，`GEMARC_ATLAS_VIEW_004`） |
| `trust[]` | `id`（顺序即安全视图的分区顺序）、`domain` | 信任域到域的映射 | 默认信任域；未知值自成一区（`GEMARC_ATLAS_VIEW_003`） |
| `domains[]` | `color` | 节点底色 | `#5b7090`（`GEMARC_ATLAS_VIEW_010`） |
| `layers[]` | `domain`、`level`、`levelKinds` | 房间 `layer` 的域与详细度提升 | 忽略 |
| `levels[]` | `id`、`rank` | 详细度顺序 | `REQ`、`BD`、`DD` |
| `nodeStates[]` | 可选的 `color` | 状态环颜色 | 内置表；未知状态中性灰环（`GEMARC_ATLAS_VIEW_007`）；该节点自己的状态机的状态不算未知（见下） |
| `defaults.trust` | 字符串 | 没有任何信任信息的节点 | 无 |

要点：

- **建筑顺序就是 `model.space.buildings` 的数组顺序**；不再有写死的建筑列表。楼层高度读 `floor.level`（同一建筑内重复、非数字或绝对值超过 `MAX_FLOOR_LEVEL`（`1e6`）的 `level` 会被移到建筑顶部并警告 `GEMARC_ATLAS_VIEW_006`，位置仍然有限且互不重叠；超过上限的值若直接使用会让单精度缓冲区溢出）。
- **无法解析到建筑的节点**（房间不存在、房间的楼层不存在、楼层的建筑不存在）进入 **未分配区**：3D 的框键与 2D 的建筑 id 都是 `model-access.js` 的常量 `UNASSIGNED_ID`（`"~unassigned"`；真实 id 以 ASCII 字母或数字开头，所以不会相撞），同时 `arrangeView`/`cardLayout` 返回 `unassigned`（节点 id 数组）。不再丢弃节点，也不再让渲染器抛错。
- **机器状态**：`makeBuffers` 对 `id` 属于 `model.machines` 的节点，把该机器的 `states` 当作已知状态。取值既不是 `profile.nodeStates` 也不是内置状态表、但属于该节点自己机器的状态，**不画状态环与光晕、不警告**（查看器没有这个状态的颜色；与参考实现一致）；取值是 profile 状态（例如 `Running`）则照常画环。不属于任何机器的节点取到未知状态，仍然画中性灰环并警告 `GEMARC_ATLAS_VIEW_007`。`stateColor(vocab, state, ownStates)` 的第三个参数（可空的集合）就是这个豁免。
- **数据视图的成员**：先取 `views[].kinds` 列出的种类的节点（核心），再取“与核心节点之间有一条关系、该关系的 `views` 含 `data-view`”的其它节点（所有者），**两个方向都算**。参考实现只认少数几个写死的关系；本契约改由 profile 的 `relations[].views` 决定，所以当 `belongsTo`、`references` 这类（profile 把它们放进数据视图的）边连到数据节点时，数据视图会比参考实现多显示那端的节点与这条边。要收窄，改 profile（把关系从 `data-view` 的 `views` 里去掉），不改代码。
- **id 的假设**：查看器假定节点 id、边 id 在模型内唯一（展开模型的契约检查保证；手工改过的整包不保证，重复 id 不报警告，位置取后一个）。id 可以与 `Object.prototype` 的成员同名（`constructor`、`toString`、`valueOf` ……），所以**所有按 id 取值的对象**（`positions`、`worldPoints`、`retreat`、`overlay.nodeStates/marks/edgePaints`、`runs.baselines`）都不继承原型：模块输出的是无原型的字典（`dict()`），读取一律走 `own(obj, key)`。界面通道读取这些对象时也必须用 `own`，不要假设它们是普通对象。
- 模块中没有任何业务房间字面量或 id 前缀判断；门禁（`view-genericity.test.mjs`）扫描源码保证。

## 4. 运行结果（`runs`）

```
"runs": {
  "baselines": {"<caseId>": Run, ...},
  "pairs": [Pair, ...]
}
```

- `baselines` 是对象，键为用例 id，值为该用例**无干预**的运行（4D 播放与 5D 的 A 侧）。
- `pairs` 是数组，每项是一条作者写的（干预，用例）对，上限 200 对（构建时超出则报错）。A 侧运行取同一用例的 `baselines[caseId]`，不重复嵌入。
- 查看器用 `playback.js` 的 `baselineFor(runs, caseId)` 与 `pairRuns(runs, caseId, interventionId)` 取用；找不到返回 `null`，界面显示空态。

### 4.1 `Run`〔引擎通道待确认；字段取自 `engine/sim.py` 的 `simulate`〕

| 字段 | 类型 | 查看器是否读取 | 说明 |
|---|---|---|---|
| `caseId` | 字符串 | 是 | |
| `status` | `OK` \| `DRAFT` \| `REJECTED` \| `LIMIT_EXCEEDED` | 是（判定空态） | `DRAFT`、`REJECTED` 的 `trace` 为空 |
| `errors` | 字符串数组 | 显示 | |
| `trace` | `Event[]` | 是 | 按 `index` 排列，`trace[i].index == i` |
| `initialState` | `{nodeId: state}` | 是 | 折叠的起点；状态值 ∈ `profile.nodeStates` 或机器的状态 |
| `finalState` | `{nodeId: state}` | 否（QA、因果效果显示） | |
| `snapshots` | `[{index, state}]` | 是（可选加速） | `index` 升序，首项 `index == -1` 且 `state == initialState`，之后每 4 个事件一个（引擎实际行为）；缺失或为空时从 `initialState` 折叠 |
| 其余字段 | 任意 | 否 | 原样保留，查看器忽略 |

### 4.2 `Event`（`trace[i]`）〔引擎通道待确认〕

| 字段 | 类型 | 查看器是否读取 | 说明 |
|---|---|---|---|
| `index` | 整数 | 是 | 0 起 |
| `sequence` | 整数 | 否 | `index + 1` |
| `eventId` | 字符串 | 显示 | `<caseId>#<seq>`，重试等带后缀 |
| `logicalTime` | 数 | 显示 | |
| `sourceNodeId`、`targetNodeId` | 字符串 | 是 | 可能是插入元素的 id |
| `edgeId` | 字符串 \| null | 是 | 事件绑定的边；插入元素时可能是链边 id |
| `eventType`、`outcome` | 字符串 | 是（`outcome` ∈ `Blocked`、`Denied`、`Failed`、`Timeout` 时边显示为“被阻断”样式） | |
| `stateChanges` | `[{nodeId, from, to}]` | **是（折叠的依据）** | 引擎输出的键名；设计文字里的 `changes` 是同一数据，`playback.js` 的 `changesOf` 优先读 `stateChanges`，没有时读 `changes`，两者都没有按空处理 |
| `cause` | `{code, args}` \| null | 否 | 结构化原因；文字由引擎在构建时渲染成 `why`（见 4.4），查看器不解析 `cause` |
| 其余字段（规则、证据、错误码、重试序号、合成标记……） | | 否 | 日志行显示用，查看器的纯模块不读 |

**折叠规则**：位置 `idx` 的状态 = `initialState` 依次应用 `trace[0..idx]` 的 `stateChanges`，每条 `{nodeId, to}` 置 `state[nodeId] = to`。`idx = -1` 是初始状态。`seekState(run, idx)` 从不晚于 `idx` 的最近快照起折叠，结果必须等于 `stateByFold(run, idx)`（测试对每个位置断言）。播放位置 `idx` 只在 `normIndex(idx, length)` 一处归一化：有限的数取整并夹到 `-1..length-1`，其它（`NaN`、字符串、`null`、`undefined`、`Infinity`）取 `-1`；输入若不是区间内的整数，调用者警告 `GEMARC_ATLAS_VIEW_008`。`seekState`、`stateByFold`、`clampIndex`、`replayView` 都走它，所以对任何输入结果一致，且不抛 `RangeError`。

### 4.3 `Pair`〔引擎通道待确认〕

```
{"caseId": "...", "interventionId": "...", "status": "OK|DRAFT|REJECTED", "errors": [],
 "B": Run, "diff": {...}, "chain": Chain}
```

- `B` 是施加该干预后的运行；`diff` 是引擎比较两次运行的结果（含 `outcomeClass`），查看器只在判定空态与显示结论时用 `outcomeClass`。
- `status` 不是 `OK` 时 `B` 可以为 `null`，`chain` 仍带 `root` 与 `headline`（5D 显示原因而不播放）。

### 4.4 `Chain`（5D 的因果链）〔引擎通道待确认；字段取自 `engine/causal.py` 的 `causal_chain`〕

```
{"caseId", "interventionId", "status": "OK|DRAFT|REJECTED", "outcomeClass", "question", "headline",
 "root": {"id": "root", "role": "cause", "type", "label", "nodeIds": [], "edgeIds": [], "facts": [], "element": Element|null},
 "steps":   [{"id", "side": "A|B", "role", "kind", "index", "at", "aIndex", "bIndex", "eventType", "sourceNodeId", "targetNodeId",
              "edgeId", "outcome", "cause", "why", "logicalTime", "parents", "via", "design"}],
 "effects": [{"nodeId", "a", "b", "name", "parents", "design"}],
 "rows": [...], "counts": {}, "design": [...], "confidence": {...}}
 Element = {"id", "kind", "name", "between": [nodeId, nodeId] | null, "replacedEdgeId", "edges": [{"id", "sourceId", "targetId"}], "roomId": string | null}
```

查看器读取的字段：`root.nodeIds`、`root.edgeIds`、`root.element`、`steps[].{side, role, at, bIndex, sourceNodeId, targetNodeId, edgeId, outcome}`、`effects[].nodeId`、`status`。上面只列了其中一部分字段，引擎另有字段，查看器忽略。**`status` 不是 `OK` 的链（例如被拒绝的干预对）没有可播放的内容**：`replayView` 在 5D 页签对它返回 `null`（界面显示空态与原因），`causalLayer` 返回空覆盖层。其余字段（`rows`、`design`、`confidence`……）由 5D 面板直接显示。

约定：

- `steps[].role` 取自引擎的 15 个角色（`cause`、`block`、`deny`、`fail`、`timeout`、`reject`、`starve`、`degrade`、`retry`、`recovery`、`compensation`、`bypass`、`gain`、`lost`、`effect`）；颜色表 `playback.js` 的 `ROLE_COLOR`，未知角色用中性灰 `NEUTRAL_ROLE_COLOR`，标签读 `i18n`。
- `steps[].at` 是该步对应的 B 侧播放位置（A 侧独有的步取“它原本发生的位置”）；`bIndex` 为 `null` 表示该步只在 A 侧。
- `root.element.roomId`：插入元素所在的房间，由展开模型的 `element_home_room(rewrite, edges, nodes)`（`model-schema.md` 5.13）算出，草稿元素为 `null`。**这是相对引擎当前输出新增的一个字段，〔引擎通道待确认〕**：缺失按 `null` 处理，`causalLayer` 一律输出 `roomId`，`makeBuffers` 用它决定插入元素的域色（没有房间时取 `defaults.trust` 的域）。
- 文字（`question`、`headline`、`why`、`label`）由引擎按**主语言**在构建时渲染；结构化部分（`cause`、`via`）保留代码形式。多语言界面下这些预渲染文字不随界面语言切换〔引擎通道/界面通道待确认：是否按语言各存一份，默认不存〕。

## 5. 空态

| 情形 | 查看器行为 |
|---|---|
| 模型没有用例 | 4D、5D 显示“没有可播放的用例”；`arrangeView('process')` 返回空的可见集；其余视图照常 |
| 用例未封存（`runs.baselines` 没有它） | 4D 不列出该用例的轨迹；`baselineFor` 返回 `null`，`replayView` 返回 `null` |
| 干预对未封存或 `status != OK` | 5D 只显示原因与结论；`pairRuns` 返回 `null`、`B` 为 `null`，或 `chain.status != 'OK'` 时 `replayView` 在 5D 页签返回 `null` |
| 5D 没有因果链 | `replayView({tab: '5D', chain: null})` 返回 `null`，不抛错（参考实现在启动时就会崩溃） |
| 模型没有干预 | 5D 面板为空态；`search.js` 不生成干预条目 |
| 没有任何建筑或房间 | 所有节点进入未分配区；2D 只有一个未分配建筑 |

## 6. 警告

纯模块共用一个警告收集器：`makeWarnings()`（`model-access.js`），元素 `{code, subject, detail}`，同一 `(code, subject)` 只记一次，每个代码最多保留 25 个不同对象，其余只计数（`suppressed[code]`）。`makeBuffers` 的返回值带 `warnings`（同一个收集器）。

| 代码 | 含义 |
|---|---|
| `GEMARC_ATLAS_VIEW_001` | 节点种类不在词表：中性形状 |
| `GEMARC_ATLAS_VIEW_002` | 关系不在词表：中性颜色，只出现在开放视图 |
| `GEMARC_ATLAS_VIEW_003` | 信任值不在词表：默认域 / 自成一区 |
| `GEMARC_ATLAS_VIEW_004` | 视图 id 未知：逻辑布局 |
| `GEMARC_ATLAS_VIEW_005` | 节点无法解析到建筑，或可见节点没有位置：未分配区 / 不绘制 |
| `GEMARC_ATLAS_VIEW_006` | 同一建筑内楼层 `level` 重复、不是数字或超出 `MAX_FLOOR_LEVEL` |
| `GEMARC_ATLAS_VIEW_007` | 节点状态既不在词表、内置表，也不是该节点自己状态机的状态：中性环 |
| `GEMARC_ATLAS_VIEW_008` | 播放位置不是区间内的整数（越界、小数、非数字）：已归一化 |
| `GEMARC_ATLAS_VIEW_009` | 节点的 `level` 不在词表：改取房间层或种类的详细度 |
| `GEMARC_ATLAS_VIEW_010` | 域不在词表：中性颜色 |

构建期把词表警告写入 `meta.warnings`（`assemble.py` 对展开模型跑一遍 `arrangeView`、`cardLayout`、`shownAtLevel`）；运行时建议界面把收集器的 `items` 暴露为 `window.__ATLAS_WARNINGS__`，与设计 9.3 的 `window.__ATLAS_ERRORS__` 并列。警告的 `detail` 是面向作者的英文诊断串，不是界面文字，所以不受 2.2 “无英文字面量”的约束。这些警告面向作者（规则 RC-002 是同一类问题的模型层检查），不进入 AI 的 `check` 返回。

## 7. 纯模块一览（给界面通道与打包通道）

| 文件 | 主要导出 | 说明 |
|---|---|---|
| `model-access.js` | `makeVocab`、`makeWarnings`、`indexModel`、`kindInfo`、`relationInfo`、`levelOfNode`、`shownAtLevel`、`keepFocus`、`nodeDomain`、`domainColor`、`relevance`、`RETREAT_TARGET`、`stepEase`、`UNASSIGNED_ID`、`own`、`dict`、`asList`、`isRecord` | 唯一知道 `gemarc-atlas/1` 字段名与词表字段名的模块 |
| `coords.js` | `newCamera`、`saneCamera`、`orbit`、`pan`、`zoom`、`viewProj`、`project`、`pick`、`fitCamera`、`pointsOutside`、`edgePath` | 左手系数学；`fitCamera` 按场景边界取景，并放宽缩放上限与远平面 |
| `layout.js` | `arrangeView`、`placeBuildings`、`cardLayout`、`cardFocus`、`gemFaces`、`gemOutline`、`layoutPoints`、`layoutBounds`、`floorLevels`、`VIEW_IDS` | 3D 六个视图、2D 卡片布局、宝石几何；`opts.vis` 是详细度过滤（`shownAtLevel` 的结果），隐藏的节点不占空间 |
| `scene-buffers.js` | `makeBuffers`、`toInstanceBuffers`、`stepEaseAll`、`stateColor`、`colorToRgb` | 图 + 布局 + 聚焦 + 覆盖层 → 平坦数组；`instances` 与 `toInstanceBuffers` 给实例化绘制 |
| `label-fit.js` | `shrinkText`、`placeLabel`、`truncateText`、`estimateTextWidth` | 画布标签缩放、省略、避让；字体栈是参数 |
| `search.js` | `makeSearchIndex`、`search`、`SEARCH_TABS` | 命中携带 `kind` 键，不含显示文字 |
| `playback.js` | `replayView`、`seekState`、`stateByFold`、`caseStage`、`chainStage`、`stageSubset`、`causalStepStops`、`stepIndex`、`normIndex`、`baselineFor`、`pairRuns` | 在预先算好的轨迹上折叠 `stateChanges`；播放位置的纯状态转移 |

**打包约定**（`tools/bundle_viewer.py` 依赖，纯模块已遵守）：

- 文件后缀 `.js`，ES 模块语法；`import` 一律单行 `import { a, b } from './x.js';`，相对路径、无通配、无默认导入。
- 只用 `export const|function|class` 声明导出：没有 `export { ... }` 列表、没有 `export default`、没有再导出。
- 模块依赖无环：`coords` ← `model-access` ← `layout` ← `scene-buffers` ← `playback`；`search` 只依赖 `model-access`；`label-fit` 与 `assemble` 不依赖任何模块（没有 `import`）。
- **作用域**：打包通道可以给每个模块套一层自己的函数作用域（推荐），也可以去掉 `import` 与 `export` 关键字后直接按依赖顺序拼接成一个经典脚本。后一种做法之所以可行，是因为八个模块（含 `assemble.js`）的**顶层名字互不重复**（私有辅助函数也有唯一名字：`LABEL_PAD`、`CARD_PAD`、`asList`、`isRecord`……）；`view-hardening.test.mjs` 把八个文件拼接后用 `vm.Script` 编译并运行，同时断言顶层名字唯一，新加模块或顶层名字时由它拦住冲突。
- 源码全是 ASCII（非 ASCII 字符一律写成 `\uXXXX`），不使用 `fetch`、`import()`、`eval`、`new Function`、`Math.random`、`Date`、`document`、`window`；这些由 `view-genericity.test.mjs` 扫描。

## 8. 待其它通道确认的事项

1. **引擎通道**：4.1–4.4 的字段是否与 `simulate` / `causal_chain` / `compare_runs` 的最终输出逐项相同；`Element.roomId` 是否由 `assemble.py` 补写还是引擎直接输出；预渲染文字是否需要多语言各存一份。
2. **文档通道**：2.3、2.4 已改写为文档通道的最终形状；`diagrams[].type` 的 `sequence` 由 oracle 通道登记。
3. **界面通道**：`i18n` 的键名表；`window.__ATLAS_WARNINGS__` 的暴露；`markPrefix` 的两个键（`cause`、`effect`）的翻译键；是否启用 `edgeStyle: 'relation'`（默认启用，`'class'` 退回单一空闲色）。
4. **打包通道**：`calibrationHooks.docSelections` 的选择集 K 与 FNV 实现；`meta.warnings` 的生成位置。
