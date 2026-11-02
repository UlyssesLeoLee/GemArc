# 屏幕产物规范：`gemarc-atlas-screen/1`

本文是 `protoui` 的构建端合同：一个已展开的 `gemarc-atlas-ui/1` 模型怎样变成 `screens/` 目录（每个界面状态一个语义 SVG、每屏一张画面卡、索引、导航、画廊页）。作者端格式见 `ui-schema.md`；本文不重复。设计依据是 A3 设计的第 4.3-4.5、6.0、6.2 节与决定 D-2、D-12、D-13；与设计不同的地方集中在第 12 节。

AI 从不手写 SVG 或 HTML：作者只写事实，固定代码派生下面的一切。构建是纯函数：同一个模型加同一份配置得到逐字节相同的文件（没有时间、路径、随机数，不依赖集合顺序）。

## 1. 产物树

```
screens/
  INDEX.md  INDEX-2.md ...        页 §SCREENS（§SCREENS-2 ...）  界面表与需求覆盖（card，≤6,000 B）
  NAV.md    NAV-2.md ...          页 §NAV（§NAV-2 ...）          Mermaid 导航图、别名对照、导航表（card，≤6,000 B）
  <enc>.card.md  <enc>.card-2.md  页 §CARD-<enc>（§CARD-<enc>-2）  每屏一张画面卡，含全部状态（page，≤9,000 B）
  <enc>.svg                       默认状态（真源）
  <enc>~<状态>.svg                每个声明的状态一个（≤20,000 B）
  build-info.json                 schema gemarc-atlas-ui-build/1（信任根，不进清单）
site/screens/<enc>.html           画廊页：该屏画面卡的 HTML 孪生（≤40,000 B）
```

`<enc>` 是 `reading.api.enc(屏幕 id)`（对整组屏幕用 `enc_many`），`~` 是 `enc` 对 `#` 的写法。分片只用数字（`INDEX-2.md`），与冻结的页 id 文法一致。INDEX、NAV 的 HTML 孪生由阅读面的孪生渲染器写，不在本构建里；画面卡的孪生就是画廊页。

## 2. SVG 的属性集

每个文件一个 `<svg>`，一行一个元素，扁平的兄弟分组加 `data-parent` 表达树，所有可见文字都是 `<text>`；没有轮廓化文字、图片、脚本、外链；颜色与字号来自 `<style>` 里的类规则（CSS 会覆盖展示属性）。

| 位置 | 属性 |
|---|---|
| `<svg>` | `xmlns` `viewBox="0 0 W H"` `width` `height`（H = 画布高 + 状态条高） `role="group"` `aria-label` `data-schema="gemarc-atlas-screen/1"` `data-screen` `data-variant` `data-frame` `data-layout` `data-ui-sha8`；`data-model-sha8` 仅当至少一条已校验的 5D 链接存在 |
| `<title>` `<desc>` | `desc` 依次写：`包含:` 控件数与种类、本状态显示 k / 共 N 与未显示的控件（点名，附原因）、`不含:`、状态说明、几何只是示意的声明；不写按 id 引用别的元素的 `aria-*` 属性 |
| 状态条 | `<g id="<屏>.state" data-kind="state-label" data-box="0 0 W h">`：第一行 `屏 · 状态 · frame WxH · layout 1 · ui <sha8> · 示意线框 · 显示 k/N`，其后是状态说明（≤60 字符，全文在 `desc`） |
| 根分组 | `<g id="<屏>.root" data-kind="screen" data-box="0 0 W H" transform="translate(0 h)">`，有屏幕级跳转或需求时带 `data-goto/nav/on`、`data-req` |
| 控件分组 | `id="<屏>.<local>"` `data-kind` `data-box="x y w h"`（首个子 `<rect>` 同盒） `data-n`（先序序号，与状态无关） `data-parent` `aria-label`；有则写 `data-emph` `data-bind` `data-api` `data-req` `data-case` `data-rule` `data-in`（仅当不是所有状态都显示） `data-goto` `data-nav` `data-on` `data-sample`（`true`：画了示例行；`none`：没有示例数据的灰条） |
| 圆标 | `<circle class="m">` 加 `<text data-mark="n">n</text>`，圆心 `(max(x-10, 10), y+10)`，半径 9 |
| 推导物 | 渲染器自己加的东西（骨架、`（空）`占位、`+k 行未画`）带 `data-derived="true"`，画成斜体或浅色，卡里单列一节 |

`local` 不得叫 `root` 或 `state`（UX-010）。字体栈 `'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif`；页面底色 `#f6f7f9`。作者字符串在进入任何产物之前经 `textfit.author`：合法的单行 XML 文本，最多 400 个字符（更长的以省略号结束，SVG、卡、画廊一致）。

## 3. 布局 L1.1（`layoutVersion` = 1）

整数运算，不依赖随机数与集合顺序；改变任何盒子坐标的修改把 `layoutVersion` 加 1。布局结果不进 UI 模型。

