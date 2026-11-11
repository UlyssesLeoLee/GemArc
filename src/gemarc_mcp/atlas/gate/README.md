# gate:无头浏览器门禁(M0 gate spike + 验证后的加固)

本目录是 Atlas 渲染产物(单文件 HTML)的浏览器门禁。M0 阶段只在合成桩页面上证明这套技术是确定的;真正的 atlas 页面在 M4/M6 才接入。设计依据:`DESIGN.md` 第 9.3 节、第 10 节 M0、R14。

先说结论的边界:门禁能挡住「疏忽和偷懒」(陈旧或写死的结果、空操作的渲染、空白画布、页面自报的数字),挡不住「刻意欺骗的页面」。详见下面的「信任边界」。

## 它怎么工作

```
smoke.py ──启动──> msedge/chrome --headless=new ... --dump-dom file:///.../page.html?gate=1&gn=<每次启动的随机 nonce>
                         │
                         │ 页面内:selftest.js(setTimeout 驱动,不用 rAF;<head> 最前面内联,先于页面代码就位)
                         │   就位时:包住 WebGL/2D 的绘制调用和 getContext(计数、登记画布),挂上错误捕获
                         │   每个 tab:设置 hash -> 睡 50 ms -> 同步 renderOnce() 两次 -> 自己读每个可见 canvas -> 量取
                         │   最后写 <pre id="atlas-gate" data-done="1">{json}</pre>,删除所有 <script>
                         │   之后捕获继续有效:晚到的错误镜像到 pre[data-late-errors],直到转储
                         ▼
        stdout = 转储后的 DOM      stderr = 浏览器日志(CONSOLE 行)
                         │
smoke.py 解析 pre 元素(html.parser,严格 UTF-8)-> 核对 nonce -> 逐项判定 -> PASS / FAIL / SKIP
```

要点:

- 页面只负责量取原始数字(`selftest.js` 里没有任何 PASS/FAIL 判断),所有阈值和判定都在 `smoke.py`。
- 画布的判定只用门禁自己读到的东西:像素、`gl.getError()`、`UNMASKED_RENDERER_WEBGL`、`renderOnce` 期间页面发出的绘制调用数。页面从 `renderOnce()` 返回的 `nonBgRatio` / `renderer` 只是原样记入结果(`render` 字段),任何判定都不使用。
- 不用 CDP、不用 Node、不用第三方包,只有标准库 `subprocess`。
- 为什么是 `setTimeout` 而不是 `requestAnimationFrame`:在 `--virtual-time-budget` 下 rAF 被饿死(实测 `raf 1 @23 ms`,`raf 2 @8024 ms`),计时器照常触发。

## 信任边界

页面自己控制自己的 DOM 和 JavaScript,所以**页面可以伪造一切**:读 `location.search` 取出 nonce、自己造一个通过的结果、改写 `console.error`、替换我们包住的绘制方法。门禁没有、也不声称有对抗这种页面的能力。它防的是下面这些现实中会发生的事故(每一项都有真实浏览器下的夹具和测试):

| 事故 | 挡住它的东西 | 夹具 |
|---|---|---|
| 页面里写死一个通过的 `<pre id="atlas-gate">`,从没渲染过 | 每次启动的随机 nonce(回显在结果里,不符 = `BAD_RESULT`);重试每次启动换新 nonce,所以旧 DOM 不会被接受 | `fake-static` |
| `renderOnce` 什么都没画,页面却自报 `nonBgRatio: 0.9`、`renderer: SwiftShader` | 门禁在同一个任务里自己读 canvas;绘制调用计数 | `canvas-gl-blank-lying` |
| 帧计数器 `frame()` 每被调用一次就加一(假心跳) | 连续读两次 `frame()` 之间什么都没渲染,必须相等 | `frame-counts-reads` |
| 画布空白 / 根本没有上下文 / 声明了的画布不可见 | 自动的 `NON_BG`(阈值在 `calibration.json`,不是旗标) | `canvas-2d-blank` `canvas-no-context` `canvas-declared-hidden` |

**挡不住的**(`tests/atlas/test_gate_smoke.py::KnownLimitTests` 把它们钉成测试,谁改进了门禁就更新这里):

- 读取自己 URL 里 nonce 并回显的页面(`test_a_page_that_reads_its_own_url_can_forge_the_whole_result`)。
- 没有 canvas、`renderOnce` 只是把计数器加一的页面(`counter-bump-only`):没有任何独立可读的东西,只能 PASS。
- 超出虚拟时间预算(默认 20 s)之后才发生的错误(`beyond-budget`)。

## 文件

| 文件 | 作用 |
|---|---|
| `smoke.py` | 运行器 + 判定 + CLI(`python -m gemarc_mcp.atlas.gate.smoke`) |
| `selftest.js` | 页内自检(真实页面会把它内联到 `<head>` 最前面,让错误捕获和绘制计数先于页面代码就位) |
| `calibration.json` | 按浏览器族分开的窗口宽度偏移、阈值(含画布空白下限、渲染器子串)、控制台错误模式、时限;`provisional` 列出尚未实测的数字 |
| `tests/atlas/gate_fixtures/*.html` | 合成桩页面(带 `<!--ATLAS_SELFTEST-->` 占位符,运行时由 `smoke.assemble_page` 内联) |
| `tests/atlas/gate_spike.py` | 把每个桩页面跑 N 次并打印矩阵,给出 GO / NO-GO |
| `tests/atlas/test_gate_smoke.py` | 无浏览器的单元测试 + 有浏览器的集成测试 + 已知局限测试(没有浏览器时大声 SKIP) |

## 页面契约(`selftest.js`)

URL 带 `?gate=1` 时才启用;否则 `window.AtlasGate.active === false`,`run()` 是空操作,页面可以无条件调用。

