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
| `fragments` | 对象，见 2.3 | 文档通道（`fragments.py`） | 1D 文档 | 是（内容可为空：`items` 为空数组；缺失时 1D 显示不可用的空态） |
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
| `counts` | 对象 | `{nodes, edges, cases, interventions, requirements, tbds, sealedCases, sealedPairs}`，整数；构建写页面时从同一个模型数出来，QA 页把它与页面里嵌入的数据现数的结果对照（列名“构建时记录”）。**它能发现的是构建写下数量之后页面数据被截断、被改动或被清洗掉记录；它不独立于构建，不评判模型本身**（独立的核对由 `docSelections` 的哈希与 BD 检查承担） |
| `warnings` | 数组 | 构建期词表警告，元素 `{code, subject, detail}`，形状与 `makeWarnings().items` 相同；上限见第 6 节 |
| `qa` | 对象，**可选** | 构建摘要，QA 页读取（界面通道定义，生产者待定）：`rules` = `{block, error, warn, info}`（最近一次 check 或 build 的规则条数，整数）；`gate` = `{release: 布尔, blockers: 整数}`（发布关卡）；`oracle` = `{sealed, revised, confirmed, pending, sheet}`（期望的分类计数，`sheet` 是 oracle 清单文件的相对路径文字）；`notes` = 数组，元素形状同 `warnings`，放 LB-001、RC 类规则的警告。**生产者是 `build.py`（10.3）**：缺省时（例如只调用 `render` 而没有项目）QA 页显示“构建未记录”的空态，字段类型不对按缺省处理，从不抛错。`rules.block` 与 `gate.blockers` 是两件不同的事，QA 页分开标注：前者是完整度报告里阻断后续工作的规则发现条数，后者是预言机发布关卡尚未满足的发布条件条数 |

`meta` 不含时间、路径、构建机信息。

### 2.2 `i18n`

```
"i18n": {"languages": ["zh-CN", "ja-JP", "en-US"], "tables": {"zh-CN": {"tab.2d": "..."}, "ja-JP": {...}, "en-US": {...}}}
```

- 界面文字表，键是扁平的点分串，值是字符串。三种语言的键集合必须相同（规则 XP-001，CI 检查）。
- 运行时 JS 里没有任何中日文字面量，也没有面向用户的英文字面量：显示文字一律由键查表（门禁与 CI 双重检查）。**例外**：纯模块写入警告收集器的 `detail`（见第 6 节）是面向作者的诊断串，不是界面文字，不走 `i18n`；界面若要展示警告，按 `code` 查键。
- 词表标签（种类、关系、状态……）不放在这里，读 `profile` 里各条目的 `label[<locale>]`。
- `scene-buffers.js` 的 `markPrefix`（`{cause, effect}`）与 `search.js` 的 `kind` 键由界面通过本表翻译；这两个纯模块自己不产生显示文字。
- **键名与来源**：表文件是 `viewer/i18n/{zh-CN,ja-JP,en-US}.json`（打包时嵌入 `i18n.tables`，`i18n.languages` 的顺序固定为 `zh-CN`、`ja-JP`、`en-US`）。键写成 `<区>.<名>`（区：`app tab ask hdr panel nav insp flag empty slot search kind doc twod qa warn status`），占位符写 `{name}`，三种语言的占位符集合必须一致。运行时代码里键只以**整串字面量**出现（不拼接），所以“用到的键 = 表里的键”可以静态核对。缺键时界面显示键本身并向 `__ATLAS_ERRORS__` 记一条，绝不显示 `undefined`。
- 界面语言的选择：本地偏好，其次 `meta.primaryLanguage`，其次浏览器语言，最后 `en-US`。警告按 `code` 取键（`warn.view001` 到 `warn.view010`、`warn.lb001`、`warn.rc001`、`warn.rc002`，其它代码显示 `warn.other`），`detail` 不显示。
- 词表标签（种类、关系、域、信任、详细度、证据等级、视图、层、节点状态）读 `profile` 各条目的 `label[<locale>]`，没有时回退 `en-US`，再回退 id；CI 检查每个条目三种语言都有标签。

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

数组，每个元素是一个图表单元。文档通道（Python）里的完整形状是：

```
{"id": "component-<building>", "type": "component|class|state|er|sequence", "title": "...",
 "covers": {"nodeIds": [], "edgeIds": [], "caseIds": []}, "requirementIds": [],
 "evidenceLevel": "Designed", "evidenceIds": [], "data": {...}, "mermaid": "...", "issues": []}
```

**页面里嵌入的单元只有 `build.UNIT_PAGE_FIELDS`：`id`、`type`、`title`、`covers`、`requirementIds`、`evidenceLevel`、`mermaid`、`issues`。** `data`（生成 Mermaid 文本用的结构化形式）与 `evidenceIds` 留在 Python/文档通道，不进页面（`build.compact_unit`，参考规模约少 54 KB）。`compact_unit` 同时把 `covers` 里的 id 列表去重（保持首次出现的顺序）：同一条边只算覆盖一次。

查看器读 `id`、`type`、`title`、`covers`、`mermaid`；2D 图表板显示“涉及 N 个部件、M 条连接、K 个用例”时按**不同的 id** 计数，部件数为 0 的单元（例如楼栋总览图，它画的是楼栋，不列部件）省掉“部件”一项。图表只以 Mermaid **源文本**显示，页面里写明这一点；不绘制，也不加载任何外部内容（离线规则）。

`search.js` 只读 `id`、`title`、`type`。`sequence` 单元只描述已封存的用例（由 oracle 通道登记）。

### 2.5 `calibrationHooks`

```
{"docSelections": [{"level": "BD", "elementIds": ["..."], "hash32": "0a1b2c3d"}],
 "traceLengths": [{"caseId": "...", "interventionId": null, "length": 12}],
 "scene": {"nodeCount": 169, "buildingIds": ["..."], "unassignedCount": 0}}
```

- `docSelections`：K 个规范选择（构建应取 **K = 20**，`docbuild.viewer_payload(model, lang, 20)`），`hash32` 是 Python 对组装出的文字的 UTF-8 字节计算的 32 位 FNV 哈希（a 变体，小写 8 位十六进制）。门禁模式在页面里重新组装并比较（`AtlasApp.checkDocSelections()`，9.6 的 `doc-twin` 步骤），不一致时写入 `window.__ATLAS_ERRORS__`，孪生实现漂移会让每次构建失败；QA 页也显示“N 个选择中 M 个一致”。
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
- 文字（`question`、`headline`、`why`、`label`）由引擎按**主语言**在构建时渲染；结构化部分（`cause`、`via`）保留代码形式。引擎通道不按语言各存一份（决定，不再待确认）。**界面不直接显示引擎的英文句子，而是用结构化部分按界面语言重写**：事件类型、事件结果、结论类别、部件状态都按 `profile` 的 `eventTypes`、`eventOutcomes`、`outcomeClasses`、`nodeStates` 的 `label` 显示（集合名写错会悄悄退回成原始 id，`tests/atlas/js/ui-fix-b3.test.mjs` 检查界面传给 `api.label` 的每个集合名都在词表里）；比较块的一行摘要由 `effects`（部件名、状态词）组成（`play.headline`）；因果链的问句由改动说明与用例标题组成（`chain.question`）。**仍然是引擎主语言原文的只有每一步的 `why` 说明行**，页面在有这类行时写一行说明（`chain.why.note`）；`chain.headline` 只在一个不能播放的对比（被拒或草稿）的原因块里原样显示。

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

