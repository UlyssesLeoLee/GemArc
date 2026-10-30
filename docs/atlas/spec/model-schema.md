# GemArc Atlas 展开模型契约：`gemarc-atlas/1`

状态：**规范性（normative）**。本文是所有 Atlas 通道（格式解析与展开器、规则、引擎、文档与图表构建、查看器、证据校验、合成数据生成器）共同遵守的数据契约。

实现与测试位置：

| 内容 | 位置 |
|---|---|
| 常量、id 语法、推导函数、canonical JSON、契约检查器 | `src/gemarc_mcp/atlas/model.py` |
| 词表加载与索引 | `src/gemarc_mcp/atlas/vocab.py` |
| 词表数据（3 种语言标签） | `src/gemarc_mcp/atlas/profiles/software.json` |
| 契约测试 | `tests/atlas/test_model_contract.py` |

**一条总原则：每个推导字段只在 `model.py` 中计算一次。** 合成数据生成器与展开器都调用 `build_*` 与 `derive_*` 函数，不得各写一份。`contract_issues(model)` 会重新计算每个推导字段并与模型比较，返回 `[]` 才算合规。

## 1. 范围与术语

- **作者事实**：AI 写入 `.atl` 或 JSON-lines 的内容（格式由格式通道定义，本文第 6 节给出选项到字段的映射）。
- **展开模型**：展开器的输出，即本文描述的 `gemarc-atlas/1` 对象。引擎、规则、文档、查看器只读展开模型。
- **参考实现**：本设计所依据的、已有的五维图册实现。本文只在“字段名保持一致”和“差异表”两处提到它，不引用其数据。
- **推导字段**：作者不写、由固定代码从事实算出的字段。每个推导字段在第 5 节有精确公式。
- **规范 GemArc 模型**：`src/gemarc_mcp/model.py` 的 0.2.0 模型。Atlas 不修改它，也不读写它；导入导出器推迟实现（设计 D1）。

字段命名原则：凡通用引擎或查看器代码读取的字段，沿用参考实现的字段名，使移植代码改动最小；设计明确要求不同的地方才改（见第 10 节）。**若某字段的类型与参考实现不同，则同时改名**，使移植的 JavaScript 在读取旧名时失败而不是误读（例如空数组在 JavaScript 里为真值）。

## 2. 顶层结构与约定

```
{
  "schema": "gemarc-atlas/1",
  "meta": {...},
  "space": {"buildings": [], "floors": [], "rooms": []},
  "nodes": [], "edges": [], "ports": [], "evidences": [], "machines": [],
  "cases": [], "interventions": [], "requirements": [], "tests": [], "tbds": [], "policies": []
}
```

`model.SCHEMA = "gemarc-atlas/1"`，`model.COLLECTIONS = (meta, space, nodes, edges, ports, evidences, machines, cases, interventions, requirements, tests, tbds, policies)`，顶层键恰为 `schema` 加这 13 个，不得多、不得少。`model.empty_model()` 返回合规的空模型。

通用约定：

1. **数组顺序即作者顺序**。节点按 `nodes` 段出现顺序（跨切片按切片文件名顺序），建筑顺序即 `space.buildings` 顺序，由展开器生成的记录（`ports`、`owns` 边）的位置在第 5 节规定。任何代码不得为了“稳定”而对数组重新排序。
2. **可选字段未作者时省略，不写 `null`**。例外是下列字段，它们永远存在：`edge.sourcePortId`、`edge.targetPortId`（机器节点一侧为 `null`）、`edge.guardId`（恒为 `null`）、`evidence.lineRange`（未盖章时为 `null`）、`machine.errorCode`（可为 `null`）、`policy.chapter`（可为 `null`）。
3. **展开模型中所有引用都是规范 id**。作者写的 `alias`、三元组字符串、`~` 等都由展开器解析掉，模型里不留。
4. **模型不含时间戳、路径、绝对位置**。构建时间写入 `build-info.json`，不进入模型，使 `model_sha` 可复现。
5. **自由对象**（`props`、`context`、`params`、`guard`、`when`、`fault`、`expect.terminal` 等）是开放字典，键在 canonical JSON 中递归按字典序排序（2.1）。

### 2.1 canonical JSON

```
canonical_dumps(model)  = json.dumps(canonicalize(model), ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False)
canonical_bytes(model)  = (canonical_dumps(model) + "\n").encode("utf-8")      # 写盘字节：UTF-8，仅一个 LF，无 BOM
model_sha(model)        = sha256(canonical_bytes(model)).hexdigest()            # model_sha8 取前 8 位
canonical_dumps(model, indent=2)                                                 # 供人读的形式：同样排序，LF，不参与哈希
```

- 键在每一层都按字典序排序；数组保持作者顺序，**永不重排**。
- `ensure_ascii=False`：中日文原样输出。`NaN` 与 `Infinity` 抛 `AtlasModelError`（`GEMARC_ATLAS_MODEL_002`）。
- `tuple`、`MappingProxyType` 先被转成 `list`、`dict`；非字符串键与不可序列化类型抛错。
- 不得复用 `paths.encode_json`（它使用 `indent=2`，与本节不同）；其它通道的黄金文件依赖本节规定。
- **嵌套深度**：模型中任何值（`props`、`params`、守卫、`when` 等）嵌套不得超过 `model.MAX_NESTING`（64）层，也不得有环。`contract_issues` 对超限的模型只返回一条 `GEMARC_ATLAS_MODEL_010`；`canonicalize`、`canonical_dumps`、`canonical_bytes`、`model_sha` 抛 `AtlasModelError`（`GEMARC_ATLAS_MODEL_002`），不会因递归过深抛 `RecursionError`。判断函数是 `model.nesting_exceeds`（迭代实现）。
- **版本戳**：参考实现里的模型版本号与修订号（显示在界面抬头和运行结果里）在本契约里**没有手写字段**，统一用 `model_sha8(model)`（canonical 字节哈希的前 8 位）作版本戳。构建时间只写入 `build-info.json`。

## 3. id 语法

| 名称 | 正则 | 用于 |
|---|---|---|
| `ID_RE` | `^[A-Za-z0-9][\w.:-]{0,127}$`（`re.UNICODE`） | 与 `src/gemarc_mcp/model.py:23` **逐字相同**，测试断言 pattern 与 flags 相等；所有 id 的上界 |
| `AUTHORED_ID_RE` | `^[A-Za-z0-9][\w.-]{0,123}$`（`re.ASCII`） | 节点、机器、插入元素：`ID_RE` 的严格子集，**无冒号，至多 124 字符，只接受 ASCII** |
| `STATE_RE` | `^[A-Za-z][A-Za-z0-9_]{0,39}$` | 状态机的状态名 |
| `TOKEN_RE` | `^[A-Za-z0-9][\w.-]{0,39}$` | `group`、`area`、仓库别名 |
| `PROJECT_ID_RE` | `^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$` | `meta.projectId` |

**为什么 `AUTHORED_ID_RE` 只接受 ASCII**：两个看起来相同的节点 id（西里尔字母 `а` 与拉丁字母 `a`、全角字母、阿拉伯-印度数字、带重音字符的 NFC 与 NFD 两种写法）若被当成不同的 id，图上会出现无法分辨的两个节点。节点、机器、元素 id 与边的端点因此限定为 ASCII（`re.ASCII` 下 `\w` 只含 `[A-Za-z0-9_]`，仍是 `ID_RE` 的子集，规范等同测试不受影响）。`ID_RE` 本身**逐字沿用规范模型**，第 2 个字符起仍接受 Unicode 单词字符，所以证据、用例、需求等记录的 id 可能含形近字符；检测它们是规则层的 lint，不在契约检查器内。

为什么节点 id 不允许冒号并限 124 字符：派生 id（`<node>:in`、`<node>:out`、`src:rel:tgt`、`<element>:<n>`）必须**可逆且不越过 128 字符**。若节点 `x:in` 存在，会与节点 `x` 的端口撞名；若 `a:b` + `calls` + `c` 与 `a` + `calls` + `b:calls:c` 都合法，则两条不同的边得到同一个 id。限制后，所有派生 id 都能证明满足 `ID_RE`（测试对 124 字符的极端 id 逐一断言）。

### 3.1 唯一性

所有**作者 id**在整个模型内全局唯一（对应规则 ST-002）：`space.*`、`nodes`、`edges`、`evidences`、`cases`、`interventions`、`requirements`、`tests`、`tbds`、`policies`。**派生 id**（`ports[].id`、`rewrite.chain[].id`、生成的 `owns` 边）不得与任何作者 id 相同。机器 id 就是节点 id，不单独占用。转移 id 只需在其机器内唯一。重复由 `contract_issues` 报告为 `GEMARC_ATLAS_MODEL_021`，信息中带第一处占用位置。

### 3.2 派生 id 的精确公式

```
hash_id(domain, *parts) = "h:" + sha256("\n".join([domain, *parts]).encode("utf-8")).hexdigest()[:32]   # 共 34 字符，仅一个冒号

edge_id(src, rel, tgt)          = plain                     若 len(plain) <= 120，plain = src + ":" + rel + ":" + tgt
                                = hash_id("edge", src, rel, tgt)   否则
port_id(node, "in"|"out")       = node + ":in" | node + ":out"
transition_id(machine, a, b)    = machine + "." + a + "_to_" + b    若长度 <= 120
                                = hash_id("transition", machine, a, b)   否则
chain_edge_id(element, k)       = element + ":" + str(k)            k 为 1..98
event_id(case, seq)             = case + "#" + str(seq)             seq 从 1 起（事件 id，见 4.8）
```