```js
AtlasGate.run({
  tabs: ["overview", "detail"],       // 逐个走访;默认用 location.hash = "#/" + tab 切换
  setTab: function (tab) { ... },     // 可选,覆盖默认的切换方式
  tabState: function () { return document.body.dataset.tab; }, // 可选。给了就断言相邻 tab 的状态确实变化
  renderOnce: function (tab) {...},   // 必需。同步渲染并真的绘制;返回值(可 JSON 化)原样进入结果,仅供参考
  frame: function () { return n; },   // 必需。帧计数器(纯读取,不得在被调用时自增),心跳用
  headerSelector: "[data-gate~=header]", // 可选。头部控件所在容器
  steps: [{ name, run }],             // 可选。脚本化步骤;返回 false 或字符串表示失败
  sleepMs: 50
});
```

页面标记约定:

- 头部容器 `data-gate="header"`:**每个 tab 都必须有至少一个可见控件**,否则 `HEADER_MISSING`。真的没有头部的页面显式关掉:`run_gate(..., require_header=False)` / CLI `--no-header`(关的是运行器这一侧,页面不能替自己豁免)。
- 必须完整可见的元素 `data-gate="fit"`。
- 场景 canvas 标 `data-gate="canvas"`:声明后在每个 tab 都必须可见;没声明的 canvas 只要可见同样会被检查,隐藏的不检查。
- `window.__ATLAS_ERRORS__`(错误边界收集的数组)会并入错误;`finish` 之后新增的条目也会被轮询到(间隔 500 ms,共 30 s 虚拟时间)。
- `AtlasGate.pixelStats(gl)` / `AtlasGate.glRenderer(gl)` 保留给页面自己打日志用,门禁不读它们的结果。

画布规则(自动,无需任何旗标):

- 每个可见(或声明)的 canvas,在第二次 `renderOnce()` 之后的**同一个任务里**由 `selftest.js` 自己读:WebGL 用 `readPixels`(先把读帧缓冲临时绑到默认帧缓冲再还原,页面遗留的离屏帧缓冲不会让读取失败或读错),2D 与其他用 `drawImage` 到临时 canvas 再 `getImageData`,所以不依赖 `preserveDrawingBuffer`。
- 背景 = 采样像素里出现最多的颜色(不是页面给的、也不是角落像素);`nonBgRatio` = 与它相差超过 8 的像素占比。必须大于 `thresholds.nonBgFloor`(`calibration.json`,默认 0.0005,**暂定值**)。
- `gl.getError()` 在 `readPixels` 前后各排空一次,页面留下的 WebGL 错误会报 `GL_ERROR`;上下文丢失同。
- WebGL 画布的渲染器必须含 `gl.rendererSubstring`(默认 `SwiftShader`),否则 `RENDERER`。
- 有上下文的可见画布,在那次 `renderOnce` 期间必须发生过绘制调用(WebGL 的 `drawArrays`/`drawElements`/实例化/范围/multi-draw 变体;2D 的 `fillRect`/`strokeRect`/`fill`/`stroke`/`fillText`/`strokeText`/`drawImage`/`putImageData`),否则 `NO_DRAW`。
- WebGL 扩展对象的绘制方法(`ANGLE_instanced_arrays`、`WEBGL_multi_draw`)在 Chromium 里不是全局构造函数,只存在于 `getExtension()` 交出的对象上,所以 `selftest.js` 在 `getExtension` 第一次交出该对象时才包住它们(实测:只靠 `ANGLE_instanced_arrays` 画图的 WebGL1 页面,不包住就会得到 `draws = 0` 的误报,夹具 `ok-gl1-instanced`)。
- 只对 `2d` / `webgl` / `webgl2` / `experimental-webgl` 计绘制调用。别的上下文种类(`webgpu`、`bitmaprenderer`)数不了,只按像素判,不会因 `NO_DRAW` 失败。

结果 JSON(`v: 2`)里每个 tab 的原始量取:`frames=[f0,f1,f2]`、`frameReads=[a,b]`、`header={roots,n,maxOverlapPx,pair,outside}`、`scrollWidth`、`clientWidth`、`fit`、`textBad`、`draws`、`canvases[]`、`state`(给了 `tabState` 时)、`render`(页面自报,仅供参考);顶层另有 `nonce`、`innerWidth`、`errors`、`steps`、`missingHooks`、`tabStateChecked`、`ua`。`v: 1`(旧自检,没有 nonce / clientWidth / 画布读取)会被判为 `BAD_RESULT`。

## 场景边界(`sceneBounds`,M6)

页面可以提供钩子 `sceneBounds(tab)`,返回 `{ frames: [{id, x, y, w, h}], nodes: [{id, x, y, r?}], occupancy? }`(`r` = 节点投影后的半径,缺省 0;占用率按节点盒(中心 ± r)计)(相对画布左上角的 CSS 像素,用画出这一帧的相机),没有场景的 tab 返回 `null`。`selftest.js` 在第二次 `renderOnce` 之后的同一个任务里调用它,并**自己**量第一个可见的 `canvas[data-gate~=canvas]` 的 CSS 尺寸,一起写入 `rec.scene` 与 `rec.sceneCanvas`(页面报告的坐标是页面的话,画布尺寸不是)。判定只在 `smoke.py`:

| 码 | 名称 | 触发 |
|---|---|---|
| 023 | FRAME_CLIPPED | 某个框越出画布超过 `thresholds.sceneClipPx`(2 px)、在相机背后(`w`/`h` 为 `null`)、或者量不到画布 |
| 024 | NODE_CLIPPED | 某个节点越出画布或在相机背后 |
| 025 | SCENE_SPARSE | 节点包围盒面积 / 框包围盒面积 < `thresholds.sceneOccupancyMin`(0.1),或有框没有节点;钩子写 `occupancy: false` 时跳过(画布内测试仍然执行) |

只量节点会放过被裁掉的视图,所以框计入。夹具在 `tests/atlas/gate_fixtures/scene/`(`clipped-scene` 失败 `FRAME_CLIPPED`,`sparse-scene` 失败 `SCENE_SPARSE`,各 20 次);标定数字与 2 倍余量的写法见 `calibration.json` 的 `provisional` 与 `docs/atlas/spec/bundle-contract.md` 第 11 节。没有钩子的页面完全不受影响。

## 判定与错误码

