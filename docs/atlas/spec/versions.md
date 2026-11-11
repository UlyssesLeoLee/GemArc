# 指纹与版本：`gemarc-atlas-versions/1`

本规范定义三样东西：**指纹**（内容是什么）、**版本标记**（这个内容是哪一版、什么时候产生的）、**台账**（一次构建记了什么）。

对应实现：`src/gemarc_mcp/atlas/versions.py`（指纹与台账）、`src/gemarc_mcp/atlas/build.py`（构建时登记）、`src/gemarc_mcp/atlas/tools.py` 的 `versions()`（MCP 工具 `gemarc_atlas_versions`）。

## 1. 三个词

| 词 | 形状 | 含义 |
|---|---|---|
| 指纹 `sha8` | 8 位十六进制 | 一个文件的 SHA-256 的前 8 位。切片戳（`slices.sha8`）与阅读面页尾哨兵用的是同一条规则 |
| 版本标记 `label` | `<sha8>-<YYYYMMDD-HHMM>` | 指纹 + 产生的分钟（UTC）。例如 `3f2a1b9c-20261110-1534` |
| 台账 `ledger` | `.atlas/versions.json` | 一次构建一条：站点指纹 + 每个 1D 文档的指纹与标记 |

一个指纹说「内容是什么」，不说「什么时候」；一个标记说「这个内容的这一版是什么时候产生的」。两者分开的原因是 V-1。

## 2. 规则

### V-1 标记不进产物

同一个模型的两次构建，产物必须逐字节相同（`build.py` 的确定性规则；BD-007 拒绝时间戳）。分钟不是可复现的，所以：

- **产物里只放内容指纹**：`dist/build-info.json` 的 `fingerprints` 段（`site` 与 `documents`）。
- **带分钟的标记只进台账与 MCP 回复**：`.atlas/versions.json`。

反向也不成立：产物里不能有任何依赖当前时刻的东西。台账因此不是产物，不发布，不进指纹。

### V-2 带指纹的文件不进指纹

下列文件不进站点指纹，理由逐条写明：

| 文件 | 为什么 |
|---|---|
| `dist/build-info.json` | 它自己装指纹 |
| `dist/manifest.jsonl` | 清单登记每个文件的 sha8，它不能登记自己（`reading/manifest.py` 的 `SKIP_NAMES` 是同一条规则） |
| `dist/.verify/**` | 浏览器门的工作目录，不是产物 |
| `dist/oracle-sheet.md` | 清单只登记它的存在、不登记字节：它在构建之后写，还会再写一次（`reading/manifest.py` 的 `finalize`） |

### V-3 站点指纹

站点指纹 = 对「路径 指纹」逐行拼接（按路径排序）求 SHA-256，`sha8` 取前 8 位。

- 同一个模型的两次构建：同值。
- 不同的站点：不同值（路径参与哈希）。
- 一个文件改了：值就变。

站点指纹是**整站**的版本身份，回答「我现在这份多媒体设计网页是哪一版」。

### V-4 1D 文档指纹

1D 层 = 派生文档 `docs/<lang>/{REQ,BD,DD}.md`（以及 `docs/<lang>/by-requirement/**`）与阅读面页面 `read/1D/**`。**每个文件一个指纹**，记在 `build-info.json` 的 `fingerprints.documents` 与台账里。

指纹清单覆盖产出目录里的每一个文件（V-2 排除的除外），所以阅读面页面只要写进了 `dist/`，就自动有一个指纹，不必再改这条规则。阅读面自己另有每页的 sha8（页尾哨兵）与 `manifest.jsonl`。

派生文档的指纹是模型的纯函数：`sha8(整份文档正文的 UTF-8 字节)`。构建写出的文件就是这份正文，所以「从模型算出来的指纹」与「磁盘上文件的指纹」相等。

### V-5 标记是内容的一个版本，不是一次构建