- `edge_id` 的三个部分都不得含 `:` 或换行，违反抛 `AtlasModelError(GEMARC_ATLAS_MODEL_001)`。因此普通 id 恰含两个冒号，哈希 id 恰含一个冒号，节点 id 不含冒号，端口 id 以 `:in`/`:out` 结尾：四类 id 互不相等。
- 哈希输入以 LF 连接各部分，`domain` 用于隔离 `edge` 与 `transition`。截断按十六进制字符，不在字符中间截断。
- **构造函数的输入校验**：`port_id`、`edge_ports`、`transition_id`、`assign_transition_ids`、`derive_ports`、`machine_states`、`build_machine`、`number_events`、`inherit_props`、`edge_ref_index`、`hash_id`、`evidence_kind` 以及 `build_node`、`build_edge`、`build_machine` 对类型或形态错误的输入抛 `AtlasModelError`（`GEMARC_ATLAS_MODEL_001`、`002` 或 `020`），而不是 `AttributeError`、`KeyError`、`IndexError`、`TypeError` 之类的裸异常。`transition_id` 的两个状态必须满足 `STATE_RE`，机器 id 必须是无冒号、无换行的非空串；`port_id` 的节点 id 同样。**`cap_verification` 与 `merge_policy` 不校验**（未知等级、非整数策略值原样返回，由契约检查器报告），调用者（展开器、生成器）必须先校验。
- **别名规则**：边行上的 `alias=<A>` 使该边的 `id` 就是 `<A>`。`<A>` 必须满足 `AUTHORED_ID_RE`（**无冒号**，至多 124 字符）且全局唯一，因此别名永远不会与普通边 id（两个冒号）、哈希 id（一个冒号）或端口 id（一个冒号）相撞；`build_edge` 对违反者抛 `GEMARC_ATLAS_MODEL_020`。契约检查器要求每条边的 `id` 等于派生的 `edge_id(sourceId, relation, targetId)`，或满足 `AUTHORED_ID_RE`，这样手写的 `foo:bar` 或 `x:calls:y` 无法冒充派生 id，拼错的三元组引用也不会悄悄命中另一条边。别名是显式 id，不是第二个名字；展开模型的边没有 `alias` 字段。重复的三元组（同 `src`、`rel`、`tgt`）会派生出同一个 id，所以第二条及以后**必须**带别名，否则报重复 id。
- **转移 id 冲突**：状态名允许下划线，所以 `(a_to, b)` 与 `(a, to_b)` 会派生同一个 id。`assign_transition_ids(machine, transitions)` 返回 `(ids, duplicates)`，按 **id** 而不是按状态对检测重复；重复的转移必须写显式 `id=`（`build_machine` 在有重复时抛 `GEMARC_ATLAS_MODEL_021`）。重复状态对（SM-004）仍是警告。
- **边引用解析**（`edge_ref_index(edges)`）只做精确字符串查找：边 id → id；当且仅当恰有一条边具有某个三元组时，纯三元组字符串 `src:rel:tgt` → 该边 id，且不遮蔽任何真实边 id。**任何地方都不得按 `:` 拆分 id。** 哈希边若被其它记录引用，作者应给它加 `alias`，或写出完整三元组字符串。

## 4. 各集合字段

约定：类型栏的 `id` 指满足 `ID_RE`，`aid` 指满足 `AUTHORED_ID_RE`，`ids` 为 id 数组，`text` 为非空字符串；“来源”栏中 **A** = 作者写入，**D** = 推导，**C** = 常量。词表来源写作 `vocab.<group>`，指 `profiles/software.json` 中的同名分组。

### 4.1 `meta`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `projectId` | string，`PROJECT_ID_RE` | 是 | A（`init`） | 同时用于查看器本地偏好键 `gemarc-atlas:<projectId>:<modelSha8>` |
| `title` | string | 是（可为空串） | A（`init`） | |
| `mode` | `asis` \| `tobe` | 是 | A（文件头 `#atlas 1 mode=tobe`） | `enums.modes`；`tobe` 时推导值不超过 `Designed` |
| `languages` | string[] | 是 | A（`init`） | 非空、不重复、⊆ `vocab.locales`；首项是主语言 |
| `profile` | string | 是 | A（`init`） | 词表文件名，当前 `software` |
| `repos` | string[] | 是（可为空） | A（`@set repo` 与 `atlas.toml`） | 已声明的仓库别名，`TOKEN_RE`；只列别名，不含路径 |

### 4.2 `space`

`space` 是恰含 `buildings`、`floors`、`rooms` 三个数组（按此顺序）的对象。

**`space.buildings[]`**：`{id, name, trust}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `id` | id | 是 | A | |
| `name` | text | 是 | A | |
| `trust` | string | 是 | A / D | `vocab.trust`；未写时取 `vocab.defaults.trust`（`internal`） |

**`space.floors[]`**：`{id, buildingId, name, level}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `id` | id | 是 | A | |
| `buildingId` | id | 是 | A | → `space.buildings` |
| `name` | text | 是 | A | |
| `level` | int | 是 | A | 同一建筑内楼层 `level` 唯一（规则 RC-001，规则层检查，不在契约检查器内）；3D 高度与 2D 排序读它 |

**`space.rooms[]`**：`{id, floorId, name, [layer], [tier], [area]}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `id` | id | 是 | A | |
| `floorId` | id | 是 | A | → `space.floors` |
| `name` | text | 是 | A | |
| `layer` | string | 否 | A | `vocab.layers`（当前仅 `business`）：该房间所有节点取该层的域色，且层内 `levelKinds` 中的种类升到该层的详细度 |
| `tier` | string | 否 | A | `vocab.tiers`（`T1`..`T5`），展开时写入节点 `props.tier` |
| `area` | string，`TOKEN_RE` | 否 | A | 功能区，展开时写入节点 `props.area` |

### 4.3 `nodes[]`

`{id, kind, name, roomId, sub, evidenceIds, verification, props, portIds, [group], [level]}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `id` | aid | 是 | A | |
| `kind` | string | 是 | A | `vocab.kinds` 中 `authorable` 为真者；`Element` 仅用于插入元素，不得作为节点 |
| `name` | text | 是 | A | |
| `roomId` | id | 是 | A（`room=`，可由 `@set`） | → `space.rooms` |
| `sub` | string | 是 | A | 一句说明 |
| `evidenceIds` | ids | 是（可为空） | A（`ev=`） | → `evidences` |
| `verification` | string | 是 | A（`verif=`）/ D | `vocab.verification`；缺省规则见 5.3 |
| `props` | object | 是（可为空） | A + D | 作者 `props` 加上继承的 `tier`、`area`、`trust`（5.2）；已知键见 7.4 |
| `portIds` | string[] | 是 | D | `[<id>:in, <id>:out]`；机器节点为 `[]`（5.1） |
| `group` | string，`TOKEN_RE` | 否 | A | 图表成员标签，取代参考实现中按 id 前缀过滤的写法 |
| `level` | string | 否 | A | `vocab.levels`：覆盖详细度；有效详细度见 5.9 |

### 4.4 `edges[]`

`{id, sourceId, targetId, type, directed, relation, static, label, verification, evidenceIds, [cardinality], sourcePortId, targetPortId, guardId, [group]}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `id` | id | 是 | D / A（`alias=`） | `edge_id(sourceId, relation, targetId)`；有别名时即别名（无冒号，3.2）；检查器拒绝两者之外的任何值 |
| `sourceId` | aid | 是 | A | → `nodes` |
| `targetId` | aid | 是 | A | → `nodes`；允许自环 |
| `type` | string | 是 | C | 恒为 `"directed"` |
| `directed` | bool | 是 | C | 恒为 `true` |
| `relation` | string | 是 | A | `vocab.relations` |
| `static` | bool | 是 | D | `= vocab.relations[relation].static`；静态关系不参与事件绑定 |
| `label` | text | 是 | A | 自动生成的 `owns` 边取机器节点的 `name` |
| `verification` | string | 是 | A（`verif=`）/ D | 缺省规则见 5.3（以**该边自己的**证据为准） |
| `evidenceIds` | ids | 是（可为空） | A（`ev=`，`ev=^`） | `ev=^` = 两端节点证据的并集，源端在前、去重，且验证等级封顶 `Designed` |
| `cardinality` | string，`CARDINALITY_RE` | 否 | A（`card=`） | 如 `N:1`、`1:0..1`；主要用于 `references` 边 |
| `sourcePortId` | string \| null | 是 | D | `<sourceId>:out`；源端是机器节点时为 `null` |
| `targetPortId` | string \| null | 是 | D | `<targetId>:in`；目标端是机器节点时为 `null` |
| `guardId` | null | 是 | C | 恒为 `null`（保留字段） |
| `group` | string，`TOKEN_RE` | 否 | A | 图表成员标签 |

### 4.5 `ports[]`

整个集合是推导结果（5.1）：`{id, nodeId, direction, type}`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | `<nodeId>:in` 或 `<nodeId>:out` |
| `nodeId` | aid | → `nodes` |
| `direction` | `in` \| `out` | `enums.portDirections` |
| `type` | `Message` \| `Record` | `Record` 当且仅当节点 `kind` 的 `vocab.kinds[kind].record` 为真，否则 `Message`；插入元素的端口另可为 `Any`（`enums.elementPortTypes`） |

端口类型兼容规则（PT-003，警告级）：`a === b` 或任一端为 `Any`；其它组合按关系给出警告而非错误。

### 4.6 `evidences[]`

`{evidenceId, sourceKind, repository, filePath, lineRange, anchor, topic, verification}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `evidenceId` | id | 是 | A | |
| `sourceKind` | string | 是 | D | `evidence_kind(filePath)`，见 5.4 |
| `repository` | string | 是 | A（`@set repo`）/ D | 仓库别名（`TOKEN_RE`）或 `""`；`user-requirement` 恒为 `""`。类型为字符串，不用 `null` |
| `filePath` | text | 是 | A（`file`） | 仓库内相对路径，或 `user:<name>`；不得是绝对路径、盘符、含 `..`（验证器报错） |
| `lineRange` | `[from, to]` \| null | 是 | D（证据验证器盖章） | **永不由作者写入**；展开器恒输出 `null`，验证器按 `anchor` 解析后写入盖章文件，构建时可合并 |
| `anchor` | text | 是 | A | 逐字出现在文件中的片段；`user:` 证据里是需求原文句子 |
| `topic` | text | 是 | A | 该锚点证明了什么（对抗敷衍引用的最便宜手段，必填） |
| `verification` | string | 是 | D | `evidence_verification(sourceKind, mode)`，见 5.4 |

