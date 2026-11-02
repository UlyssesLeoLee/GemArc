# 界面原型：ui-* 切片（独立于 5D）

- 第二类产物：切片名 `ui-` 开头（`ui-10-login`），第一行 `#atlas-ui 1`，与 5D 的 `#atlas 1` 互不相认。只写事实（屏幕、状态变体、控件树、导航）；布局、SVG、画面卡由工具生成，不要写 HTML 或 SVG。
- 每个切片 4 到 6 屏（约 750 字节/屏）：一屏的 variants、widgets 和以它为起点的 nav 写在同一个切片里。put 预算 4 + 2 x ceil(屏数/4)；修复用 `mode=patch`。
- 语法同 .atl（表头照抄、单元格不能空、`@set`、`~`）。各段：
{{uiheaders}}
- id：屏幕 id 以字母开头，不含 `. : # ~`，不分大小写不得是 INDEX、NAV、START、CARD 或设备名（CON、NUL…），两个 id 不得只差大小写；控件 local 小写且不叫 `root` 或 `state`；完整 id 是 `屏.local`、`屏#状态`（`default` 状态自动存在，不要写）。派生键（id、order、parentId、widgetIds…）不要写，写了是 DV-001。
- 控件树：`parent` 是同屏的容器，行序即兄弟顺序；`in=error,empty` 限定控件只在这些状态里出现；w、h 取 fill、hug、像素数或 Nfr；align 取 start、center、end。容器的 label 不画：要可见标题就加 heading 控件。变体的 `text=` 是界面文案（空态、出错态画进体内），`note` 是设计说明。
- 导航：`from` 是屏幕或 `屏.控件`（须是可点击的种类），`to` 是屏幕或 `屏#状态`；同一对 from、to 的第二条要 `alias=`。写完后加 `@final nav`。
- 到 5D 的链接（actor、req、api、case、rule、machine、state）都是可选的：有 5D 时核对，没有时只记一条 INFO；5D 里删了那个 id，这里的链接就变红。
- 种类：控件 {{uiids:widgetKinds}}；屏幕 {{uiids:screenKinds}}；框架 {{uiids:frames}}；状态 {{uiids:variantKinds}}；强调 {{uiids:emphasis}}；触发 {{uiids:triggers}}。种类到 DOM 的对照表在 docs/atlas/spec/ui-schema.md。
- 前端核对的判定写进 `ui-verdict-*` 切片（只含 conform、diffs；不改 ui_sha8）：`sha=` 抄画面卡的判定戳，`checked` 加 unverifiable 行数等于核对点数，`basis` 是 `别名:路径:行号`；只记差异，没看的逐个写成 unverifiable。
