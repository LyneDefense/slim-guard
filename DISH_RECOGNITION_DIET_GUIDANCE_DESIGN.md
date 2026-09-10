# SlimGuard 菜品识别与饮食适宜性多 Agent 编排设计

> 版本：v0.1  
> 日期：2026-09-10  
> 状态：设计草案，尚未实现  
> 范围：只识别照片中有哪些菜，并结合结构化数据库和经审核 RAG 给出饮食适宜性建议；不估算克重、热量或营养素摄入量。

## 1. 结论先行

本期把图片营养能力收敛为：

```text
识别菜品 → 必要时让用户确认 → 查结构化菜品数据库和 RAG
        → 结合用户目标与明确限制给出“可以吃、调整后吃、少吃、避免或信息不足”
```

不再建设 FoodSAM、食物分割、份量估计和图片热量计算。第一版继续使用现有视觉模型接口，因此不需要
自建 GPU 推理服务。

此前提出的三个新模块角色保留，但结合现有代码后，不应把它们全部做成与旧 Agent 并列的新 Agent：

1. **菜品识别 Agent（新增）**：识别一张图片中有哪些菜，输出候选和不确定性；
2. **营养证据检索 Agent（新增）**：规范化菜名，规划并执行受控的数据库/RAG 检索，形成证据包；
3. **饮食建议 Agent（升级）**：由现有 `NutritionAgent` 演进而来，不再复制一个职责重叠的新 Agent。

因此目标态共有六个业务 Agent：

```text
1. Orchestrator                 现有，升级
2. Dish Recognition Agent      新增
3. Nutrition Retrieval Agent   新增
4. Diet Guidance Agent         由现有 Nutrition Agent 升级
5. Response Style Agent        现有，保留
6. Response Reviewer Agent     现有，扩展审查规则
```

现有单 Agent Harness 在迁移期间继续负责线上业务工具执行和回退；新链路按 `off → shadow → canary → on`
发布，不能一次替换。

## 2. 当前系统里实际有哪些 Agent

以下盘点以当前代码为准，并区分“已经实现”“是否默认启用”和“只是支持组件”。

### 2.1 当前主 Agent：Core Harness Agent

对应代码：

- `src/slim_guard/agent/prompt.py`
- `src/slim_guard/harness/runner.py`
- `src/slim_guard/agent/runtime.py`

它是当前功能最完整的主对话 Agent，能够：

- 理解文本和图片消息；
- 调用体重、体脂、饮食、运动、提醒、记忆和图片工具；
- 执行业务写入及确认流程；
- 返回最终回复。

它没有出现在多 Agent Graph 的 `AgentRole` 中，可以理解为迁移前的单 Agent 主链路。当前默认配置是
`AGENT_RUNTIME_MODE=harness`、`MULTI_AGENT_MODE=off`，所以“代码中实现了多 Agent”不等于默认就在使用。

### 2.2 当前多 Agent Graph 的四个 Agent

| Agent | 代码角色 | 当前职责 | 当前限制 |
|---|---|---|---|
| 对话编排 Agent | `orchestrator` | 判断直接回答还是进入专业营养分析，生成 `TurnDirective` | 当前是只读候选编排，没有业务工具权限 |
| 营养专业 Agent | `nutrition_expert` | 基于 Evidence、确定性观察和知识引用生成 `ProfessionalAssessment` | 能力偏通用；目前不具备菜品级适宜性契约 |
| 表达风格 Agent | `response_style` | 按启用的 Style Profile 改写结构化回复计划 | 不允许新增事实、结论、风险或引用 |
| 忠实度审查 Agent | `response_reviewer` | 检查遗漏、改义、无依据结论、医疗越界和不当语气 | 还没有菜品识别置信度和饮食适宜性专用规则 |

这四个角色已经冻结在：

- `src/slim_guard/agents/contracts.py`
- `src/slim_guard/harness/manifest.py`
- `src/slim_guard/agent/composition.py`
- `src/slim_guard/orchestration/coordinator.py`

### 2.3 使用模型、但不是业务 Agent 的工作单元

以下组件会调用模型，但不应在产品中宣称为独立业务 Agent：

| 组件 | 职责 | 为什么不是本设计中的业务 Agent |
|---|---|---|
| Memory Ingestor | 从用户原话摄取明确长期事实并写入记忆 | 属于统一 Memory Plane 的摄取 Worker |
| Memory Recaller | 对长期记忆做候选召回和筛选 | 属于上下文准备 Worker，不形成业务结论 |
| `inspect_image` | 调用视觉模型返回图片观察 | 当前是主 Agent 的只读工具，不是独立 Agent |
| Evidence Builder | 把权威记录、用户自述和视觉观察整理成证据包 | 是确定性代码组件，不需要模型自主判断 |
| Style Resolver | 解析当前启用的风格版本 | 是版本解析组件，不是 Agent |
| Output Guard | 对最终文本做安全兜底 | 是确定性安全边界，不是 Agent |