`GEMARC_GATE_NNN`,`findings` 为空才是 PASS。001-018 沿用,019-022 是新增,023-025 是场景边界(M6,见上一节)。

| 码 | 名称 | 触发 |
|---|---|---|
| 001 | NO_BROWSER | 找不到 Edge/Chrome,或 `GEMARC_ATLAS_BROWSER` / `--browser` 指定的路径不是文件:SKIP,不是 PASS,也不回退到别的浏览器 |
| 002 | PROCESS | 超时 / 崩溃 / 输出被截断 / 退出码非 0 且重试用尽;或浏览器根本启动不了(文件不存在、不是可执行文件,不重试) |
| 003 | NO_RESULT | DOM 完整,但没有 `#atlas-gate` |
| 004 | BAD_RESULT | 多个元素、没有 `data-done="1"`、JSON 损坏、不是 UTF-8、`v` 不符、`data-late-errors` 损坏、**nonce 不符**(此时结果里的其他内容一律不采信) |
| 005 | WIDTH | 页面报告的 `innerWidth` 不等于目标宽度(校准漂移) |
| 006 | PAGE_ERRORS | 页内 `onerror` / `unhandledrejection` / `console.error` / 资源加载失败 / 错误边界,**包括 walker 结束之后(直到转储)才发生的** |
| 007 | CONSOLE_STDERR | stderr 中来自页面源的错误类 `CONSOLE` 行 |
| 008 | HEADER_OVERLAP | 头部交互元素两两重叠深度 > 2 px |
| 009 | HEADER_OUTSIDE | 头部交互元素超出视口 |
| 010 | OVERFLOW_X | `scrollWidth > clientWidth`(`clientWidth` 不含经典竖向滚动条;旧的只和 `innerWidth` 比,会漏掉最多约 15 px,`width:100vw` 就是这个)。`html` / `body` 上的 `overflow-x:hidden` 藏不掉它(`scrollWidth` 仍含被裁掉的内容);要收起屏外装饰元素,用带 `overflow:hidden` 的包装容器或 `position:fixed`,它们不计入(实测,有测试) |
| 011 | FIT_CLIPPED | `[data-gate=fit]` 元素被裁剪 |
| 012 | TEXT_BAD | 可见文本、`document.title`、`title` / `aria-label` / `alt` / `placeholder` 属性含 `undefined` / `NaN` / `[object` |
| 013 | HEARTBEAT | 连续两次 `renderOnce()` 帧计数器没有前进(场景冻结);或两次相邻读取之间计数器就变了(`frame()` 数的是自己被调用的次数) |
| 014 | RENDER | `renderOnce` 抛出、缺少钩子 |
| 015 | STEP_FAILED | 脚本化步骤失败 |
| 016 | NO_TABS | 没有量取任何 tab(什么都没检查) |
| 017 | NON_BG | 可见或声明的 canvas:空白(`nonBgRatio` 不大于下限)、没有渲染上下文、读不回像素、声明了却不可见、`data-gate=canvas` 标在非 canvas 上 |
| 018 | RENDERER | WebGL 画布的渲染器不含 `gl.rendererSubstring`(默认 SwiftShader) |
| 019 | HEADER_MISSING | 某个 tab 里没有任何位于 `data-gate=header` 内的可见控件(没标记、或标了但是空) |
| 020 | GL_ERROR | `gl.getError()` 非空或上下文丢失 |
| 021 | NO_DRAW | 有上下文的可见画布,但 `renderOnce` 期间页面没发出任何绘制调用 |
| 022 | TAB_STATE | 页面提供了 `tabState` 钩子,而相邻两个 tab 的状态相同或读不出来(`setTab` 什么都没做) |

「重叠深度」= 两个矩形相交区域的 `min(宽, 高)`,所以共用 1 px 边框算约 1,不会误报。

运行器的覆盖项:`run_gate(non_bg_floor=..., require_renderer=..., require_header=...)`,CLI `--non-bg-floor`(负数 = 关)、`--require-renderer`(空串 = 关)、`--no-header`。不给就用 `calibration.json`。

## 运行器的旗标(逐个实测过)

```
--headless=new --disable-gpu --enable-unsafe-swiftshader --use-angle=swiftshader
--user-data-dir=<每次启动的临时目录> --window-size=<W+偏移>,<H>
--virtual-time-budget=20000 --enable-logging=stderr --v=0 --dump-dom <url>
```

在本机(Windows 11,Edge 152.0.4191.53)的对照实验:

| 变体 | 结果 |
|---|---|
| 设计全套旗标 | 有结果,`UNMASKED_RENDERER` 为 SwiftShader,约 0.8 s |
| 去掉 `--virtual-time-budget` | **没有结果**:转储发生在 load 时,50 ms 计时器还没触发 |
| 只留 `--disable-gpu` | 有结果,但渲染器是 `Microsoft Basic Render Driver`(WARP) |
| 什么 GPU 旗标都不加 | 有结果,但渲染器是真实显卡(Intel UHD):像素与机器相关,不可接受 |
| Chrome 156 + 全套旗标 | 与 Edge 行为一致,窗口偏移不同;自动的 `RENDERER` / `NON_BG` / `NO_DRAW` 在 Chrome 上同样通过正例、拒绝反例 |

因此 SwiftShader 三个旗标和 `--virtual-time-budget` 都是承重的,`RENDERER` 检查现在是自动的。

URL 用 `Path.as_uri()`(百分号编码),所以日文目录名和空格都安全;stderr 里回显的源地址也是同样的编码,控制台过滤按解码后的前缀比较。

## 窗口宽度校准(`calibration.json`)

`window-size` 的宽度减去页面看到的 `innerWidth` 就是偏移,**每个浏览器族不同**,不能共用一个常数:

| 浏览器 | 偏移 | 窗口高 800 时 innerHeight | 已验证宽度 |
|---|---|---|---|
| Edge 152.0.4191.53 | 24 px | 708 | 1024 1256 1366 1576 1600(另见 700 800 2536) |
| Chrome 156.0.8078.73 | 16 px | 705 | 1024 1256 1366 1576 1600(另见 700 800) |