### 4.7 `machines[]`

`{id, nodeId, initial, errorCode, evidenceIds, states, terminal, transitions}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `id` | aid | 是 | A | **等于一个节点的 id**。是否机器节点只看 id 是否属于 `machines`，不看 `props.machine` |
| `nodeId` | aid | 是 | D | `= id` |
| `initial` | string，`STATE_RE` | 是 | A | |
| `errorCode` | string \| null | 是 | A（`err=`） | 机器级错误码；`null` 时引擎使用默认 `INVALID_TRANSITION` |
| `evidenceIds` | ids | 是（可为空） | A（`ev=`） | |
| `states` | string[] | 是 | D | `[initial]` 加上按首次出现顺序遍历每个转移的 `from`、`to` 所见的状态（5.5） |
| `terminal` | string[] | 是 | D | `states` 中没有出边的状态，保持 `states` 的顺序 |
| `transitions` | object[] | 是（可为空） | A + D | 见下 |

`transitions[]`：`{id, from, to, [guard], [errorCode]}`

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `id` | id | D / A（`id=`） | `transition_id(machine, from, to)`；仅被干预引用的转移才需要显式 `id=` |
| `from`、`to` | string，`STATE_RE` | A | |
| `guard` | object | A（`guard=`） | 守卫（见 7.5）；无守卫则**省略**键 |
| `errorCode` | string | A（`err=`） | 仅有守卫的转移通常带它 |

### 4.8 `cases[]`

`{caseId, category, title, requirementIds, evidenceIds, policy, context, [initialMachines], expectedTerminalState, [expectedOutcomes], [expectedBy], linkedTestIds, events}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `caseId` | id | 是 | A | |
| `category` | string | 是 | A（`cat`） | `vocab.caseCategories`：`normal abnormal boundary concurrency recovery combination`。旧拼写 `combined` 只作为别名提示 |
| `title` | text | 是 | A | |
| `requirementIds` | ids | 是（可为空） | A（`req=`） | → `requirements` |
| `evidenceIds` | ids | 是（可为空） | A（`ev=`） | → `evidences` |
| `policy` | `{timeoutMs, retryMax, retryBackoffMs}` | 是 | A + D | **三个键永远齐全**：`merge_policy(作者的部分策略)`，缺省 `{timeoutMs: 2000, retryMax: 0, retryBackoffMs: 0}`（5.7） |
| `context` | object | 是 | A（`ctx=`） | 缺省 `{}`；守卫里的 `$ctx.<key>` 读它 |
| `initialMachines` | `{machineNodeId: state}` | 否 | A（`init=`） | 键是机器节点，值是该机器的状态 |
| `expectedTerminalState` | `{nodeId: state}` | 是（可为空） | A（`expect` 段） | 期望的终态断言；非机器节点的值 ∈ `vocab.nodeStates`，机器节点的值 ∈ 该机器的 `states`。缺省的 `Completed` 在展开时写成显式值 |
| `expectedOutcomes` | `[{ruleId, outcome, [errorCode]}]` | 否 | A（`expect` 段） | `outcome` ∈ `vocab.eventOutcomes` |
| `expectedBy` | `author` \| `user` | 否 | A（`by=`） | 缺省视为 `author`；只有在确认摘要匹配时 `user` 才被承认（规则层） |
| `linkedTestIds` | ids | 是 | A（`tests=`）/ D | 缺省 = 所引需求的 `tstIds` 并集（5.8），→ `tests` |
| `events` | object[] | 是（可为空） | A | 事件，见下 |

**`events[]`**：`{seq, type, from, to, [options...]}`

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `seq` | int | D | 1..n，等于行序（5.6） |
| `type` | string | A | `vocab.eventTypes` |
| `from`、`to` | aid | A | → `nodes`；非自事件须能绑定到一条运行时边（正反向皆可，引擎判断） |
| `ruleId` | string | A | 步骤名，仅作标注与期望匹配；允许在一个用例内重复 |
| `edge` | id | A | 显式指定边，→ `edges`（规范 id） |
| `latencyMs` | int ≥ 0 \| int[] | A | 数组为每次尝试的延迟 |
| `ack` | bool | A | 立即完成（`Completed`）而非 `Running` |
| `section` | bool | A | 参与聚合节点的降级规则 |
| `group` | string | A | 并行分支名；相邻同名事件同时起跑 |
| `dedupKey` | string | A | 与目标 `props.dedup` 配合 |
| `role` | `recovery` | A | `enums.eventRoles` |
| `params` | object | A | 转移事件的 `target` 状态与守卫参数 |
| `when` | object | A | 执行条件（见 7.5） |
| `guard` | object | A | 事件级守卫 |
| `fault` | `{mode, [errorCode], [httpStatus]}` | A | `mode` ∈ `enums.faultModes` |
| `errorCode` | string | A | 事件级守卫拒绝时的错误码 |
| `ms` | int ≥ 0 | A | `Wait` 事件推进的逻辑时间 |

`errorCode` 与 `ms` 不在设计 3.2 的事件选项表中，但引擎会读取，契约把它们列为可选字段，作者格式通道可按需开放。

**事件选项的类型与取值**（契约检查器逐项核对，违反报 `GEMARC_ATLAS_MODEL_011`，枚举与引用分别报 `030`、`040`）：

| 字段 | 要求 |
|---|---|
| `ruleId`、`group`、`dedupKey`、`errorCode` | 非空字符串 |
| `ack`、`section` | 布尔值（`"yes"`、`1` 都不行） |
| `params` | 对象 |
| `ms` | 整数 ≥ 0（布尔不算整数） |
| `latencyMs` | 整数 ≥ 0，或非空的此类整数数组 |
| `role` | ∈ `enums.eventRoles` |
| `edge` | → `edges`（规范 id） |
| `fault` | 对象；只允许 `mode`（必需，∈ `enums.faultModes`）、`errorCode`（非空串）、`httpStatus`（100..599 的整数） |
| `guard`、`when` | 形态见 7.5；`when` 里的节点必须存在，状态必须是该节点可取的状态（机器节点取其 `states`，其它节点取 `vocab.nodeStates`） |

**事件 id**：引擎与因果比较用 `<caseId>#<seq>` 称呼用例里的一个事件（`model.event_id(case_id, seq)`，`model.parse_event_id` 反向解析；`seq` 从 1 起，不带前导零）。`#` 不在 `ID_RE` 内，所以用例 id 不会含它，拆分是无歧义的。`interventions[].targetEventId` 必须是这种形态，且（1）该用例存在，（2）`1 <= seq <= 该用例的事件数`，（3）该用例在这条干预的 `appliesTo` 里（否则延迟永远不会生效）。参考实现的因果比较靠这个格式的 `#` 后部分对齐基线事件，所以作者无需也不应自造事件 id。

### 4.9 `interventions[]`

`{interventionId, type, [type-specific params], description, appliesTo, expect, note, [why]}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `interventionId` | id | 是 | A | |
| `type` | string | 是 | A | `vocab.interventionTypes` |
| `description` | text | 是 | A | |
| `appliesTo` | ids | 是 | A（`applies=`） | → `cases`；**非空**：没有用例可运行的干预无法执行（参考实现的搜索在没有用例时会因 `appliesTo[0]` 缺失而崩溃） |
| `expect` | `{[outcomeClass], [terminal], [status]}` | 是 | A（`class`、`terminal`、`status`） | `outcomeClass` ∈ `vocab.outcomeClasses`；`terminal` 是 `{nodeId: state}`，键可以是节点或本干预插入的元素；`status` ∈ `enums.runStatuses` |
| `note` | string | 是 | A | 缺省 `""`；预测比对原样带出 |
| `why` | string | 否 | A | 作者给出该预期的理由；属于 Atlas 新增 |

类型相关参数（由 `vocab.interventionTypes[type].required/optional` 规定，契约检查器逐项核对；出现不属于该类型的参数是错误）：

| `type` | 必需参数 | 可选参数 |
|---|---|---|
| `disable_module`、`resource_failure`、`identity_failure` | `targetNodeId` | |
| `disable_edge` | `targetEdgeId` | |
| `change_timeout` | `timeoutMs`（>0） | |
| `change_retry` | `retryMax`（≥0） | `retryBackoffMs`（≥0） |
| `delay_message` | `addLatencyMs`（≥0） | `targetEdgeId`、`targetEventId`（`<caseId>#<seq>`，见 4.8）、`targetRuleId`（**至少一个**） |
| `change_guard` | `machineId`、`transitionId`、`guard`（对象；`null` 表示移除守卫） | |
| `insert_element` | `element`、`rewrite`（数组，**可为空**：草稿元素尚未连边） | |
| `combined_intervention` | `parts`（非空数组，每项 `{type, ...参数}`，可再嵌套 `combined_intervention`，至多 `MAX_PART_DEPTH`=6 层） | |

**参数取值**（契约检查器逐项核对）：`timeoutMs` 整数 ≥ 1；`retryMax`、`retryBackoffMs`、`addLatencyMs` 整数 ≥ 0（布尔、字符串、小数都不行）；`targetNodeId` → `nodes`；`targetEdgeId` → `edges`；`targetEventId` 见 4.8；`targetRuleId` 必须是某个**被应用用例**（`appliesTo`）里至少一个事件的 `ruleId`；`machineId` → `machines`，`transitionId` 必须是该机器的转移 id；`guard` 为 `null`（移除守卫）或 7.5 形态的对象。`disable_module`、`resource_failure`、`identity_failure` 的 `targetNodeId` 可以是机器节点（参考引擎只检查节点存在，机器也是节点）。