定时提醒、消息发送、风格版本构建 Worker 也属于后台服务，不计入对话 Agent 清单。

## 3. 本期目标与非目标

### 3.1 目标

1. 一张餐食照片能够识别出一道或多道菜；
2. 每道菜提供候选、置信度和不确定原因，而不是只返回一段自由文本；
3. 不确定时只问最能消除歧义的问题，并能在下一轮继续；
4. 用户确认后，把菜名匹配到内部规范菜品实体；
5. 从结构化数据库读取菜品特征和明确规则，从 RAG 读取经审核的解释性知识；
6. 结合用户的减脂目标、过敏、饮食限制和明确医嘱，给出有依据的适宜性建议；
7. 所有识别、确认、检索、建议和改写都可在 Trace 中复核；
8. 不需要自建视觉模型或 GPU 服务。

### 3.2 明确不做

- 不根据图片估算克重、份量、热量或三大营养素；
- 不从菜品外观推测隐藏的油、糖、盐、馅料或具体配方；
- 不用 FoodSAM 做食物区域分割；
- 不把一次图片识别自动写成用户的长期饮食偏好；
- 不把减脂食物简单分成道德化的“好食物/坏食物”；
- 不用模型内部知识代替数据库和已发布 RAG 来源；
- 不进行疾病诊断、治疗或药物相关饮食处方；
- 不在运行时直接搜索互联网，并把未审核网页当成营养依据。

## 4. 目标 Agent 职责

### 4.1 Agent A：对话编排 Agent（现有，升级）

负责：

- 判断用户是在打卡、问某道菜能否吃、发图求识别，还是普通聊天；
- 决定是否调用菜品识别、证据检索和饮食建议；
- 控制追问、跨轮恢复、业务写入和最终响应路径；
- 只把最小必要上下文投影给下游 Agent；
- 保证任何 Agent 都不能自行调用另一个 Agent。

不负责：

- 自己识图；
- 自己查询知识后给营养结论；
- 自己创造结构化数据库 ID；
- 自己修改专业结论的置信度。

当前 Orchestrator 是只读候选节点。目标态需要逐步获得经 Coordinator 授权的业务工具能力；迁移完成前，
Core Harness 仍负责业务写入。

### 4.2 Agent B：菜品识别 Agent（新增）

负责：

- 判断图片是否为可用的餐食图片；
- 识别图片里有几道主要菜；
- 为每道菜输出 top-k 菜名候选；
- 输出可见的主要食材和烹饪方式候选；
- 明确遮挡、相似菜、画质和不可见配料造成的不确定性；
- 生成建议追问，但不直接向用户发消息。

不负责：

- 查询营养知识；
- 判断能不能吃；
- 估算热量和重量；
- 写饮食记录；
- 把“可能是”改成确定事实。

它是唯一可以读取本轮原始餐食图片的业务 Agent。其他 Agent 只接收其结构化结果。

### 4.3 Agent C：营养证据检索 Agent（新增）

负责：

- 把用户确认或达到采用条件的菜名匹配到内部 `dish_entity_id`；
- 识别同义词、地方叫法、常见做法和可能的多个实体；
- 根据“菜品 + 用户目标 + 明确限制”生成受控检索计划；
- 请求 Coordinator 查询结构化菜品数据库和已发布 Nutrition RAG；
- 将检索结果整理为 `DishEvidenceBundle`；
- 发现实体或资料不足时明确返回缺口。

不负责：

- 直接输出“能吃/不能吃”；
- 将 RAG 片段当作菜品识别证据；
- 选择未发布、已退休或适用人群不匹配的资料；
- 访问用户完整档案、历史聊天或原始图片；
- 修改数据库内容。

精确菜名命中时应优先走确定性查询，不必为了“像 Agent”而调用模型。只有别名消歧、查询规划等确实需要
语义判断时才调用模型。模型提出的实体 ID 和来源 ID 必须由 Coordinator 回库验证。

### 4.4 Agent D：饮食建议 Agent（由 Nutrition Agent 升级）

负责：

