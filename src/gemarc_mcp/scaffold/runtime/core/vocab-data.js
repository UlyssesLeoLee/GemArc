/**
 * GENERATED FILE - DO NOT EDIT.
 *
 * Source: src/gemarc_mcp/scaffold/vocab/*.json
 * Digest: 10b92f6520eeb7fdf7793a9891c1edd300d9e1e9ba8773a981d75cd8cf12b935
 * Regenerate: python -m gemarc_mcp.scaffold.sync
 *
 * The vocabulary is the single source of truth shared by the prompt, the
 * Python validator and this runtime. A drift test asserts that this file
 * still matches the JSON sources.
 */
export const VOCAB_DIGEST = '10b92f6520eeb7fdf7793a9891c1edd300d9e1e9ba8773a981d75cd8cf12b935';

export const VOCAB = Object.freeze({
  "case-categories": {
    "categories": [
      {
        "assertion": "预期路径成立",
        "id": "normal",
        "label": "正常",
        "requiresIntervention": false,
        "specType": "Normal"
      },
      {
        "assertion": "预期失败或降级被显式观测到",
        "id": "abnormal",
        "label": "异常",
        "requiresIntervention": true,
        "specType": null,
        "umbrellaFor": [
          "Error",
          "Timeout",
          "Retry",
          "Compensation",
          "PartialFailure",
          "CascadingFailure",
          "Security"
        ]
      },
      {
        "assertion": "极限输入或极限规模下的行为被确定",
        "id": "boundary",
        "label": "边界",
        "requiresIntervention": true,
        "specType": "Boundary"
      },
      {
        "assertion": "多个条件同时成立时的交互被确定",
        "id": "combination",
        "label": "组合",
        "requiresIntervention": true,
        "specType": "Combined"
      },
      {
        "assertion": "故障后恢复到已知良好状态的过程被确定",
        "id": "recovery",
        "label": "恢复",
        "requiresIntervention": true,
        "specType": "Recovery"
      }
    ],
    "description": "统一管理正常、异常、边界、组合及恢复 Case。Case 与 Scenario 的关系是：Case 是一个可执行的实例（scenarioId + 干预配置 + 期望），Scenario 是规则与事件的定义。本词表是 Case 的分诊轴（5 类）；v5.0 提示词 §13 的 14 种分析类型登记在 case-types 词表，由 cases[].caseType 承载。两个字段互补：category 说“这类 Case 属于哪一档”，caseType 说“这次分析属于哪一种分析类型”。",
    "fields": {
      "caseType": "case-types 词表中的分析类型 ID（v5.0 提示词 §13 case_types）",
      "category": "本词表中的分类 ID",
      "disabledNodeIds": "本次 Case 的干预集合（模块屏蔽）",
      "evidenceClass": "沿用 evidence-levels 词表；Hypothetical 的 Case 不得被描述为实际运行结果",
      "expected": {
        "blocked": "预期被阻断事件数",
        "delivered": "预期送达事件数",
        "notes": "文字判定说明"
      },
      "id": "稳定 ID，命名规则 <scenarioId>__<caseId>",
      "reality": "§13 要求的 Case 现实标记：Observed / ImplementedModel / DesignedModel / Hypothetical",
      "scenarioId": "指向 definitions.scenarios 中的场景 ID",
      "title": "人类可读标题"
    },
    "id": "case-categories",
    "rules": [
      "每个 Case 必须绑定一个 scenarioId，且该 scenario 必须存在于模型中。",
      "requiresIntervention 为 true 的分类必须给出非空 disabledNodeIds 或显式说明无干预原因。",
      "Case 库与 4D/5D 视图共享同一 scenarioId 引用，切换视图不得改变 Case 语义。",
      "specType 登记本分类默认对应的 §13 分析类型；abnormal 是伞形分类，本身不对应单一 specType。",
      "§13 要求每个 Case 标记 Observed / ImplementedModel / DesignedModel / Hypothetical：缺证据的 Case 不得被描述为真实运行事实。",
      "组合 Case 必须先检查参数、前置条件、状态与时间约束；矛盾时拒绝执行并说明原因。"
    ],
    "schemaVersion": "0.2.0",
    "specCaseContract": {
      "fields": [
        "caseId",
        "name",
        "description",
        "category",
        "initialState",
        "inputEvents",
        "preconditions",
        "executionRules",
        "expectedOutcomes",
        "terminationConditions",
        "assertions",
        "evidenceIds",
        "assumptions"
      ],
      "note": "v5.0 提示词 §13 case_contract。全部字段可选，但缺失项必须在界面上标注为缺失而不是伪造。"
    },
    "title": "Scenario & Causal Lab 用例分类（分诊轴）"
  },
  "case-types": {
    "animationPrimitives": {
      "ids": [
        "Propagation",
        "Branching",
        "Parallel",
        "Waiting",
        "Timeout",
        "Retry",
        "Blocking",
        "FailurePropagation",
        "Recovery",
        "Compensation",
        "FeedbackLoop",
        "Convergence",
        "StateTransition",
        "DataTransformation",
        "Escalation",
        "Cancellation",
        "Rollback",
        "OutcomeComparison"
      ],
      "note": "§14 animation_primitives。原语只消费计算轨迹，不得反过来创造业务事件或因果关系。",
      "terminalStates": [
        "Completed",
        "Failed"
      ]
    },
    "compositionModes": {
      "ids": [
        "Sequential",
        "Parallel",
        "Conditional",
        "Nested",
        "Repeated",
        "Interrupted",
        "ReversibleVisualReplay",
        "Synchronized",
        "Branched",
        "Converged"
      ],
      "note": "§14 composition_modes：动画组合方式。组合方式只决定呈现，不得改变计算轨迹的因果顺序。"
    },
    "description": "v6.0 提示词 §13：第五维必须以正常、异常、边界、并发与组合 Case 为基本分析单位，不局限于固定的 A/B 预制动画。本词表登记 §13 列出的全部 14 种分析类型。cases[].category 是分诊轴（见 case-categories 词表），cases[].caseType 是本词表的分析类型：两个字段互补，不互相替代。§14 的动画原语、终态、组合方式与示例组合也登记在本词表内。",
    "id": "case-types",
    "illustrativeCompositions": {
      "entries": [
        {
          "caseType": "Normal",
          "sequence": [
            "Propagation",
            "StateTransition",
            "Parallel",
            "Convergence",
            "Completed"
          ],
          "terminalState": "Completed"
        },
        {
          "caseType": "Timeout",
          "sequence": [
            "Propagation",
            "Waiting",
            "Timeout",
            "Retry",
            "Failed"
          ],
          "terminalState": "Failed"
        },
        {
          "caseType": "Recovery",
          "sequence": [
            "Propagation",
            "FailurePropagation",
            "Retry",
            "Recovery",
            "Completed"
          ],
          "terminalState": "Completed"
        },
        {
          "caseType": "CascadingFailure",
          "sequence": [
            "FailurePropagation",
            "Propagation",
            "Blocking",
            "Escalation",
            "OutcomeComparison"
          ],
          "terminalState": null
        },
        {
          "caseType": "Compensation",
          "sequence": [
            "Propagation",
            "PartialFailure",
            "Compensation",
            "Recovery",
            "OutcomeComparison"
          ],
          "terminalState": null
        }
      ],
      "note": "§14 illustrative_compositions。规格明确标注这些只是动画语法示例，不属于目标仓库自动存在的真实事件轨迹，必须由实际 Case 计算生成。示例里既可能出现原语，也可能出现终态或 Case 类型名，因此本表逐项记录原样，不做归并。"
    },
    "reality": {
      "note": "§13 要求每个 Case 标记 Observed / ImplementedModel / DesignedModel / Hypothetical。缺证据的 Case 不得被描述为项目的真实运行事实。",
      "values": [
        "Observed",
        "ImplementedModel",
        "DesignedModel",
        "Hypothetical"
      ]
    },
    "rules": [
      "cases[].caseType 取自本词表；cases[].category 取自 case-categories 词表。",
      "requiresIntervention 为 true 的类型必须给出非空 disabledNodeIds 或显式说明无干预原因。",
      "组合 Case 必须先检查参数、前置条件、状态与时间约束；矛盾时拒绝执行并说明原因。",
      "缺少足够规则时返回 Unknown 或 Unsupported，而不是猜测结果。",
      "primitives 只是可选的动画语法提示，不得当作该 Case 实际发生过的轨迹。"
    ],
    "schemaVersion": "0.2.0",
    "title": "5D Case 分析类型（v6.0 §13 case_types / §14 动画语法）",
    "types": [
      {
        "assertion": "正常业务执行",
        "category": "normal",
        "id": "Normal",
        "label": "正常",
        "primitives": [
          "Propagation",
          "StateTransition",
          "Parallel",
          "Convergence"
        ],
        "requiresIntervention": false
      },
      {
        "assertion": "边界值与极限条件",
        "category": "boundary",
        "id": "Boundary",
        "label": "边界",
        "primitives": [
          "Propagation",
          "Blocking",
          "OutcomeComparison"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "输入、处理或业务规则错误",
        "category": "abnormal",
        "id": "Error",
        "label": "错误",
        "primitives": [
          "Propagation",
          "Blocking",
          "FailurePropagation",
          "Recovery"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "超时与延迟",
        "category": "abnormal",
        "id": "Timeout",
        "label": "超时",
        "primitives": [
          "Propagation",
          "Waiting",
          "Timeout",
          "Retry"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "重试与退避策略",
        "category": "abnormal",
        "id": "Retry",
        "label": "重试",
        "primitives": [
          "Propagation",
          "Waiting",
          "Retry"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "故障恢复",
        "category": "recovery",
        "id": "Recovery",
        "label": "恢复",
        "primitives": [
          "Propagation",
          "FailurePropagation",
          "Retry",
          "Recovery"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "事务补偿或纠正动作",
        "category": "abnormal",
        "id": "Compensation",
        "label": "补偿",
        "primitives": [
          "Propagation",
          "Compensation",
          "Recovery",
          "OutcomeComparison"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "并发任务/事件",
        "category": "combination",
        "id": "Concurrency",
        "label": "并发",
        "primitives": [
          "Propagation",
          "Parallel",
          "Waiting",
          "Convergence"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "不同执行顺序竞争",
        "category": "combination",
        "id": "RaceCondition",
        "label": "竞争条件",
        "primitives": [
          "Propagation",
          "Parallel",
          "Branching",
          "OutcomeComparison"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "资源竞争",
        "category": "boundary",
        "id": "ResourceContention",
        "label": "资源竞争",
        "primitives": [
          "Propagation",
          "Waiting",
          "Blocking",
          "Escalation"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "局部模块故障",
        "category": "abnormal",
        "id": "PartialFailure",
        "label": "局部故障",
        "primitives": [
          "Propagation",
          "FailurePropagation",
          "Compensation",
          "OutcomeComparison"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "故障级联",
        "category": "abnormal",
        "id": "CascadingFailure",
        "label": "级联故障",
        "primitives": [
          "FailurePropagation",
          "Propagation",
          "Blocking",
          "Escalation",
          "OutcomeComparison"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "身份、权限和安全边界",
        "category": "abnormal",
        "id": "Security",
        "label": "安全",
        "primitives": [
          "Propagation",
          "Blocking",
          "Escalation",
          "OutcomeComparison"
        ],
        "requiresIntervention": true
      },
      {
        "assertion": "多种条件和异常组合",
        "category": "combination",
        "id": "Combined",
        "label": "组合",
        "primitives": [
          "Propagation",
          "Branching",
          "Parallel",
          "Convergence",
          "OutcomeComparison"
        ],
        "requiresIntervention": true
      }
    ]
  },
  "diagram-types": {
    "description": "v5.0 提示词 §8.1 要求按真实项目证据生成的图示。不得为了凑数量而生成虚假图示——只有具备证据的图示才允许出现在 view 列表中。",
    "id": "diagram-types",
    "rules": [
      "每个 view 必须声明 diagramType，且该类型必须在本词表中登记。",
      "view 的 nodeIds 只能包含该类型 nodeKinds 允许的实体；edgeKinds 同理。",
      "fidelity 列出的元素若在模型中不存在对应信息，必须在界面上标注为缺失，不得伪造。",
      "不得为了凑数量而生成虚假图示：没有证据支撑的图示类型不允许出现在 view 列表中。",
      "specFamilies 登记 v5.0 提示词 §9 supported_diagrams 的家族归属；一个类型只能属于一个家族。",
      "§9 规定 Data 家族含 DataDependency、Architecture 家族含 EventFlow：这两种类型不可省略。"
    ],
    "schemaVersion": "0.2.0",
    "specFamilies": {
      "Architecture": [
        "logical-view",
        "development-view",
        "process-view",
        "deployment-view",
        "data-view",
        "security-view",
        "operations-view",
        "dependency-diagram",
        "event-flow",
        "causal-view"
      ],
      "Data": [
        "er-diagram",
        "dfd",
        "data-dependency"
      ],
      "UML": [
        "uml-class",
        "uml-component",
        "uml-package",
        "uml-sequence",
        "uml-state-machine",
        "uml-activity",
        "uml-deployment",
        "uml-use-case"
      ]
    },
    "title": "图示类型与保真要求",
    "types": [
      {
        "dimension": "2D",
        "edgeKinds": [
          "inherits",
          "implements",
          "composes",
          "aggregates",
          "dependsOn"
        ],
        "fidelity": [
          "attributes",
          "methods",
          "visibility",
          "multiplicity"
        ],
        "id": "uml-class",
        "label": "UML 类图",
        "nodeKinds": [
          "Class",
          "Interface",
          "Entity",
          "Table",
          "Relationship"
        ],
        "notation": "UML"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "dependsOn",
          "implements",
          "contains",
          "publishes",
          "subscribes"
        ],
        "fidelity": [
          "ports",
          "interfaces"
        ],
        "id": "uml-component",
        "label": "UML 组件图",
        "nodeKinds": [
          "Component",
          "Service",
          "Module",
          "Subsystem"
        ],
        "notation": "UML"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "contains",
          "dependsOn"
        ],
        "fidelity": [
          "nesting"
        ],
        "id": "uml-package",
        "label": "UML 包图",
        "nodeKinds": [
          "Module",
          "Subsystem",
          "System"
        ],
        "notation": "UML"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "calls",
          "sends",
          "receives"
        ],
        "fidelity": [
          "lifeline",
          "message",
          "activation",
          "guard"
        ],
        "id": "uml-sequence",
        "label": "UML 时序图",
        "nodeKinds": [
          "Actor",
          "Service",
          "Class",
          "Component",
          "Module"
        ],
        "notation": "UML"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "transitions",
          "triggers"
        ],
        "fidelity": [
          "initial",
          "final",
          "guard",
          "state"
        ],
        "id": "uml-state-machine",
        "label": "UML 状态机图",
        "nodeKinds": [
          "State",
          "Transition",
          "Event",
          "Class"
        ],
        "notation": "UML"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "triggers",
          "dependsOn",
          "contains"
        ],
        "fidelity": [
          "decision",
          "merge",
          "fork",
          "join",
          "guard"
        ],
        "id": "uml-activity",
        "label": "UML 活动图",
        "nodeKinds": [
          "Event",
          "Scenario",
          "Constraint",
          "Actor"
        ],
        "notation": "UML"
      },
      {
        "dimension": "3D",
        "edgeKinds": [
          "contains",
          "publishes",
          "subscribes",
          "dependsOn"
        ],
        "fidelity": [
          "node",
          "executionEnvironment",
          "artifact"
        ],
        "id": "uml-deployment",
        "label": "UML 部署图",
        "nodeKinds": [
          "System",
          "Subsystem",
          "Service",
          "Component"
        ],
        "notation": "UML"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "calls",
          "dependsOn"
        ],
        "fidelity": [
          "systemBoundary",
          "include",
          "extend"
        ],
        "id": "uml-use-case",
        "label": "UML 用例图",
        "nodeKinds": [
          "Actor",
          "Service",
          "Module"
        ],
        "notation": "UML"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "composes",
          "owns",
          "dependsOn"
        ],
        "fidelity": [
          "cardinality",
          "primaryKey",
          "foreignKey",
          "attribute"
        ],
        "id": "er-diagram",
        "label": "ER 图",
        "nodeKinds": [
          "Entity",
          "Table",
          "Attribute",
          "Relationship"
        ],
        "notation": "ER"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "reads",
          "writes",
          "sends",
          "receives"
        ],
        "fidelity": [
          "externalEntity",
          "dataStore",
          "process"
        ],
        "id": "dfd",
        "label": "数据流图",
        "nodeKinds": [
          "Process",
          "Service",
          "Module",
          "Entity"
        ],
        "notation": "DFD"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "dependsOn",
          "imports"
        ],
        "fidelity": [],
        "id": "dependency-diagram",
        "label": "依赖图",
        "nodeKinds": [
          "Module",
          "Component",
          "Service",
          "Class"
        ],
        "notation": "generic"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "contains",
          "dependsOn",
          "calls"
        ],
        "fidelity": [],
        "id": "logical-view",
        "label": "逻辑视图",
        "nodeKinds": [
          "System",
          "Subsystem",
          "Module",
          "Service",
          "Entity"
        ],
        "notation": "generic"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "dependsOn",
          "contains",
          "inherits"
        ],
        "fidelity": [],
        "id": "development-view",
        "label": "开发视图",
        "nodeKinds": [
          "Module",
          "Package",
          "Class",
          "Component"
        ],
        "notation": "generic"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "calls",
          "sends",
          "receives",
          "publishes",
          "subscribes"
        ],
        "fidelity": [],
        "id": "process-view",
        "label": "进程视图",
        "nodeKinds": [
          "Service",
          "Component",
          "Module",
          "Actor"
        ],
        "notation": "generic"
      },
      {
        "dimension": "3D",
        "edgeKinds": [
          "contains",
          "publishes",
          "subscribes",
          "sends",
          "receives"
        ],
        "fidelity": [
          "building",
          "floor",
          "room"
        ],
        "id": "deployment-view",
        "label": "部署视图",
        "nodeKinds": [
          "System",
          "Subsystem",
          "Service",
          "Component",
          "Module"
        ],
        "notation": "generic"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "reads",
          "writes",
          "owns",
          "composes"
        ],
        "fidelity": [],
        "id": "data-view",
        "label": "数据视图",
        "nodeKinds": [
          "Entity",
          "Table",
          "Attribute",
          "Service"
        ],
        "notation": "generic"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "dependsOn",
          "calls",
          "sends",
          "receives",
          "contains"
        ],
        "fidelity": [
          "trustBoundary",
          "principal"
        ],
        "id": "security-view",
        "label": "安全视图",
        "nodeKinds": [
          "System",
          "Subsystem",
          "Service",
          "Component",
          "Actor"
        ],
        "notation": "generic"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "dependsOn",
          "publishes",
          "subscribes"
        ],
        "fidelity": [
          "slo",
          "healthCheck"
        ],
        "id": "operations-view",
        "label": "运维视图",
        "nodeKinds": [
          "Service",
          "Component",
          "Service",
          "Module"
        ],
        "notation": "generic"
      },
      {
        "dimension": "4D",
        "edgeKinds": [
          "triggers",
          "contains",
          "dependsOn"
        ],
        "fidelity": [
          "counterfactual"
        ],
        "id": "causal-view",
        "label": "因果视图",
        "nodeKinds": [
          "Scenario",
          "CausalRule",
          "Event",
          "Service",
          "Module",
          "State"
        ],
        "notation": "generic"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "dependsOn",
          "imports",
          "reads",
          "writes",
          "owns",
          "composes",
          "derivesFrom"
        ],
        "fidelity": [
          "foreignKey",
          "primaryKey",
          "column",
          "dataType"
        ],
        "id": "data-dependency",
        "label": "数据依赖图",
        "nodeKinds": [
          "Table",
          "Entity",
          "Attribute",
          "Module",
          "Service",
          "Class"
        ],
        "notation": "generic"
      },
      {
        "dimension": "2D",
        "edgeKinds": [
          "sends",
          "receives",
          "publishes",
          "subscribes",
          "triggers",
          "calls"
        ],
        "fidelity": [
          "eventPayload",
          "deliveryGuarantee",
          "ordering"
        ],
        "id": "event-flow",
        "label": "事件流图",
        "nodeKinds": [
          "Service",
          "Module",
          "Component",
          "Event",
          "Actor",
          "System",
          "Subsystem"
        ],
        "notation": "generic"
      }
    ]
  },
  "edge-types": {
    "description": "Canonical Model 中边的类型清单。category 决定可视化语义；directed 决定是否可逆；trustBoundary 标记跨信任域关系。",
    "id": "edge-types",
    "rules": [
      "多条不同关系不得被错误合并：同一对节点之间不同 type 的边必须共存且各自可拾取。",
      "trustBoundary 为 true 的边必须跨信任域显式标注，渲染时需要区别样式。",
      "condition 与 weight 只有在 type 允许时才可非空。",
      "未登记的 type 必须被拒绝，不得静默接受。"
    ],
    "schemaVersion": "0.2.0",
    "title": "关系类型词表",
    "types": [
      {
        "category": "structure",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "contains",
        "label": "包含",
        "trustBoundary": false
      },
      {
        "category": "structure",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "belongsTo",
        "label": "归属",
        "trustBoundary": false
      },
      {
        "category": "structure",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "composes",
        "label": "组合",
        "trustBoundary": false
      },
      {
        "category": "structure",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "aggregates",
        "label": "聚合",
        "trustBoundary": false
      },
      {
        "category": "structure",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "dependsOn",
        "label": "依赖",
        "trustBoundary": false
      },
      {
        "category": "behavior",
        "conditionAllowed": true,
        "defaultWeight": null,
        "directed": true,
        "id": "calls",
        "label": "调用",
        "trustBoundary": false
      },
      {
        "category": "behavior",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "implements",
        "label": "实现",
        "trustBoundary": false
      },
      {
        "category": "behavior",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "inherits",
        "label": "继承",
        "trustBoundary": false
      },
      {
        "category": "behavior",
        "conditionAllowed": true,
        "defaultWeight": null,
        "directed": true,
        "id": "transitions",
        "label": "状态迁移",
        "trustBoundary": false
      },
      {
        "category": "behavior",
        "conditionAllowed": true,
        "defaultWeight": null,
        "directed": true,
        "id": "triggers",
        "label": "触发",
        "trustBoundary": false
      },
      {
        "category": "data",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "reads",
        "label": "读取",
        "trustBoundary": false
      },
      {
        "category": "data",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "writes",
        "label": "写入",
        "trustBoundary": false
      },
      {
        "category": "data",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "owns",
        "label": "拥有数据",
        "trustBoundary": false
      },
      {
        "category": "flow",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "publishes",
        "label": "发布",
        "trustBoundary": true
      },
      {
        "category": "flow",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "subscribes",
        "label": "订阅",
        "trustBoundary": true
      },
      {
        "category": "flow",
        "conditionAllowed": true,
        "defaultWeight": null,
        "directed": true,
        "id": "sends",
        "label": "发送",
        "trustBoundary": true
      },
      {
        "category": "flow",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "receives",
        "label": "接收",
        "trustBoundary": true
      },
      {
        "category": "constraint",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "constrains",
        "label": "约束",
        "trustBoundary": false
      },
      {
        "category": "evidence",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "evidences",
        "label": "佐证",
        "trustBoundary": false
      },
      {
        "category": "evidence",
        "conditionAllowed": false,
        "defaultWeight": null,
        "directed": true,
        "id": "derivesFrom",
        "label": "派生自",
        "trustBoundary": false
      }
    ]
  },
  "entity-kinds": {
    "description": "v5.0 提示词 §3.3 要求的 21 种实体类型。实际项目不存在的实体类型不必生成。",
    "id": "entity-kinds",
    "kinds": [
      {
        "graphRole": "node",
        "id": "System",
        "label": "系统",
        "note": "顶层系统边界",
        "spaceLevel": "Building",
        "uml": [
          "Package"
        ]
      },
      {
        "graphRole": "node",
        "id": "Subsystem",
        "label": "子系统",
        "spaceLevel": "Building",
        "uml": [
          "Package",
          "Component"
        ]
      },
      {
        "graphRole": "node",
        "id": "Module",
        "label": "模块",
        "spaceLevel": "Room",
        "uml": [
          "Package"
        ]
      },
      {
        "graphRole": "node",
        "id": "Component",
        "label": "组件",
        "spaceLevel": "Room",
        "uml": [
          "Component"
        ]
      },
      {
        "graphRole": "node",
        "id": "Service",
        "label": "服务",
        "spaceLevel": "Room",
        "uml": [
          "Component"
        ]
      },
      {
        "graphRole": "node",
        "id": "Class",
        "label": "类",
        "spaceLevel": "Room",
        "uml": [
          "Class"
        ]
      },
      {
        "graphRole": "node",
        "id": "Interface",
        "label": "接口",
        "spaceLevel": "Room",
        "uml": [
          "Class"
        ]
      },
      {
        "graphRole": "node",
        "id": "Method",
        "label": "方法",
        "note": "方法作为节点时需绑定宿主 Class",
        "spaceLevel": "Room",
        "uml": [
          "Class"
        ]
      },
      {
        "graphRole": "node",
        "id": "Entity",
        "label": "实体",
        "note": "领域实体",
        "spaceLevel": "Room",
        "uml": [
          "Class"
        ]
      },
      {
        "graphRole": "node",
        "id": "Table",
        "label": "表",
        "spaceLevel": "Room",
        "uml": [
          "Class"
        ]
      },
      {
        "graphRole": "node",
        "id": "Attribute",
        "label": "属性",
        "note": "字段/列，作为节点时需绑定宿主",
        "spaceLevel": null,
        "uml": [
          "Class"
        ]
      },
      {
        "graphRole": "node",
        "id": "Actor",
        "label": "参与者",
        "spaceLevel": "Room",
        "uml": [
          "UseCase"
        ]
      },
      {
        "graphRole": "node",
        "id": "Event",
        "label": "事件",
        "spaceLevel": "Room",
        "uml": [
          "Activity"
        ]
      },
      {
        "graphRole": "node",
        "id": "State",
        "label": "状态",
        "spaceLevel": "Room",
        "uml": [
          "StateMachine"
        ]
      },
      {
        "graphRole": "node",
        "id": "Transition",
        "label": "迁移",
        "spaceLevel": "Room",
        "uml": [
          "StateMachine"
        ]
      },
      {
        "graphRole": "node",
        "id": "Constraint",
        "label": "约束",
        "note": "业务/非功能约束",
        "spaceLevel": "Room",
        "uml": [
          "Activity"
        ]
      },
      {
        "graphRole": "node",
        "id": "Relationship",
        "label": "关系实体",
        "note": "需要独立身份的领域关系",
        "spaceLevel": "Room",
        "uml": [
          "Class"
        ]
      },
      {
        "graphRole": "node",
        "id": "Evidence",
        "label": "证据",
        "note": "证据实体，被节点与边通过 evidenceIds 引用",
        "spaceLevel": null,
        "uml": []
      },
      {
        "graphRole": "node",
        "id": "Scenario",
        "label": "场景",
        "spaceLevel": "Room",
        "uml": [
          "Activity"
        ]
      },
      {
        "graphRole": "node",
        "id": "CausalRule",
        "label": "因果规则",
        "spaceLevel": "Room",
        "uml": [
          "Activity"
        ]
      }
    ],
    "rules": [
      "每个对象必须使用稳定 ID。",
      "每条关系必须保存明确来源。",
      "实际项目不存在的实体类型不必生成。",
      "spaceLevel 为 null 的类型不参与 Building–Floor–Room 空间布局。"
    ],
    "schemaVersion": "0.2.0",
    "title": "CanonicalArchitectureModel 实体类型词表"
  },
  "evidence-levels": {
    "canonical": [
      {
        "assertion": "可作为已实现功能陈述",
        "id": "Implemented",
        "label": "已实现",
        "meaning": "代码确认存在",
        "rank": 6,
        "requiresEvidence": true
      },
      {
        "assertion": "可作为已发生行为陈述",
        "id": "Observed",
        "label": "已观测",
        "meaning": "执行或测试已观测",
        "rank": 5,
        "requiresEvidence": true
      },
      {
        "assertion": "只能陈述为设计意图，不得表述为已实现",
        "id": "Designed",
        "label": "已设计",
        "meaning": "设计明确描述",
        "rank": 4,
        "requiresEvidence": true
      },
      {
        "assertion": "必须同时给出推断依据",
        "id": "Inferred",
        "label": "已推断",
        "meaning": "有依据的推断",
        "rank": 3,
        "requiresEvidence": true
      },
      {
        "assertion": "不得表述为真实生产行为",
        "id": "Hypothetical",
        "label": "假设",
        "meaning": "明确的模拟假设",
        "rank": 2,
        "requiresEvidence": false
      },
      {
        "assertion": "必须在界面上显式标注为未知",
        "id": "Unknown",
        "label": "未知",
        "meaning": "证据不足",
        "rank": 1,
        "requiresEvidence": false
      }
    ],
    "description": "v5.0 提示词 §3.2 定义的 6 级事实等级。canonical 为权威值；legacyAliases 用于兼容 0.1.x 模型的 confirmed/assumed/illustrative/unknown。",
    "id": "evidence-levels",
    "legacyAliases": {
      "assumed": "Inferred",
      "confirmed": "Implemented",
      "designed": "Designed",
      "hypothetical": "Hypothetical",
      "illustrative": "Hypothetical",
      "inferred": "Inferred",
      "observed": "Observed",
      "unknown": "Unknown"
    },
    "rules": [
      "Implemented 不自动等于 Observed：代码存在只证明已实现，不证明运行时行为。",
      "不能将 Designed 当作已经实现。",
      "不能将 Hypothetical 当作真实生产行为。",
      "strict 校验要求节点与边的等级 rank >= 5（Implemented 或 Observed）且存在证据引用。"
    ],
    "schemaVersion": "0.2.0",
    "title": "事实等级词表"
  },
  "relation-classes": {
    "classes": [
      {
        "id": "Selected",
        "label": "已选中",
        "rank": 4,
        "rule": "当前焦点对象本身",
        "visual": {
          "labels": "full",
          "lineWeight": "normal",
          "opacity": 1.0,
          "outline": "strong",
          "zOffset": "front"
        }
      },
      {
        "id": "Directly Related",
        "label": "直接相关",
        "rank": 3,
        "rule": "与焦点存在直接边、路径成员、同房间归属，或被事件/因果规则直接触达",
        "visual": {
          "labels": "full",
          "lineWeight": "normal",
          "opacity": 1.0,
          "outline": "normal",
          "zOffset": "none"
        }
      },
      {
        "id": "Contextually Related",
        "label": "上下文相关",
        "rank": 2,
        "rule": "在限定跳数内可达但不直接相连；或属于当前 Case / 场景作用域",
        "visual": {
          "labels": "collapsed",
          "lineWeight": "thin",
          "opacity": 0.45,
          "outline": "weak",
          "zOffset": "none"
        }
      },
      {
        "id": "Unrelated",
        "label": "无关",
        "rank": 1,
        "rule": "不满足以上任何判定",
        "visual": {
          "labels": "hidden",
          "lineWeight": "hairline",
          "opacity": 0.12,
          "outline": "none",
          "zOffset": "back"
        }
      }
    ],
    "description": "v5.0 提示词 §7.2 定义的 4 个相关性等级。相关性依赖图连通性、边类型、路径归属、空间归属、事件传播、因果子图、工作流依赖与当前 Case。禁止仅根据三维空间距离推断业务相关性。",
    "id": "relation-classes",
    "invariants": [
      "退避属于可逆的展示变换，不得修改真实架构坐标、事实关系或工作流执行语义。",
      "Selected 与 Directly Related 不得被折叠或隐藏标签。",
      "恢复后不得残留错误透明度或位置。",
      "快速连续点击必须可以取消尚未完成的过渡动画。"
    ],
    "schemaVersion": "0.2.0",
    "title": "Focus–Context 相关性分级"
  },
  "space-levels": {
    "crossSpaceRelations": [
      "same-room",
      "cross-room",
      "cross-floor",
      "cross-building"
    ],
    "description": "v5.0 提示词 §6 的三维工程空间语义。Building/Floor/Room 可以是阅读用的虚拟空间组织，但必须保存其语义来源；虚拟房间不得冒充真实代码模块或物理部署设施。",
    "id": "space-levels",
    "levels": [
      {
        "depth": 0,
        "id": "Building",
        "label": "楼宇",
        "note": "顶层空间容器",
        "represents": [
          "系统",
          "子系统",
          "部署域",
          "应用集群",
          "信任边界",
          "Bounded Context 群组"
        ],
        "semanticSourceField": "building.semanticSource"
      },
      {
        "depth": 1,
        "id": "Floor",
        "label": "楼层",
        "note": "中间层容器",
        "represents": [
          "架构层",
          "运行层",
          "接入层",
          "应用层",
          "领域层",
          "数据层",
          "基础设施层"
        ],
        "semanticSourceField": "floor.semanticSource"
      },
      {
        "depth": 2,
        "id": "Room",
        "label": "房间",
        "note": "叶子空间容器，直接容纳节点与边",
        "represents": [
          "模块组",
          "包",
          "服务簇",
          "子域",
          "组件组",
          "数据域",
          "UML/ER 子图"
        ],
        "semanticSourceField": "room.semanticSource"
      }
    ],
    "navigations": [
      {
        "id": "overview",
        "label": "全局鸟瞰"
      },
      {
        "id": "building-select",
        "label": "Building 选择"
      },
      {
        "id": "floor-switch",
        "label": "Floor 切换"
      },
      {
        "id": "floor-exploded",
        "label": "Floor Exploded View"
      },
      {
        "id": "room-focus",
        "label": "Room Focus"
      },
      {
        "id": "room-cutaway",
        "label": "Room Cutaway"
      },
      {
        "id": "node-focus",
        "label": "Node/Edge Focus"
      },
      {
        "id": "back-to-overview",
        "label": "逐级返回总览"
      }
    ],
    "rules": [
      "禁止添加无意义的门窗、家具等建筑装饰。",
      "关系线路必须能够直接点击。",
      "图示路由路径不得冒充实际存在的网络设备或通信中间件。",
      "虚拟房间必须带 semanticSource 标注其为阅读用组织。"
    ],
    "schemaVersion": "0.2.0",
    "title": "Building–Floor–Room 空间层级"
  },
  "workflow-kinds": {
    "description": "v6.0 提示词 §2.7 与 §17 GWF-IR 6D 扩展：工作流不能只是一组可以拖动的框和箭头。每个节点、端口、关系、条件、异常、输入、输出和执行约束必须可被程序及大模型明确理解。nodeKinds / edgeKinds 覆盖 §17 列出的全部 28 种节点类型与 13 种边类型；execution* 覆盖 §25 的 6D 执行图 IR 契约；legacy* 词表登记旧写法，新写入必须使用规范 id。",
    "edgeKinds": [
      {
        "directed": true,
        "id": "control-flow",
        "label": "控制流",
        "specType": "ControlFlow"
      },
      {
        "directed": true,
        "id": "data-flow",
        "label": "数据流",
        "specType": "DataFlow"
      },
      {
        "directed": true,
        "id": "event-flow",
        "label": "事件流",
        "specType": "EventFlow"
      },
      {
        "directed": true,
        "id": "error-flow",
        "label": "错误流",
        "specType": "ErrorFlow"
      },
      {
        "directed": true,
        "id": "conditional-flow",
        "label": "条件流",
        "requiresCondition": true,
        "specType": "ConditionalFlow"
      },
      {
        "directed": true,
        "id": "compensation-flow",
        "label": "补偿流",
        "specType": "CompensationFlow"
      },
      {
        "directed": false,
        "id": "dependency",
        "label": "依赖",
        "specType": "Dependency"
      },
      {
        "directed": true,
        "id": "state-transition",
        "label": "状态迁移",
        "note": "6D：边上传递类型化状态，必须声明 stateSchemaRef 与 stateProjection",
        "specType": "StateTransition"
      },
      {
        "directed": true,
        "id": "approval-transition",
        "label": "审批迁移",
        "note": "6D：审批结果沿显式路由进入正常或异常分支",
        "specType": "ApprovalTransition"
      },
      {
        "directed": true,
        "id": "tool-result",
        "label": "工具结果",
        "note": "6D：工具调用的结果回传边",
        "specType": "ToolResult"
      },
      {
        "directed": true,
        "id": "retry-transition",
        "label": "重试迁移",
        "note": "6D：失败后按重试策略回到原节点",
        "specType": "RetryTransition"
      },
      {
        "directed": true,
        "id": "timeout-transition",
        "label": "超时迁移",
        "note": "6D：达到时间阈值后的有依据路径",
        "specType": "TimeoutTransition"
      },
      {
        "directed": true,
        "id": "resume-transition",
        "label": "恢复迁移",
        "note": "6D：从检查点恢复后的继续边",
        "specType": "ResumeTransition"
      },
      {
        "directed": true,
        "id": "sequence",
        "label": "顺序流（旧写法）",
        "note": "旧写法，等同 control-flow。新写入必须用 control-flow。",
        "specType": null
      },
      {
        "directed": true,
        "id": "conditional",
        "label": "条件流（旧写法）",
        "note": "旧写法，等同 conditional-flow。",
        "requiresCondition": true,
        "specType": null
      },
      {
        "directed": true,
        "id": "message",
        "label": "消息流（旧写法）",
        "note": "旧写法。按语义改写为 event-flow 或 data-flow。",
        "specType": null
      },
      {
        "directed": false,
        "id": "association",
        "label": "关联（旧写法）",
        "note": "旧写法。跨节点静态引用改用 dependency。",
        "specType": null
      }
    ],
    "executionConstraints": [
      {
        "id": "order",
        "label": "顺序",
        "note": "源节点完成后目标节点才可执行"
      },
      {
        "id": "exclusive",
        "label": "互斥",
        "note": "同一资源上的排他执行"
      },
      {
        "id": "parallel",
        "label": "并行",
        "note": "允许多个实例同时运行"
      },
      {
        "id": "timeout",
        "label": "超时",
        "note": "必须声明时长与超时后的处理"
      },
      {
        "id": "retry",
        "label": "重试",
        "note": "必须声明次数与退避策略"
      },
      {
        "id": "compensation",
        "label": "补偿",
        "note": "必须声明失败后的补偿动作"
      }
    ],
    "executionGraph": {
      "compilerGates": [
        "所有入边出边、Port/Schema、Guard、路由优先级、权限、终止条件必须可校验。",
        "禁止死循环、无界 Agent 迭代、隐式审批绕过、不可追踪工具调用及缺失失败路径。",
        "Graph IR 通过静态验证后才可编译为 Simulation Graph；真实运行还需执行器能力匹配和授权。"
      ],
      "edgeFields": [
        "edgeId",
        "sourceNodeId",
        "sourcePortId",
        "targetNodeId",
        "targetPortId",
        "kind",
        "guardExpression",
        "dataMapping",
        "stateSchemaRef",
        "priority",
        "onError",
        "onTimeout",
        "deliverySemantics",
        "checkpointBoundary",
        "evidenceIds"
      ],
      "nodeFields": [
        "nodeId",
        "kind",
        "executorRef",
        "inputSchema",
        "outputSchema",
        "stateReadSet",
        "stateWriteSet",
        "timeout",
        "retry",
        "idempotencyKeyStrategy",
        "authorityPolicyRef",
        "evidenceIds",
        "verification"
      ],
      "note": "v6.0 提示词 §25 的 6D 执行图 IR 契约。它是模型层的契约，不是运行器：MCP 只负责校验与派生，真实执行由获授权的后端负责。",
      "runFields": [
        "runId",
        "workflowId",
        "workflowVersion",
        "stateSchemaVersion",
        "mode",
        "status",
        "logicalTime",
        "currentNodeIds",
        "pendingEdgeIds",
        "checkpointId",
        "actorId",
        "correlationId",
        "traceId"
      ],
      "stateContract": [
        "通过版本化 JSON Schema 或等价可机器校验格式定义共享状态，标明字段来源、所有权、可变性和敏感级别。",
        "执行边传递类型化消息或状态投影；节点只能修改自己获准的状态字段，状态合并冲突必须可检测。",
        "并行分支必须定义 fan-out、join 条件、状态归并策略及冲突检测，不允许无条件覆盖对方结果。",
        "状态提交在明确的持久化边界发生，不因“边上有状态”就自动宣称可恢复。"
      ]
    },
    "executionKinds": [
      {
        "id": "agent-loop",
        "label": "智能体循环",
        "note": "受预算、状态和终止条件约束的 Agent 迭代节点。"
      },
      {
        "id": "deterministic-program",
        "label": "确定性程序",
        "note": "由明确定义的程序、纯函数或隔离任务执行的节点。"
      },
      {
        "id": "human-approval",
        "label": "人工审批",
        "note": "暂停并等待具权限操作者审批/拒绝/退回的节点。"
      },
      {
        "id": "tool-call",
        "label": "工具调用",
        "note": "需校验工具契约和访问权限的外部操作节点。"
      },
      {
        "id": "policy-gate",
        "label": "策略闸门",
        "note": "决定路由/审批要求的策略节点。"
      },
      {
        "id": "checkpoint",
        "label": "检查点",
        "note": "在关键执行边界提交可恢复快照。"
      },
      {
        "id": "join-fork",
        "label": "汇合分流",
        "note": "受控并发启动与结果汇合。"
      },
      {
        "id": "retry-timeout",
        "label": "重试超时",
        "note": "可界定的重试、超时和升级机制。"
      },
      {
        "id": "compensation",
        "label": "补偿",
        "note": "失败后执行有契约依据的补偿步骤。"
      },
      {
        "id": "end",
        "label": "结束",
        "note": "状态/输出被验证后的终止节点。"
      }
    ],
    "executionModes": [
      {
        "id": "simulation",
        "label": "模拟",
        "note": "默认模式；不得产生真实外部副作用。"
      },
      {
        "id": "dry-run",
        "label": "预演",
        "note": "解析执行图但不执行节点。"
      },
      {
        "id": "real",
        "label": "真实执行",
        "note": "必须存在已授权的执行后端，否则拒绝。"
      }
    ],
    "gwfIr": {
      "designOnlyNodeTypes": [
        "Agent",
        "HumanApproval",
        "ManualReview",
        "AgentLoop",
        "ToolCall"
      ],
      "edgeFields": [
        "edgeId",
        "sourceNodeId",
        "sourcePortId",
        "targetNodeId",
        "targetPortId",
        "edgeType",
        "direction",
        "guard",
        "dataMapping",
        "priority",
        "errorRoute",
        "verification",
        "evidenceIds",
        "stateSchemaRef",
        "stateProjection",
        "stateMergePolicy",
        "routingExpression",
        "deliverySemantics",
        "resumeBehavior",
        "policyRef"
      ],
      "edgeTypes": [
        "ControlFlow",
        "DataFlow",
        "EventFlow",
        "ErrorFlow",
        "ConditionalFlow",
        "CompensationFlow",
        "Dependency",
        "StateTransition",
        "ApprovalTransition",
        "ToolResult",
        "RetryTransition",
        "TimeoutTransition",
        "ResumeTransition"
      ],
      "nodeFields": [
        "nodeId",
        "nodeType",
        "name",
        "description",
        "inputs",
        "outputs",
        "ports",
        "preconditions",
        "postconditions",
        "stateTransitions",
        "timeoutPolicy",
        "retryPolicy",
        "errorHandlers",
        "sideEffects",
        "idempotency",
        "implementationRef",
        "evidenceIds",
        "verification",
        "executorKind",
        "authorityScope",
        "executionMode",
        "inputStateSchema",
        "outputStateSchema",
        "stateWritePolicy",
        "checkpointBefore",
        "checkpointAfter",
        "budgetPolicy",
        "iterationLimit",
        "approvalPolicyRef",
        "toolContractRef"
      ],
      "nodeTypes": [
        "Start",
        "End",
        "Task",
        "Input",
        "Output",
        "Decision",
        "ParallelFork",
        "ParallelJoin",
        "Event",
        "Timer",
        "Wait",
        "Agent",
        "HumanApproval",
        "APICall",
        "DataTransform",
        "Subflow",
        "ErrorHandler",
        "Retry",
        "Compensation",
        "DeterministicProgram",
        "AgentLoop",
        "ToolCall",
        "PolicyGate",
        "Checkpoint",
        "Resume",
        "ManualReview",
        "Escalation",
        "AuditEvent"
      ],
      "note": "v6.0 提示词 §17 的字段目录（GWF-IR 6D 扩展）。schema_versioning 为必填；GWF-IR 是规范化中间格式，不得把第三方画布快照当作唯一的工作流真相。",
      "portFields": [
        "portId",
        "direction",
        "schema",
        "cardinality",
        "required",
        "acceptedTypes"
      ],
      "validity": [
        "Port 应通过类型、方向和约束校验；Node/Edge 必须具有稳定 ID。",
        "不适用的超时、重试或副作用字段可使用有明确定义的 null/omission 语义。",
        "没有真实执行器的类型标为 Design-only，不得伪称可执行。",
        "ControlFlow、DataFlow、EventFlow、ErrorFlow 等不得混淆。",
        "GWF-IR 是规范化中间格式，不得把第三方画布快照当作唯一的工作流真相。",
        "6D 必须明确定义哪些节点是 agentic、deterministic 或 human-gated，并对每条执行边定义路由、状态投影与约束。",
        "所有执行状态具有版本化 schema；边上传递的是结构化状态/事件，跨重启的持久化由明确检查点和存储层负责。",
        "可恢复运行需要 runId、workflowVersion、checkpointId 和幂等策略；断点恢复时拒绝不兼容的工作流版本。"
      ],
      "version": "0.3.0",
      "workflowFields": [
        "workflowId",
        "name",
        "version",
        "entryPoints",
        "nodes",
        "edges",
        "dataSchemas",
        "executionPolicy",
        "errorPolicy",
        "permissions",
        "evidenceIds",
        "metadata",
        "executionGraph",
        "stateSchemaVersion",
        "checkpointPolicy",
        "approvalPolicies",
        "executionCapabilities",
        "runSecurityPolicy"
      ]
    },
    "id": "workflow-kinds",
    "nodeKinds": [
      {
        "executes": true,
        "executorClass": "deterministic",
        "id": "task",
        "label": "任务",
        "specType": "Task"
      },
      {
        "executes": false,
        "executorClass": "routing",
        "id": "decision",
        "label": "判定",
        "note": "必须声明分支条件",
        "specType": "Decision"
      },
      {
        "executes": false,
        "executorClass": "routing",
        "id": "gateway",
        "label": "网关（旧写法）",
        "note": "旧写法。并行拆分用 parallel-fork / parallel-join，汇聚用 parallel-join。",
        "specType": null
      },
      {
        "executes": false,
        "executorClass": "none",
        "id": "start",
        "label": "开始",
        "specType": "Start"
      },
      {
        "executes": false,
        "executorClass": "none",
        "id": "end",
        "label": "结束",
        "specType": "End"
      },
      {
        "executes": true,
        "executorClass": "composite",
        "id": "subprocess",
        "label": "子流程（旧写法）",
        "note": "旧写法，等同 subflow。新写入必须用 subflow。",
        "specType": null
      },
      {
        "executes": false,
        "executorClass": "none",
        "id": "event",
        "label": "事件",
        "note": "必须声明触发条件",
        "specType": "Event"
      },
      {
        "executes": false,
        "executorClass": "none",
        "id": "data",
        "label": "数据对象（旧写法）",
        "note": "旧写法。新写入用 data-transform 表达转换，纯数据载体用端口与 dataContract 表达。",
        "specType": null
      },
      {
        "executes": false,
        "executorClass": "none",
        "id": "input",
        "label": "输入",
        "note": "流程入口数据",
        "specType": "Input"
      },
      {
        "executes": false,
        "executorClass": "none",
        "id": "output",
        "label": "输出",
        "note": "流程出口数据",
        "specType": "Output"
      },
      {
        "executes": false,
        "executorClass": "routing",
        "id": "parallel-fork",
        "label": "并行分流",
        "note": "把一条控制流拆成多条并行分支",
        "specType": "ParallelFork"
      },
      {
        "executes": false,
        "executorClass": "routing",
        "id": "parallel-join",
        "label": "并行汇聚",
        "note": "等待多条并行分支汇聚",
        "specType": "ParallelJoin"
      },
      {
        "executes": false,
        "executorClass": "deterministic",
        "id": "timer",
        "label": "定时器",
        "note": "必须声明时长与到期后的处理",
        "specType": "Timer"
      },
      {
        "executes": false,
        "executorClass": "deterministic",
        "id": "wait",
        "label": "等待",
        "note": "必须声明等待条件或等待的事件",
        "specType": "Wait"
      },
      {
        "executes": true,
        "executorClass": "agentic",
        "id": "agent",
        "label": "智能体",
        "note": "涉及外部副作用，必须声明权限",
        "specType": "Agent"
      },
      {
        "executes": true,
        "executorClass": "human-gated",
        "id": "human-approval",
        "label": "人工审批",
        "note": "必须声明审批人角色与超时策略",
        "specType": "HumanApproval"
      },
      {
        "executes": true,
        "executorClass": "external",
        "id": "api-call",
        "label": "API 调用",
        "note": "涉及外部副作用，必须声明幂等要求与错误处理",
        "specType": "APICall"
      },
      {
        "executes": true,
        "executorClass": "deterministic",
        "id": "data-transform",
        "label": "数据转换",
        "note": "必须声明输入输出契约，不得编造真实数据值",
        "specType": "DataTransform"
      },
      {
        "executes": true,
        "executorClass": "composite",
        "id": "subflow",
        "label": "子流程",
        "note": "必须声明被调用的流程 ID",
        "specType": "Subflow"
      },
      {
        "executes": true,
        "executorClass": "deterministic",
        "id": "error-handler",
        "label": "错误处理",
        "note": "必须声明捕获的错误类型与处理动作",
        "specType": "ErrorHandler"
      },
      {
        "executes": true,
        "executorClass": "deterministic",
        "id": "retry",
        "label": "重试",
        "note": "必须声明次数与退避策略",
        "specType": "Retry"
      },
      {
        "executes": true,
        "executorClass": "deterministic",
        "id": "compensation",
        "label": "补偿",
        "note": "必须声明被补偿的节点或事件",
        "specType": "Compensation"
      },
      {
        "executes": true,
        "executorClass": "deterministic",
        "id": "deterministic-program",
        "label": "确定性程序",
        "note": "6D：纯函数或隔离任务；必须声明输入输出契约与幂等键策略",
        "specType": "DeterministicProgram"
      },
      {
        "executes": true,
        "executorClass": "agentic",
        "id": "agent-loop",
        "label": "智能体循环",
        "note": "6D：必须声明 iterationLimit、budgetPolicy 与终止条件，否则判为无界循环",
        "specType": "AgentLoop"
      },
      {
        "executes": true,
        "executorClass": "external",
        "id": "tool-call",
        "label": "工具调用",
        "note": "6D：必须声明 toolContractRef 与 authorityScope，未授权不得发起真实调用",
        "specType": "ToolCall"
      },
      {
        "executes": false,
        "executorClass": "routing",
        "id": "policy-gate",
        "label": "策略闸门",
        "note": "6D：必须声明 policyRef，决定路由或审批要求",
        "specType": "PolicyGate"
      },
      {
        "executes": false,
        "executorClass": "snapshot",
        "id": "checkpoint",
        "label": "检查点",
        "note": "6D：在关键执行边界提交可恢复快照",
        "specType": "Checkpoint"
      },
      {
        "executes": false,
        "executorClass": "routing",
        "id": "resume",
        "label": "恢复",
        "note": "6D：必须声明 runId 与工作流版本，不兼容版本拒绝恢复",
        "specType": "Resume"
      },
      {
        "executes": true,
        "executorClass": "human-gated",
        "id": "manual-review",
        "label": "人工复核",
        "note": "6D：人工复核节点，必须声明审批策略与超时处理",
        "specType": "ManualReview"
      },
      {
        "executes": false,
        "executorClass": "routing",
        "id": "escalation",
        "label": "升级",
        "note": "6D：把未处理的问题升级到更高权限的处理者",
        "specType": "Escalation"
      },
      {
        "executes": false,
        "executorClass": "record",
        "id": "audit-event",
        "label": "审计事件",
        "note": "6D：写入运行审计轨迹，不可改变执行状态",
        "specType": "AuditEvent"
      }
    ],
    "portCardinalities": [
      {
        "id": "1",
        "label": "恰好一个"
      },
      {
        "id": "0..1",
        "label": "可选一个"
      },
      {
        "id": "0..N",
        "label": "可选多个"
      },
      {
        "id": "1..N",
        "label": "至少一个"
      },
      {
        "id": "N",
        "label": "任意多个"
      }
    ],
    "portDirections": [
      {
        "id": "in",
        "label": "入"
      },
      {
        "id": "out",
        "label": "出"
      },
      {
        "id": "inout",
        "label": "双向"
      }
    ],
    "portKinds": [
      {
        "id": "input",
        "label": "输入"
      },
      {
        "id": "output",
        "label": "输出"
      },
      {
        "id": "control",
        "label": "控制"
      },
      {
        "id": "error",
        "label": "异常"
      }
    ],
    "qualityGates": {
      "honestReporting": "任何受环境限制未执行的功能与测试都必须标明未验证，禁止将演示状态当作真实外部执行。",
      "note": "v6.0 提示词 quality_gates_6d.reject_on。下列情形必须拒绝，不得放行。",
      "rejectOn": [
        "unbounded_agent_loop",
        "human_approval_bypass",
        "unvalidated_state_schema",
        "missing_route_guard",
        "unsafe_side_effect_replay",
        "checkpoint_without_state_integrity",
        "fake_production_execution",
        "untraceable_decision"
      ]
    },
    "rules": [
      "画布坐标、颜色和动画不应决定业务执行语义；所有计算以明确的数据契约为准。",
      "每个工作流节点必须绑定至少一个 canonical nodeId 或声明为纯流程内部元素。",
      "条件流必须带可被程序判断的 condition 表达式，不得只写自然语言。",
      "新写入必须使用 §17 的规范 id（control-flow / data-flow / event-flow / error-flow / conditional-flow / compensation-flow / dependency / state-transition / approval-transition / tool-result / retry-transition / timeout-transition / resume-transition）；旧写法只保留兼容读取。",
      "没有真实执行器的节点类型（agent / human-approval / manual-review / agent-loop / tool-call）必须标为 Design-only，不得声称可执行。",
      "端口必须声明 direction 与 cardinality；跨节点连线必须连到具体的端口（sourcePortId / targetPortId）。",
      "6D：默认运行模式是模拟；真实执行必须存在获授权的执行后端，否则拒绝。",
      "6D：Agent 循环必须声明 iterationLimit 与 budgetPolicy；人工审批必须有 approvalPolicyRef；工具调用必须有 toolContractRef 与 authorityScope。"
    ],
    "schemaVersion": "0.2.0",
    "title": "工作流语义模型词表（GWF-IR）"
  }
});