窗口小于约 500 px 时被浏览器夹住(Edge 最小 innerWidth 492,Chrome 500),此时偏移不再恒定;门禁用到的宽度都 >= 1024,不受影响。运行器每次都检查页面报告的 `innerWidth`,不符就判 FAIL(005)而不是默默放过。重新测量:

```
PYTHONPATH=src python -m gemarc_mcp.atlas.gate.smoke --calibrate --width 1024 --width 1366 --width 1600
PYTHONPATH=src python -m gemarc_mcp.atlas.gate.smoke --calibrate --browser "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

首次探测使用中性偏移 0(不拿已存的值自证),再用「目标 + 测得偏移」验证一次必须恰好得到目标宽度。结果由人手写回 `calibration.json`(命令只打印)。

阈值里 `nonBgFloor`(0.0005)和 `gl.rendererSubstring` 在 `provisional` 里标为暂定:前者只在合成桩(`nonBgRatio` 0.125)和空白画布(0.0)上观察过,真正的 atlas 场景要到 3D 里程碑再调;后者是在 Edge 152 / Chrome 156 上测的,换版本后若不再报告该子串,会以 `RENDERER` 失败,直到重新校准。

## 控制台错误:两条通道、晚到的错误、以及预算视界

实测(Edge 152 与 Chrome 156 同):`--enable-logging=stderr --v=0` 下所有 `CONSOLE` 记录,包括 `console.error`、未捕获异常、语法错误、WebGL 错误,**级别全是 `INFO`**。所以设计原文里的「stderr 中 ERROR 级 CONSOLE 行」永远不会匹配。处理:

1. 页内通道(主通道):`window` 的 `error`(捕获阶段,含资源加载失败)、`unhandledrejection`、包装后的 `console.error`、`window.__ATLAS_ERRORS__`。
2. stderr 通道(兜底,抓页内通道来不及看到的东西,如 `selftest.js` 之前的脚本语法错误):只统计来源在页面所在目录之下的 `CONSOLE` 记录,并且级别为 `ERROR/FATAL`,或消息匹配 `calibration.json` 里的模式(目前:`^Uncaught\b`、`^WebGL: (INVALID_|OUT_OF_MEMORY|CONTEXT_LOST)`)。扩展页(`chrome-extension://`)、浏览器内部 `ERROR:` 行、`GL Driver Message ... GPU stall due to ReadPixels` 这类 INFO 噪声都不计。

**晚到的错误。** walker 通常在几十到一百多毫秒(虚拟时间)内写完结果,而转储发生在虚拟时间预算(20 s)用完的时候。所以 `selftest.js` 在写完结果之后**继续捕获**,把之后的每一个错误镜像进 `<pre data-late-errors='{"count":N,"errors":[...]}'>`,运行器把它并进 `PAGE_ERRORS`(消息里标明「after the walker finished」)。只认这个属性,JSON 正文里同名的键一律丢弃。实测(`probe` 页,计时器在 100 / 1000 / 5000 / 15000 / 19000 / 19900 / 20500 / 25000 ms 各记一次):19900 ms 以内的都进了转储,20500 ms 以后的没有,所以**视界就是 `virtualTimeBudgetMs`**。

**视界之外看不见。** 预算之后才发生的错误既不在页内通道也不在 stderr 里(夹具 `beyond-budget`:25000 ms 抛错,PASS,钉在 `KnownLimitTests`)。想看更远就调大 `--budget-ms`,代价是虚拟时间推进更久(CPU 时间,不是墙钟等待)。

**`selftest.js` 之前**的 `console.error` 看不到(只有未捕获错误能被 stderr 通道抓到),所以它必须放在 `<head>` 最前面。

## 重试策略

只重试进程层失败:超时(整棵进程树用 `taskkill /T /F` 杀掉)、退出码非 0、输出被截断(没有 `</html>`)。DOM 完整但没有结果、结果损坏、任何断言失败**都不重试**;浏览器根本启动不了(`OSError`)也不重试,直接给出 `PROCESS` 并写明原因。`smoke.run_gate` 默认 `retries=1`;`gate_spike.py` 默认 `--retries 0`,让矩阵展示原始稳定性,矩阵里的 `retry` 列显示用掉重试的次数。

**nonce 按「启动」生成,不是按 `run_gate` 调用生成**:重试时新的启动用新的 nonce,上一次启动留下的 DOM 即使带着旧 nonce 也会被拒收(`test_every_attempt_has_a_fresh_nonce_and_a_stale_dom_is_rejected`)。

## 浏览器的选择

发现顺序:`GEMARC_ATLAS_BROWSER`、`PATH` 里的 Edge、默认安装路径的 Edge、`PATH` 里的 Chrome/Chromium、默认安装路径的 Chrome。

「默认安装路径」的根目录是 `ProgramFiles(x86)`、`ProgramFiles`、`LOCALAPPDATA`:先取传入的环境,缺了取进程环境,再缺就用标准安装的字面目录(`<SystemDrive>/Program Files (x86)`、`<SystemDrive>/Program Files`、`<USERPROFILE>/AppData/Local`)。MCP SDK 交给子进程的环境(Codex、Hermes)只有 `LOCALAPPDATA`、没有 `ProgramFiles*`,以前这会让装在 `ProgramFiles(x86)` 里的 Edge 找不到、门禁说 SKIP;现在不需要在配置里转发这些变量(转发无害)。`candidate_paths` 与 `find_browser` 的 `fallback_environ` 参数(默认是进程环境)是测试用的接缝。

**显式指定就不回退。** `GEMARC_ATLAS_BROWSER` 或 `--browser` 指向的不是文件时,结果是 SKIP(退出码 3,「the pinned browser ... is not a file」),**不会**悄悄改用 Edge:否则门禁跑在你没选的浏览器上,用的却是别的浏览器的校准。指向的是文件但不能执行(例如一个文本文件)时,`OSError` 被捕获成 `PROCESS` 判定(不重试),不再抛 traceback。

