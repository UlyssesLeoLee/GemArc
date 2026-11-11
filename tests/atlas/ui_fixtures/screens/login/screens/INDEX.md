页 §SCREENS · 原型界面索引 · 1/1 · 共 21 行 · 页类 card
项目 demo · zh-CN · u=9da9bdd5 · 原型界面 · 核对是否过期: gemarc_atlas_index scope="stamp"
# 原型界面索引

> 含: 界面 2 个、状态 5 个、控件 8 个、核对点 36 个、导航 2 条
> 不含: 像素级几何；各屏的控件与导航明细见各屏画面卡
> 读法: 先看本页选屏，再读该屏画面卡取控件表；导航图见 NAV 页。PNG 由 verify 或 build gate=smoke 渲染到 .verify/screens/，实时状态用 gemarc_atlas_index scope="screens"

## 界面（2）
| id | 标题 | route | frame | start | 状态数 | 控件数 | 核对点 | 出/入导航 | SVG 字节 | 画面卡 |
|---|---|---|---|---|---|---|---|---|---|---|
| `SCR-LOGIN` | 登录 | /login | desktop | true | 3 | 5 | 25 | 1/1 | 10508 | [§CARD-SCR-LOGIN](SCR-LOGIN.card.md) |
| `SCR-HOME` | 我的借阅 | /home | desktop |  | 2 | 3 | 11 | 1/1 | 5845 | [§CARD-SCR-HOME](SCR-HOME.card.md) |

## 需求覆盖（1）
| 需求 | 界面 |
|---|---|
| `RQ-1` | SCR-HOME, SCR-LOGIN |

邻页: [START](../START.md)
页尾 §SCREENS · 共 21 行 · sha8 6a2ed861 · 以上为全部内容