- **文字宽度**：CJK（U+3000-9FFF、U+AC00-D7AF、U+FF00-FFEF）与省略号 107，其余 59（百分之一 em），宽度 = `ceil(Σ × 字号 / 100)`；正文 14，`heading` 20，状态条与徽标 12。过宽的文字：`text heading button link tab menu badge checkbox radio switch` 折行；`input select textarea pagination image chart custom` 与表格单元用省略号截断。画出来的字符串（截断后的）逐字进卡。
- **框架**：`desktop` 1280x800、`tablet` 834x1112、`phone` 390x844，屏幕高 = max(框架高, 内容高)；根的边距 24（phone 20）。根下 `header`/`footer` 贴顶/贴底撑满宽，`sidebar` 贴左宽 240（phone 上排进主列），其余控件排成主列，间距 16。
- **容器**（内边距 x,y / 间距）：`stack` 0,0 / 12；`card` `form` 20,16 / 12；`list` 20,8 / 8；`sidebar` 20,16 / 8；`row` 间距 20；`header` `footer` 20,8 / 20；`toolbar` 12,8 / 20；`tabs` 间距 20；`grid` 按 `cols`（缺省 2）行优先，间距 20 与 16。横排容器（`row header footer toolbar tabs`）放不下时**折到下一行**，所以子控件永远不越出父盒。
- **居中**：desktop/tablet 上根下的 `form`/`card` 默认最宽 640 并水平居中；写了 `w`、`w=fill` 或 `align` 就不居中；`align=start|center|end` 控制任一控件在纵向容器里的水平位置，写在屏幕上则是根下控件的默认。
- **自然尺寸**：`heading` 32（每多一行 +28）、`text` 每行 22、`input` `select` 40（phone 44）、`textarea` 96、`button` 36（最小宽 88）、`badge` 22、`divider` 1、`image` 160、`chart` 200、`table` 36 + 40 × 行、`pagination` 32、`custom` 80、`header` 56、`footer` 48、`toolbar` 44、`tabs` 40；`text` `heading` `link` 与选择类标签的盒子比文字宽 4 px（余量）。
- **最小点击目标**由构造满足：desktop/tablet 32x32，phone 44x44；作者给的 px 更小时抬到最小值，并记一条 `UX-103` 的 WARN。作者的 `h` 小于内容所需时抬高（`UX-102`）。
- **状态渲染**：同一棵树按 `in` 过滤，被隐藏的不画，布局重新流动。`loading`：`table` `list` `chart` `image` 画骨架块（推导）；`empty` 与 `error`：`table`、没有可见子项的 `list` 只画表头，体内画变体的 `text=`，缺省为灰色占位 `（空）`（推导）；`note` 永远只是设计说明，不画进体内。没有 `data` 的表格在 default 里画一条灰条，不编造文字；`data` 最多画 5 行 x 6 列，其余写 `+k 行未画`。
- **状态条**高 = 12 + 16 × 行数（一行时 28），文字按帧宽的 90% 折行。
- **圆标**：每个非容器、非 `divider` 的控件一个，行与格的间距 20 让它落在空隙里。
- **嵌套**：深于 40 层的控件不画（布局逐层递归），在状态表里点名为 `(depth)`；其子孙为 `(parent)`。

v1 不能表达叠层与绝对定位、响应式断点、主题与图标库、动画、富文本、图表数据（逃生口是 `kind=custom`）。几何只是示意，规范性内容是种类、文字、字段、状态、跳转、父子与顺序。

## 4. 画面卡

页 id `§CARD-<enc>`，经阅读面的信封（`reading.api.envelope`）：定位行、`u=<ui_sha8>`（有已校验链接时另有 `m=`）、范围卡（含/不含/读法）、哨兵。结构：身份行、用途、属性行、`核对点 checkpoints=N`、`判定戳 sha=<8 位>`（该屏核对点的哈希，判定切片抄它，见第 14 节），然后是表：

| 表 | 列 |
|---|---|
| 控件（n） | `# id kind label parent in bind api emph goto 属性`；`#` 是圆标数字（= SVG 的 `data-n`），所有控件都有，含只在别的状态显示的；`属性` 稀疏折叠 `req= case= rule= data=` |
| 顺序（n） | 容器（`<屏>.root` 或控件 id）、子控件 local 的有序列表；每个有两个以上子控件的容器一行，根下有两个以上控件时含根 |
| 状态（n） | 状态（`<屏>#<名>`）、`kind`（作者写的变体的种类，`default` 为空）、说明、界面文案、文件、显示控件数、未显示 |
| 出向导航、入向导航 | 起点、终点、文字、条件、触发、链接 |
| 渲染器推导，非作者声明 | 推导物、控件、文字、状态 |
| 全部可见文字 | 每个不同的字符串一行，列出出现的状态与来源（状态条与状态说明也在） |
| 示例数据 | 每个 `table` 的列、行数、画出的行数 |

规则：每张表的标题是 `## 名称（n）`，被页边界切开的表写 `（n / m，第 a-b 行）`；一行超过 480 字符时，最长的单元换成 `见注 [k]`，全文折行放在表下；卡超过 9,000 B 时在表的行接缝续卡（`<enc>.card-2.md`，重复身份行与表头，范围卡写续）。卡里作者文字经 `textfit.card_text`：反引号、星号、竖线、`](` 里的方括号换成外形相近的字符（SVG 保留原文），`{lines}` 与 `{sha8}` 在后面加一个空格（页框的占位符不得出现在页体里）。SVG 与卡的字符串比较用 `cmp_form`（同样的映射，再去掉所有空白）。

## 5. 索引与导航

`INDEX.md`：界面表（id、标题、route、frame、start、状态数、控件数、核对点、出/入导航、SVG 字节、画面卡链接）、需求覆盖、PNG 的指针（实时状态不写进静态文件）。多于 30 屏按 `group` 分片，没有 `group` 又超过 6,000 B 时对半切；每片链接全部分片。