### 7.1 界面模块（`viewer/src/ui/`）

界面通道的模块放在子目录 `ui/`（不放在 `viewer/src/` 顶层：`view-genericity.test.mjs` 把顶层源集固定为八个纯模块并禁止页面访问）。打包约定与第 7 节相同，另有三点：**导入路径可以是 `./x.js` 或 `../x.js`**（相对于导入者所在目录，打包器按路径解析）；**所有运行时模块（顶层八个加 `ui/` 的全部）的顶层名字互不重复**（`tests/atlas/js/ui-structure.test.mjs` 把它们按依赖顺序去掉 `import`/`export` 拼成一个经典脚本，用 `vm.Script` 编译并断言名字唯一）；依赖无环。

| 文件 | 性质 | 作用 |
|---|---|---|
| `ui/dom.js` | 适配器（唯一碰页面） | 元素创建（只进文本节点）、事件、存储（读写都带 try）、位置散列、剪贴板、全局槽位、焦点保持 |
| `ui/i18n.js` `ui/boundary.js` `ui/prefs.js` `ui/nav.js` `ui/shell-state.js` | 纯逻辑 | 界面表查找、错误边界与心跳、偏好与响应式规则、路由与标签页注册表、外壳状态转移与键盘动作 |
| `ui/model-view.js` `ui/qa.js` `ui/doc-state.js` `ui/markdown.js` `ui/twod-scene.js` `ui/navigator.js` `ui/inspector.js` | 纯逻辑 | 视图上下文（`viewContext`，先清洗模型）、QA 记录、1D 选择与文本组装（调 `assemble.js`）、最小 Markdown 阅读器、2D 场景与视口算术、导航树、检查器记录 |
| `ui/shell.js` `ui/panel-views.js` `ui/render-md.js` `ui/tab-doc.js` `ui/tab-twod.js` `ui/tab-qa.js` `ui/tab-slots.js` `ui/main.js` | 外壳与标签页（只经 `dom.js` 碰页面） | 页眉、侧栏、检查器、舞台、搜索、键盘、`AtlasApp`；1D、2D、QA 标签页与 3D 4D 5D 占位；启动 |

## 8. 待其它通道确认的事项

1. **引擎通道**：4.1–4.4 的字段是否与 `simulate` / `causal_chain` / `compare_runs` 的最终输出逐项相同；`Element.roomId` 是否由 `assemble.py` 补写还是引擎直接输出；预渲染文字是否需要多语言各存一份。
2. **文档通道**：2.3、2.4 已改写为文档通道的最终形状；`diagrams[].type` 的 `sequence` 由 oracle 通道登记。
3. **界面通道**：已确定，见 2.2、第 9 节。键名表在 `viewer/i18n/`；`window.__ATLAS_WARNINGS__` 已暴露；`edgeStyle: 'relation'` 的取舍属于 3D 模块（viewer-B），2D 的线固定按关系取色、跨信任域虚线、静态关系淡化。**仍待 viewer-B**：`markPrefix` 的两个键（`cause`、`effect`）的翻译键（现无对应界面表条目，接入 4D、5D 时再加，三种语言一起）。
4. **打包通道**：`calibrationHooks.docSelections` 的选择集 K（建议 20，见 2.5）与 FNV 实现；`meta.warnings` 与可选的 `meta.qa` 的生成位置；按 9.1 的占位符填页；`pyproject` 的 package-data 需要加入 `atlas/viewer/src/**`、`atlas/viewer/i18n/*.json`、`atlas/viewer/src/atlas.css`、`atlas/viewer/src/atlas.template.html`（构建时读取；目前只列了 `atlas/viewer/src/*.js`）。

## 9. 界面外壳契约（viewer-A：外壳、QA、1D、2D；viewer-B 只加模块）

本节是界面通道（外壳）与后续通道（3D、4D、5D 模块，打包与门禁）之间的约定。外壳模块在 `viewer/src/ui/`；它们与第 7 节的纯模块一起打包。全部显示文字来自界面表与词表标签（2.2），外壳不含任何中日文字面量或面向用户的英文字面量（`tests/atlas/test_viewer_i18n.py` 逐条检查）。

### 9.1 页面模板与占位符

`viewer/src/atlas.template.html`、`viewer/src/atlas.css` 是页面骨架与样式；打包通道把下表的占位符用函数替换器各填一次（不得用带 `$` 模式的替换），填完后页面里不得再有 `<!--ATLAS_` 开头的注释（BD-002）。

| 占位符 | 填入内容 | 说明 |
|---|---|---|
| `<!--ATLAS_LANG-->` | `meta.primaryLanguage` | `html` 的 `lang` 初值，外壳启动后按界面语言改写 |
| `<!--ATLAS_SELFTEST-->` | `gate/selftest.js` 包进一个 `script` 元素 | **必须位于 `head` 最前面**，先于任何其它脚本，错误捕获才能就位；与 `gate/smoke.py` 的 `SELFTEST_MARKER` 是同一个标记 |
| `<!--ATLAS_TITLE-->` | 页面标题（转义后的 `meta.title`，空则用项目 id） | 外壳启动后也会写 `document.title` |
| `<!--ATLAS_STYLE-->` | `atlas.css` 全文 | 无 `url(`，无外部资源 |
| `<!--ATLAS_DATA-->` | 数据包的嵌入文字（构建实际填入 `JSON.parse('...')` 表达式，见 10.1；下面写的 `docbuild.embed_json` 是同一套转义的对象字面量写法） | 在模板里位于赋值语句 `window.__ATLAS__ = ...;` 之中，是一个合法的 JS 对象字面量；`embed_json` 已把 `<` `>` `&` U+2028 U+2029 写成转义，所以不会提前结束脚本，也不会出现 `</script` |
| `<!--ATLAS_RUNTIME-->` | 全部运行时模块拼成的经典脚本，外加最后一行 `bootAtlas();`，整体包进一个立即执行函数 | 顺序：数据脚本在前，运行时脚本在后；`#atlas-root` 元素在两者之前 |

页面主体只有一个空的 `div#atlas-root`，其余元素全部由外壳创建；没有 `canvas`（viewer-A），所以门禁没有画布可读。