**`combined_intervention` 的每个部分与顶层原子干预享受同样的检查**：类型必须是已知的原子类型，必需参数齐全，不得带不属于该类型的参数（也不得带 `description` 之类的记录字段），引用与数值取值同上，`insert_element` 部分还要核对 `element`、元素端口和每个 `rewrite` 项的链边推导。展平顺序是深度优先（`model.flatten_parts`），与参考引擎展平 `combined` 的结果一致。同一条干预里两个部分不得插入同一个元素 id（`GEMARC_ATLAS_MODEL_021`）。

**`element`**（`insert_element`）：`{elementId, kind, role, inputPorts[{id,type}], outputPorts[...], stateMachine{states, initial, transitions[{from,to,on}]}, eventHandlers[{on, do, [setState], [when], [retry]}], guardRules[], errorRules[{on,do}], resourceLimits{maxEvents, [maxHops]}, terminationConditions[string], latencyMs}`

- `elementId` 满足 `AUTHORED_ID_RE`，且不得与任何节点 id 相同；`kind` ∈ `vocab.elementKinds`；`eventHandlers[].do` ∈ `enums.handlerActions`。
- 元素端口 id 固定为 `<elementId>:in`、`<elementId>:out`（类型 `Message`、`Record` 或 `Any`）。元素若带 `inputPorts` 或 `outputPorts`，每项必须恰为 `{id, type}`，id 就是上述固定值，`type` ∈ `enums.elementPortTypes`（否则 `GEMARC_ATLAS_MODEL_050` 或 `030`）。类型的推导规则见 5.12，元素所在的房间见 5.13。
- 元素主体由 `use=<kind>` 的模板（模板通道提供）与作者覆盖项组成。**主体是否完整由引擎的 `validateElement` 判断**（缺项则状态为 `DRAFT`，这是可预期的，配 `expect.status = DRAFT`）；契约检查器只钉住 id、`kind`、处理动作枚举与 `rewrite`。

**`rewrite[]`**：`{replaceEdgeId, disposition, chain}`

- `replaceEdgeId` → `edges`（规范 id）；`disposition` ∈ `enums.dispositions`（`replace` `keep` `disable`）。
- `chain` 当且仅当 `disposition == "replace"` 时为恰好两条边，否则为 `[]`。第 *i* 个（0 起）rewrite 的两条链边是 `derive_rewrite_chain(elementId, i, 被替换的边)`（5.10）。
- **与参考实现的差异（引擎通道必读）**：参考实现的 `validateRewrite` 只对 `disable` 提前返回，`keep` 也要求 `chain` 恰有两条边；`resolve` 对 `keep` 却是空操作。本契约让 `keep` 与 `disable` 一样不带链（链只有在 `replace` 时才有意义），因此**移植 `validateRewrite` 时，链的各项检查（条数、衔接、端口方向与类型、环）只对 `disposition == "replace"` 执行**；`keep` 与 `disable` 只核对边存在、不是 `owns` 边。把链检查原样照搬会把任何含 `keep` 项的合规模型判为 `INVALID`。
- `expect.terminal` 的键可以是节点，或**这条干预任一部分**插入的元素；值必须是字符串：节点取 `vocab.nodeStates`（机器节点取其 `states`），元素取 `vocab.nodeStates` 加上该元素 `stateMachine.states`（元素的状态机由模板与覆盖项决定，契约只认它写出的状态）。

### 4.10 `requirements[]`

`{id, title, source, designIds, nodeIds, edgeIds, tstIds, evidenceIds, tbdIds}`

| 字段 | 类型 | 必需 | 来源 | 说明 |
|---|---|---|---|---|
| `id` | id | 是 | A | |
| `title` | text | 是 | A | |
| `source` | text | 是 | A | 来源；`tobe` 时是用户原话的引文 |
| `designIds` | string[] | 是（可为空） | A（`design=`） | 自由文本的设计章节引用 |
| `nodeIds` | ids | 是（可为空） | A（`nodes=`） | → `nodes`；**不由用例推导**（推导出的成员精度过低） |
| `edgeIds` | ids | 是（可为空） | A（`edges=`）/ D | 缺省 = 两端都在 `nodeIds` 中的边（5.8） |
| `tstIds` | ids | 是（可为空） | A（`tests=`） | → `tests`（封闭注册表） |
| `evidenceIds` | ids | 是（可为空） | A（`ev=`） | → `evidences` |
| `tbdIds` | ids | 是（可为空） | A（`tbd=`） | → `tbds`；参考实现的单值 `tbd` 改名为 `tbdIds` 并改为数组 |

需求到用例的链接是 `cases[].requirementIds` 的**逆向**：`cases_of_requirement(cases, id)`。模型里不存 `caseIds`。

### 4.11 `tests[]`、`tbds[]`、`policies[]`

**`tests[]`**：`{id, title}`，封闭注册表，需求与用例引用的测试 id 必须在此定义。

**`tbds[]`**：`{id, title, decision, evidenceIds}`。

**`policies[]`**：`{id, text, src, scope, level, chapter, kind, by}`

| 字段 | 类型 | 必需 | 缺省 | 说明 |
|---|---|---|---|---|
| `id` | id | 是 | | |
| `text` | text | 是 | | 约束正文 |
| `src` | id | 是（强制） | | → `requirements` 或 `tbds`；让每条政策可追溯 |
| `scope` | string，`SCOPE_RE` | 是 | `all` | `all`、`kind:<Kind>`、`node:<id>`、`req:<id>`，所指对象必须存在 |
| `level` | string | 是 | `REQ` | `vocab.levels`：政策出现的最浅文档层，依“REQ ⊆ BD ⊆ DD”规则出现在更深层 |
| `chapter` | int \| null | 是 | `null` | 1..20；`null` 表示取该 `kind` 在骨架中的默认章节 |
| `kind` | string | 是 | `constraint` | `vocab.policyKinds`：`constraint nfr data` |
| `by` | `ai` \| `user` | 是 | `ai` | `enums.policyBy`；质量报告统计 `by=ai` 的条数 |

## 5. 推导公式（唯一权威）

下列函数都在 `model.py`；`profile` 参数缺省取 `vocab.default_profile()`。

### 5.1 端口与边端口

```
derive_ports(nodes, machine_ids, record_kinds)
    对 nodes 按顺序，跳过 id 属于 machine_ids 的节点；对其余节点依次输出
        {id: node.id + ":in",  nodeId: node.id, direction: "in",  type: T}
        {id: node.id + ":out", nodeId: node.id, direction: "out", type: T}
    其中 T = "Record" 若 node.kind 属于 record_kinds，否则 "Message"
node.portIds              = port_ids(id, is_machine)   # [id:in, id:out]；机器节点为 []
edge.sourcePortId         = None 若 sourceId 属于 machine_ids，否则 sourceId + ":out"
edge.targetPortId         = None 若 targetId 属于 machine_ids，否则 targetId + ":in"
```

`record_kinds` = 词表中 `record` 为真的种类，软件词表中只有 `Event`。`ports` 整体等于上式结果（契约检查器逐项比较）。

### 5.2 继承（`inherit_props`）

```
props = deepcopy(作者 props)
对 tier、area：若房间有该键且 props 没有，则 props[key] = room[key]
若 building.trust 存在且 props 没有 "trust"，则 props["trust"] = building.trust
```

作者写的 `props` 键**永远优先**。继承后的值写入展开模型，使读取 `props.tier`、`props.trust` 的移植代码无需改动。

### 5.3 默认验证等级

```
evidence_kinds = 该记录自己的证据的 sourceKind 列表（无法解析的 id 被跳过，由 RF-003 报告）
基础值： 含 "source-code" → Implemented；否则有任何证据 → Designed；否则 Hypothetical
mode == "tobe"          → 封顶 Designed（cap_verification）
边且证据来自 ev=^ 继承   → 封顶 Designed
```

- `default_verification_for_node(kinds, mode)`、`default_verification_for_edge(kinds, mode, inherited)` 即上式。**绝不派生 `Observed`、`Inferred`；`Inferred` 与降级必须由作者写 `verif=`。**
- `tobe` 模式下派生值不会超过 `Designed`，因此“没有任何作者主张却触发 EV-010 错误”不会发生；作者**主动写**的 `verif=Implemented` 仍由 EV-010 报错。
- 验证等级强弱按 GemArc 的 rank 比较（`Implemented`=6 > `Observed`=5 > `Designed`=4 > `Inferred`=3 > `Hypothetical`=2 > `Unknown`=1）。`weakest(levels)` 取 rank 最低者，空输入为 `Unknown`。
- `Implemented` 必须有至少一条 `source-code` 证据（节点与边都是；`contract_issues` 报 `GEMARC_ATLAS_MODEL_051`）。

### 5.4 证据

```
evidence_kind(filePath):
    filePath 以 "user:" 开头                        → "user-requirement"      # 先判断，故 user:x.md 仍是用户需求
    取最后一个 "/" 之后的文件名（"\" 先换成 "/"）；扩展名 = 最后一个 "." 起的部分，转小写；
    点出现在位置 0（隐藏文件）或没有点 → 扩展名为空
    扩展名 ∈ vocab.docExt                           → "document"
    否则                                           → "source-code"
evidence_verification(kind, mode):
    vocab.sourceKinds[kind].verification   # source-code→Implemented，document→Designed，user-requirement→Designed
    mode == "tobe" 时封顶 Designed
repository = 作者的 repo 别名；user-requirement 为 ""；没有别名则 ""
lineRange  = null（展开器）；验证器盖章后为 [from, to]
```

### 5.5 机器