- 基于 `DishEvidenceBundle` 和最小用户上下文形成逐菜建议；
- 将结论分为“可以吃、调整后可以吃、建议少吃、建议避免、信息不足”；
- 给出一至三个可执行动作，例如少选油炸做法、减少酱汁或搭配蔬菜；
- 说明建议依据和不确定性；
- 对过敏、明确医嘱、特殊人群和医疗风险执行更严格边界；
- 资料不足时提出必要问题或保守退出。

这是现有 `NutritionAgent` 的演进版本，不新增一个重复的“营养分析 Agent”。现有
`ProfessionalAssessment` 可继续作为外层信封，内部增加菜品专用的 `DietGuidanceAssessment`。

不负责：

- 修改菜品识别结果；
- 自行检索互联网；
- 编造未检索到的营养特征；
- 在没有硬依据时输出绝对禁食；
- 输出图片热量估算。

### 4.5 Agent E：表达风格 Agent（现有，保留）

负责把结构化结论转换成当前启用的“医生专属风格”，但必须保留：

- 菜品名称及其确认状态；
- 适宜性等级；
- 关键原因和必要动作；
- 不确定性；
- 风险与转介；
- 引用绑定。

它不能把“建议少吃”改成“绝对不能吃”，也不能把“可能是红烧茄子”改成“就是红烧茄子”。

### 4.6 Agent F：忠实度审查 Agent（现有，扩展）

新增审查项：

- 是否保留菜品识别置信度与确认状态；
- 是否存在未绑定数据库记录或 RAG 引用的营养结论；
- 是否因减脂目标而无依据地使用“不能吃”；
- 是否遗漏用户明确的过敏或饮食限制；
- 是否把典型做法说成照片中确定可见的配方；
- 是否输出热量、克重或营养数值；
- 是否把一般营养教育写成诊断或治疗建议；
- Style Agent 是否改变了上游结论。

## 5. 总体编排

### 5.1 目标主链路

```text
渠道输入
  ↓
Input Guard
  ↓
Memory Ingestion / Recall（支持 Worker）
  ↓
Orchestrator
  ├─ 普通聊天/单纯记录确认 ───────────────────────┐
  │                                               │
  ├─ 餐食图片 → Dish Recognition Agent            │
  │                 ↓                             │
  │          DishRecognitionResult                │
  │             ├─ 不确定 → Pending Confirmation ─┤
  │             └─ 可采用                         │
  │                    ↓                          │
  └─ 文本已给出菜名 ─→ Nutrition Retrieval Agent  │
                            ↓                     │
                    DishEvidenceBundle            │
                            ↓                     │
                     Diet Guidance Agent          │
                            ↓                     │
                   DietGuidanceAssessment         │
                            └──────────────────────┤
                                                   ↓
                                          Response Plan
                                                   ↓
                                          Response Style Agent
                                                   ↓
                                        Response Reviewer Agent
                                            ├─ pass → Output Guard
                                            └─ repair 一次 → 对应上游
                                                   ↓
                                                用户
```

### 5.2 编排原则

1. **只调用需要的 Agent**：普通体重记录不调用三个餐食 Agent；
2. **识别不确定就先停**：需要确认时不提前查一堆资料，也不让饮食建议 Agent 猜；
3. **一次图片只识别一次**：识别 Artifact 在同一 Turn 内复用，禁止 Core Harness 和新 Graph 重复请求视觉模型；
4. **数据库先于 RAG**：规范菜名、别名、结构化标签和硬规则从数据库获得；RAG 用来补充指南依据和解释；
5. **Agent 不直接互调**：所有调用、工具权限、Artifact 校验和返回边都由 Coordinator 控制；
6. **保持来源强度**：图片是观察，用户确认是用户报告，业务记录才是权威记录；下游不得擅自升级；
7. **先结论后风格**：专业 Agent 形成内容，Style Agent 只负责表达；
8. **失败可降级**：任何新节点失败都不能影响已经完成的打卡写入或基本回复。

## 6. 典型路由

| 用户输入 | 调用路径 | 结果 |
|---|---|---|
| “今天 82.3kg” | Core/Orchestrator → 体重工具 → Style → Reviewer | 不调用餐食 Agent |
| “午饭吃了番茄炒蛋，只帮我记录” | Core/Orchestrator → 饮食工具 → Style → Reviewer | 不强制做营养分析 |
| “番茄炒蛋减脂能吃吗？” | Orchestrator → Retrieval → Guidance → Style → Reviewer | 跳过图片识别 |
| 用户发一张两菜一饭的照片并问“这些能吃吗” | Orchestrator → Recognition → 判断是否确认 → Retrieval → Guidance → Style → Reviewer | 完整链路 |
| 图片可能是红烧茄子或地三鲜 | Orchestrator → Recognition → Pending Confirmation → Style → Reviewer | 本轮只问一个澄清问题 |
| 用户下一轮说“是地三鲜” | Pending Resume → Retrieval → Guidance → Style → Reviewer | 复用上一轮图片结果，不再次识图 |
| “我花生过敏，这个能吃吗？” | Recognition/文本菜名 → Retrieval → 规则检查 → Guidance → Reviewer | 未确认配料时不能保证安全，必要时建议查配料或避免 |
| “这顿多少卡？” | Orchestrator → 范围边界回复 → Style → Reviewer | 明确说明本期不提供图片热量估算 |

