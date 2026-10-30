# 词表（profile software）：id 必须原样使用，区分大小写

- 节点种类：{{ids:kinds}}
- 关系（边的 rel）：{{ids:relations}}
- 验证等级（verif=）：{{ids:verification}}（由强到弱；Implemented 需要源码证据）
- 楼的 trust：{{ids:trust}}；房间 tier：{{ids:tiers}}
- 用例类别：{{ids:caseCategories}}
- 事件类型：{{ids:eventTypes}}
- 干预类型：{{ids:interventionTypes}}；结果类别：{{ids:outcomeClasses}}
- 节点状态（expect 的 nodes=节点=State）：{{ids:nodeStates}}
- 元素种类（use=）：{{ids:elementKinds}}
- 政策种类：{{ids:policyKinds}}；by：ai | user；expect 的 by：author | user

你写的 id：ASCII 字母、数字、`.`、`_`、`-`，例如 `svc.loan`、`EVD-1`。边 id 是派生的：`src:rel:tgt`。
枚举值写错是 ST-003，并列出允许值；状态名区分大小写。