`NAV.md`：`flowchart LR`（`derive/mermaid.py` 的 `build_component`，节点 id 是生成的 `n1`、`n2`，别名对照表给出屏幕 id）、导航表、不可达屏幕（从 `start` 屏幕出发）、没有出向导航的屏幕。导航表第一列是导航 id；只有当某行的 id 是别名或哈希时才多出 `起点`、`终点` 两列。多于 40 屏分片；分片的图只画两端都在本片里的行，每一行都在某一片的表里，跨片的行标出终点所在的页。

## 6. 画廊页

`site/screens/<enc>.html`：卡的表（同一份卡模型渲染成 HTML 表）加每个状态的内联 SVG（`<figure id="st-<状态>">`）。有跳转的控件的分组前后各插一行 `<a href>` 与 `</a>`（只链该控件的第一个跳转；目标是 `<enc>.html`，目标状态是 `#st-<状态>`）；删掉这些插入行得到 `.svg` 文件逐字节本身。无脚本、无外链、无 `data:`。内联后超过 40,000 B 时只内联默认状态，其余各写一行指向 `.svg` 的链接并写 `未内联: ...`。首行与哨兵用逻辑计数（块数、表数）代替行数，哨兵的 `sha8` 是第一张卡的 `sha8`。

## 7. 构建信息

`screens/build-info.json`：规范 JSON（键排序、LF、无时间无路径）：`schema`（`gemarc-atlas-ui-build/1`）、`uiSha8`、`modelSha8`（无已校验链接时为 `null`）、`layoutVersion`、`states`、`screens`、`widgets`、`checkpoints`、`status`（`ok`/`invalid`）、`degraded`（降级的规则 id，排序去重）。

## 8. 降级（L 类）

构建绝不因作者数据大而失败。每一步是一条 finding，有规则 id，进 `degraded`，需要人决定的步骤另有一行英文 `repair` 指引，点名屏幕（配置知道切片名时也点名切片）：

| 触发 | 降级 | 规则 | repair |
|---|---|---|---|
| 某状态的 SVG >20,000 B | 示例行 5→3→0 | SV-204 | 降到 0 行仍超过：`split screen X: n widgets in one screen make a N-byte SVG ...` |
| 画廊 >40,000 B | 只内联默认状态 | SV-213 | `split screen X: n controls x m states exceed the gallery size ...` |
| 卡 >9,000 B | 续卡 | RS-101 | `split screen X: its card needs n pages ...` |
| 索引或导航 >6,000 B 或屏幕过多 | 分片 | RS-101（INFO） | - |
| 文字超出盒子、点击目标被抬高 | 截断、抬高 | UX-102、UX-103（WARN） | - |

## 9. 检查 `SV-201` 至 `SV-216`

检查读文件的字节（SVG 用 XML 解析器，卡当文本），不读内存里的布局对象；类别见设计 6.0。I 类 ERROR 只可能是生成器的缺陷，使 `status` 为 `invalid`；L 类只 WARN；T 类（`include_t=True`）不进构建。`check.check_files(files, model, five, cfg, include_t=False, regenerate=True)` 返回全部 finding；`regenerate=True` 时再整树重建一次，与文件逐字节比较，没有别的规则能解释的差异按文件族报告（SVG：SV-211；卡与索引：SV-207；导航：SV-214；画廊：SV-213；build-info：SV-212）。一个坏掉的 SVG 文件只由它自己的规则报告一次，画廊里的副本不再重复报告。

| 规则 | 类 | 检查什么 |
|---|---|---|
| SV-201 | I | 根属性、viewBox = 画布 + 状态条、`role`、标题与 `desc`（含与不含、显示计数、几何声明）、数据属性与 `data-model-sha8` 的条件、没有 `aria-label` 以外的 `aria-*` |
| SV-202 | I | id 唯一且形如 `<屏>.<local>`；`data-box` = 首个子 rect；盒在父盒内 |
| SV-203 | I | 无图片、`foreignObject`、脚本、外链；可见文字都在 `<text>`；`<path>` 不超过文件的 25% |
| SV-204 | L | SVG ≤20,000 B，示例行 ≤5 |
| SV-205 | I / T | 兄弟盒不重叠（I）；圆标不压别的控件的盒（T） |
| SV-206 | I | `data-goto`、`data-nav` 在模型里；有 5D 时 `data-api/req/case/rule` 在 5D 里 |
| SV-207 | I | 卡的文字集合 ⊇ SVG 的 `<text>`；控件表 = SVG 分组的并集且 `#` = `data-n`；`checkpoints=N` 按定义，且每个核对点的值（路由、控件各列、顺序、状态种类）都能在卡里读到；卡与索引的哨兵 |
| SV-208 | I（只在渲染时跑） | PNG 的尺寸、`svgSha8`、像素探针；由 `png.py` 负责（第 13 节），构建时不跑 |
| SV-209 | I | 字号 ≥12；可点击盒 ≥32x32（phone 44x44）且互不重叠 |
| SV-210 | I | 含 CJK 的文字，字体栈含 CJK 字体 |
| SV-211 | I | 从 SVG 读回的 (id, 文字, 种类, 父, bind, goto) = 模型该状态的可见控件；每个状态恰有一个 SVG |
| SV-212 | I | 页首的 `m=` 只在有已校验链接时出现且等于配置；build-info 与重建一致 |
| SV-213 | I / L | 画廊内联的 SVG = `.svg`；每个 href 有目标；无脚本；≤40,000 B（L） |
| SV-214 | I | 导航图的节点数 = 屏幕数、边数 + 跨片的行 = 导航数；别名表覆盖图里的别名；导航页的哨兵 |
| SV-215 | I | 状态条第二行 = 变体说明的前 60 字符 |
| SV-216 | I | `data-n` 在所有状态里恒等于先序序号 |