```
states   = [initial] + 按顺序遍历每个转移，依次加入 from、to 中尚未出现的状态
terminal = [s for s in states if 没有任何转移的 from == s]
transition.id = 显式 id 或 transition_id(machine.id, from, to)
nodeId   = id
```

`states` 没有作者覆盖项，因此“孤立状态”不可能存在；可达性由 SM-002 检查。无出边的机器（如循环生命周期）`terminal` 为空，属正常。

**顺序说明**：`states` 与 `terminal` 按首次出现顺序推导。参考实现手写的状态列表在 5 台机器里有 1 台与首次出现顺序不同（作为集合相同），所以顺序只影响显示，不影响引擎；需要固定显示顺序的作者应调整转移的书写顺序，而不是手写 `states`。

### 5.6 用例与事件

```
event.seq    = 在该用例 events 中的位置 + 1（1..n 连续）
case.policy  = merge_policy(作者的部分策略)   # 默认 DEFAULT_POLICY；DEFAULT_POLICY 是 model.py 中唯一的常量
expect 一项  = expect_cell("id")   → (id, DEFAULT_EXPECTED_STATE)   # DEFAULT_EXPECTED_STATE = "Completed"
               expect_cell("id=State") → (id, State)
```

### 5.7 `DEFAULT_POLICY`

`{"timeoutMs": 2000, "retryMax": 0, "retryBackoffMs": 0}`，是只读映射（`MappingProxyType`）；`default_policy()` 返回可修改的副本；`merge_policy(partial)` 只接受这三个键，忽略其它键。引擎、生成器、展开器共用，参考实现里的两份重复常量不再保留。

### 5.8 需求与用例的默认值

```
case.linkedTestIds     = 缺省时：按所引用需求的顺序，依次取各需求的 tstIds，去重（default_linked_tests）
requirement.edgeIds    = 缺省时：edges 中 sourceId 与 targetId 都在 requirement.nodeIds 里的边 id，按 edges 顺序
requirement→case 链接 = cases_of_requirement(cases, requirementId)
```

**过度归属风险**：`edgeIds` 的默认值把两端都在 `nodeIds` 里的**所有**边算进需求，而作者手写的 `edges` 通常只列真正承载该需求的边。在参考实现的 16 条需求上，默认值与手写 `edgeIds` 一条都不相等，12 个节点的需求会多出 11 条边。所以质量报告要把“使用默认边集”的需求单独计数，需要精确追溯的需求应显式写 `edges=`。

### 5.9 详细度与域色（查看器、文档读取，不写入模型）

```
node_level(node, room):
    node.level 若存在                                          → node.level
    room.layer 的层存在且 node.kind ∈ 该层 levelKinds           → 该层 level
    否则                                                       → vocab.kinds[node.kind].level（未知种类为 "DD"）
relation_level(relation) = vocab.relations[relation].level（未知关系为 "DD"）
node_domain(node, room, building):
    vocab.kinds[kind].domain 非空                  → 该域
    room.layer 的层存在                            → 该层 domain
    node.props.trust 或 building.trust ∈ vocab.trust → 对应的域
    否则                                           → vocab.defaults.trust
```

一条边在详细度 L 可见，当且仅当 `relation_level(relation) <= L` 且两端节点在 L 可见。未知种类或信任值**不抛错**，按上式回退并由规则 RC-002 给出警告。

### 5.10 插入元素的链边

```
chain_edge_id(element, k)         = element + ":" + k            # k 为该元素全部链边中 1 起的序号，1..98
derive_rewrite_chain(el, i, e):   # e 是被替换的边，i 是该 rewrite 在元素 rewrite 列表中的 0 起位置
    [ {id: el:(2i+1), sourceId: e.sourceId, targetId: el,         sourcePortId: e.sourcePortId, targetPortId: el:in },
      {id: el:(2i+2), sourceId: el,         targetId: e.targetId, sourcePortId: el:out,         targetPortId: e.targetPortId} ]
```

单个 rewrite 的链边是 `<el>:1` 与 `<el>:2`。链边只出现在 `rewrite[].chain`，不进入 `edges`。`owns` 边与两端口为 `null` 的边不能被替换（引擎的 `validateRewrite` 拒绝）。

### 5.11 生成的 `owns` 边

对每台机器的作者选项 `owner=<节点>`，展开器在**所有作者边之后**、按机器顺序追加一条边（`build_owns_edge`）：

```
id = edge_id(owner, "owns", machine.nodeId)       label = 机器节点的 name
relation = "owns"   static = false                evidenceIds = machine.evidenceIds
verification = default_verification_for_edge(这些证据的 sourceKind, mode)
sourcePortId = owner + ":out"   targetPortId = null    # 机器节点无端口
```

- `owns` 必须是运行时关系：`Transition` 事件从所属模块到机器节点，正是通过这条边绑定的；若标成静态，所有转移事件都会 `Unbound`。
- 作者再写一条与它三元组相同的 `owns` 边是错误（重复 id），需要改用 `owner=` 或给作者边加别名。
- 展开模型无法区分“生成的”和“作者写的”`owns` 边；编码器遇到与 `build_owns_edge` 输出完全相同的边应写成 `owner=`，其它写成边行。

### 5.12 插入元素的端口与类型

```
element_port_types(replaced_edges, ports) -> (input_type, output_type)
    input_type  = 被替换的边的源端口（sourcePortId）的类型；各边类型一致则取该类型，否则 "Any"
    output_type = 被替换的边的目标端口（targetPortId）的类型；规则相同
    某一侧没有任何端口（机器端点为 null）、或没有被替换的边（草稿元素） → "Any"
derive_element_ports(element_id, input_type, output_type)
    -> inputPorts  [{id: element_id + ":in",  type: input_type}]
       outputPorts [{id: element_id + ":out", type: output_type}]
```

端口兼容规则见 4.5（PT-003，警告级）：`a == b` 或任一端为 `Any`。元素的端口类型由被替换的边决定，所以作者不需要写它。

### 5.13 插入元素所在的房间

插入元素没有 `roomId`（它只存在于一条干预里）。查看器按 `element_home_room(rewrite, edges, nodes)` 放置它：取第一条能解析到边的 `rewrite` 项，用被替换边**源节点**的房间；没有任何可解析的项（草稿元素）时返回 `None`，查看器用自己的默认房间。参考实现的 `x.roomId || 默认房间` 在本契约里由这条规则取代。

### 5.14 类图的机器链接

参考实现的类图把“接口 → 所属模块 → 状态机”写成固定表。本契约用数据代替：接口或类节点的 `props.owner` 是所属模块的节点 id，该模块通过 `owner=` 生成的 `owns` 边指向机器节点，类图的链接就是 `linked_machine_ids(node, edges, machine_ids)`——`props.owner` 的 `owns` 边的目标中属于机器的那些，按边的顺序、去重。没有 `props.owner` 或没有这样的边时为空列表，类图不画状态枚举的核对，由规则 DG-002 给出提示。

### 5.15 机器的所有者

每个机器节点都应有至少一条指向它的 `owns` 边（`owner=` 生成）：`Transition` 事件只能经这条边绑定，状态图也从它找到所属模块。契约检查器**不**强制这一点（规则 SM-003 “owner valid” 负责，见第 8 节），所以文档与图表构建代码遇到没有所有者的机器必须报告 SM-003 而不是崩溃（参考实现的状态图在这种情况下会崩溃）。

## 6. 作者选项到展开字段的映射

本表只说明**展开后的字段名**；`.atl` 的列与选项语法由格式通道定义，下表中的选项名取自设计 3.2。

| 段 | 作者写入 | 展开字段 |
|---|---|---|
| buildings | `id`、`name`；`trust` | `id`、`name`、`trust` |
| floors | `id`、`building`、`name`、`level` | `id`、`buildingId`、`name`、`level` |
| rooms | `id`、`floor`、`name`；`layer` `tier` `area` | `id`、`floorId`、`name`；同名 |
| nodes | `id`、`name`、`sub`；`room` `kind` `ev` `verif` `props` `group` `level` | `id`、`name`、`sub`；`roomId` `kind` `evidenceIds` `verification` `props`（合并继承）`group` `level`；推导 `portIds` |
| edges | `from`、`rel`、`to`、`label`；`ev` `verif` `card` `alias` `group` | `sourceId`、`relation`、`targetId`、`label`；`evidenceIds` `verification` `cardinality` `id`（别名）`group`；推导 `type` `directed` `static` `sourcePortId` `targetPortId` `guardId` |
| evidences | `id`、`file`、`anchor`、`topic`；`repo` | `evidenceId`、`filePath`、`anchor`、`topic`；`repository`；推导 `sourceKind` `verification` `lineRange` |
| machines | `id`、`initial`；`owner` `err` `ev` | `id`、`initial`；`errorCode` `evidenceIds`；`owner` 生成 `owns` 边；推导 `nodeId` `states` `terminal` |
| transitions | `from`、`to`；`guard` `err` `id` | `from`、`to`；`guard` `errorCode` `id`（缺省推导） |
| tests | `id`、`title` | `id`、`title` |
| cases | `id`、`cat`、`title`；`req` `ev` `policy` `ctx` `init` `tests` | `caseId`、`category`、`title`；`requirementIds` `evidenceIds` `policy`（补全）`context` `initialMachines` `linkedTestIds`（缺省推导） |
| events | `type`、`from`、`to`；`case` 与各选项 | 嵌入对应用例的 `events`；同名选项；推导 `seq` |
| expect | `case`、`nodes`；`by` `outcomes` `out` | 写入对应用例：`expectedTerminalState`（`nodes` 里写 `id` 表示 `id=Completed`，写 `id=State` 表示该状态）、`expectedOutcomes`、`expectedBy` |
| interventions | `id`、`type`、`description`；`target` `applies` `class` `terminal` `status` `why` `note` `params` `element` `use` `rewrite` `parts` | `interventionId`、`type`、`description`；`target` 依类型落在 `targetNodeId` 或 `targetEdgeId`；`appliesTo`；`expect.outcomeClass` `expect.terminal` `expect.status`；`why` `note`；`params` 展开为类型相关键；`element`（模板加覆盖）；`rewrite`（推导 `chain`）；`parts` |
| requirements | `id`、`title`、`source`；`nodes` `edges` `tests` `ev` `tbd` `design` | `id`、`title`、`source`；`nodeIds` `edgeIds`（缺省推导）`tstIds` `evidenceIds` `tbdIds` `designIds` |
| tbds | `id`、`title`、`decision`；`ev` | `id`、`title`、`decision`；`evidenceIds` |
| policies | `id`、`text`；`src` `scope` `level` `chapter` `kind` `by` | 同名，缺省值见 4.11 |

