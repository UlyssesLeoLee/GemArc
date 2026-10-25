/**
 * GENERATED FILE - DO NOT EDIT.
 *
 * Source: src/gemarc_mcp/scaffold/vocab/*.json
 * Digest: fb8d1213f44a1d626f6d519df97806a86424731308b5a445c4ac672542f5e886
 * Regenerate: python -m gemarc_mcp.scaffold.sync
 *
 * The vocabulary is the single source of truth shared by the prompt, the
 * Python validator and this runtime. A drift test asserts that this file
 * still matches the JSON sources.
 */
export const VOCAB_DIGEST = 'fb8d1213f44a1d626f6d519df97806a86424731308b5a445c4ac672542f5e886';

export const VOCAB = Object.freeze({
  "case-categories": {
    "categories": [
      {
        "assertion": "预期路径成立",
        "id": "normal",
        "label": "正常",
        "requiresIntervention": false
      },
      {
        "assertion": "预期失败或降级被显式观测到",
        "id": "abnormal",
        "label": "异常",
        "requiresIntervention": true
      },
      {
        "assertion": "极限输入或极限规模下的行为被确定",
        "id": "boundary",
        "label": "边界",
        "requiresIntervention": true
      },
      {
        "assertion": "多个条件同时成立时的交互被确定",
        "id": "combination",
        "label": "组合",
        "requiresIntervention": true
      },
      {
        "assertion": "故障后恢复到已知良好状态的过程被确定",
        "id": "recovery",
        "label": "恢复",
        "requiresIntervention": true
      }
    ],
    "description": "统一管理正常、异常、边界、组合及恢复 Case。Case 与 Scenario 的关系是：Case 是一个可执行的实例（scenarioId + 干预配置 + 期望），Scenario 是规则与事件的定义。",
    "fields": {
      "category": "本词表中的分类 ID",
      "disabledNodeIds": "本次 Case 的干预集合（模块屏蔽）",
      "evidenceClass": "沿用 evidence-levels 词表；Hypothetical 的 Case 不得被描述为实际运行结果",
      "expected": {
        "blocked": "预期被阻断事件数",
        "delivered": "预期送达事件数",
        "notes": "文字判定说明"
      },
      "id": "稳定 ID，命名规则 <scenarioId>__<caseId>",
      "scenarioId": "指向 definitions.scenarios 中的场景 ID",
      "title": "人类可读标题"
    },
    "id": "case-categories",
    "rules": [
      "每个 Case 必须绑定一个 scenarioId，且该 scenario 必须存在于模型中。",
      "requiresIntervention 为 true 的分类必须给出非空 disabledNodeIds 或显式说明无干预原因。",
      "Case 库与 4D/5D 视图共享同一 scenarioId 引用，切换视图不得改变 Case 语义。"
    ],
    "schemaVersion": "0.2.0",
    "title": "Scenario & Causal Lab 用例分类"
  },
  "diagram-types": {
    "description": "v5.0 提示词 §8.1 要求按真实项目证据生成的图示。不得为了凑数量而生成虚假图示——只有具备证据的图示才允许出现在 view 列表中。",
    "id": "diagram-types",
    "rules": [
      "每个 view 必须声明 diagramType，且该类型必须在本词表中登记。",
      "view 的 nodeIds 只能包含该类型 nodeKinds 允许的实体；edgeKinds 同理。",
      "fidelity 列出的元素若在模型中不存在对应信息，必须在界面上标注为缺失，不得伪造。",
      "不得为了凑数量而生成虚假图示：没有证据支撑的图示类型不允许出现在 view 列表中。"
    ],
    "schemaVersion": "0.2.0",
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
    "description": "v5.0 提示词 §2.7：工作流不能只是一组可以拖动的框和箭头。每个节点、端口、关系、条件、异常、输入、输出和执行约束必须可被程序及大模型明确理解。",
    "edgeKinds": [
      {
        "directed": true,
        "id": "sequence",
        "label": "顺序流"
      },
      {
        "directed": true,
        "id": "conditional",
        "label": "条件流",
        "requiresCondition": true
      },
      {
        "directed": true,
        "id": "message",
        "label": "消息流"
      },
      {
        "directed": false,
        "id": "association",
        "label": "关联"
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
    "id": "workflow-kinds",
    "nodeKinds": [
      {
        "executes": true,
        "id": "task",
        "label": "任务"
      },
      {
        "executes": false,
        "id": "decision",
        "label": "判定",
        "note": "必须声明分支条件"
      },
      {
        "executes": false,
        "id": "gateway",
        "label": "网关",
        "note": "并行/汇聚/排他"
      },
      {
        "executes": false,
        "id": "start",
        "label": "开始"
      },
      {
        "executes": false,
        "id": "end",
        "label": "结束"
      },
      {
        "executes": true,
        "id": "subprocess",
        "label": "子流程",
        "note": "必须声明被调用的流程 ID"
      },
      {
        "executes": false,
        "id": "event",
        "label": "事件",
        "note": "必须声明触发条件"
      },
      {
        "executes": false,
        "id": "data",
        "label": "数据对象"
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
    "rules": [
      "画布坐标、颜色和动画不应决定业务执行语义；所有计算以明确的数据契约为准。",
      "每个工作流节点必须绑定至少一个 canonical nodeId 或声明为纯流程内部元素。",
      "条件流必须带可被程序判断的 condition 表达式，不得只写自然语言。"
    ],
    "schemaVersion": "0.2.0",
    "title": "工作流语义模型词表"
  }
});