### 6.1 “只记录”和“顺便分析”必须区分

用户明确“只记录”时，不应机械地启动检索和建议链路。以下情况才进入饮食建议：

- 用户问能否吃、怎么搭配、需不需要少吃；
- 用户要求点评这一餐；
- 产品入口明确是“拍照问饮食建议”；
- 未来存在用户明确开启的自动餐食点评偏好。

仅发送餐食图片但意图不明确时，第一版可以完成识别预填并询问“要记录还是帮你看看是否适合减脂”，
不擅自点评。

## 7. 不确定性与跨轮确认

### 7.1 不能只依赖一个置信度数字

是否追问由版本化策略决定，至少考虑：

- top-1 和 top-2 是否接近；
- 是否存在多个相似中式菜名；
- 主要菜是否被遮挡；
- 烹饪方式是否影响建议；
- 用户问题是否涉及过敏、疾病或明确医嘱；
- 该模型在冻结评测集上的校准结果。

高风险场景即使视觉置信度高，也应让用户确认菜名或配料。阈值不能凭感觉硬编码，应记录
`recognition_policy_version` 并通过评测确定。

### 7.2 PendingDishConfirmation

需要增加跨轮待确认对象：

```text
PendingDishConfirmation
  id
  user_id / thread_id / source_turn_id / asset_id
  recognition_artifact_id
  unresolved_item_ids[]
  candidate_groups[]
  question
  expires_at
  status: pending | resolved | expired | cancelled
```

下一轮只接受能够唯一映射到待确认候选的用户回复。存在多个待确认对象或用户回答含糊时继续澄清，不能用
关键词硬匹配。

用户确认后创建新的 `ConfirmedDishSet` Artifact，不能覆盖原识别结果：

```text
视觉候选（不可变） → 用户确认（不可变） → 规范实体匹配（不可变）
```

这样管理台可以看出“模型最初认为是什么、用户改成了什么、最终用了哪个菜品实体”。

## 8. Agent 间数据契约

### 8.1 DishRecognitionResult

```text
DishRecognitionResult
  schema_version
  asset_id
  model / prompt_version / policy_version
  image_kind: meal | non_food | unusable
  quality_flags[]
  dishes[]
    observation_id
    candidates[]
      label
      confidence
    visible_ingredients[]
    preparation_candidates[]
    uncertainty_reasons[]
    requires_confirmation
  suggested_question
  overall_requires_confirmation
```

约束：

- 不包含 calories、grams 或营养素字段；
- `confidence` 不能被后续 Agent 提高；
- 菜品数不确定时明确标记，不能悄悄漏掉；
- 菜名是候选文字，不允许视觉 Agent 编造内部 `dish_entity_id`；
- 用户在图片附言中已经明确菜名时，也要保留“用户陈述”和“视觉观察”两个来源。

### 8.2 DishLookupPlan

```text
DishLookupPlan
  dish_inputs[]
    dish_ref
    confirmed_name
    aliases_to_try[]
    preparation_terms[]
  user_goal_tags[]
  applicability_tags[]
  constraint_refs[]
  database_queries[]
  rag_queries[]
```

该计划由 Retrieval Agent 提议，由 Coordinator 验证和执行。Agent 不能直接把任意 SQL、URL 或数据库 ID
放进计划。

### 8.3 DishEvidenceBundle

```text
DishEvidenceBundle
  schema_version
  dishes[]
    dish_ref
    entity_match
      dish_entity_id
      canonical_name
      match_status: exact | alias | ambiguous | not_found
      source_version
    known_traits[]
      trait
      certainty: defined | typical | possible
      source_refs[]
    applicable_rules[]
      rule_id / effect / applicability / source_refs[]
    knowledge_citations[]
    missing_information[]
  corpus_status
  retrieval_receipt_ids[]
```

`typical` 只能表达典型做法，例如“鱼香茄子通常用油较多”，不能伪装成照片中这一盘的确定配方。