SKIP 的文字(`no_browser_message`)**不写入**指定的路径,只说「the pinned browser (GEMARC_ATLAS_BROWSER) is not a file」:路径是主机的环境值,而这句话会进工具回复和 `dist/.verify/gate-report.json`。命令行(`smoke.main`)的使用者自己输入了路径,所以命令行带路径输出(`show_path=True`)。`tools.scrub_verdict` 仍然在回复和报告里再清一遍,作为第二道防线。

CLI 输出对控制台编码做了容错(`errors="backslashreplace"`):页面路径里有 gbk 编码不了的字符时,仍然打印判定,不会在浏览器跑完之后因为 `UnicodeEncodeError` 退出。

## 工具路径(`build gate=smoke` 与 `verify`):判定的第四种结果、挂钟预算、有上限的管道

命令行(`smoke.main`)和 `run_gate` 的语义不变:每个宽度各启动一次,超时是 `FAIL` / `PROCESS`。工具路径(`build.run_gate` → `smoke.verify_gate`)在此之上多了下面几条,每条都有测试(`tests/atlas/test_gate_html_fixes.py`、`tests/atlas/test_gate_tool_path.py`)。

- **INCONCLUSIVE。** 一个宽度的每次启动都超时(别无其它失败),这个宽度是 `INCONCLUSIVE`:页面没被评判,不是通过,也不是页面的缺陷。整个判定里 `FAIL` 高于 `INCONCLUSIVE` 高于 `PASS`;`SKIP`(没有浏览器)最高。工具回复 `GEMARC_ATLAS_BUILD_033`。每次启动的挂钟上限是 `VERIFY_TIMEOUT_SECONDS = 90`(好页面在空闲机器上 2 到 3 s,八个并行时最坏约 23 s;页面自己的时间由虚拟时间预算约束,所以更大的上限对好页面没有代价),超时不重试(`retry_on_timeout=False`:负载高的机器上第二次启动只会重复一次超时)。
- **挂钟预算。** 各宽度合起来约 `VERIFY_BUDGET_SECONDS = 150`:每次启动的上限是 `min(90, 剩余)`、不低于 `VERIFY_MIN_LAUNCH_SECONDS = 10`;剩余不足 10 s 就不再启动;一个宽度里因超时以外的原因失败的启动(浏览器退出码非 0、输出被截断)的重试也计入预算(`run_gate(time_left=...)`:重试最多得到剩下的时间,剩下不足 10 s 就不重试,发现里写明 `no retry`)。启动结束时杀进程树、读管道的清理(最坏约 15 s 等进程加每路管道 5 s)不在预算内,所以总时间是「约 150 s 加最后一次启动的清理」,不是硬上限;**第一个 INCONCLUSIVE 之后不再启动后面的宽度**(机器忙到超时,再启动只会加重负载)。没启动的宽度是一行 `INCONCLUSIVE`,带 `notRun: true`、代码 `NOT_RUN`、原因(`message` 以 `NOT RUN:` 开头),判定的 `message`、`lines` 与 `budget`(`seconds`、`usedSeconds`、`notRun`)都写明哪些宽度没有启动。第一个宽度总会启动。预算参数(`budget_seconds`、`stop_after_inconclusive`、`clock`)默认关闭,所以命令行与直接调用 `verify_gate` 的测试不受影响。
- **页面级发现里的路径。** 页面自己出错时(`PAGE_ERRORS`、`CONSOLE_STDERR`、被挡的资源)浏览器引用的是页面与脚本的网址(`file:///C:/Users/<用户>/AppData/...`);`summarize_result` 把发现的 `detail` 与 `message` 里的 `file://` 网址、盘符路径和常见 POSIX 根下的路径改成文件名(保留 `:行:列`),所以 `tools.verify` 的回复与 `dist/.verify/gate-report.json` 不含机器路径(命令行 `main` 直接打印的 `GateResult` 不经过这一步,那是读命令行输出的人自己的机器)。
- **有上限的管道。** 浏览器的 stdout 与 stderr 各由读线程读进有上限的缓冲(`STDOUT_CAP_BYTES` 16 MiB、`STDERR_CAP_BYTES` 4 MiB),超出的部分继续读、丢弃(子进程不会因管道满而阻塞);任何一路超限都是 `PROCESS` 失败(「truncated ... cap」),不是判定;读管道时出错(`OSError`)不当作文件结束:读到一半的 stderr 会漏掉后面的控制台错误,所以它同样是 `PROCESS` 失败(「could not be read to its end」,超时的启动仍先按超时算)。浏览器自己已经退出、而它的子孙还握着管道时,杀死已退出的进程够不到它们:等管道的时间只加到每路 1 s(不是 5 s),启动仍按超时处理、绝不判为通过,子孙进程可能活到它自己结束(Windows 上没有用作业对象去追;真实的 Edge 没有观察到留下这样的子进程)。上限来自实测:Edge 152、构建出的参考规模页面、门禁模式,stdout 约 40 到 82 KB(没有门禁模式、脚本还在 DOM 里时 2.1 MB),stderr 2 到 121 KB;stdout 上限是构建对页面的硬上限(8 MiB)的两倍,所以通过构建的页面不会超限;stderr 上限约是最大实测的 35 倍。
- **机器负载(门禁的 CPU 余量)。** 空闲时每次启动中位数 2.2 s;8 个忙进程 2.6 s;12 个忙进程 2.9 s;16 个忙进程(约等于逻辑核数)时 8 次启动里只有 2 次通过,其余在 30.8 到 33.0 s 超时;48 个忙进程时 8 次里 1 次;空闲机器上 8 个启动并行最长 23.2 s。数字来自验证者的 `load.py`(Edge 152,16 个逻辑核),本批没有重新测量。这是超时上限取 90 s、并设 INCONCLUSIVE 的依据:在 30 s 左右有一道悬崖。
- **文字扫描。** `textAllow` 钩子(页面自己的数据里含 `undefined`、`NaN`、`[object` 的片段不算 `TEXT_BAD`;见 `bundle-contract.md` 9.6);运行器一侧的显式关闭项是 `run_gate(scan_text=False)` 与 `--no-text-scan`。扫描的缺口:`visibility:hidden` 的文字、画在 canvas 上的文字(都读不到)。
- **步骤必须同步。** 页面脚本化步骤返回 thenable(Promise)算 `STEP_FAILED`:门禁只等同步的返回值,异步步骤会在结果写出之后才完成,等于没有被检查。
- **嵌套的控件不算重叠。** 页眉里一个控件包着另一个控件(带关闭按钮的标签)时,两者的重叠是嵌套,不算 `HEADER_OVERLAP`(`selftest.js` 里 `contains` 判断);只比较互不包含的控件。

