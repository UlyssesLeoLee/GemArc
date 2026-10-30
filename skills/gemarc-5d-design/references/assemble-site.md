# 组装站点（远程流程 A 第 7 步）

`gemarc_render` 返回两样东西：

- `files[]`：随模型变化的 `data/architecture.json`、`data/scenarios.json`、`data/capabilities.json`，每项有 `path`、`bytes`、`sha256` 和（`inline: true` 时）`content`。
- `kit`：固定件（站点外壳 + 共用运行时，static 约 52 个文件，压缩约 170 KB），含 `path`（`/kit/static.zip` 或 `/kit/vite.zip`）、`url`、`archiveSha256`、`run`。

固定件 + `files[]` = 完整站点。服务端什么都没保存。

## 取得 kit 的地址

`kit.url` 在服务端知道自己的公开地址时才有值；本地 stdio 或未配置公开地址时是 `null`。这时用 **MCP 地址去掉末尾的 `/mcp`** 再接上 `kit.path`：

```
MCP 地址  https://<project>.vercel.app/mcp
kit 地址  https://<project>.vercel.app/kit/static.zip
```

## bash（macOS / Linux / Git Bash）

```bash
BASE="https://<project>.vercel.app"            # MCP 地址去掉 /mcp
mkdir -p site/data
curl -fsSL "$BASE/kit/static.zip" -o kit.zip
echo "<kit.archiveSha256>  kit.zip" | sha256sum -c -   # macOS 用: shasum -a 256 -c -
python -m zipfile -e kit.zip site                      # 或 unzip -q kit.zip -d site
# 把 files[] 的每一项按 path 写进 site/（content 原样写入：UTF-8、无 BOM、LF），然后核对：
echo "<files[0].sha256>  site/data/architecture.json" | sha256sum -c -
cd site && python -m http.server 8000 --bind 127.0.0.1   # 即 kit.run；vite 为 npm install && npm run dev
```

如果下载返回 401（或被 302 重定向到登录页）：kit 不要求令牌（`/kit/*` 是公开的），所以这不是 `GEMARC_MCP_TOKEN` 造成的，而是 Vercel 的 Deployment Protection（预览与暂存部署的专属 URL 默认受保护）。
改用生产域名，或按 `docs/09_部署/01_Vercel部署.md` 第 7 节用 `vercel curl` / 绕过头；加 `Authorization` 头没有用。

## PowerShell（Windows PowerShell 5.1 / 7）

```powershell
$base = "https://<project>.vercel.app"          # MCP 地址去掉 /mcp
New-Item -ItemType Directory -Force site\data | Out-Null
Invoke-WebRequest "$base/kit/static.zip" -OutFile kit.zip
(Get-FileHash kit.zip -Algorithm SHA256).Hash.ToLower() -eq "<kit.archiveSha256>"   # 必须为 True
Expand-Archive kit.zip -DestinationPath site -Force

# 写数据文件：必须是 UTF-8 **无 BOM**、LF 换行，并且以一个换行结尾（content 就是这样结尾的）。
# Windows PowerShell 5.1 的 Set-Content -Encoding utf8 会加 BOM，sha256 会对不上。
# here-string 不含最后那个换行；脚本若存成 CRLF，里面的换行也会变成 CRLF，所以写之前统一还原：
$text = @'
<files[0].content 原样粘贴>
'@
$text = $text.Replace("`r`n", "`n") + "`n"
[System.IO.File]::WriteAllText("$PWD\site\data\architecture.json", $text, (New-Object System.Text.UTF8Encoding $false))
(Get-FileHash site\data\architecture.json -Algorithm SHA256).Hash.ToLower() -eq "<files[0].sha256>"

Set-Location site; python -m http.server 8000 --bind 127.0.0.1
```

内容含中文等非 ASCII 字符时：Windows PowerShell 5.1 会把**没有 BOM** 的 UTF-8 `.ps1` 按 ANSI 解码，写出的字节数和 sha256 都会不对。脚本要存成 UTF-8 **带 BOM**，或改用 PowerShell 7。

如果你有自己的文件写入工具（多数 agent 有），直接用它写 `files[].content`，比 shell 转义更稳：内容不要改动、不要缩进（末尾那个换行是内容的一部分，要保留）。

## 核对清单

1. `kit.archiveSha256` 与下载文件一致；
2. 每个 `files[].sha256`、`bytes` 与写出的文件一致（`bytes` 是 UTF-8 字节数）；
3. `inline` 为 `false` 时没有 `content`，**不能**组装，改用本地流程 B 或在本地运行 `gemarc build <project> --profile <profile>`；
4. 打开 `index.html` 前先说明：站点已通过结构与 schema 校验，**没有**做过真实浏览器、WebGL 或性能测试。