### 8.4 DietGuidanceAssessment

```text
DietGuidanceAssessment
  schema_version
  scope: weight_management_general | constraint_specific | safety_referral
  dish_assessments[]
    dish_ref
    canonical_name
    suitability:
      suitable
      suitable_with_adjustment
      limit
      avoid
      insufficient_information
    reasons[]
      statement / evidence_refs[] / citation_refs[] / confidence
    actions[]
    uncertainty_note
  meal_level_advice[]
  questions[]
  risk_flags[]
  referral
```

“avoid” 必须满足至少一个硬条件：

- 用户明确报告的过敏或禁忌与已确认配料直接冲突；
- 用户明确提供的医生要求与该菜直接冲突；
- 已审核、适用人群匹配的规则明确要求避免；
- 存在需要立即规避的食品安全风险。

仅因为用户正在减脂，不能使用 `avoid`。高油、高糖、高盐或能量密度较高通常只能得到 `limit` 或
`suitable_with_adjustment`。

## 9. 结构化数据库与 RAG 分工

### 9.1 当前已有能力

当前 PostgreSQL 已经有：

- `meal_records`：保存用户吃了哪些食物，但 `foods` 仍是名称和文字份量；
- `nutrition_knowledge_sources`：版本化知识来源；
- `nutrition_knowledge_chunks`：知识分块及可选 embedding 字段；
- `nutrition_knowledge_reviews`：append-only 审核记录；
- `draft → approved → published → retired` 生命周期；
- 只允许检索已发布资料的服务和引用校验。

当前组合代码没有注入 `KnowledgeVectorScorer`，因此线上知识检索主要是词法候选和代码重排；接口虽支持
向量候选，但还没有形成完整的向量检索链路。这不妨碍第一版上线，RAG 不等于必须先部署向量数据库。

### 9.2 需要新增的结构化数据

第一版至少增加：

```text
dish_entities
  id / canonical_name / cuisine / status / version

dish_aliases
  dish_entity_id / alias / region / source_ref / review_status

dish_traits
  dish_entity_id / trait / certainty / preparation_scope
  source_ref / version / review_status

diet_rules
  id / condition_type / condition_value
  effect / applicability / source_ref / version / review_status

dish_recognition_events
  asset_id / model / prompt_version / result_artifact_id / created_at

dish_confirmations
  recognition_artifact_id / user_evidence_ref / confirmed_payload / created_at
```

建议的第一批 `dish_traits` 只记录与适宜性有关、且能找到依据的特征：

- 主要食物组；
- 常见烹饪方式；
- 是否通常油炸或油煎；
- 是否通常有浓酱、勾芡或含糖酱汁；
- 常见过敏原和“可能包含”配料；
- 是否容易通过少酱、去皮、换烹饪方式调整。

本期不要求建立每 100g 营养成分表，也不需要菜谱克重数据库。

### 9.3 哪些信息放数据库

- 规范菜名、别名和层级；
- 已审核的菜品典型特征；
- 明确的过敏/限制规则；
- 规则的适用人群、版本和来源；
- 识别模型结果、用户确认和实体匹配；
- 每次检索和规则命中的 Receipt。

这些内容需要精确过滤、版本控制和稳定引用，不能只放在向量库里。

### 9.4 哪些信息放 RAG

- 中国居民膳食指南中的通用饮食搭配原则；
- 成人减重相关食养指南；
- 油炸、含糖饮料、高盐调味等饮食教育解释；
- 食物替换、外食选择和烹饪调整建议；
- 特殊人群的边界和何时需要转介。

RAG 只检索人工审核并发布的资料。结构化硬规则不能从召回片段临时生成；RAG 找不到依据时，Agent 应返回
信息不足，而不是调用模型常识补齐。

### 9.5 检索顺序

```text
确认菜名
  ↓
规范实体精确匹配
  ├─ 唯一命中 → 读取 traits/rules
  ├─ 多个命中 → 让用户消歧
  └─ 未命中   → 别名搜索；仍未命中则标记 unknown
  ↓
按菜品、目标和适用人群构造 RAG 查询
  ↓
只从 published chunks 召回
  ↓
校验来源、适用人群、状态、哈希和本次 Invocation
  ↓
生成 DishEvidenceBundle
```

## 10. 用户上下文与安全边界

饮食建议 Agent 只能看到与本次问题相关的最小上下文：

- 用户当前目标，例如一般减脂；
- 用户明确报告且仍有效的过敏、忌口和饮食限制；
- 用户明确提供的医生要求；
- 本轮确认菜品；
- 本轮检索证据；
- 必要时最近少量已记录饮食，但不能把“未记录”解释为“没吃”。