## 10. 接口

```python
from gemarc_mcp.atlas.protoui import build, check
cfg = build.ScreensConfig(lang=None, project=None, model_sha8=None, html=True, slices={})   # 也接受映射、有同名属性的对象或 None
result = build.build_screens(ui_model, five_index, cfg)       # 纯函数；five_index 是 expand.FiveIndex 或 None
```

`ScreensResult`：`files`（路径 → 字节，路径相对 `dist/`）、`records`（每个文件一条：`path kind cls ai twin ...`；INDEX 与 NAV 的 `twinBy` 是 `reading`，build-info 的 `manifest` 是 `false`）、`ids`（`ids.tsv` 的行：`id kind role path line hdr name`，屏幕、控件、状态、导航）、`findings`、`degraded`、`repairs`、`info`、`status`、`twins`（卡的每个分页 → 画廊）、`assets`、`sample_rows`。没有屏幕的模型返回空 `files`。

## 11. 测试

`tests/atlas/test_screens_layout.py`（宽度模型、固定盒子、不变量、40 个随机模型）、`test_screens_svg.py`（27 个变异体，每个 I 类规则恰好它自己红；40 次随机的单字符破坏都被发现且检查器不抛异常；50 个随机模型往返；三个 `PYTHONHASHSEED`）、`test_screens_build.py`（卡、索引、导航、画廊、降级、纯度、句子、黄金）、`test_gate_screens.py`（真浏览器；需要 `GEMARC_SCREENS_EDGE=1` 与 edge 锁）、`test_screens_png.py` 与 `test_png_render.py`（PNG 读取、探针、渲染流程，不启动浏览器）、`test_gate_png.py`（真浏览器；需要 `GEMARC_PNG_EDGE=1` 与 edge 锁）。`test_ui_verdict.py`（判定规则的变异体、判定戳、存储路径、证据读取）与 `test_screens_scan.py`（探测、FOUND 率、十个种子缺陷、输出卫生、命令；合成前端在 `frontend_synth.py`）。黄金在 `tests/atlas/ui_fixtures/screens/`；改动之后用 `python -m tests.atlas.test_screens_build --write-golden` 重写。

## 12. 与 A3 设计的差异

1. 状态条高不是固定的 28：一行时 28，每多一行加 16，因为 phone 的 390 px 放不下一行；SV-201 以状态条分组的盒高为准。
2. `state` 也是保留的 `local`（UX-010），因为状态条分组的 id 是 `<屏>.state`。
3. 设计 D-13 提到的 18 px 容器标题带不做：4.4 第 4 条已定容器的 `label` 只作无障碍名。
4. 导航表以导航 id 为第一列；INDEX 没有导航表，所以阅读面 RS-116 里"nav → INDEX 的导航表行"应读作 NAV 页。
5. 画廊的首行与哨兵用逻辑计数；孪生渲染器与 RS-110（孪生里 `<style>` 总量 ≤2,000 B）要把画廊页当例外：画廊每个内联的 SVG 带自己的 `<style>`（SV-213 要求内联逐字相同）。
6. SV-207 同时管卡与索引页，SV-214 管导航页；回退到重建比较时按文件族报告。
7. `SV-208` 与 `UX-101` 至 `UX-104` 没有在这里注册：前者属于 PNG 一步，后者的内容由布局的 `UX-102`、`UX-103` finding 提供（L 类 WARN）。
8. 文字最长 400 个字符，嵌套最多画 40 层；设计里没有这两个上限。
9. PNG（第 13 节）与设计的差异：长边超过 1,600 px 时缩放，但缩放系数不低于 0.5（再小的图读不了，探针的边距也不再安全）；每张图的探针是状态条 2 个点、页面背景至多 4 个点、控件盒至多 8 个点，控件盒少于 4 个时不补（状态条与背景已经保证两种颜色，任何单色页都过不了）；页面背景探针不是必须的（整页被控件盒盖满时只有状态条与控件盒）；启动失败以外的失败重试一次，超时不重试；`mode="all"` 已实现（每个状态一张图）；`skipped:link`（目录是链接或联接点）与 `skipped:budget` 是设计里 `skipped:*` 的具体取值。
10. 画面卡多一行 `判定戳 sha=…`（`model.screen_sha8`，见第 14 节）；设计没有写这一行，但判定要引用的戳必须出现在 AI 读的卡上。黄金与清单随之重写。

## 13. PNG（`protoui/png.py`、`protoui/pngread.py`，检查 `SV-208`）

PNG 是 SVG 与画面卡旁边的缓存，给能看图的读者用；它从不进入 `done` 的条件。`png.py` 自己不启动任何进程（源码里没有 `subprocess`，有测试），浏览器由调用方注入的 `launcher` 启动；`pngread.py` 是只用标准库的 PNG 读取器（8 位、非隔行、颜色类型 0/2/3/4/6，按行惰性解滤波）。