### 9.2 页面全局名（唯一的外部接口）

| 名字 | 谁写 | 内容 |
|---|---|---|
| `window.__ATLAS__` | 页面数据脚本 | 第 2 节的包，外壳只读 |
| `window.AtlasApp` | `bootAtlas()` | 应用对象，见 9.3 |
| `window.__ATLAS_ERRORS__` | 外壳（若不存在则创建一次） | 数组，**只追加、从不重新赋值**（门禁持有同一个引用）；元素 `{where, message, stack?}`；错误边界、`window` 的 `error` 与 `unhandledrejection`、界面表缺键、孪生核对不一致都写进来，门禁据此判 `PAGE_ERRORS` |
| `window.__ATLAS_WARNINGS__` | 外壳 | 词表警告收集器的 `items`（第 6 节），随渲染增长 |
| `window.AtlasGate` | `gate/selftest.js` | 只有 `?gate=1` 时 `active` 为真；外壳在 `bootAtlas()` 末尾调用 `AtlasGate.run(app.gateConfig())`，没有行走器时什么都不做 |

`dom.js` 是唯一碰页面的模块；其余模块要么完全不接触页面（逻辑模块），要么只通过 `dom.js` 创建与改动元素（外壳与标签页模块）。`tests/atlas/js/ui-structure.test.mjs` 按这个分类扫描。

### 9.3 `AtlasApp` 接口

| 成员 | 类型 | 说明 |
|---|---|---|
| `version` | 数 | 接口版本，现为 1 |
| `tabs()` | 函数 | 已注册标签页 id，按标签顺序（`1D 2D 3D 4D 5D QA`，其它 id 在其后） |
| `tab`、`level`、`selection` | 只读属性 | 当前标签页、详细度、选择（`{type, id}` 或 `null`） |
| `state()` | 函数 | 外壳状态记录（只读使用） |
| `registerTab(def)` | 函数 | 注册或替换标签页（9.4）；返回 `{ok, reason?}`，不抛错；成功后重绘 |
| `registerSideTab(def)` | 函数 | 注册检查器的页签（9.5）；返回 `{ok, reason?}` |
| `setTab(id)`、`setLevel(level)`、`select(sel)` | 函数 | 切换并同步重绘；`setTab` 对未注册的 id 返回 `false` |
| `renderOnce(tab?)` | 函数 | **同步**完整重绘当前标签页（页眉、侧栏、舞台、检查器）；可选参数先切换标签页；返回 `{tab, frame, ok, error}`；**心跳计数器在 `finally` 里加一**，所以渲染抛错也算一帧，页面不会因为标志位卡死 |
| `frame()` | 函数 | 心跳计数器的**纯读取**（门禁要求：被调用时不得自增）；只有 `renderOnce` 使它前进，没有定时器 |
| `errors` | 数组 | 就是 `window.__ATLAS_ERRORS__` |
| `warnings()` | 函数 | 词表警告 `items` |
| `calibrationHooks` | 对象 | 包里的 2.5 |
| `checkDocSelections()` | 函数 | 对 `calibrationHooks.docSelections` 的每一项在页面里重新组装并比较哈希，返回 `[{level, count, expected, actual, ok}]` |
| `gateConfig()` | 函数 | 交给 `AtlasGate.run` 的配置（9.6） |
| `ctx`、`api`、`bundle` | 对象 | 视图上下文、标签页接口（9.4）、包 |

### 9.4 标签页定义

```
{ id, labelKey, questionKey?, order?, inspector?: boolean,
  controls?(host, api), render(host, api) }
```

- `id` 是标签页 id（`1D` `2D` `3D` `4D` `5D` `QA` 或新的）；`labelKey`、`questionKey` 是界面表的键；`inspector` 为真时舞台右侧有检查器；`controls` 存在时侧栏多一个"控制"页签（1D 的选择界面就在这里）；`render(host, api)` 把内容追加到 `host`。
- **后注册的定义替换同 id 的先注册者**：3D、4D、5D 在 viewer-A 里是占位定义（`ui/tab-slots.js` 的 `SLOT_TABS`）：4D、5D 显示空态（没有用例、没有干预、没有封存项时是"先写出预期"一句），3D 显示带 `DEGRADED` 标注的 2D 图。viewer-B 的做法：新增模块导出真定义，在 `ui/main.js` 的 `TAB_DEFINITIONS` 里把它放在占位定义之后，不改其它文件。
- 一个标签页的渲染一旦抛错，错误边界记录到 `window.__ATLAS_ERRORS__`，外壳其余部分照常显示，下一次 `renderOnce` 仍然前进。
- `api` 是稳定对象：`ctx`（`ui/model-view.js` 的 `viewContext`：词表 `vocab`、模型索引 `ix`、列表与空态标志 `hasCases` `hasInterventions` `hasSealed`）、`t(key, vars)`、`label(collection, id)`（词表标签）、`title(id)`、`state`（只读）、`select(sel)`、`setLevel`、`setTab`、`setDoc`、`setTwod`、`setTwodView`、`setNavFilter`、`setInspectorTab`、`openUnit(id)`、`redraw()`、`setStatus(text)`（页眉状态串的补充，例如 `WebGL(software)`）、`boundary`、`registry`、`cache`（每个应用一个的 `Map`，标签页放重建代价高的渲染结果，键里要带界面语言）、`dragged()` / `markDragged()`（拖动结束后抑制那一次点击）。
- 标签页只通过 `ui/dom.js` 创建元素（`domEl`、`domSvg`，文字只进文本节点，永远不解析标记）。viewer-B 的画布标 `data-gate="canvas"`，并让 `renderOnce` 在同一个任务里完成绘制。

### 9.5 检查器与侧栏

- 检查器有一个固定页签 `detail`（所选节点、边、用例、需求的事实：字段、证据行、连接、相关用例与需求），其余页签是槽位：`registerSideTab({id, labelKey, order?, tabs?: [标签页 id], render(host, api)})`。viewer-B 的 `chain`（因果链）与 `diagram`（图）页签在此注册，`tabs` 限定出现在哪些标签页。
- 侧栏有"导航"（楼栋 > 楼层 > 房间 > 节点的树，随详细度与筛选变化，无法定位的节点在最后一组"没有位置的部件"），有 `controls` 的标签页多一个"控制"页签。

### 9.6 门禁钩子（`gateConfig()`）

```
{ tabs, setTab(id), tabState(), renderOnce(tab), frame(), headerSelector: "[data-gate~=header]", sceneBounds(tab), textAllow(), steps: [...] }
```