用户自述疾病仍保持 `user_reported`，不能自动升级为医学诊断。若建议需要疾病专属判断，而系统没有专门
审核的资料和适用规则，应返回一般信息并建议咨询医生或注册营养师。

### 10.1 用户看到的语言

内部使用严格枚举，外部表达保持自然：

| 内部结论 | 推荐表达 |
|---|---|
| `suitable` | “可以吃” |
| `suitable_with_adjustment` | “可以吃，做一点调整会更合适” |
| `limit` | “能吃，建议少一点/少频繁一些” |
| `avoid` | “按你已经说明的限制，这个建议避免” |
| `insufficient_information` | “现在还不能确定，先确认一下……” |

系统不得仅凭减脂目标说“这个不能吃”。真正的 `avoid` 必须同时展示触发它的用户限制或审核规则。

## 11. 迁移期如何与现有 Core Harness 共存

### 11.1 当前事实

当前 Core Harness 先执行完整模型/工具循环；多 Agent Coordinator 目前主要生成候选，并在 Canary/On 时
保留现有 Harness 回复中的业务结果。因此新链路不能假设 Orchestrator 已经能够完全取代当前主 Agent。

### 11.2 推荐迁移方式

#### 阶段一：复用业务写入，新增只读专业链路

- Core Harness 继续处理体重、饮食、运动和记忆写入；
- 图片识别结果升级为结构化、可持久化 Artifact；
- 新 Coordinator 读取同一份识别 Artifact，禁止重复调用视觉模型；
- Retrieval 和 Guidance 保持只读；
- 新回复必须保留已有业务写入成功/失败信息；
- 先运行 Shadow，只在管理台比较结果。

#### 阶段二：Coordinator 接管餐食图片路由

- Orchestrator 决定是否调用 Dish Recognition；
- Coordinator 管理确认和跨轮恢复；
- 用户确认后，Coordinator 再授权 `record_meal`；
- Core Harness 对餐食图片保留回退，不再重复识图。

#### 阶段三：统一业务编排

- Orchestrator 通过 Coordinator 获得版本化业务工具授权；
- Core Harness 退化为紧急回退路径；
- 多 Agent Graph 成为正式线上主链路；
- 仍保留一键切回 `MULTI_AGENT_MODE=off` 的能力。

## 12. Graph 与 Manifest 变更

### 12.1 新 AgentRole

```text
dish_recognition
nutrition_retrieval
```

`nutrition_expert` 可以暂时保留角色值以兼容历史 Trace，但在 Prompt、Schema 和管理台名称中显示为
“饮食建议 Agent”。不要直接重命名旧枚举，避免历史记录无法解析。

### 12.2 新 GraphNode

```text
DISH_RECOGNITION_RUNNING
DISH_CONFIRMATION_PENDING
DISH_CONFIRMATION_RESOLVED
NUTRITION_RETRIEVAL_RUNNING
NUTRITION_EVIDENCE_READY
```

关键合法边：

```text
ORCHESTRATOR → DISH_RECOGNITION
DISH_RECOGNITION → DISH_CONFIRMATION_PENDING
DISH_RECOGNITION → NUTRITION_RETRIEVAL
DISH_CONFIRMATION_RESOLVED → NUTRITION_RETRIEVAL
ORCHESTRATOR → NUTRITION_RETRIEVAL             # 文本已明确菜名
NUTRITION_RETRIEVAL → NUTRITION_EVIDENCE_READY
NUTRITION_EVIDENCE_READY → NUTRITION_EXPERT
NUTRITION_EXPERT → STYLE_RESOLVED
```

不允许：

```text
DISH_RECOGNITION → NUTRITION_EXPERT            # 跳过数据库/RAG
NUTRITION_RETRIEVAL → RESPONSE_STYLE            # 检索 Agent 自己下结论
RESPONSE_STYLE → NUTRITION_RETRIEVAL            # 风格 Agent 扩大事实范围
```

### 12.3 建议权限

| Agent | 可见上下文 | 工具权限 |
|---|---|---|
| Orchestrator | 当前消息、最小记忆、待确认对象、业务工具回执 | 经策略授权的业务工具 |
| Dish Recognition | 当前用户本轮图片、用户对图片的文字说明 | 只读图片访问、视觉模型 |
| Nutrition Retrieval | 确认菜名、目标/限制标签 | 只读菜品库、只读规则、只读 Nutrition RAG |
| Diet Guidance | DishEvidenceBundle、最小用户证据 | 第一版无直接工具；只消费已验证 Artifact |
| Response Style | ResponsePlan、Style Profile、审核示例 | 只读风格资料 |
| Response Reviewer | 上游结构化结果、最终候选文本和引用摘要 | 无业务写工具 |