```python
from gemarc_mcp.atlas.protoui import png
cfg = png.PngConfig(mode="default", browser=<浏览器路径或 None>, jobs=4, timeout=20.0, budget_seconds=30.0, min_launch_seconds=3.0,
                    retries=1, max_edge=1600, scratch_root=None, refresh=False, label="", clock=time.monotonic)
result = png.render(dist, cfg, launcher)      # PngResult：status, entries, findings, message, seconds, index, leftovers；summary() 是回复里的 screens 对象
png.probe(png_bytes, probes) -> bool           # 像素探针本身；png.probes_for(svg, scale) 给出一张 SVG 的探针
png.read_index(dist) / png.state_of(index, "screens/<文件>.svg", svg_bytes, dist=None)    # 读取侧：ok | stale | absent | FAIL | skipped:*
png.check_files(dist)                          # 没有浏览器时复查已有的图（SV-208），返回 Finding 列表
png.svg_key(svg_bytes)                         # 缓存键与 png.json 的 svgSha8
```

**launcher** `launcher(args, timeout, scratch)`：`args` 是完整命令行，`args[0]` 是浏览器；launcher 自己加 `--disable-gpu` 与 `--user-data-dir=<scratch>/profile`，把它创建的所有文件放在 `scratch`（一个已存在的、只属于这次启动的空目录，`png.py` 事后删除），超时杀整棵进程树，返回带 `returncode timed_out seconds error` 的对象（有 `overflow`、`read_error` 时也读）；`gate/smoke.py` 的 `_Launch` 就是这样的对象。没有 launcher 是 `skipped:no-launcher`，没有 `cfg.browser` 是 `skipped:no-browser`，都不是通过。

**命令行**：`msedge.exe --headless=new --hide-scrollbars --window-size=<W>,<H> --force-device-scale-factor=<s> --screenshot=<scratch>/shot.png <file:// URL>`；URL 是 `Path.resolve().as_uri()`；`s` 恒写（1 或缩放系数），所以图的尺寸不随显示器缩放变化。

**只渲默认状态**（`mode="all"` 渲全部）；**缓存键** = 去掉根标签里 `data-model-sha8` 的 SVG 全文的 sha8（5D 模型变了不重画）。命中缓存要求 `png.json` 里该文件是 `ok`、键相同、图文件在、并且图再过一遍尺寸与探针。

**验证（SV-208）**：不信退出码、不信文件大小、不信非背景像素占比（浏览器的"找不到文件"页是满尺寸的图，退出码 0，背景 `#f6f6f6` 与页面色 `#f6f7f9` 差不超过容差）。接受一张图当且仅当：尺寸等于 SVG 的（缩放后）尺寸；每个探针命中，容差每通道 ±3。探针由 SVG 自己的几何与样式给出：状态条 2 个点（`(3, 条高-4)` 与 `(W-4, 条高-4)`，期望色是条的填充）、页面背景至多 4 个点（四角、边中点……，取任何控件都盖不到的点，期望色是页面的填充）、控件盒至多 8 个点（盒左上角向内缩进，叶子优先；只取"只有一个 rect、没有 path、不是推导物、填充是十六进制"且之后画的东西盖不到的盒）。点集必须含两种"任何单色页都无法同时匹配"的颜色（通道差大于 6），否则不出点集（`skipped:probe`）。

**状态**（每个条目与整次运行）：`ok`；`FAIL`（启动失败、没写出图、尺寸或探针不对；原因是固定的文字，不写时长，不写目录）；`skipped:no-launcher`、`skipped:no-browser`、`skipped:budget`（挂钟预算用完）、`skipped:probe`（做不出可靠点集，或 PNG 是读取器不解码的种类）、`skipped:link`（`dist`、`screens`、`.verify`、`.verify/screens` 或临时根是链接/联接点，不跟随，什么都不写）；整次运行是 `FAIL` 当任一条目失败，否则第一个跳过，否则 `ok`；没有屏幕是 `absent`；`mode="none"` 是 `skipped:none`。不合格的图从不保留：失败或跳过的条目没有图文件，旧图被删。

**`png.json`**（`dist/.verify/screens/png.json`，原子写）：`schema`（`gemarc-atlas-png/1`）、`status`、`mode`、`counts`、`renderer`（有 `label` 才有）、`entries`（按 SVG 路径排序：`svg screen variant svgSha8 uiSha8 status`，`ok` 时加 `file width height scale probes`，否则加 `reason`）。没有耗时、没有路径、没有机器名，同样的 SVG 得到同样的字节。

**写入范围与临时目录**：只写 `dist/.verify/screens/`；每次启动的私有目录在 `dist/.verify/work/atlas-png-*/job-NNNN/`（`cfg.scratch_root` 可改），在工作线程里随用随删（Windows 会短暂占着浏览器的配置目录，删除带重试），删不掉的名字进 `PngResult.leftovers`；下一次运行只清理自己前缀且超过 10 分钟的旧目录。

**并行与预算**：`jobs` 个启动同时进行（默认 4，各有自己的目录）；每个启动的超时是 `min(cfg.timeout, 剩余预算)`，剩余不足 `min_launch_seconds` 的图记 `skipped:budget`；超时的启动不重试，其他失败重试 `retries` 次（重试前删掉上一次留下的图）。条目按文件名排序，不依赖完成顺序。

**实测**（Edge 152.0.4191.53，CPython 3.12.10，本机 16 个逻辑核；十二屏样例的默认状态，1280x828 为主）：单次启动 1.02 s（串行，中位）；四路并行时每次启动的中位 2.14 s（p90 2.23 s），十二张的墙钟中位 7.35 s（5 次：6.98 至 7.48 s），即每张 0.61 s；串行十二张的墙钟中位 14.9 s。探针 7 至 14 个点，读取与比对每张约 5 至 40 ms（Paeth 行多的图到 160 ms）。数字会随机器负载变化，不是阈值。