- `textAllow()`（可选）返回页面**自己数据**（模型的名称、说明、标签；不含界面词表）里含 `undefined`、`NaN`、`[object` 的片段，每处四种：词两侧各 12 个字符、只左侧、只右侧、两侧各 4 个字符（名称被省略号截短或被标记包住时仍能对上其中一种）；前后文不足 4 个字符的光杆词不收（否则页面上所有同名词都被放行）。走查器在扫描前把这些片段在**原文**里的所有出现一并抹去（重叠的片段不会互相破坏上下文），再扫描。所以模型的节点名可以写“returns undefined when empty”，而界面自己的 `Total: undefined` 照样 `TEXT_BAD`。片段按**不同的片段**计数，上限 480 个（重复同一句话不耗预算）；没有这个钩子时什么都不放行；运行器另有显式关闭项（`run_gate(scan_text=False)`、`smoke.py --no-text-scan`）。
- `gateWithTab(id, fn)`：页面里没有这个标签页是**步骤失败**（返回说明字符串），不再算通过；缺一个标签页的页面不会因为它的步骤跟着消失而通过。

- `tabState()` 读 `body` 的 `data-tab`；`renderOnce` 返回的对象只作参考，门禁的判定不用它。
- 页面标记：页眉容器 `data-gate="header"`（里面的按钮、选择框都是门禁量取的交互元素；页眉用 `flex-wrap`，不用绝对定位）；`data-gate="fit"` 用在必须完整可见的短文字（品牌、状态串、2D 模式标签、1D 统计行）；不含 `canvas`。
- 脚本化步骤（每个返回 `true`，或失败说明字符串）：`doc-twin`（在页面里重新组装每个 `docSelections` 并比较哈希；**不一致时同时把一条记录写进 `window.__ATLAS_ERRORS__`**，所以即使运行器不认识这个步骤也会因 `PAGE_ERRORS` 失败，孪生实现漂移让每次构建失败）、`levels`（逐个切换详细度并核对 `body` 的 `data-level`，之后恢复）、`inspector`（选中第一个节点，检查器出现详情，之后取消）、`twod-labels`（切到 2D 画布，临时关掉细节层级的“隐藏文字”，取所有 `text.frame-label` 的实际包围盒两两比较，任意两个重叠深度超过较矮者高度的 10% 即失败；用 `gate-checks.js` 的 `gateOverlaps`，之后恢复）、`qa-rows`（切到 QA 页，至少有一个 `[data-qa-row]`）、`table-words`（在 1D 与 QA 页里，凡是不含空白、不超过 40 个字符的 `th`、`td` 文字，用 Range 量取，被排在多于一行就失败：词在中间被折断）、`search`（打开再关闭搜索对话框）。每个步骤都把切换过的标签页与状态恢复。共 7 个步骤；`build.GATE_STEPS = 7`，页面报告的步骤数少于它时，`build.run_gate` 把该宽度的 PASS 改为 FAIL（`STEPS_MISSING`，门禁运行器只数页面带来的步骤，所以这里补上“缺步骤即失败”）。这些是**页面侧**的断言，不是 `gate/selftest.js` 里的走查器断言（改走查器要重跑 25 分钟的矩阵）；`WebGL(software)` 状态、逐个打开检查器页签（目前只有 `detail`）与 3D 帧边界、占用率仍属 viewer-B。viewer-B 追加：选第一个用例、播放三步（M6）。
- `calibrationHooks.docSelections` 的个数 K：构建应取 **20**（M4 验收 (e)）；`docbuild.viewer_payload(model, lang, 20)`。

### 9.7 偏好、路由、响应式、键盘

- **偏好键** `gemarc-atlas:<projectId>:<modelSha8>`（即 `meta.prefKey`），一条 JSON 记录：`{v, lang, theme, level, navWidth, inspectorWidth, navOpen, inspectorOpen, sub}`；读取接受任何输入（缺失、损坏、外来记录都给出完整的默认值）；`localStorage` 被禁用或抛错时页面照常工作，只是不记忆。不同项目、不同模型版本互不串味。
- **路由** 写在位置散列里：`#/<TAB>?level=<id>&sel=<type>:<id>&req=<id>`，用 `replaceState` 写、启动与 `hashchange` 时读；未知的标签页、详细度、已不存在的选择一律丢弃。
- **版面**：视口宽度 >= 1100 时侧栏与检查器是并排的列（按偏好宽度，给舞台至少留 360 px）；< 1100（门禁的 1024）时是覆盖层，同一时间最多开一个，点遮罩或按 Esc 关闭；页眉在任何宽度都可换行（< 1500 时 `data-wrap="1"`）。关闭的覆盖层不渲染可见内容，不产生横向滚动；宽表格放在带滚动的容器里。
- **主题**：`auto`（跟随系统）、`light`、`dark`，写在根元素的 `data-scheme`；颜色全是 `atlas.css` 顶部的变量。
- **键盘**：`1` 到 `9` 切标签页，`[` `]` 开关侧栏与检查器，`/` 或 Ctrl+K 打开搜索，Esc 每按一次关一层（搜索、覆盖层、选择）；在输入框里打字时除 Esc 外都不触发；页眉按钮可用左右方向键、Home、End 切换；2D 画布是可聚焦的组，方向键平移，`+` `-` `0` 缩放与适应；搜索框、筛选框在重绘后保持焦点与光标。

### 9.8 空态与降级

| 情形 | 行为 |
|---|---|
| 没有 `fragments` 数据 | 1D 显示"此页面没有文档数据"，QA 的文档行显示无核对选择 |
| 没有需求 | 1D 从"单个元素"模式开始 |
| 没有用例 | 4D、5D 显示"没有可播放的用例" |
| 有用例但没有封存项（`runs.baselines` 为空） | 4D 显示"先写出预期"；QA 的预期部分显示同一句 |
| 没有干预 | 5D 显示"没有可对比的改动"；有干预但没有封存的干预对时显示"先写出预期" |
| 3D 模块未加入 | 标签页显示 `DEGRADED` 一行与"3D 场景模块尚未加入"，下面是 2D 图，**2D 图自己标注为独立模式**（"2D 模式：平面图（没有深度，不能播放）"） |
| 没有图表单元 | 2D 的"图表"面板显示空态 |
| 模型缺集合、词表未知值 | 空列表、中性默认值与警告，从不抛错（`viewCleanModel` 先过滤掉没有可用 id 的记录） |

### 9.9 已知限制（诚实记录）

- 外壳没有 WebGL，也没有 `canvas`；3D 的帧边界、占用率断言在 viewer-B 加入后才有意义。
- 2D 是一幅平面图：没有 3D 的深度、播放、散开聚焦；节点很多时（400 个以上）线条会很密，靠缩放、平移与搜索淡化使用。
- 1D 的渲染是自带的最小 Markdown 阅读器，只认文档构建器写出的几种结构；其它写法按文本显示。文档正文只有主语言，界面语言不同时显示一行说明。
- 门禁的断言在 Edge 的无头模式下用虚拟时间验证；没有在 Firefox 或 Safari 上验证。
- 2D 平面图在参考规模（约 170 个节点、4 栋建筑并排）下，适应整幅图时（Edge 无头，窗口 1366 × 900，画布 648 × 466 px）缩放比例是 0.24，卡片名约 2.9 px；1024 宽时画布 966 × 365 px，比例 0.19，约 2.2 px：读不了。所以见 9.10 的细节层级：缩放比例低于 0.5 时不画任何文字，只显示一行“放大后才显示文字”的提示，建筑、楼层和房间的外框与卡片色块照常画；要读名字用放大、搜索或导航栏。约 40 个节点的图在 1366 宽时比例 0.56（卡片名约 6.7 px，勉强能读），1024 宽时 0.46，低于阈值，文字被隐藏。这是已接受的可读性上限，不是缺陷。