## 13. 失败与降级

| 失败点 | 用户侧行为 | 禁止行为 |
|---|---|---|
| 视觉服务失败 | 请用户直接输入菜名或稍后重试 | 猜图片内容 |
| 图片不可用 | 请重拍或直接说菜名 | 进入营养建议 |
| 菜名不确定 | 给 2～3 个候选并问一个问题 | 自动选 top-1 后点评 |
| 菜品实体未命中 | 可给基于用户描述的一般性建议并标明资料不足，或追问主要食材/做法 | 编造实体 ID |
| RAG 无结果 | 只使用已验证的结构化规则；都没有则保守回答 | 用模型常识伪造引用 |
| Retrieval Agent 失败 | 跳过专业判断，说明暂时无法可靠分析 | 让 Style Agent补内容 |
| Guidance Agent 失败 | 返回中性“资料不足”并保留已完成的记录确认 | 输出半成品 JSON |
| Reviewer 拒绝 | 修复对应上游一次；仍失败使用中性回退 | 无限循环互评 |

## 14. Trace 与管理台

一次完整餐食建议应展示：

1. Orchestrator 为什么选择该路径；
2. 图片识别出的菜品候选、置信度和不确定原因；
3. 用户是否确认或更正；
4. 每道菜匹配到的规范实体和匹配方式；
5. 结构化数据库命中的 traits/rules；
6. RAG 召回、采用和拒绝的来源；
7. 饮食建议 Agent 对每道菜的结论与依据；
8. Style 使用的 Profile 版本；
9. Reviewer 的结论、问题和修复目标；
10. 最终回复来自哪个 Artifact。

管理台至少增加三张卡片：

- **菜品识别**：原始候选、模型版本、用户确认；
- **营养证据检索**：菜品实体、结构化规则、RAG 引用、未命中项；
- **饮食适宜性建议**：逐菜等级、原因、动作、风险和不确定性。

用户对菜名的更正可以进入独立评测素材，但不能未经审核自动修改 Prompt、菜品数据库或线上模型。

## 15. 评测与验收

### 15.1 菜品识别 Agent

- 中国家庭餐、食堂、外卖和混合菜的逐菜 precision/recall/F1；
- top-1 和 top-3 菜名准确率；
- 一张图多道菜的漏检率；
- 应追问样本召回率；
- 错误自动采用率；
- 用户一次确认成功率；
- 同一图片不会重复调用视觉模型。

上线门槛应优先控制“错误自动采用率”，不能只追求 top-1 平均准确率。

### 15.2 营养证据检索 Agent

- 规范实体唯一命中准确率；
- 别名和地方菜名覆盖率；
- 错误实体绑定率；
- 已发布来源引用覆盖率；
- 退休/未审核资料泄漏率必须为 0；
- 适用人群过滤错误率必须为 0；
- 所有 ID 均能回库验证。

### 15.3 饮食建议 Agent

- 每道菜都有明确结论或 `insufficient_information`；
- 所有知识性理由都有来源；
- 所有 `avoid` 都命中硬规则或明确用户限制；
- 仅因减脂目标产生的无依据绝对禁食数量为 0；
- 不输出图片克重、热量或营养数值；
- 过敏、医嘱、特殊人群和医疗越界测试全部通过；
- 无资料时能够保守退出。

### 15.4 端到端

- 不确定图片能够跨轮确认并继续原任务；
- 用户更正后不会继续使用旧菜名；
- 单纯记录不会被强制扩展成营养课；
- 风格改写不改变适宜性、不确定性和引用；
- 任一新 Agent 失败时，已完成的业务写入不丢失；
- Shadow、Canary、On 和回退都可在 Trace 中区分。

## 16. 分阶段实施计划

### Increment DG-0：冻结范围和评测样本

- 将本期范围固定为“识别菜品 + 饮食适宜性”，删除图片热量承诺；
- 准备第一批中国餐食冻结评测集；
- 定义适宜性人工评审标准；
- 确认第一版服务人群和医疗转介边界。

验收：任何后续需求都不能在没有单独设计的情况下重新引入图片克重和热量估算。

### Increment DG-1：契约、Graph 和 Manifest

- 新增两个 AgentRole 和 GraphNode；
- 实现 `DishRecognitionResult`、`ConfirmedDishSet`、`DishLookupPlan`、
  `DishEvidenceBundle`、`DietGuidanceAssessment`；