## 14. 前端核对：手工流程、判定与扫描（`protoui/verdict.py`、`protoui/scan.py`，规则 `UV-001` 至 `UV-008`）

画面卡与 SVG 是给 AI 对着前端核对用的真源。本节是核对这一步的合同：AI 怎样读卡并逐点比对，把结论写成 `ui-verdict-*` 切片，哪些由机器检查，`screens scan` 能帮到哪里、帮不到哪里。设计依据是 A3 设计的 4.8 与 6.3。

**范围（说准）**：核对**内容层、结构层（父子与同级顺序）与跳转**：文字、字段名、种类、路由、跳转目标、状态有无、容器归属与顺序。**不核对**布局、间距、颜色、响应式（几何是示意，不是规格）。结论只对静态字面量前端（HTML 与脚本里直接写着文字和路由）成立：文字是翻译键、路由在配置里、组件在运行时拼出来的前端，`scan` 会给出大量 `ABSENT`，这种情形没有测过。

### 14.1 手工流程（读卡的 AI 照此做）

1. **确认原型是当前的**：`gemarc_atlas_status` 里 `build` 门禁绿；过期就先 `gemarc_atlas_build`。卡上有 `u=<ui_sha8>`，PNG 状态条里有 `ui <sha8>`，二者要相等，否则 PNG 是旧的，只看卡。
2. **定顺序**：读 `screens/INDEX.md`（界面表、`start` 屏、核对点数），再读 `screens/NAV.md`（从 `start` 屏出发的导航，别名对照表）。一次核对一屏。
3. **取卡**：`gemarc_atlas_doc(unit="screen:SCR-LOGIN")` 或直接读 `screens/SCR-LOGIN.card.md`。卡里有三样东西要用：`核对点 checkpoints=N`（要核对的总数）、`判定戳 sha=<8 位>`（写判定时抄它）、控件表（每个非空单元格一个核对点，id = `<控件 id>@<列名>`）。能看图的读者再看 `.verify/screens/SCR-LOGIN.png`（只在 `png.json` 里是 `ok` 时有效）。PNG 与 SVG 的几何是示意，不要把居中的 640 px 表单当成设计要求。
4. **在前端里找实现**，先按 `route` 找到那一屏的文件，再逐核对点问：

   | 列 | 问什么 |
   |---|---|
   | `route` | 前端有这个路由吗？还有没有原型里没有的路由（`extra`）？ |
   | `kind` | 元素种类对吗？种类到 DOM 的对照见 `ui-schema.md` 第 5 节（线索级：按钮是 `<button>`，链接是 `<a href>`……） |
   | `label` | 文字逐字相同吗？（卡里的 `ˋ` `∗` `¦` 是反引号、星号、竖线的替身，前端里是原字符） |
   | `parent`、`order` | 控件在前端里也在这个容器里吗？同级顺序也一样吗？ |
   | `bind` | 字段名是同一个吗？ |
   | `emph`、`in` | 强调与"只在哪些状态显示"有对应的实现吗？ |
   | `api`、`goto` | 点击后调的接口与到达的路由是原型写的吗？ |
   | `#<状态>@state` | 前端有这个状态的显示分支吗？ |

   `rg` 与 `Read` 是主要工具；`python -m gemarc_mcp.atlas.protoui.scan` 是辅助（14.4）：它只回答"这个字符串在不在源码里"，**不回答"对不对"**。
5. **记判定**：只记**差异**。看过且相同的核对点不写；没有看的核对点**逐个**写成 `unverifiable` 并说明原因（强调色在外部样式表里、文字来自翻译表……）。`checked` 是看过的核对点数，`checked` 加上 `unverifiable` 行数必须等于卡头的 N。同一屏的 `conform` 行与它的 `diffs` 行写在同一个切片里（或先写 `diffs` 再写 `conform`）。`gemarc_atlas_put(slice="ui-verdict-login", text=…)`，红了按提示改，用 `mode=patch`。
6. **看覆盖**：`gemarc_atlas_check mode=full` 的 `UV-008` 一行给出已判定的屏数与看过的核对点数；`conforms+u3` 表示有三个核对点没能核对，不要和"全部核对"混为一谈。
7. **方向**：默认是"前端对照原型"。原型本身是对既有前端的还原（as-is）时，差异多半说明原型写错了：改 `ui-*` 切片（`sha` 随之变，旧判定自动过期），不要改前端。

**判定是 AI 的证词**。机器保证的是"两边的引文都是真的"（`basis` 与 `found` 在真实文件里、`expected` 就是原型的值）、"没看的核对点被如实标成 `unverifiable`"、"不拿过期判定充数"；它不能保证"没看就说相同"完全不可能。所以 `conforms` 的价值取决于读者，评审时抽查 `basis` 的行（T-S3 的种子篡改回归测的就是这一条）。

### 14.2 判定切片与判定戳

切片名 `ui-verdict-<名>`，首行 `#atlas-ui 1`，只有两个段（别的段是 ST-004，反过来原型切片也不收这两段）：

```
#atlas-ui 1
## conform: screen | verdict | basis | opts      opts: sha checked repo
## diffs:   ref | kind | note | opts              opts: expected found basis
```