- 文字扫描（`TEXT_BAD`）的已知空隙：`visibility:hidden` 的文字、画在 `canvas` 里的文字（例如画布上的 `NaN`）扫不到；`display:none` 的文字按设计不算。可见文字、屏幕外、`opacity:0`、SVG 文字、`title`/`aria-label`/`alt`/`placeholder` 都扫得到。
- “运行时代码里没有英文字面量”（`test_viewer_i18n.py`，XP-001）是一个启发式扫描：两个词以上的英文、首字母大写的词、非 ASCII 字符、模板字符串、未使用或缺失的键都会失败；**单个小写英文词的字面量（例如 `"save"`）扫不到**。这条保证是扫描加人工复核，不是证明；新增代码的界面文字一律走 `i18n/*.json`。
- 脚本化步骤是同步的：返回 Promise（有 `then`）的步骤算失败（`STEP_FAILED`，说明“steps must be synchronous”），不会因为运行器没有等待它而悄悄通过；被拒绝的 Promise 仍会经 `unhandledrejection` 记为页面错误。
- 页眉里一个控件包含另一个控件（带关闭按钮的 `role=tab`，包着按钮的链接）不算重叠；两个互不包含的控件相交才算 `HEADER_OVERLAP`。
- 2D 的版面密度：模型有很多房间时整幅图的适应比例很小（几百个节点约 0.2），这时卡片名与框名都不画（`lod-low`，提示“放大查看”）。`twod-labels` 步骤因此**不在适应比例下量取**（那时的框只有两三个像素高，量到的是字形度量的取整，不是排版），而是临时把 SVG 放到“名字刚好会被画出来”的最小比例（`TWOD_LOD_SCALE`），在那里量真实包围盒；排版本身与密度无关。200、600、1000、1500 个节点（40 至 120 个房间）在 1024、1366、1600 宽度都通过。
- 页面的数据量随模型线性增长（1500 个节点约 6.8 MB，超过 3 MiB 的 BD-005 警告线，没到 8 MiB 的失败线）。

### 9.10 2D 平面图与表格的版面契约（检查点后补）

- **适应 = 场景边界。** `twodScene().viewBox` 就是适应视图；SVG 用 `preserveAspectRatio="xMidYMid meet"`，由浏览器在实际窗格里居中并缩放，所以绘制标签页时（宿主元素还没进页面，没有尺寸）不再猜窗格大小。只有用户操作（滚轮、按钮、拖动、方向键）时才读 SVG 的实际尺寸：`twodFit(scene, w, h)` 给出与窗格同纵横比的视图。`twod.view` 为 `null` 表示“适应”；“适应”按钮把它设回 `null`。
- **画布高度。** `.stage[data-tab="2D"]`（3D 占位页同样，因为它复用 2D 画布）是纵向 flex：`.tab-host`、`.twod-wrap` 与 `.twod-pane` 依次 `flex: 1`，窗格不少于 360 px，不再用固定的 `74vh`；画布因此不会越过窗口底部，窗口放不下时舞台滚动。
- **标签带（不重叠）。** `twod-scene.js` 的 `twodFrameLabels(scene, unassignedText)` 决定所有框名的位置与截断：建筑名在建筑上沿（基线 +16），楼层名在楼层**第一个房间**的第一行（基线 +11），房间名在房间页眉的第二行（基线 +28）；卡片行从房间上沿 +40 开始（布局的 HEAD 24 加 CARD_PAD 16），所以两行名字都在第一排卡片之上，且两两不共用一行。无归属的伪楼层与伪房间没有名字。`twodLabelOverlaps(labels)` 是纯函数，节点测试对 11 个场景的每个详细度断言为空；浏览器里的同一检查是门禁步骤 `twod-labels`。字号：`svg.twod text.frame-label` 11px，卡片名 12px、种类 10px（样式表里要用 `svg.twod text.<类>` 才压得过 `svg.twod text` 的统一字号）。
- **细节层级（LOD）。** `twodScale(view, paneW, paneH)` 是每个场景单位对应的屏幕像素；低于 `TWOD_LOD_SCALE = 0.5`（卡片名 6 px 以下）时 SVG 与窗格带 `lod-low`，样式隐藏所有 SVG 文字并显示 `.lod-hint`。类在每次缩放、拖动后由 `apply()` 更新，首次绘制用 `api.cache` 里上一次的结果，之后由 `domLater(…, 0)` 在窗格有尺寸时校正；门禁步骤 `twod-labels` 量取前会临时去掉 `lod-low`。
- **表格。** 单元格 `overflow-wrap: break-word; word-break: normal`（不是 `anywhere`，`anywhere` 会让自动表格布局把列挤到词中间折断）；`render-md.js` 给“不含空白且不超过 40 个字符”的 `th`、`td` 加类 `tok`（`white-space: nowrap`，连连字符处也不换行），`code` 在表格里也不换行；表格已在 `.tbl-wrap` 里，窄窗口横向滚动而不是折词。`.tbl-wrap` 不带 `data-gate="fit"`（`measureFit` 会把任何不可见溢出且 `scrollWidth > clientWidth` 的元素判为被裁）。
- **长文字。** 页眉品牌里的标题（`.brand strong`）用省略号截断，`.brand` 本身保持 `overflow: visible`（它带 `data-gate="fit"`）；导航栏的楼栋、楼层、房间行与节点名都用 `overflow-wrap: anywhere`，不产生横向滚动条。

## 10. 构建与打包（打包通道：`tools/bundle_viewer.py`、`build.py`、`htmlassemble.py`）

本节是打包通道对第 2、4、9 节的补充，只增不改：第 2、4 节规定的形状不变，这里写的是构建如何把引擎通道的封存结果和构建期发现放进这些形状。

### 10.1 数据脚本的写法（补充 9.1 的 `<!--ATLAS_DATA-->`）

占位符被填入的不是对象字面量，而是表达式 `JSON.parse('<转义后的 JSON>')`：JSON 的键按字典序排列，`<` `>` `&` U+2028 U+2029 写成 `\uXXXX`，反斜杠与单引号为单引号字符串字面量转义。原因有二：作者文字不可能提前结束脚本、打开注释或注入标签；键 `__proto__` 作为对象字面量会改写原型，经 `JSON.parse` 则是普通自有属性。页面启动前后不需要任何其它改动：`window.__ATLAS__ = JSON.parse('...');`。