- 增加权限、循环预算、Artifact 完整性和历史兼容测试；
- 管理台先支持显示占位 Trace。

验收：非法跨节点、伪造 ID、置信度升级和越权工具调用均被代码拒绝。

### Increment DG-2：菜品识别与确认

- 升级现有 `inspect_image` 餐食输出或增加专用识别入口；
- 实现 Dish Recognition Agent；
- 保存不可变识别 Artifact；
- 实现 `PendingDishConfirmation` 和跨轮恢复；
- 确保一次图片只触发一次视觉请求；
- 在管理台支持人工更正菜名。

验收：不确定时不进入营养建议；确认后不重复识图并能继续原任务。

### Increment DG-3：菜品结构化数据库

- 增加菜品实体、别名、特征和规则表；
- 实现离线导入、审核、发布、退休和版本治理；
- 先覆盖评测集和常见中国家常菜，不追求一次覆盖全部菜系；
- 所有特征和规则保留来源及审核人。

验收：线上只能查询已发布实体/规则；每项判断能够追溯到数据版本。

### Increment DG-4：营养证据检索 Agent 与 RAG

- 实现精确匹配、别名匹配和歧义返回；
- 增加版本化只读菜品/规则工具；
- 复用现有 Nutrition Knowledge 生命周期和引用校验；
- 导入、审核和发布第一批权威资料；
- 词法检索先上线，只有离线评测证明需要时再接向量召回。

验收：未发布资料泄漏率为 0；实体和引用全部可回库验证；无结果时不编造。

### Increment DG-5：升级 Nutrition Agent 和 Reviewer

- 将现有 Nutrition Agent 扩展为 Diet Guidance 能力；
- 增加逐菜适宜性和硬性 `avoid` 校验；
- 增加“典型做法不等于本盘事实”的校验；
- 扩展 Reviewer 的图片不确定性、引用和绝对禁食规则；
- 保持 Style Profile 自主迭代能力不变。

验收：人工黄金集上，所有建议有证据，所有绝对避免结论有硬依据。

### Increment DG-6：管理台、Shadow 和发布

- 管理台展示三个新模块的完整 Trace；
- 支持按识别是否确认、实体是否命中、适宜性和 Reviewer 结论筛选；
- 对同一批冻结样本比较 Legacy 与新链路；
- 按 `off → shadow → canary → on` 发布；
- 保留快速切回当前 Harness 的路径。

验收：全量启用前完成识别、检索、建议和端到端人工评审；回退演练通过。

## 17. 推荐配置策略

不建议为每个 Agent 暴露一套互相冲突的发布模式。使用两层开关：

```text
MULTI_AGENT_MODE=off|shadow|canary|on
MEAL_GUIDANCE_ENABLED=false|true
```

内部能力开关只用于诊断和紧急降级：

```text
DISH_RECOGNITION_ENABLED
NUTRITION_RETRIEVAL_ENABLED
DIET_GUIDANCE_ENABLED
```

正式采用时还必须满足：

```text
RESPONSE_REVIEWER_ENABLED=true
STYLE_RENDER_ALL_NORMAL_REPLIES=true
NUTRITION_RAG_ENABLED=true
```

如果菜品数据库或 RAG 尚未准备好，可以在 Shadow 中运行，但不得以正式专业建议对用户全量启用。

## 18. 最终产品边界

第一版用户体验应是：

> 我先帮你认出这几道菜；看不准的地方会问你。确认以后，我会根据你明确告诉我的目标和限制，查已经
> 审核过的菜品资料和饮食指南，告诉你哪些可以正常吃、哪些需要调整或少吃。图片本身不能可靠算出克重
> 和热量，所以这一版不会给看似精确的热量数字。

这条边界同时决定技术实现：视觉 Agent 只认菜，Retrieval Agent 只找证据，Guidance Agent 只基于证据
做判断，Style Agent 只负责表达，Reviewer 负责阻止结论在传递过程中被放大或改写。

## 19. 与已有文档的关系

- `NUTRITION_VISION_AGENT_RESEARCH.md` 保留为完整图片营养能力的前期调研；
- 本文覆盖并收窄其中与 FoodSAM、份量和热量相关的实施范围；
- `MULTI_AGENT_ARCHITECTURE.md` 中 Agent 不直接互调、结构化 Artifact、最小上下文和 Reviewer 修复边界继续有效；
- `MULTI_AGENT_ROLLOUT.md` 中 `off → shadow → canary → on` 和快速回退策略继续有效。

若本文与前期图片热量设计冲突，本期以本文为准。