## 页面本身的内容安全策略

构建出的页面以 `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'">` 开头(`head` 的第一个元素,在自检脚本之前)。门禁因此在这条策略下跑真实页面:内联脚本与样式可以运行,被动加载(图片、脚本、框架、对象、字体、媒体)和 `fetch`、XHR、`sendBeacon`、WebSocket 这类请求被挡,`form-action 'none'` 挡表单提交,`base-uri 'none'` 挡 `<base>`;策略违规会作为浏览器控制台错误出现在 stderr 上。**这条策略不挡页面跳转**:`location` 赋值、`meta refresh`、链接导航都无法用 CSP 阻止(实测:真实 Edge 里脚本跳转到外部网址仍然到达)。又因为 `script-src 'unsafe-inline'` 允许内联脚本,它不是 XSS 边界,只是纵深防御;页面被改过之后再注入标记的防线是构建检查 BD-001 至 BD-007 与「构建后被改过的页面在门禁之前就被 `GEMARC_ATLAS_BUILD_032` 拒绝」,模型文字本身进不了标记(BD-003)。`tests/atlas/test_gate_csp.py` 在真实 Edge 里证明:合成的小预设与参考预设各一份已封存页面,三个宽度的门禁模式下原始 stderr 零行违规,门禁零发现,3D、4D、5D 的 WebGL 画布都画了;反向对照(故意放一个被挡的图片与请求)能在同一份原始 stderr 里看到违规行。构建检查 BD-004 要求这条 `meta` 逐字存在、是第一个元素、整页只有这一条 `http-equiv`;细节见 `bundle-contract.md` 10.6。

## 清理

每次启动用一个全新的 `--user-data-dir`(`tempfile.mkdtemp`,可用 `tmp_root` 指定父目录),结束后重试删除(Windows 在进程退出后仍会短暂持有句柄),删不掉的记入 `GateResult.leftovers`。

浏览器子进程的 `TEMP` / `TMP` / `TMPDIR` 被指到工作目录里的一个子目录(`smoke._browser_env`),随工作目录一起删。原因:Edge 会在 `--user-data-dir` 之外、往系统临时目录里写东西(更新日志 `cv_debug.log`、首次运行导入器目录 `Importer_0_4`、`msedge_url_fetcher_*` / `*.tmp`),存活够久的启动(比如超时被杀的)会把它们留在用户的临时目录里。实测:继承系统环境变量的正常 0.8 s 启动 1100 次一个都没留;超时被杀的启动留下 3 个;重定向之后系统临时目录是空的(这一条是手工用真实浏览器跑一个死循环页面、30 s 超时被杀后核对的,没有进套件,因为要 30 s;套件里只断言了传给浏览器的环境变量)。

## 用法

```
# 单页(--inline-selftest 只用于带占位符、自包含的桩页面;真实构建产物已内联,原地运行,不需要)
PYTHONPATH=src python -m gemarc_mcp.atlas.gate.smoke dist/index.html --width 1024 --width 1366 --width 1600
# 没有头部的页面:显式关掉头部检查
PYTHONPATH=src python -m gemarc_mcp.atlas.gate.smoke page.html --no-header
# 退出码:0 PASS,1 FAIL,3 SKIP(没有浏览器 / 指定的浏览器不是文件),2 用法错误

# M0 验收矩阵
python tests/atlas/gate_spike.py --runs 20

# 只跑本目录的测试
PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_gate*.py"
```

`--inline-selftest` 把组装后的页面写进临时目录,所以带相对资源(`src` / `href`)的页面在那里找不到资源:这类资源 404 会以 `PAGE_ERRORS` 失败(不会悄悄通过,有测试),但正确做法是构建时就内嵌自检、原地运行。

## 实测结果

命令(重试关闭,让矩阵展示原始稳定性;`TEMP` / `TMP` 指到草稿目录):`python tests/atlas/gate_spike.py --runs 20`。本机 Windows 11,Edge 152.0.4191.53,最终代码。