### 10.2 `runs`：封存提供者的形状到页面形状（只含封存或已确认的项）

输入是 `oracle.sealed_runs`（见 `oracle-provider.md`），页面里写的是第 4 节的形状：

| 页面 | 来源 | 处理 |
|---|---|---|
| `runs.baselines[caseId]` | `baselines[caseId].run` | 去掉 `snapshots`（查看器从 `initialState` 折叠，第 4.1 节写明可缺）；`initialState`、`finalState` 里取值为 `Idle` 的节点不写（查看器对 `Idle` 不画状态环，缺失即 `Idle`）；`trace[i]` 去掉值为 `null`、`false`、`[]`、`{}` 的键，以及 `sequence`（`index + 1`）、`caseId`（同所在运行）、`previousState`、`nextState`（`stateChanges` 已含）；其余字段原样 |
| `runs.pairs[]` | `pairs[]` | `{caseId, interventionId, status: result.status, errors: result.errors, B: 压缩后的 result.B, diff: result.diff, chain}`；`result.A` 不重复写（等于该用例的基线），`result.explanation` 不写 |
| `calibrationHooks.traceLengths` | 压缩后的轨迹 | 基线 `{caseId, interventionId: null, length}`，对 `{caseId, interventionId, length: B 的步数}` |
| `diagrams` | 文档通道的结构单元，加上 `sealed_runs.diagrams`（`sequence`、`staterun`） | 顺序：结构单元在前 |
| 不写入页面 | `withheld`、`provenance` | `withheld` 的 id 只出现在 `build-info.json`；账本来源与徽标文字写进 `meta.qa.oracle`（10.3） |

未封存、已修订的用例与干预不出现在上面任何一处（ORACLE_FIRST，由 `tests/atlas/test_build_project.py` 在原始 JSON 文本里核对）；`meta.counts.sealedCases`、`sealedPairs` 是嵌入的数目。

### 10.3 `meta.warnings` 与 `meta.qa` 的生产者

- `meta.warnings`：构建在 Python 里复现查看器运行时的词表警告，代码与主语（subject）与查看器一致（`GEMARC_ATLAS_VIEW_001`、`_002`、`_003`、`_005`、`_006`、`_009`），所以 QA 页按 `code|subject` 合并两处来源；每个代码最多 25 条，其余不写入页面（`build.vocab_findings` 返回被省略的条数 `suppressed`）。
- `meta.qa.notes`：规则 RC-001（同一建筑内楼层 `level` 重复或不是可用的数）、RC-002（未知的节点种类或信任值，以中性默认值绘制）、LB-001（节点名在 2D 卡片上被截断：`estimate_text_width(name, 12) > 116`，与 `label-fit.js` 的 `estimateTextWidth` 同一公式，由测试对拍）。三个规则 id 登记在 `rules/registry.py`（第 3 波，`modes=("build",)`，不进入 slice 或 full 检查）。
- `meta.qa.rules`：`{block, error, warn, info}`（`block` 是完整度报告的 BLOCK 条数，其余是最近一次完整检查的诊断条数）；`meta.qa.gate`：`{release, blockers}`（预言机部分的发布闸门，`release_gate`）；`meta.qa.oracle`：`{sealed, revised, confirmed, pending, sheet: "oracle-sheet.md", ledger: "ok"|"unverified", badge}`，其中 `ledger` 与 `badge`（项目语言的证明文字，来自 `oracle.attestation`）是新增的可选字段，QA 页尚未显示它们（viewer-B）。
- `calibrationHooks.scene.unassignedCount` 是 Python 对“节点无法解析到房间、楼层、建筑”的计数，不是常量 0；非 0 时 QA 页显示警告。

### 10.4 打包产物与新鲜度

`tools/bundle_viewer.py`（只用标准库，不需要 Node）读取 `viewer/src/*.js`、`viewer/src/ui/*.js`、`atlas.css`、`atlas.template.html`、`viewer/i18n/*.json`，写出已提交的 `viewer/dist/{atlas-runtime.js, atlas.css, atlas.template.html, atlas-i18n.json}` 与 `viewer/SOURCES.sha256`。每个模块包进自己的函数作用域（`const __mod_<路径> = (function () {...; return {导出};})();`），模块私有的顶层名字不会相撞；导入的名字是否被目标模块导出在打包时核对。`SOURCES.sha256` 记录每个输入、打包器本身与每个输出的 SHA-256。`build.py` 只读 `dist/`（`pyproject.toml` 已列 `atlas/viewer/dist/*`），并拒绝与 `SOURCES.sha256` 不符的 `dist/`；改了源码而没有重新打包会让 `tests/atlas/test_bundle_sources.py` 失败：运行 `python tools/bundle_viewer.py`。

### 10.5 构建检查 BD-001 至 BD-007（`htmlassemble.check_page`，作用于最终页面文本）

| 编号 | 检查 | 严重度 |
|---|---|---|
| BD-001 | 可移植：代码部分（模板、样式、门禁脚本、运行时）没有 `fetch(`、`import(`、`@import`、远程 `url(`、`XMLHttpRequest`、`sendBeacon`、`importScripts`、`WebSocket`、`EventSource`、`eval(`、`new Function`，以及不是裸调用的写法：`Function(`、`window.fetch`、`document.write`、`new Image`、字符串参数的 `setTimeout`/`setInterval`（**这是记号扫描，不是证明**：拼出来的名字、计算属性名扫不到，页面是否真的离线靠整页只有一个 `<script>` 的结构检查与门禁里的页面行为）；页面里除骨架元素外没有别的元素，没有任何 `src`、`href`、`action`。数据部分不按词扫描：证据锚点会引用代码，而数据是惰性的（BD-003） | ERROR |
| BD-002 | 占位符：六个占位符各出现且只出现一次，填完后页面里没有 `<!--ATLAS_` | ERROR |
| BD-003 | 数据：数据脚本恰好有第 2 节的十个键，解析回来等于输入包，且不含 `<` `>` `&` U+2028 U+2029 与控制字符 | ERROR |
| BD-004 | 结构：恰好三个脚本（门禁脚本在 `head` 最前，数据，运行时，运行时以启动调用结束），内容与预期逐字相同；标题文字等于所给标题；`lang` 取自支持的语言 | ERROR |
| BD-005 | 大小：超过 3 MiB 警告，超过 8 MiB 失败 | WARN / ERROR |
| BD-006 | 编码：合法 UTF-8，只有 LF，没有 BOM 与 NUL | ERROR |
| BD-007 | 可复现：同一输入填两次逐字节相同；代码部分没有本机路径（盘符、`/Users/`、`/home/`）与时间戳形文字 | ERROR |

