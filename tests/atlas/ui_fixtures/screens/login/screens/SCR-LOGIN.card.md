页 §CARD-SCR-LOGIN · 登录 · 1/1 · 共 65 行 · 页类 page
项目 demo · zh-CN · u=9da9bdd5 · 原型界面 · 核对是否过期: gemarc_atlas_index scope="stamp"
# 登录

> 含: 控件 5 个、状态 3 个、出向导航 1 条、核对点 25 个
> 不含: 像素级几何、叠层、动画、响应式断点（几何只是示意）
> 读法: 规范性内容: 种类、文字、字段、状态、跳转、父子与顺序；示意性内容: 位置与尺寸。圆标 = 控件序号 = 表里的 #。PNG 有效当且仅当其状态条里的 ui 值等于本卡的 u=。kind 与 DOM 的对照见 ui-schema.md

屏幕 `SCR-LOGIN` · 页面 · 桌面 1280x800
用途: 会员用邮箱和密码进入借阅台。
属性: frame=desktop · route=/login · kind=page · start=true · actor=usr.member · req=RQ-1
核对点 checkpoints=25（控件表每个非空单元格一个，id 是控件 id 加 @ 加列名；另有 @order、@route 和 #状态@state）
判定戳 sha=36523f9f（`ui-verdict-*` 的 conform 行抄它；本屏的核对点变了它才变）
## 控件（5）
| # | id | kind | label | parent | in | bind | api | emph | goto | 属性 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `SCR-LOGIN.form` | form | 登录表单 |  |  |  |  |  |  |  |
| 2 | `SCR-LOGIN.email` | input | 邮箱 | form |  | email |  |  |  |  |
| 3 | `SCR-LOGIN.pwd` | input | 密码 | form |  | password |  |  |  |  |
| 4 | `SCR-LOGIN.err` | text | 邮箱或密码不正确 | form | error |  |  | danger |  |  |
| 5 | `SCR-LOGIN.submit` | button | 登录 | form |  |  | svc.loan | primary | SCR-HOME | case=CS-BORROW-OK rule=R-BORROW |

## 顺序（1）
| 容器 | 子控件的顺序 |
|---|---|
| `SCR-LOGIN.form` | email,pwd,err,submit |

## 状态（3）
| 状态 | kind | 说明 | 界面文案 | 文件 | 显示控件数 | 未显示 |
|---|---|---|---|---|---|---|
| `SCR-LOGIN#default` |  |  |  | `SCR-LOGIN.svg` | 4/5 | SCR-LOGIN.err |
| `SCR-LOGIN#loading` | loading | 提交后等待服务器响应。 |  | `SCR-LOGIN~loading.svg` | 4/5 | SCR-LOGIN.err |
| `SCR-LOGIN#error` | error | 邮箱或密码不正确。 |  | `SCR-LOGIN~error.svg` | 5/5 |  |

## 出向导航（1）
| 起点 | 终点 | 文字 | 条件 | 触发 | 链接 |
|---|---|---|---|---|---|
| `SCR-LOGIN.submit` | `SCR-HOME` | 凭证正确,进入首页 | 凭证正确 | submit | case=CS-BORROW-OK rule=R-BORROW |

## 入向导航（1）
| 起点 | 终点 | 文字 | 触发 |
|---|---|---|---|
| `SCR-HOME.logout` | `SCR-LOGIN` | 退出登录 | tap |

## 渲染器推导，非作者声明（0）
（无）

## 全部可见文字（9）
| 文字 | 状态 | 来源 |
|---|---|---|
| 邮箱 | 全部 | SCR-LOGIN.email |
| 密码 | 全部 | SCR-LOGIN.pwd |
| 登录 | 全部 | SCR-LOGIN.submit |
| SCR-LOGIN · default · desktop 1280x800 · layout 1 · ui 9da9bdd5 · 示意线框 · 显示 4/5 | default | 状态条 |
| SCR-LOGIN · loading · desktop 1280x800 · layout 1 · ui 9da9bdd5 · 示意线框 · 显示 4/5 | loading | 状态条 |
| 提交后等待服务器响应。 | loading | 状态说明 |
| 邮箱或密码不正确 | error | SCR-LOGIN.err |
| SCR-LOGIN · error · desktop 1280x800 · layout 1 · ui 9da9bdd5 · 示意线框 · 显示 5/5 | error | 状态条 |
| 邮箱或密码不正确。 | error | 状态说明 |

## 示例数据（0）
（无）

邻页: [索引](INDEX.md) · [START](../START.md)
页尾 §CARD-SCR-LOGIN · 共 65 行 · sha8 cd6d86f4 · 以上为全部内容
