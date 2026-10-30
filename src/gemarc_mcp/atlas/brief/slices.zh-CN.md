# 切片与 put

- 一个切片就是一段 `.atl` 文本，名字 `[a-z0-9-]{1,40}`。按阅读顺序编号：`00-space`（buildings、floors、rooms），`10-structure`、`11-structure` ...（nodes、edges、evidences、machines、transitions），`20-scenarios` ...（tests、cases、events、expect、interventions、requirements、tbds、policies）。
- 大小：每个切片约 11 个节点连同它们的边和证据（4-7 KB）。超过 8 KB 是 SZ-001，超过 64 KiB 被拒。100 个节点的系统大约是十个结构切片加若干场景切片。
- `put(project, slice, text, mode, format, base)`：没有 ERROR 就提交（WARN、INFO 不阻断），并归档旧版本（保留 10 份）。出现任何 ERROR 就什么都不提交，你的全文被存成该切片的红色草稿，不会丢。
- 修复用 `mode=patch`，不要重发或重读切片。有红色草稿时补丁作用于草稿：同一标识的行替换原行（标识：id；边 from|rel|to；expect 的 case；转移 machine + from|to）；`-svc.old` 或 `-a | calls | b` 删除一行；事件块（`@set case=X` 加它的行）替换 X 的全部事件，空块则删除。典型的修复只有 1-6 行。 替换一行时若补丁没有自带 `@set` 行，就沿用被替换行的 `@set` 默认值；空补丁（只有 `#atlas 1`）会重新检查红稿，例如它等的那个切片已经写好之后。
- 同样的文本得到同样的 sha8 和 `unchanged:true`。`base=<sha8>` 在切片已被改动时拒绝写入（CONFLICT，什么都没写）。
- 切片可以引用任何 id，只定义自己的 id；重复定义是 ST-002，会指出两处位置。还没定义的 id 是 pending（INFO），与某个已定义 id 只差一次编辑时是 WARN 疑似拼写错误：不要去追，接着写定义它的切片。先写边，再写 `@final edges`，再写用例。
- 恢复进度或丢了上下文：`status view=next`。按顺序读各行：行首数字是优先级（1 红色草稿或错误，2 BLOCK 空位，3 未定义的 id，4 需求缺口，5 没有期望的用例，6 不可达状态，7 覆盖）；最后一行是维度就绪度 1D-5D。`phase` 取 collect、author、repair、build、review、done 或 needs-user：needs-user 时停下来问用户，绝不编造。
- `index` 用紧凑行列出已有内容（nodes、room:<id>、req:<id>、case:<id>、ids:<前缀>）。`check` 永不写入：mode=slice 检查单个切片，mode=full 检查整个项目（每个红稿是一条 ST-009，连同它们留下的未定义项一起列出：先修红稿）。