内容没变 → 标记与分钟沿用旧的。判据是 **sha8 相等**，不是分钟相等。文档与整站一视同仁：站点指纹没变，整站的标记也不变。

后果：一次构建改了模型但没有改任何 1D 文档时，那些文档的标记不动；站点指纹变了（`index.html` 变了），整站的标记也换新的。反过来，两次构建之间没有任何东西变，两次的标记完全相同——标记说的是「内容的这一版」，不是「一次构建」。

### V-6 台账

- 位置：`<项目>/atlas/.atlas/versions.json`（项目自己的状态目录）。
- schema：`gemarc-atlas-versions/1`。
- 一次构建一条，最多 50 条（最旧的丢）。
- 每条：`{at（构建时刻）, modelSha8, site{sha256, sha8, label, at（这一版从什么时候开始）}, docs{路径: {sha8, label, at}}}`。
- 损坏的、外来的台账**读成空**：台账是工具的状态文件，读的人永远不该被一个坏文件挡住。
- 台账不是产物（V-1）。

### V-7 预备更新时的核对只报告

核对回答两个问题：每个 1D 文档**现在**的指纹是什么？它与台账里最后一次记录的**是不是同一版**？

- **只报告**：不拒绝、不写任何东西、不改任何判据。
- 核对的范围是**能由模型直接得出的** 1D 文档（派生文档）。台账里记着但**当前模型给不出指纹**的（例如阅读面页面，由阅读面那条线单独构建）单独列出，标为未核对。
- 空台账（还没有构建过）→ `was` 为空、`same` 为假，报告照旧。

### V-8 历史

MCP 命令给出三类历史：

1. **构建历史**：台账里的每一次构建（时刻、模型戳、站点指纹、站点标记）。
2. **文档版本**：每个 1D 文档的历次指纹与标记（新的在前）。
3. **切片归档**：`.atlas/history/` 里每个切片的旧版本（本来就在存，但没有出口）。

## 3. 接口

`gemarc_atlas_versions(project, view, scope)`，只在 `atlas`（本地）工具集里，只读。

| 输入 | 默认 | 说明 |
|---|---|---|
| `project` | 必填 | 项目名 |
| `view` | `history` | `history`（历史）、`fingerprints`（当前产物的指纹）、`check`（预备更新时的核对） |
| `scope` | `""` | 子串过滤：只显示路径或切片名含它的行 |

**`view=history`** 输出：

```
{status, authority, view, project,
 builds:    [{at, modelSha8, site:{sha8, sha256}, label}],
 documents: {<路径>: [{sha8, label, at}]},
 slices:    [{slice, versions:[{sha8, at}]}],
 counts:    {builds, documents, slices}}
```

**`view=fingerprints`** 输出（产物不存在时 `site` 为空、`documents` 为空数组）：

```
{status, authority, view, project,
 site:     {sha256, sha8, files},
 documents:[{p, k, dim, sha8, b}],
 source:   "build-info" | "dist"}
```

`k` 是文件的类别：`doc`（派生文档）、`read`（阅读面页面与屏幕卡）、`diagram`、`prompt`、`viewer`（`index.html`）、`twin`（`site/**.html`）、`data`（其余）。`dim` 是维度：派生文档是 `1D`，阅读面页面取路径里的那一段（`read/1D/**` 是 `1D`）。

**`view=check`** 输出：

```
{status, authority, view, project,
 same:      true | false,
 documents: [{p, now, was, label, same}],
 notChecked:[{p, sha8, label}]}
```

`now` 是当前模型的指纹，`was` 是台账最后一次记录的指纹，`label` 是被记录那一版的标记，`same` 是两者相等。`same` 整体为真表示每个可核对的 1D 文档都与记录同版。

## 4. 不做什么

- 不把标记写进任何产物（V-1）。
- 不因为核对结果拒绝任何操作（V-7）。
- 不改任何既有判据：发布门、浏览器门、证据门、切片校验的规则一律不动。