构建不含时间戳，页面只含模型哈希；`build-info.json` 也没有时间戳与路径，两次构建的 `dist/`（除 `.verify/` 与 `oracle-sheet.md`）逐字节相同。浏览器门禁的报告在 `dist/.verify/`。

## 11. 舞台：3D、4D、5D（viewer-B）

本节补充第 9 节：`ui/main.js` 的 `TAB_DEFINITIONS` 现在注册真正的 `3D`、`4D`、`5D` 定义（`viewer-A` 的占位定义已不再注册，`slot.3d.missing` 与 `slot.module.missing` 两个键随之删除；`slot.degraded` 的文字改为"此页面无法显示 3D 场景"，同时用于没有 WebGL 的情形）。

### 11.1 模块

| 模块 | 性质 | 内容 |
|---|---|---|
| `ui/stage-state.js` | 纯 | 4D/5D 提供哪些项（只列 `runs.baselines` 里有基线的用例、`runs.pairs` 里有的对），不可播放的全部原因（`no-cases`、`nothing-sealed`、`no-interventions`、`no-pairs`、`pair-status`、`run-status`、`no-run`），播放器的状态转移（位置始终在 `-1..长度-1`，5D 自动播放只访问因果步骤），事件行、因果链行、效果行 |
| `ui/scene-model.js` | 纯 | 视图上下文加舞台状态得到要画的内容：布局、显示的部件和边、画出的框、相关度与覆盖层、相机要框住的点、淡出目标、门禁要量的投影边界与占用率、图例数据、标签摆放 |
| `ui/gl-renderer.js` | 纯（只调用传进来的上下文） | 五个着色器程序与绘制：部件按形状实例化（WebGL 2，或 WebGL 1 加 `ANGLE_instanced_arrays`，都没有时画扁平网格）、边线、状态光晕与事件光点（面向相机的四边形，在 CPU 上展开）；时钟是参数 |
| `ui/diagram-model.js` | 纯 | 已封存运行的时序图几何、状态机的状态图几何（原生 SVG，不用图表库） |
| `ui/tab-slots.js` | 只经 `dom.js` 碰页面 | 画布与相机、拾取、悬停、标签层、图例、3D 标签页、4D/5D 标签页、检查器页签 `chain`（因果链）与 `diagram`（图）、脚本化步骤。文件名是历史遗留：`ui-structure.test.mjs` 把这个路径列为可以建元素的模块，这是新标签页不改那个测试就能用的唯一路径 |

### 11.2 行为约定

- **只显示已封存的项。** 4D 的用例列表来自 `runs.baselines`，5D 的变更列表来自 `runs.pairs`；没有基线的用例没有条目、没有标题、没有轨迹，只在一行里报告"有 N 个用例没有封存结果，不提供播放"。未封存或没有用例时显示第 5 节的空态，没有画布。
- **4D/5D 固定用最细的详细度**：舞台上的每个部件都要看得见；详细度开关只作用于 3D 标签页。舞台只画房间框（不画楼栋框），所以相机能框住画出的全部内容；运行引擎停止时（`LIMIT_EXCEEDED`、`DRAFT`、`REJECTED`）只显示原因。没有可画内容的对（变更不在因果路径上）显示"没有可画的内容"，播放器、事件列表与对比表仍在。
- **没有 WebGL**：3D 标签页显示 `DEGRADED` 一行与 2D 平面图；4D/5D 不画画布，显示 `DEGRADED` 一行，播放器、事件列表、图仍可用。页面里不存在空白的画布（空白画布会被门禁判为 `NON_BG`）。
- **动效可选，不决定结果。** 门禁模式与"减少动态效果"偏好下，淡出与散开目标立即生效，着色器时钟取帧数；其它情形下目标用 `stepEaseAll` 缓动。拖动、缩放、悬停只重画画布，不重建外壳。
- **画布与 GL 状态放在 `api.cache`**，每次重绘重新挂回同一个元素（外壳每次重建整棵树，每次新建画布就是每次新建一个 WebGL 上下文）。外壳在树进入页面之后调用挂载回调（`api.mounted(fn)`），所以绘制和 `renderOnce` 在同一个任务里：门禁读像素与绘制调用数读到的就是这一帧。
- 播放状态（用例、变更、位置、视图、框、图例、图表种类）是 `api.cache` 里的一条记录（键 `stage-ui`），只通过 `stageSet` 改变；搜索命中一个用例（`sel: {type: 'case'}`）会让 4D/5D 采用它。路由里没有用例参数（`case=` 深链接在范围削减清单上）。

### 11.3 门禁

标签页定义可以带两个可选字段：`sceneBounds(api)`（返回 `{frames: [{id, kind, x, y, w, h}], nodes: [{id, x, y, r}], w, h, occupancy}`（`r` 是部件投影后的半径，单位像素，缺省按 0；部件不是一个点，一两个部件的场景按它们自己的大小占框），单位是相对画布左上角的 CSS 像素，用的就是画出这一帧的相机与纵横比；没有画布的标签页返回 `null`）与 `gateSteps(api)`（返回脚本化步骤）。`gateConfig()` 把前者交给走查器（`sceneBounds(tab)`），后者并入 `steps`。

页面侧步骤现在共 11 个（`build.GATE_STEPS = 11`）：原有 7 个，加 `inspector-tabs`（逐个打开每个带检查器的标签页的每个检查器页签，各页签必须有内容）、`scene-3d`（3D 画布至少发出一次绘制调用、状态行非空）、`case-play`（选第一个已封存的用例，核对播放器步数等于 `calibrationHooks.traceLengths` 与嵌入的轨迹长度，前进三步并核对位置）、`pair-play`（同上，对第一个状态为 OK 的封存对，并核对对比表）。没有已封存的项时这些步骤返回 `true`（空态由别处的检查证明）。每个步骤把切换过的标签页、选择与播放状态恢复。

走查器（`gate/selftest.js`，只量取不判定）在每个标签页的第二次 `renderOnce` 之后的同一个任务里调用 `sceneBounds`，并记录页面声明的可见画布（`data-gate="canvas"`）的 CSS 尺寸，写入 `rec.scene` 与 `rec.sceneCanvas`；判定在 `gate/smoke.py`：

| 码 | 名称 | 触发 |
|---|---|---|
| 023 | FRAME_CLIPPED | 投影后的某个框（楼栋、楼层、房间）越出画布超过 `thresholds.sceneClipPx`（2 px），或在相机背后；报告了边界却量不到画布 |
| 024 | NODE_CLIPPED | 某个投影后的节点越出画布，或在相机背后 |
| 025 | SCENE_SPARSE | 节点包围盒面积除以框包围盒面积小于 `thresholds.sceneOccupancyMin`；有框却没有任何节点。钩子可以写 `occupancy: false` 跳过占用率（4D/5D 的舞台框住整个房间，不要求填满），画布内测试永远执行 |