## 7. 词表（`profiles/software.json`）

### 7.1 结构

标签型分组的元素是 `{id, label: {zh-CN, ja-JP, en-US}, ...}`，**三种语言必须齐全且非空**（`vocab.profile_issues` 与测试都检查）：`verification sourceKinds trust domains tiers layers levels kinds relations views caseCategories eventTypes interventionTypes outcomeClasses nodeStates eventOutcomes elementKinds policyKinds`。

纯枚举（无标签）放在 `enums`：`portDirections portTypes elementPortTypes handlerActions dispositions faultModes guardOps guardCombinators eventRoles modes runStatuses expectBy policyBy dedupModes edgeTypes`。另有 `docExt`、`defaults`、`aliases`、`knownProps`。

### 7.2 与规范 GemArc 词表共用 id

与 `src/gemarc_mcp/scaffold/vocab/*.json` 中已有 id 一致；Atlas 只在下表增加新 id，测试逐项断言“Atlas id ⊆ 规范 id ∪ 下表”“下表 ∩ 规范 = ∅（含大小写与连字符变体）”“下表与差集完全相等”。

| 分组 | 对应规范文件 | 新增（additive） |
|---|---|---|
| `kinds` | `entity-kinds.json` | `API`、`Storage`、`Workflow`、`Element` |
| `relations` | `edge-types.json`（`category` 必须与规范一致） | `references` |
| `caseCategories` | `case-categories.json` | `concurrency` |
| `views` | `diagram-types.json` | 无（分层依赖视图使用规范 id `dependency-diagram`，其余五个沿用 `*-view`） |
| `verification` | `evidence-levels.json` | 无；id 与 rank 逐项相等，顺序即 GemArc rank 顺序 |

**zh-CN 标签与规范词表有四处不同**（id 一致，只是显示文字）：`Table` 用“数据表”（规范为“表”，单字在图上难以辨认）、`State` 用“状态机”（本契约里它标注机器节点，规范的“状态”会与节点状态混淆）、`owns` 用“拥有”（规范的“拥有数据”只描述数据所有权，而 `owns` 还连接模块与状态机）、`belongsTo` 用“隶属”（规范为“归属”）。测试固定恰好这四处，新增差异必须在此补一句理由。

**`docExt`（5.4）的取舍**：除文档类扩展名外，还包含办公与图表格式 `.rtf .tex .xls .xlsx .ods .odp .vsd .vsdx .drawio`。**故意不含** `.txt .html .htm .csv`：`requirements.txt`、`CMakeLists.txt`、网页模板与数据文件在仓库里通常是源码，把它们当文档会把实现证据降成 `Designed`。代价是以这些扩展名保存的设计资料会被分类为 `source-code`（得到 `Implemented`），作者应改用上面列出的格式。

两处特别说明：

- 设计文字里的 `combined` 与规范 id `combination` 冲突，**以规范为准**，使用 `combination`；`aliases.caseCategories.combined → combination` 只用于给 AI 提示，不是合法值。
- 关系 id 一律用规范的驼峰写法（`dependsOn`、`calls`、`writes` 等），不使用蛇形命名。

### 7.3 节点种类与关系摘要

| 种类 | 形状 | 详细度 | 域 | 记录型 | 过程视图行 | 可作节点 | 备注 |
|---|---|---|---|---|---|---|---|
| `Actor` | round | REQ | | | 3 | 是 | |
| `API`（新增） | tri | REQ | | | 3 | 是 | 对外入口 |
| `Workflow`（新增） | hex | REQ | | | 2 | 是 | |
| `Module` `Service` `Component` | hex | BD | | | 2 | 是 | |
| `Class` | box | DD | | | 2 | 是 | |
| `Interface` | tri | BD | | | 2 | 是 | |
| `Entity` `Table` | box | BD | | | 0 | 是 | |
| `Storage`（新增） | box | BD | | | 0 | 是 | |
| `Event` | diamond | DD | | **是** | 0 | 是 | 唯一产生 `Record` 端口的种类 |
| `State` | octa | BD | `business` | | 1 | 是 | 机器节点通常用它 |
| `Element`（新增） | crown | DD | | | 2 | **否** | 仅插入元素使用 |

| 关系 | 类别 | 运行时 | 静态 | 详细度 | 跨信任域 |
|---|---|---|---|---|---|
| `calls` | behavior | 是 | 否 | REQ | 否 |
| `sends` | flow | 是 | 否 | REQ | **是** |
| `owns` | data | **是** | 否 | REQ | 否 |
| `receives` `publishes` `subscribes` | flow | 是 | 否 | BD | **是** |
| `reads` `writes` | data | 是 | 否 | BD | 否 |
| `triggers` | behavior | 是 | 否 | DD | 否 |
| `dependsOn` `implements` `contains` `belongsTo` | structure / behavior | 否 | **是** | BD | 否 |
| `constrains` `references`（新增） | constraint / data | 否 | 是 | DD | 否 |

`trustBoundary`（上表最后一列）与规范词表 `edge-types.json` 的同名标志逐项相等（测试断言）；安全视图用它高亮跨信任域的关系。`Profile.trust_boundary_relations` 给出集合。

`runtime` 与 `static` 互为补集（词表校验与测试都断言）；引擎的 `STATIC_RELATIONS = {static 为真的关系}`。每个关系还带 `views`（它出现在哪些视图）与 `color`；每个视图带 `kinds`（`null` 表示全部）。

### 7.4 `props` 已知键

`props` 是开放字典，未知键仅作显示。`profiles/software.json` 的 `knownProps` 列出被引擎、图表或文档读取的键：

| 键 | 含义 | 读取者 |
|---|---|---|
| `aggregator` | bool：节点容忍失败区段并以 `Degraded` 结束 | 引擎、文档 |
| `dedup` | `{mode: reject\|ignore, [key], [errorCode], [httpStatus]}`；`key` 是去重键的名称（文档显示，事件的 `dedupKey` 是它的取值） | 引擎、文档 |
| `columns` | 列名字符串，或 `{name, [type], [pk], [fk], [unique], [nullable], [required], [pii]}` | ER 图、文档 |
| `methods` | 方法签名列表 | 类图、文档 |
| `enums` | `{EnumName: [value, ...]}` | 类图、文档 |
| `owner`、`store` | 所属模块 / 底层存储的节点 id（`owner` 还决定类图的机器链接，见 5.14） | 文档、类图 |
| `route`、`stack`、`perms`、`role` | 显示用 | 文档 |
| `tier`、`area`、`trust` | 继承自房间与建筑（5.2） | 查看器、图表 |

列的标记（`pk`、`unique`、`nullable`、`required`、`pii`、`fk`）是**数据**，不再由固定代码按表名与列名猜测。

### 7.5 守卫与 `when` 的形态

- 守卫：`{param, op, [value]}`，`op` ∈ `enums.guardOps`（`present absent eq neq in`）；组合子 `{all: [...]}`、`{any: [...]}`、`{not: 守卫}`；`value` 可以是 `"$ctx.<key>"`，从用例 `context` 取值。`present` 与 `absent` 不带 `value`。
- `when`：`{node, is: [状态...]}`、`{node, not: [状态...]}`（等价于 `not_in`）、`{any: [...]}`、`{all: [...]}`、`{not: when}`、`{elementState}`，或一个参数守卫。
- **检查器核对的形态**：守卫必须是对象；组合子对象恰有一个键（`all`、`any` 是非空数组，`not` 是守卫）；参数守卫的键只有 `param`（非空串）、`op`（∈ `enums.guardOps`）、`value`；`present`、`absent` 不带 `value`；`eq`、`neq`、`in` 必须带 `value`，`in` 的值是数组或 `$ctx.<key>` 引用，`eq`、`neq` 的值是标量或 `$ctx.<key>` 引用（`$ctx.` 后必须有键名）。`when` 的 `node` 必须存在，`is`/`not` 是非空状态数组且每一项都是该节点可取的状态，`is` 与 `not` 二选一，`elementState` 是非空字符串。守卫与 `when` 的嵌套不得超过 `MAX_GUARD_DEPTH`（16）层。同一套检查用于转移守卫、事件的 `guard` 与 `when`、`change_guard` 的 `guard`。

## 8. 契约检查器

`contract_issues(model, profile=None) -> list[{code, path, message}]`，`check_contract(model)` 在有问题时抛 `AtlasModelError`。它从不抛出其它异常（任意垃圾输入也只返回问题列表）。

