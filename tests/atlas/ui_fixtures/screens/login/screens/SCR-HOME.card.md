页 §CARD-SCR-HOME · 我的借阅 · 1/1 · 共 64 行 · 页类 page
项目 demo · zh-CN · u=9da9bdd5 · 原型界面 · 核对是否过期: gemarc_atlas_index scope="stamp"
# 我的借阅

> 含: 控件 3 个、状态 2 个、出向导航 1 条、核对点 11 个
> 不含: 像素级几何、叠层、动画、响应式断点（几何只是示意）
> 读法: 规范性内容: 种类、文字、字段、状态、跳转、父子与顺序；示意性内容: 位置与尺寸。圆标 = 控件序号 = 表里的 #。PNG 有效当且仅当其状态条里的 ui 值等于本卡的 u=。kind 与 DOM 的对照见 ui-schema.md

屏幕 `SCR-HOME` · 页面 · 桌面 1280x800
用途: 列出会员当前借出的书。
属性: frame=desktop · route=/home · kind=page · start=false · req=RQ-1
核对点 checkpoints=11（控件表每个非空单元格一个，id 是控件 id 加 @ 加列名；另有 @order、@route 和 #状态@state）
判定戳 sha=5d9fe578（`ui-verdict-*` 的 conform 行抄它；本屏的核对点变了它才变）
## 控件（3）
| # | id | kind | label | parent | in | bind | api | emph | goto | 属性 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `SCR-HOME.bar` | header | 借阅台 |  |  |  |  |  |  |  |
| 2 | `SCR-HOME.logout` | link | 退出 | bar |  |  |  |  | SCR-LOGIN |  |
| 3 | `SCR-HOME.loans` | table | 借出的书 |  |  |  |  |  |  |  |

## 顺序（1）
| 容器 | 子控件的顺序 |
|---|---|
| `SCR-HOME.root` | bar,loans |

## 状态（2）
| 状态 | kind | 说明 | 界面文案 | 文件 | 显示控件数 | 未显示 |
|---|---|---|---|---|---|---|
| `SCR-HOME#default` |  |  |  | `SCR-HOME.svg` | 3/3 |  |
| `SCR-HOME#empty` | empty | 没有任何借出的书。 |  | `SCR-HOME~empty.svg` | 3/3 |  |

## 出向导航（1）
| 起点 | 终点 | 文字 | 条件 | 触发 | 链接 |
|---|---|---|---|---|---|
| `SCR-HOME.logout` | `SCR-LOGIN` | 退出登录 |  | tap |  |

## 入向导航（1）
| 起点 | 终点 | 文字 | 触发 |
|---|---|---|---|
| `SCR-LOGIN.submit` | `SCR-HOME` | 凭证正确,进入首页 | submit |

## 渲染器推导，非作者声明（2）
| 推导物 | 控件 | 界面文案 | 状态 |
|---|---|---|---|
| 无示例数据的灰条 | `SCR-HOME.loans` |  | default |
| 空态占位 | `SCR-HOME.loans` | （空） | empty |

## 全部可见文字（6）
| 文字 | 状态 | 来源 |
|---|---|---|
| 退出 | 全部 | SCR-HOME.logout |
| 借出的书 | 全部 | SCR-HOME.loans |
| SCR-HOME · default · desktop 1280x800 · layout 1 · ui 9da9bdd5 · 示意线框 · 显示 3/3 | default | 状态条 |
| （空） | empty | SCR-HOME.loans （渲染器推导） |
| SCR-HOME · empty · desktop 1280x800 · layout 1 · ui 9da9bdd5 · 示意线框 · 显示 3/3 | empty | 状态条 |
| 没有任何借出的书。 | empty | 状态说明 |

## 示例数据（1）
| 控件 | 列 | 行数 | 画出行数 | 说明 |
|---|---|---|---|---|
| `SCR-HOME.loans` |  | 0 |  | 未提供示例数据 |

邻页: [索引](INDEX.md) · [START](../START.md)
页尾 §CARD-SCR-HOME · 共 64 行 · sha8 079b108f · 以上为全部内容