| 字段 | 取值与含义 |
|---|---|
| `conform.verdict` | `conforms`、`differs`、`unchecked`（没看）；`unchecked` 不要求 `sha`、`checked` 与 `basis` 的文法 |
| `conform.basis` | `<别名>:<路径>:<行号>`，别名是 `GEMARC_ATLAS_ROOTS` 里的名字，路径相对该根；`conforms` 与 `differs` 必须写 |
| `conform.sha` | 卡上的 `判定戳`：该屏**核对点**的哈希（`model.screen_sha8`）。只有该屏的某个核对点变了它才变；标题、用途、`req`、`data`、尺寸不动它，所以改一屏不会让别的屏的判定过期。设计里写作 `screen=<screen_sha8>`，与位置列 `screen` 重名，所以选项叫 `sha` |
| `conform.checked` | 看过的核对点数（`conforms`、`differs` 必写） |
| `conform.repo` | 前端根的别名；与最近一次扫描的根不同时，扫描不参与 `UV-006` |
| `diffs.ref` | 核对点 id；`extra` 行的 `ref` 是屏幕 id 或 `<屏>@route` |
| `diffs.kind` | `differs`（值不同）、`missing`（前端没有）、`extra`（前端多出）、`unverifiable`（没能核对） |
| `diffs.expected` | 原型里该核对点的值（卡里的写法也接受） |
| `diffs.found` | 前端里的值；必须与 `basis` 一起写 |
| `diffs.basis` | `<别名>:<路径>:<行号>`，`found` 必须出现在该行前后 3 行内 |

判定行**不进** `ui_sha8`（它们有自己的哈希 `verdict_sha8`，规范字节是 `{"schema":"gemarc-atlas-ui-verdict/1","conform":[…],"diffs":[…]}`），所以写一份判定不改变 `ui_sha8`，不使 `screens/` 过期，也不碰 5D 的任何文件（测试断言）。判定切片的 `put` 预算是 `2 + ceil(屏数/2)`，计数在 `atlas/.atlas/ui/puts.json` 的 `verdictPuts`。

### 14.3 规则 `UV-001` 至 `UV-008`

规则有自己的表（`verdict.RULES`），不进 `rules.REGISTRY`（那里钉着原型切片的规则）。`put` 判定切片时对新写的行跑，`check` 对全部判定跑（`store.put_slice`、`store.check_store`）；证据根默认取服务器环境 `GEMARC_ATLAS_ROOTS`，调用方可以传 `verdict_roots`。

| id | 波 | 级别 | 检查什么 |
|---|---|---|---|
| `UV-001` | 1 | ERROR | `diffs.ref` 是当前原型的核对点（`extra` 行：屏幕 id 或 `<屏>@route`）；`conform.screen` 是原型里的屏幕 |
| `UV-002` | 1 | ERROR | 同一 `ref` 在 `diffs` 里只出现一次；同一屏幕只有一条 `conform` |
| `UV-003` | 1 | ERROR | `diffs.expected` 等于原型里该核对点的值（抓 AI 误引原型；卡的写法也接受） |
| `UV-004` | 3 | ERROR / INFO | `basis` 在证据根里解析，`found` 在该行前后 3 行内（抓 AI 误引前端）。敏感文件（`.env*`、密钥库、`secrets.*`）拒读且不回显；路径越出根、不是相对路径、文件不存在、行号超出、不是文本都是 ERROR。别名没有根：每个别名**一行 INFO** `SKIP`，**不算通过** |
| `UV-005` | 1 | ERROR | `checked` 加 `unverifiable` 行数（`ref` 是该屏核对点的）等于该屏核对点数；`conforms`、`differs` 写了 `checked` 与可解析的 `basis`；`conforms` 没有 `differs`、`missing`、`extra` 行，`differs` 至少有一行 |
| `UV-006` | 3 | ERROR | `conforms` 与最近一次扫描里 `ABSENT` 的字面量核对点矛盾：该核对点没有 `unverifiable` 行（写了 `differs` 等行的是 `UV-005`）。扫描的 `screenSha8` 不是当前戳，或 `repo` 不同，或没有扫描文件时不适用 |
| `UV-007` | 1 / 3 | ERROR / WARN | `sha` 是该屏当前的戳：**新写**的 `conforms` 不是当前值（含没写）是 ERROR；已存的或非 `conforms` 的旧判定是 WARN；过期的判定显示为 `conforms(stale)`，不计入已判定的屏数 |
| `UV-008` | 3 | INFO | 只在 `check` 全量时：已判定屏数、`conforms`/`differs`/过期/`unchecked`/无判定各多少、看过的核对点数 |

与设计 6.3 的差异（实现里补的，设计没有写）：
1. `UV-005` 还要求 `conforms` 不带差异行、`differs` 至少带一行：否则"conforms 加一堆 differs 行"与"differs 却没有差异"都能过；`unchecked` 不受 `checked` 的等式约束。
2. `UV-004` 要求写了 `found` 就必须有 `basis`：否则去掉 `basis` 就绕过了引文检查。
3. `UV-002` 同时管 `conform` 的重复行。
4. `UV-007` 对非 `conforms` 的新行只给 WARN，对 `unchecked` 不要求 `sha`。
5. `UV-004` 的回显：只有 `context_echo_allowed` 的文件类型才在提示里写"第 N 行是：…"，经 `echo_source_line`，看起来像凭据的行写成 `[withheld: line looks sensitive]`；`found` 不在窗口里时提示只写**行号**（最近的出现处），不写文字。
6. 判定的 `sha` 选项名（见 14.2）；卡新增一行 `判定戳 sha=…`（见第 4 节的表之上的说明行），屏幕卡、画廊与黄金随之变了。