| 代码 | 含义 |
|---|---|
| `GEMARC_ATLAS_MODEL_001` | 构造 id 的某部分含 `:`、换行或为空 |
| `GEMARC_ATLAS_MODEL_002` | 辅助函数收到违反契约的参数（方向、序号、NaN 等） |
| `GEMARC_ATLAS_MODEL_010` | 顶层结构：缺键、多键、`schema` 错误、集合不是数组 |
| `GEMARC_ATLAS_MODEL_011` | 记录字段：缺必需字段、未知字段、类型不符 |
| `GEMARC_ATLAS_MODEL_012` | 字符串（或对象键）含孤立的 UTF-16 代理码位（如 U+D800），无法编码为 UTF-8，`model_sha` 与文件写入都会失败 |
| `GEMARC_ATLAS_MODEL_013` | 模型里任何位置出现非有限数（`NaN`、`Infinity`、`-Infinity`），无法写成规范 JSON |
| `GEMARC_ATLAS_MODEL_020` | id 不满足对应语法 |
| `GEMARC_ATLAS_MODEL_021` | id 重复（全局唯一、转移 id、元素与节点冲突） |
| `GEMARC_ATLAS_MODEL_030` | 取值不在词表或枚举内 |
| `GEMARC_ATLAS_MODEL_040` | 引用的对象未定义 |
| `GEMARC_ATLAS_MODEL_050` | 推导字段与重新计算的值不一致 |
| `GEMARC_ATLAS_MODEL_051` | `Implemented` 缺少 `source-code` 证据 |
| `GEMARC_ATLAS_MODEL_060` | 机器完整性（重复转移 id 等） |

**`012` 与 `013` 的范围**（新增，原有代码的含义不变）：检查器在其它检查之前迭代遍历整个模型，覆盖所有自由文本（`name`、`sub`、`title`、`label`、`anchor`、`filePath`、`topic`、`errorCode`、策略文本等）、id、数组项、`props`、`params`、`options`、`context` 里任意深度的值和对象键，包括 `tuple`。每处问题一条，`path` 形如 `$.nodes[0].props.dedup.httpStatus`；键含代理码位时路径写成 `[ascii(键)]`，`path` 与 `message` 本身永远可以编码为 UTF-8，也不回显出问题的原文。每个代码最多列出 100 条，超出时追加一条 `path` 为 `$` 的汇总（`N more issues of this kind are not listed`）。合法的增补平面字符（如表情符号）（一个码位）不算代理码位。格式层仍在自己的输入边界拒绝同样的值（ST-007、ST-008），本条覆盖用其它方式构造的模型。

检查器核对：顶层与记录形态、id 语法与全局唯一、词表与枚举、引用完整性、每个推导字段、事件选项的类型与取值（4.8）、守卫与 `when` 的形态（7.5）、干预的必需参数、数值边界与引用（4.9，含 `appliesTo` 非空、`targetEventId`、`targetRuleId`）、`combined_intervention` 的每个展平后的部分、元素端口与 `rewrite` 链的推导、`expect.terminal` 的键与状态值、嵌套深度（2.1）。

契约检查器**不做**，各由下列规则或引擎负责（括号里是设计 5.1 的规则 id，新增的建议写明）：

| 不做的检查 | 归属 |
|---|---|
| AI 面向的规则：错别字提示、`pending`、覆盖率、预言机、证据在磁盘上是否存在 | 规则层 |
| 同建筑楼层 `level` 唯一 | RC-001 |
| 状态可达、终态已声明 | SM-002、SM-005 |
| 每个机器节点至少有一条 `owns` 边（所有者有效） | SM-003（构建代码遇到缺失必须报告而不是崩溃，见 5.15） |
| 事件是否绑定到运行时边 | CS-001（需引擎） |
| `expectedOutcomes[].ruleId` 是否出现在用例事件中 | OR-001 一族（预言机） |
| `evidence.repository` 是否在 `meta.repos` 里声明 | EV-007 |
| `linkedTestIds` 是否是所引需求 `tstIds` 的子集（设计 3.3 第 6 条的“子集检查”） | RF-005 |
| 事实等级要求证据：规范词表 `evidence-levels.json` 对 `Implemented` `Observed` `Designed` `Inferred` 都标了 `requiresEvidence`，契约只强制 `Implemented` 要有 `source-code`（`051`） | 其余等级无证据由 EV-009 报告（节点，警告）；**边没有对应规则**，留给规则通道决定 |
| 非节点 id 的形近字符（`ID_RE` 沿用规范，见第 3 节） | 规则层 lint |
| 干预的目标是机器节点 | 不禁止：参考引擎只检查节点存在 |
| 元素主体是否完整、`rewrite` 是否为空、`owns` 边不可替换、同一条边被改写两次、端口方向与类型兼容 | 引擎的 `validateElement` 与 `validateRewrite`（链检查只对 `replace`，见 4.9） |

## 9. 引擎、查看器、文档构建读取的字段

字母含义：**E** = 引擎（`simulate`、干预编译、因果比较、守卫扫描）与完整性校验；**V** = 查看器（3D 与 2D 布局、场景缓冲、详细度过滤、搜索、界面外壳）；**D** = 文档、图表、提示词构建。表是最小读取面：某一列没有标记，说明该消费者不应依赖这个字段。

### 9.1 `meta`、`space`

| 字段 | E | V | D | 备注 |
|---|---|---|---|---|
| `meta.projectId` `meta.mode` `meta.languages` `meta.title` | | V | D | 偏好键、语言切换、文档抬头 |
| `space.buildings[].id` `.name` | E（校验） | V | D | 建筑顺序即数组顺序 |
| `space.buildings[].trust` | | V | D | 域色、安全视图分区、继承 |
| `space.floors[].id` `.buildingId` `.name` | E（校验） | V | D | |
| `space.floors[].level` | | V | D | 3D 楼层高度、2D 排序、组件图依赖方向检查 |
| `space.rooms[].id` `.floorId` `.name` | E（校验） | V | D | |
| `space.rooms[].layer` `.tier` `.area` | | V | D | 域色、详细度、图表分组 |

### 9.2 `nodes`

| 字段 | E | V | D | 备注 |
|---|---|---|---|---|
| `id` | E | V | D | |
| `kind` | | V | D | 形状、缩放、详细度、过程视图行、搜索 |
| `name` | | V | D | 标签 |
| `roomId` | E（校验） | V | D | 空间链、成员查询 |
| `sub` | | V | D | 检视面板、文档 |
| `evidenceIds` | E（因果设计关联） | V | D | |
| `verification` | E（最弱等级） | V | D | 置信徽标、图表 `evidenceLevel` |
| `props.dedup` `props.aggregator` | E | | D | 事件去重、聚合降级 |
| `props.trust` | | V | D | 域色、安全视图 |
| `props.tier` | | V | D | 图表过滤 |
| `props.columns` `props.methods` `props.enums` `props.owner` `props.store` | | V | D | ER、类图、文档 |
| `portIds` | E（校验） | | D | |
| `group` | | | D | 图表成员（取代 id 前缀过滤） |
| `level` | | V | D | 详细度覆盖 |

### 9.3 `edges`

| 字段 | E | V | D | 备注 |
|---|---|---|---|---|
| `id` | E | V | D | |
| `sourceId` `targetId` | E | V | D | |
| `relation` | E（`owns` 特例） | V | D | 视图、详细度、颜色、事件绑定 |
| `static` | E（路由时排除静态边） | V（依赖视图分层） | | 取代引擎对 `STATIC_RELATIONS` 的查表 |
| `label` | | V | D | 边标签 |
| `verification` `evidenceIds` | E（因果） | V | D | |
| `cardinality` | | V | D | ER 关系 |
| `sourcePortId` `targetPortId` | E（插入元素校验） | | D | |
| `type` `directed` `guardId` | | V（箭头） | D（表） | 常量 |
| `group` | | | D | 图表成员 |

### 9.4 其余集合

| 集合与字段 | E | V | D | 备注 |
|---|---|---|---|---|
| `ports[].id` `.nodeId` `.direction` `.type` | E（元素端口与方向、类型校验） | V（检视） | D | |
| `evidences[].evidenceId` `.filePath` `.anchor` `.topic` `.lineRange` `.sourceKind` `.verification` | E（`Implemented` 校验读 `sourceKind`） | V | D | `lineRange` 可能为 `null` |
| `machines[].id` `.nodeId` `.initial` `.states` `.terminal` `.transitions` `.errorCode` | E | V | D | 转移执行、守卫扫描、状态图 |
| `cases[].caseId` `.category` `.title` | | V | D | |
| `cases[].policy` `.context` `.initialMachines` `.evidenceIds` | E | | D | |
| `cases[].events[]`（全部字段） | E | V（过程视图、播放） | D | |
| `cases[].expectedTerminalState` `.expectedOutcomes` | E（预言机比对） | V（质量页） | D | 仅在封存后才展示结果 |
| `cases[].requirementIds` `.linkedTestIds` | | V | D | 追溯 |
| `interventions[]`（全部字段） | E | V（5D） | D | `expect` 供预测比对；`combined_intervention` 先 `flatten_parts` 再编译；`rewrite` 的链检查只对 `replace`（4.9）；`targetEventId` 是 `<caseId>#<seq>`（4.8） |
| `requirements[].id` `.title` `.source` `.nodeIds` `.edgeIds` `.designIds` `.tstIds` `.evidenceIds` `.tbdIds` | E（因果设计关联读 `nodeIds` `edgeIds` `designIds`） | V | D | |
| `tests[]` `tbds[]` | | V（搜索） | D | |
| `policies[]` | | | D | 章节正文 |

## 10. 与参考实现的差异（逐项，附理由）