只量节点会放过参考页面里被裁掉的 3D 视图（相机夹紧切掉框），所以框计入。占用率下限的标定（合成图册，3D 标签页，三个宽度，`calibration.json` 的 `provisional` 里有记录）：真实视图最低 0.603（40 个部件，节点按投影半径计）、参考规模 0.762 至 0.768、400 节点 0.829 至 0.835；单个部件的模型 0.15 至 0.25，`sparse-scene` 夹具 0.0005；下限取 0.1，即比最低的真实视图低 5 倍以上、比夹具高 200 倍，两侧都满足 2 倍余量。

### 11.4 页面里的事件（体积）

舞台只读事件的 `sourceNodeId`、`targetNodeId`、`edgeId`、`eventType`、`outcome`、`stateChanges`（以及位置）。`build.EVENT_UNREAD_KEYS = ("evidenceIds", "cause", "ruleId", "attempt")` 这四个键不再嵌入页面（参考规模的页面约少 68 KB；加入舞台模块之后，最大的实测种子 7 的页面是 1,964,952 字节，不丢这四个键是 2,049,606，会超过 2,000,000 的预算）。运行的 `hash`、引擎结果与文档、图表不受影响（它们在压缩之前由 Python 生成）。`tests/atlas/test_gate_3d.py::PageCompactionTests` 断言舞台模块不读这四个键。

### 11.5 诚实记录

- 门禁看到的是"画出了场景"：框和边都画出而部件一个也没画，门禁不会发现（`NON_BG` 与 `NO_DRAW` 只在整个画布空白或没有任何绘制调用时触发）。部件的绘制由 `tests/atlas/js/view3d-gl.test.mjs`（调用记录）与截图人工查看补上。
- 未测：硬件 GPU（只有 SwiftShader）、Firefox、Safari、触摸缩放、屏幕阅读器；没有实际操作画布的键盘与指针的真实浏览器运行（桩页面里测了事件处理器的接线，没有测几何）。
- 一个房间里部件很多、而用例只用其中几个时，4D/5D 的舞台里部件很小（相机框住房间框）；缩放是办法，没有自动聚焦。
- 范围削减（设计 9.3 允许）：图表窗口不做停靠与浮动，图例不做浮动，没有散点聚焦；图表窗口是检查器页签。

## 12. 修复批 b3 的约定（门禁判定、文字语言、版面）

### 12.1 门禁判定的形状

- `smoke.verify_gate` 的整体状态：`PASS`（每个宽度通过）、`FAIL`（有宽度失败，**优先于**下面两个）、`SKIP`（没有浏览器，响亮地说没有验证）、`INCONCLUSIVE`（没有失败，但某个宽度的每次启动都超时）。`INCONCLUSIVE` 不是通过，也不记为页面缺陷：机器忙时浏览器可能超过限时（实测：忙进程数约等于逻辑核数时成了悬崖，12 个忙进程 2.9 s，16 个超过 30 s），再跑一次即可；**在空闲的机器上再次超时，就是页面本身卡住了**（一个真正死循环的页面与忙机器看起来一样，所以这个状态只说“没验证”，不说“没问题”）。`tools.build`/`tools.verify` 把它报告为 `invalid`，码 `GEMARC_ATLAS_BUILD_033`（`build.E_GATE_INCONCLUSIVE`）。
- 工具路径（`build gate=smoke`、`verify`、命令行）每次启动的限时是 `smoke.VERIFY_TIMEOUT_SECONDS = 90`（页面自己的时间由虚拟时间预算限定，墙钟限时放大对通过的页面没有代价），超时**不重试**（第二次启动只会重复它），其它进程级失败仍重试一次。底层 `run_gate` 的默认（`calibration.json` 的 `limits.timeoutSeconds = 30`，超时重试）不变；`GateResult.seconds` 仍是最后一次启动，`total_seconds`（报告里的 `seconds`/`totalSeconds`）是各次相加。
- 每个宽度的摘要多一个 `stepNames`。`build.require_steps` 对 `PASS` 行还有两条要求：走查到的标签页**正好**是 `build.GATE_TABS`（`1D 2D 3D 4D 5D QA`，顺序一致），否则 `TABS_MISSING`；页面报告的步骤名包含 `build.GATE_STEP_NAMES` 的 11 个，否则 `STEPS_MISSING`；结果里没有步骤名同样失败。多出来的步骤允许。运行器只数页面带来的东西，所以丢了一个标签页（连带丢了它的步骤）的页面以前会通过。
- 声明：构建的尺寸报告里 `skeleton` 是**余数**（总数减去其它几部分，即模板文字；`data` 部分是整段数据脚本，含 `window.__ATLAS__ = ` 与 `;`），各部分之和按构造等于总数；各键的字节数是各自 JSON 的长度，与页面里的序列化只差千分之几。

### 12.2 渲染入口

`build.render()` 对无法编码的模型（孤立代理项 `GEMARC_ATLAS_MODEL_012`、NaN 或无穷 `..._013`）抛 `BuildRefused(E_CONTRACT)`，不再从编码器里抛 `UnicodeEncodeError`；其它契约问题仍由调用方判断。工具路径本来就由 `put` 在入口拒绝这类文字。

### 12.3 界面细节

- 搜索：一个部件有 2D 与 3D 两个页面，搜索结果合并成一行，标出两个页面（`2D / 3D`）；点击时打开访客当前所在的那一个（若是其中之一），否则第一个（`shellMergeHits`、`shellHitFrom`）。
- 1D：窄宽度（侧栏变成覆盖层）时，主栏里有一行说明“需求与元素的选择在侧栏，打开‘导航’”（`doc.panel.hint`）。
- 序列图：参与者多于面板宽度时（图宽超过 300 px）在图下写一行“向左右滚动”（`diagram.scroll`）；消息上的事件类型与结果按界面语言写。
- 很长的、不含空白的名称：导航栏的名称与检查器标题最多占三行（`-webkit-line-clamp`），全文仍在页面里。
- 3D/4D/5D 的“适应”：在 `SCENE_FIT_PITCHES = [0.5, 0.75, 1.0]` 里各适应一次，选框住画布面积最多的那个，除非别的不比第一个多至少 12% 就保持第一个（`sceneBestFit`）。实测（参考规模的合成图册，Edge 无头，框包围盒占画布面积；括号内是只用 0.5 的旧值）：3D 在 1366 宽 0.241（0.170），1024 宽 0.307（0.217），1600 宽 0.272（0.192）；5D 部件包围盒占画布 0.130（0.039）。这是包围盒的占比，不是图形的密度：参考规模的 3D 本质上是一条七栋楼并排的长条，部件仍只有几个像素，放大、选中与搜索是进入的办法。
- 4D/5D 的状态变化摘要、因果链问句、事件类型与结果的文字见 4.4。因果链每一行的角色标签与结果词相同时只写一个。