```
fixture                      width  expect codes                               match   med s  p95 s  max s  retry
---------------------------------------------------------------------------------------------------------------------------
ok-stub                       1256  PASS  -                                  20/20     0.82   0.85   0.86      0
ok-stub                       1366  PASS  -                                  20/20     0.83   0.91   7.09      0
ok-stub                       1576  PASS  -                                  20/20     0.82   0.86   0.86      0
ok-canvas-2d                  1366  PASS  -                                  20/20     0.79   0.83   2.13      0
ok-gl-fbo-left-bound          1366  PASS  -                                  20/20     0.82   0.86   0.90      0
ok-gl1-instanced              1366  PASS  -                                  20/20     0.79   0.90   7.00      0
bad-header-overlap            1256  FAIL  HEADER_OVERLAP                     20/20     0.79   0.82   0.83      0
bad-header-overlap            1366  FAIL  HEADER_OVERLAP                     20/20     0.77   0.82   0.83      0
bad-header-overlap            1576  PASS  -                                  20/20     0.78   0.82   0.84      0
frozen-scene                  1366  FAIL  HEARTBEAT                          20/20     0.79   0.82   0.83      0
no-result                     1366  FAIL  NO_RESULT                          20/20     0.79   0.84   0.85      0
console-error                 1366  FAIL  PAGE_ERRORS                        20/20     0.81   0.82   0.85      0
wide-overflow                 1366  FAIL  OVERFLOW_X                         20/20     0.78   0.83   0.87      0
early-error                   1366  FAIL  CONSOLE_STDERR                     20/20     0.78   0.84   0.84      0
page-errors                   1366  FAIL  CONSOLE_STDERR,PAGE_ERRORS         20/20     0.79   0.81   0.83      0
multi-defect                  1366  FAIL  FIT_CLIPPED,HEADER_OUTSIDE,TEXT_BAD 20/20     0.77   0.80   0.81      0
header-unmarked               1366  FAIL  HEADER_MISSING                     20/20     0.78   0.81   0.84      0
overflow-vscroll              1366  FAIL  OVERFLOW_X                         20/20     0.77   0.81   0.81      0
console-error-late            1366  FAIL  PAGE_ERRORS                        20/20     0.77   1.76   7.00      0
late-throw                    1366  FAIL  CONSOLE_STDERR,PAGE_ERRORS         20/20     0.77   0.83   1.01      0
fake-static                   1366  FAIL  BAD_RESULT                         20/20     0.76   0.81   0.82      0
frame-counts-reads            1366  FAIL  HEARTBEAT                          20/20     0.76   0.81   0.85      0
canvas-gl-blank-lying         1366  FAIL  NON_BG,NO_DRAW                     20/20     0.81   0.84   0.85      0
canvas-2d-blank               1366  FAIL  NON_BG,NO_DRAW                     20/20     0.79   0.80   0.82      0
canvas-no-context             1366  FAIL  NON_BG                             20/20     0.78   0.84   7.00      0
canvas-declared-hidden        1366  FAIL  NON_BG                             20/20     0.78   0.83   7.03      0
gl-error                      1366  FAIL  CONSOLE_STDERR,GL_ERROR            20/20     0.79   0.88   7.07      0
settab-noop                   1366  FAIL  TAB_STATE                          20/20     0.78   0.83   7.01      0
nan-in-attr                   1366  FAIL  TEXT_BAD                           20/20     0.76   0.80   0.81      0
ok-stub (non-ASCII path)      1366  PASS  -                                  20/20     0.79   0.82   0.83      0
```

- 30 行(夹具与宽度的组合,加日文带空格目录的一行)乘 20 次,共 600 次启动,**每行 20/20 与预期完全一致**(判定和错误码集合都要对),0 次重试,0 个残留的目录。
- 正例(`ok-stub` 三个宽度、`ok-canvas-2d`、`ok-gl-fbo-left-bound`、`ok-gl1-instanced`、日文路径)全部 PASS;门禁自己读到的 `nonBgRatio` 在每一行里恒定(0.125 / 0.15 / 0.18 / 0.0713),页面报告的 `innerWidth` 恒等于目标宽度。
- 反例每个都得到预期的、而且只有预期的错误码;`bad-header-overlap` 在 1256 和 1366 失败、1576 通过。
- 耗时:中位数 0.79 s,p95 0.84 s,最大 7.09 s。`STOP/GO: GO`(要求每行至少 19/20,且 p95 不超过 8 s)。后备方案阶梯(截图加 PNG 解码、再往后的 CDP)不需要启用。
- `max s` 列里的约 7 s 是下一节说的偶发停顿,不是某个夹具的特性;结果和判定不受影响。
- 套件(`test_gate_smoke*.py`)是矩阵之外的另一道检查:无浏览器的单元测试加真实浏览器的集成测试,后者在没有浏览器时大声 SKIP。

## 启动耗时与停顿

**口径。** 下面全是安静机器(Windows 11,Edge 152.0.4191.53,没有别的负载)上的数字。验收的「单次启动不超过 8 s」按 p95 判定,是**安静机器**的口径;负载下会明显变长(验证人在 8 路并发加 6 个占 CPU 的进程下测过,中位数 5.9 s,这个数我没有复现)。运行器的 30 s 超时不变,没有拿 8 s 当超时。

**常态。** 最终矩阵 600 次启动:中位数 0.79 s,p95 0.84 s。

**偶发的 7 s 停顿。** 大约每 170 次启动出现一次,时长稳定在 7.0 到 7.3 s,和页面无关(同一个夹具有时快有时慢)。本轮在 2580 次启动里记到 15 次(约 0.6%),上一轮 1100 次里 7 次;另有 23 到 32 s 的停顿,出现在机器上有两个失控的外部进程占满 CPU 的时候。保留了 stderr 的 5 次停顿,全都带着 Edge 的 `components\edge_auth\oneauth\oneauth_config.cc` 日志块(Microsoft 账号的 WAM / MSAL 令牌流程,`WindowsIntegratedAuth`),检查过的一次正常启动没有这个块。这只是相关性,没有证明因果。每次停顿的启动结果仍然正确,30 s 超时之内判定不受影响。

**额外旗标的 A/B(假设已测,结论:不采纳)。** 假设:`--no-first-run --disable-background-networking --disable-component-update --disable-sync --disable-extensions` 能去掉停顿。同一页面、同一条完整命令行,交替执行 400 对(偶数对先跑基线、奇数对先跑旗标组):

| | 启动数 | 中位数 | p95 | 最大 | 超过 3 s |
|---|---|---|---|---|---|
| 设计旗标(现状) | 400 | 0.886 s | 1.071 s | 2.29 s | 0 |
| 再加上述五个旗标 | 400 | 0.875 s | 1.020 s | 7.29 s | 2 |

两组的结果都是 400/400 正确(nonce 回显一致)。旗标没有去掉停顿:旗标组有 2 次 7 s、基线 0 次。样本太小,不能说旗标有害,但足以说它没用,所以**不加**,`build_args` 保持设计的那一套。基线的 0 次也不能当成「常态没有停顿」:下面另一批 300 对里基线有 1 次。要检出 0.5% 量级的差别需要上千对,不值得。

**浏览器临时目录重定向的影响。** 重定向(见「清理」)不该影响耗时,也单独测了:同样交替 300 对,基线对「`TEMP` / `TMP` / `TMPDIR` 指到工作目录里」:

| | 启动数 | 中位数 | p95 | 最大 | 超过 3 s |
|---|---|---|---|---|---|
| 继承环境变量 | 300 | 0.835 s | 0.907 s | 7.11 s | 1 |
| 重定向到工作目录 | 300 | 0.827 s | 0.901 s | 7.12 s | 2 |