| # | 参考实现 | 本契约 | 理由 |
|---|---|---|---|
| 1 | 集合名 `entities`、`spatial`、`stateMachines` | `nodes`、`space`、`machines` | 设计 D1 的命名；与规范模型的 `nodes` 一致 |
| 2 | `spatial` 带 `rule`、`evidenceIds`；建筑带 `zone` | 一律去掉 | 没有通用消费者读取它们 |
| 3 | 元数据含版本号、修订号、提交、采集时间、`views` | `meta` 改为 `projectId title mode languages profile repos`；视图清单进词表 | 模型不得含时间戳，使 `model_sha` 可复现；仓库路径来自服务器环境 |
| 4 | 端口 id `p.<node>.in`、`p.<node>.out` | `<node>:in`、`<node>:out` | 设计 D1；可逆且在 `ID_RE` 内 |
| 5 | 边 id 手写 | `src:rel:tgt`，超过 120 字符用哈希，`alias` 即 id | 设计 D1；让 AI 不必为绝大多数边发明 id；规范 `ID_RE` 不接受 `>` |
| 6 | 边无 `static`、`group` | 增加 `static`（推导）与可选 `group` | 设计 3.3；`group` 取代按 id 前缀过滤图表成员 |
| 7 | 验证等级强弱次序以 `Observed` 为首 | 使用 GemArc 的 rank（`Implemented` > `Observed`） | 设计 D1“证据 rank = GemArc 顺序”；移植的 `weakest()` 必须按 rank 比较 |
| 8 | 节点 `props` 里每个节点都写 `tier`、`area`、`trust` | 房间声明 `tier`、`area`，建筑声明 `trust`，展开时写入 `props`，作者键优先 | 设计 3.3；省去重复作者字节，读取方无需改动 |
| 9 | 用 `props.machine` 标记机器节点；`props.physical`、`props.module`、`props.component` 驱动详细度与图表 | 机器节点 = `id ∈ machines`；用 `level`、`group` 取代后三者 | 避免固定代码里的领域字符串 |
| 10 | 房间 id 字面量决定业务层 | 房间 `layer=business` | 去掉领域常量，由数据驱动 |
| 11 | 参考实现的三值信任集 | `external perimeter internal restricted`（旧值不再沿用），`domains` 另含 `business` | 通用软件系统的信任分区；颜色由词表给出，未知值回退而不崩溃 |
| 12 | `tier`、`area` 为自由字符串 | `tier` 取自 `T1..T5`；`area` 仍为记号 | 封闭词表便于校验与图表分层 |
| 13 | 关系名为蛇形、专用；`STATIC_RELATIONS` 写在代码里 | 规范驼峰 id；`runtime`/`static` 标记在词表；新增 `references` | 设计 D1；词表驱动视图，未知关系不再让视图变空 |
| 14 | 节点种类集合写在代码里 | 词表，每种带形状、缩放、详细度、域、`record`、过程视图行、`authorable` | 设计 D10“词表驱动”；新增 `API Storage Workflow Element` |
| 15 | 用例类别 `combined` | 规范 id `combination`；`concurrency` 为新增 | 规范词表优先 |
| 16 | 证据 `sourceKind` 只有两种；`lineRange` 必为区间 | 增加 `user-requirement`；`lineRange` 可为 `null`（验证器盖章） | 设计 3.3 与 D9；**这是唯一保留原名但类型放宽的字段**，文档构建必须处理 `null` |
| 17 | `repository` 为字符串 | 仍为字符串，`user-requirement` 与未声明时为 `""` | 不引入 `null`，保持原类型 |
| 18 | 机器 `states`、`terminal`、转移 id 手写；`nodeId` 另写 | 推导；`nodeId = id`；`errorCode` 可为 `null` | 设计 3.3 |
| 19 | `owns` 边手写 | 由 `owner=` 生成并钉死 id、标签、证据与验证等级 | 设计 3.3；保证机器节点有所有者 |
| 20 | 事件 `seq` 手写 | 推导为行序 | 设计 3.3 |
| 21 | 用例 `policy` 可缺项；默认值在两处重复 | 永远三键齐全；单一常量 | 设计 3.3 与引擎四处修复之一 |
| 22 | 用例 `linkedTestIds`、需求 `edgeIds` 手写 | 缺省推导 | 设计 3.3 |
| 23 | 需求 `tbd`（单个字符串，缺省为 `null`） | `tbdIds`（数组，缺省 `[]`） | 一个需求可有多个未决问题；类型变了所以改名，避免移植代码里 `if (r.tbd)` 对空数组误判 |
| 24 | 需求引用的测试是自由字符串 | `tests` 封闭注册表 | 关闭“发明 id”的通道 |
| 25 | 无政策集合 | `policies`，`src` 强制 | 设计 D4；让约束可追溯 |
| 26 | 干预元素种类名含空格 | 无空格驼峰（`NewService` 等），共 14 种 | 作者选项记号里不能有空格 |
| 27 | 元素端口 `p.<el>.in`；链边 `e.<el>.1` | `<el>:in`；`<el>:<n>` | 与端口、边 id 规则统一 |
| 28 | 干预无 `why` | 增加可选 `why` | 设计 3.2 |
| 29 | 用例无 `expectedBy` | 增加可选 `expectedBy` | 设计 D8 的预言机来源标记 |
| 30 | 全局唯一只覆盖节点、边、用例 | 所有作者 id 全局唯一，派生 id 不得冲突 | 设计 ST-002；节点 id 另限无冒号、至多 124 字符 |
| 31 | 序列化为插入顺序键 | 每层键排序的 canonical JSON | 两种前端与编码器都得到同一字节，哈希稳定 |
| 32 | 事件类型、引擎状态、结果、守卫运算符 | 不变，进入词表 | 引擎端口按原语义移植 |
| 33 | `validateRewrite`：只有 `disable` 提前返回，`keep` 也要求 `chain` 恰有两条边 | `keep` 与 `disable` 都是 `chain: []`，链检查只对 `replace` | 链只有替换时才有意义，`resolve` 对 `keep` 本是空操作；引擎通道按此移植（4.9） |
| 34 | 模型版本号与修订号随模型保存，界面抬头与运行结果显示它们 | 没有手写字段，版本戳是 `model_sha8` | 手写版本号会与内容脱节；canonical 字节哈希前 8 位由内容决定（2.1） |
| 35 | 去重信息 `props.dedup` 读取 `mode`、`errorCode`、`httpStatus` 与 `key` | 词表登记 `key`（去重键名称，文档用） | 提示词构建读取它，契约不应让它成为未登记的键（7.4） |
| 36 | 插入元素的位置用 `x.roomId \|\| 默认房间`，元素本身没有房间字段 | `element_home_room`：第一条被替换边的源节点所在房间 | 元素只存在于一条干预里，房间必须能从模型推出（5.13） |
| 37 | 类图用固定表把接口映射到所属模块与状态机 | `props.owner` 加 `owns` 边，`linked_machine_ids` | 去掉领域常量，由数据驱动（5.14） |
| 38 | 机器的 `states` 手写，顺序有时与首次出现顺序不同 | 首次出现顺序，集合相同 | 推导只有一种；顺序只影响显示（5.5） |
| 39 | 事件 id `<caseId>#<seq>` 只出现在引擎代码里 | 写进契约，`targetEventId` 按它校验 | 作者无法猜出格式，错的 id 会让延迟悄悄失效（4.8） |
| 40 | 状态图在没有 `owns` 边时崩溃 | 契约不强制所有者（SM-003 负责），构建代码必须报告而不是崩溃 | 契约检查器只管结构，所有者属于规则层（5.15） |
| 41 | 需求 `edgeIds` 手写，只列承载该需求的边 | 缺省为两端都在 `nodeIds` 的所有边（过度归属，5.8），可显式覆盖 | 设计 3.3 第 8 条；质量报告单独计数默认值 |
| 42 | 节点 id 允许任何 Unicode 单词字符 | 节点、机器、元素 id 与边端点限定 ASCII | 形近字符会造出无法分辨的两个节点（第 3 节） |
| 43 | 关系没有“跨信任域”标志 | `trustBoundary`，与规范词表逐项相等 | 安全视图按规范规则高亮（7.3） |
| 44 | `docExt` 只含文档类扩展名 | 增加办公与图表格式，仍不含 `.txt .html .htm .csv` | 见 7.2 的取舍 |

## 11. 已做决定与待确认事项

- **决定 D-1**：节点、机器、元素 id 用 `AUTHORED_ID_RE`（无冒号、至多 124 字符）；其余 id 用 `ID_RE`。
- **决定 D-2**：别名即 id，且必须满足 `AUTHORED_ID_RE`（无冒号）；检查器要求边 id 为派生值或这样的别名；`edge_ref_index` 只做精确查找。
- **决定 D-3**：`tobe` 下推导值封顶 `Designed`，作者主张越界仍由 EV-010 报错。
- **决定 D-4**：每层键排序的 canonical JSON，`model_sha` 对紧凑字节取 sha256。
- **决定 D-5**：类别用规范 id `combination`；设计文字中的 `combined` 作为别名提示。
- **决定 D-6**：`edge.static`、`edge.group`、`node.group`、`node.level`、`cases[].expectedBy`、`interventions[].why`、`requirements[].tbdIds` 为新增或改名字段。
- **决定 D-7**：`keep` 的 `rewrite` 项不带链，由引擎只在 `replace` 时检查链（4.9，第 10 节第 33 行）。
- **决定 D-8**：事件 id 是 `<caseId>#<seq>`，`targetEventId` 按它校验（4.8）；`appliesTo` 非空；`combined_intervention` 的部分与顶层原子干预同样检查（4.9）。
- **决定 D-9**：事实等级的“要证据”检查、`evidence.repository`、`linkedTestIds` 子集、机器所有者留在规则层（第 8 节列出规则 id），契约只强制 `Implemented` 要有源码证据。
- **决定 D-10**：`Profile` 与它返回的每个词表项都是只读的（`FrozenDict`、`FrozenList`；`copy.deepcopy` 得到可改的副本），因为词表被整个进程缓存共享。
- **待确认**：`.vercelignore` 目前只排除 `/src/gemarc_mcp/atlas`，不排除 `/tools`（`tools/gen_provenance.py` 无害，但部署会把它上传）。本通道无权修改该文件，已转交主线。
- **待确认**：`pyproject.toml` 的 `package-data` 需要增加 `gemarc_mcp = [..., "atlas/profiles/*.json"]`，否则 wheel 安装后找不到词表（可编辑安装与源码运行不受影响）。本通道无权修改该文件，已转交主线。