### 14.4 `screens scan`

`python -m gemarc_mcp.atlas.protoui.scan <project> --repo <别名> [--glob G] [--screen S ...] [--all] [--dry-run] [--root 工作区]`（集成方把同一个函数登记成 `python -m gemarc_mcp.atlas screens <project> scan`；设计里的 `--root DIR` 与全局的 `--root`（工作区）重名，所以前端根的选项叫 `--repo`，而且只收**别名**，路径被拒绝）。写入需要 `GEMARC_ALLOW_WRITE=1`；`--dry-run` 只打印。

对每个核对点里带字面量的列做探测，结果只有 `FOUND <别名>:<路径>:<行号>` 与 `ABSENT near:<源码里最接近的字符串>`：

| 列 | 探测 |
|---|---|
| `route` | 路由作为一个词出现在源码里：不在更长的路径里（`/login/sso`、`/api/login` 不算），`{id}` 也接受 `:id`、`${…}`、`[id]` |
| `label` | 文字作为**一个完整的字符串**出现（标签之间、属性值、引号里）；只是更长字符串的一部分不算，`near` 给出那个更长的字符串；`<title>` 与注释里的不算；`&` 等写成实体的也认 |
| `bind` | 字段名作为属性值（`name=`、`id=`、`v-model`……）、引号字符串或单词出现；`type=`、`class=`、`for=`、`placeholder=` 的值不算 |
| `goto` | 目标屏幕的路由出现在**找到该控件的位置前后 5 行内**（不是整个文件）；控件没找到则没有可查的位置 |

另有 `data-atlas-id="<控件 id>"`（推荐约定，生成前端时写在实现该控件的元素上）：找到的、缺的、前端多出的（属于这一屏而原型没有的 id）分别列出；`kind` 只给线索：控件旁边的标签与 `ui-schema.md` 的 DOM 对照不符时写 `kind? SCR-LOGIN.submit@kind button is <a>`，**从不写"相同"或"一致"**（输入类控件按字段名所在的标签判断，不按旁边的 `<label>`）。

`parent`、`order`、`emph`、`in`、`state`、`api` 没有字面量，`scan` 不碰，AI 读代码判断。输出全文只是线索。

**文件**：每屏写 `atlas/.atlas/ui/scan-<enc(屏)>.json`（只有这条命令写它，`store.write_scan`，原子、在界面锁内、受围栏约束），内容是规范 JSON：`schema`（`gemarc-atlas-ui-scan/1`）、`screen`、`screenSha8`、`uiSha8`、`repo`、`glob`、`files`、`skipped`、`counts`、`literals`（核对点 id → `state` `at` `how` `near` `why`）、`hints`、`ids`。没有时间、机器路径、绝对路径；同一棵树同样的字节（三个 `PYTHONHASHSEED` 下相同）。`UV-006` 读它（`store.read_scans`）。

**读取与脱敏**：只经 `evidence.read_text_file` 读；`.env*`、密钥库、`secrets.*`、`credentials*` 不打开（计入 `skipped.sensitive`）；回显源码行只经 `echo_source_line`；`near` 来自看起来像凭据的行时写 `[withheld]`；不跟随链接与联接点；跳过 `node_modules`、`.git`、`dist`、`build`、点开头的目录；单个文件 ≤1 MiB，最多 3,000 个文件。默认 `--glob` 是 `**/*.{html,htm,jsx,tsx,vue,svelte,js,ts}`。

### 14.5 实测

数字来自 `tests/atlas/test_ui_verdict.py`、`test_screens_scan.py` 与它们用的合成前端 `tests/atlas/frontend_synth.py`（六屏、42 个控件、164 个核对点，其中 72 个带字面量；前端由程序生成，字面量都写在常规的位置，所以 FOUND 率是这种前端的上限，翻译键之类的前端没有测）。

- 干净前端上 FOUND：72 / 72（route 6/6、label 42/42、bind 12/12、goto 12/12），带 `data-atlas-id` 的树也是 72 / 72；没有 `kind?` 线索，也没有缺的或多出的 id。
- T-S3 的十个种子缺陷，只用 `scan`（探测规则在跑数之前固定：该缺陷的核对点在干净树与缺陷树之间由 FOUND 变 ABSENT，或出现新的 `kind?`，或（带 id 的树）出现缺的或多出的 id）：
  - 普通前端：**7 / 10** 有变化，其中 **6 / 10** 是缺陷自己的字面量（缺字段 2、错文字 2、错路由 1、错跳转 1），缺状态 1 个只是因为随它一起删掉的文字（`SCR-LOGIN.err@label`）变成了 ABSENT；没看到：多出控件、放错容器、顺序错。
  - 带 `data-atlas-id` 的前端：**8 / 10**，多出一个"多出控件"（多出的 id）；放错容器与顺序错两种仍然看不到。
  - 设计预计"至多 6 / 10"：按缺陷自己的字面量数正好 6；7 是因为缺状态连带删掉了文字。已知的漏报：新文字错了、旧文字却还作为完整字符串出现在页面别处（测试里有这个反例）。
- 判定规则：`UV-001` 至 `UV-008` 各有变异体（`test_ui_verdict.py`，干净基线 0 条，每个变异体恰好触发所写的规则）。
- 速度：六屏、8 个文件的扫描中位 6 ms；308 个文件 154 ms。