没有可测的差别(停顿 1 对 2 是噪声量级)。继承环境变量的那一组没有往系统临时目录里留任何东西(重定向组按构造也不会);只有活得够久的启动(超时被杀)才会留,那种情况在重定向之后也不再泄漏。

## 诚实的局限

- 信任边界见上:页面是被测物,也是自检的宿主。能挡住疏忽和偷懒,挡不住刻意欺骗。
- 只覆盖 Chromium 系引擎、软件 GL(SwiftShader);不评判视觉质量;没有 Firefox / Safari。
- 像素检查证明的是「画布不是单一颜色」,不是「画对了」:纯渐变背景加空场景会通过;`nonBgFloor` 是暂定值。只读经过 `HTMLCanvasElement.getContext` 的画布;worker 里的 `OffscreenCanvas` 的绘制不计数;WebGPU 画布数不了绘制调用,只按像素判。
- 没有 canvas 的页面(DOM / SVG 渲染),门禁没有独立可读的东西:心跳只能证明 `frame()` 是纯读取且在两次 `renderOnce()` 之间前进;一个 `renderOnce` 只把计数器加一的页面会通过(`counter-bump-only`)。
- `tabState` 是可选的:不给钩子,`setTab` 什么都不做的页面仍会把同一个视图量取 N 次(`settab-noop` 在没有钩子时 PASS,有钩子时 `TAB_STATE`)。
- 文本扫描看 `innerText`(可见文本)和 `title` / `aria-label` / `alt` / `placeholder` / `document.title`;隐藏元素里的正文不扫(`display:none` 里的 `NaN` 不报)。
- 头部检查只检查标了 `data-gate="header"` 的容器;现在没标记会失败(`HEADER_MISSING`),只有显式 `--no-header` 才跳过。
- 挂在 DOM 里、有尺寸但摆在屏外的辅助 canvas,和别的可见 canvas 一样被读、被判(空白就是 `NON_BG`);没挂进 DOM 的离屏 canvas 看不到,也不判。
- `[data-gate=fit]` 只检查视口内与自身 `overflow` 裁剪,不检查祖先裁剪。
- 3D 投影边界与占用率下限(M6)由页面的 `sceneBounds` 钩子提供数据,判定在 `smoke._scene_findings`;真实场景的脚本化步骤(选案例、播放步进、检视器页签)在页面侧(`ui/tab-slots.js`)。门禁看到的是"画出了场景",看不到部件有没有画出:框和边画出而部件一个也没画会通过。
- 错误视界 = 虚拟时间预算(默认 20 s),之后的错误看不到。
- 窗口偏移是在本机两个浏览器版本上测的;换版本或换平台必须重测(运行器会用 005 报警,而不是放过)。
- 启动耗时是**安静机器上的数字**;见上一节,负载下会明显变长,8 s 的验收是安静机器的口径,运行器的 30 s 超时不变。
- 进程树清理:Windows 用 `taskkill /T /F`(已用真实子进程验证);POSIX 用独立会话 + `os.killpg`(代码已写,本机没有 Linux 环境,未验证)。
- 本任务没有在参考页面副本上做头部重叠校准(那是后续里程碑的工作,且副本必须留在仓库之外)。

## 与构建的集成(M4)

- `smoke.verify_gate(page, widths=(1024, 1366, 1600), **run_gate 的参数)`:同一个浏览器依次在每个宽度启动一次,返回一个裁决 `{status, widths[], lines[], browser, message}`。`PASS` 要求每个宽度都通过;找不到浏览器时每个宽度都是 `SKIP`,总裁决是 `SKIP`,`lines` 与 `message` 写明"没有运行,不算通过";任何宽度的断言失败、或运行器本身抛出异常,总裁决都是 `FAIL`(异常记为 `PROCESS`)。`summarize_result` 给出单个宽度的纯数据(状态、发现名、标签页、脚本步骤数、耗时)。
- `tools.build(gate="smoke")` 与 `tools.verify` 调用它,报告写在 `<项目>/atlas/dist/.verify/gate-report.json`,浏览器的临时目录写在 `.verify/work` 下并在结束后删除。`build` 在没有浏览器时仍然成功,但回复里 `gate` 是 `SKIP` 并有一行"页面未验证";`verify` 在没有浏览器时回复 `invalid`(`GEMARC_ATLAS_BUILD_031`:SKIPPED, NOT PASSED);门禁失败是 `GEMARC_ATLAS_BUILD_030`。发布构建(`release`)要求门禁是 `PASS`,而且门禁是在暂存目录里的页面上跑的(报告先写在暂存目录的 `.verify/`,发布通过后才和页面一起换入 `dist/.verify/`;被拒绝的发布不留下任何报告,`dist/` 不动)。
- 页面里的门禁钩子由 `viewer/src/ui/shell.js` 的 `gateConfig()` 提供(六个标签页、四个脚本步骤 `doc-twin`、`levels`、`inspector`、`search`);`selftest.js` 在构建时由 `htmlassemble.py` 放进 `<head>` 最前面,本目录的行走器没有为此改动。`doc-twin` 在页面里重新组装 20 个规范选择并比较哈希,孪生实现漂移时把一条记录写进 `window.__ATLAS_ERRORS__`,所以运行器报 `PAGE_ERRORS` 与 `STEP_FAILED`。
- 校准夹具的清单在 `tests/atlas/gate_fixtures/MATRIX.json`:`bad-header-overlap`(1256、1366 失败,1576 通过)、`frozen-scene`、`missing-result`(即 `no-result.html`)、`unknown-building`(测试时由合成模型生成,不存已构建的页面)、以及 viewer-B 的 `clipped-scene`(`FRAME_CLIPPED`)、`sparse-scene`(`SCENE_SPARSE`),它们在 `gate_fixtures/scene/`(不放在 `gate_fixtures/` 根目录:`test_gate_smoke` 会枚举根目录的 `*.html` 并要求每个都被它自己的源码用到)。
- 在构建出的页面上做 N 次测量:`python tests/atlas/gate_html_matrix.py --runs 20 --fixtures`(退出码 0 GO、1 NO-GO、3 没有浏览器)。
