# SlimGuard 三方群聊消息路由与教练风格技术设计

状态：设计稿（暂不实现）  
适用范围：Turn Harness、Core Agent、营养/RAG、记忆、工具、教练风格、移动端消息和后台 Trace。

## 1. 设计结论

SlimGuard 的聊天界面按群聊理解，群里有三个可见参与者：

~~~text
用户 user
教练 coach（当前启用医生风格）
系统助手 system_assistant
~~~

用户是输入方；教练和系统助手是系统生成的两个可见发言者。Core Agent、营养 Agent、RAG、记忆、工具和 Harness 都是后台执行节点，不作为群成员直接出现。

这不是三个真实账号，也不是三个独立 Harness。后端仍围绕同一个用户、同一个 Turn 和同一套权限工作，只是给用户可见消息标记不同的 participant 并分别渲染。

核心原则：

1. 先确定事实、状态、建议和待确认事项，再决定由哪个群聊参与者表达。
2. 系统助手可以发送自然语言，也可以发送卡片；卡片只是渲染方式。
3. 教练只表达已经批准的语义，不读取或复述 RAG 原始证据，不负责工具状态和业务澄清。
4. 大模型可以提出参与者建议，但最终路由由结构化规则校验；无法安全判断时默认由系统助手表达。
5. Style Agent 只改写教练消息，不改变参与者、事实、工具状态或系统卡片。

## 2. 背景与问题

当前系统把系统执行结果和医生语气压缩成一段 text，导致：

- 操作说明、追问和安全边界可能被错误送进教练风格。
- 工具执行结果和教练自然语言认可无法分别渲染。
- 系统助手的自然语言和系统卡片被误认为是一回事。
- RAG 证据、内部分析和最终用户表达边界不清楚。
- Trace 无法说明消息由谁发、为什么出现、是否经过 Style Agent、是否来自工具执行。

一次 Turn 应产出有序群聊消息序列：

~~~text
用户输入
  -> Turn Harness / Core Agent
  -> 专家 Agent、RAG、记忆、工具
  -> 语义结果（事实、状态、建议、待确认项）
  -> 群聊参与者路由器
  -> 系统助手消息 / 教练候选消息
  -> 仅对教练消息执行 Style Agent
  -> 文本、教练消息、系统卡片编排
  -> 持久化、Trace、发送渠道
~~~

## 3. 三方群聊参与者

### 3.1 用户

用户消息包括文字、图片和对待确认问题的补充。用户消息进入当前 Turn 或恢复已有 PendingTask，不经过教练风格迁移。participant=user 的消息只允许由客户端或发送渠道创建，不能由模型伪造。

### 3.2 教练（当前为医生风格）

教练是关系性表达通道，负责在语义已经确定后，用自然、简短、口语化的方式表达：

- 对用户行为或餐食的认可、评价和鼓励；
- 对已通过营养/安全检查的建议进行简短表达；
- 在合适时进行温和督促；
- 在不涉及操作承诺时作一句简短回应。

“医生”是当前教练的风格配置，不是固定的后端角色。未来可以增加其他教练风格，但共用群聊协议和路由校验。

教练不负责：

- 确认图片中不确定的菜品或餐次；
- 宣布记录、删除、同步等工具状态；
- 提出用户必须回答的业务澄清问题；
- 直接读取、引用或复述 RAG 原始资料；
- 在没有上游结论和证据时新增健康或医学判断。

### 3.3 系统助手

系统助手是操作、事实和安全通道，负责：

- 解释系统做了什么、没有做什么以及当前状态；
- 发送成功、失败、待确认、重试和权限结果；
- 提出菜品、餐次、日期等业务澄清问题；
- 输出安全边界、不确定性、医疗限制和错误恢复说明；
- 展示结构化查询、记录摘要、趋势结果和必要的证据说明；
- 将已验证的工具结果投影为系统卡片。

系统助手既可以发送自然语言，也可以发送卡片。例如：

~~~text
系统助手：我还不能确认图片里的菌菇是什么，请问是香菇、口蘑还是其他？
系统助手：这次晚餐还没有记录成功，请先确认餐次。
系统助手：[已记录今日晚餐] 煎三文鱼、藜麦沙拉
~~~

## 4. 用户可见消息模型

参与者和渲染方式是两个独立维度：

~~~python
class ParticipantRole(str, Enum):
    USER = "user"
    COACH = "coach"
    SYSTEM_ASSISTANT = "system_assistant"


class RenderKind(str, Enum):
    TEXT = "text"
    CARD = "card"
    IMAGE = "image"
~~~

system_assistant 可以使用 text 或 card；coach 当前只使用 text；user 可以使用 text 或 image。

推荐将当前单一 ResponseContentBlock 演进为两层：

~~~python
class SemanticBlock(BaseModel):
    block_id: str
    semantic_type: str             # record_status / clarification / evaluation / advice
    neutral_text: str | None
    claims: list[str]
    source_refs: list[str]
    required: bool = True
    coach_eligible: bool = False
    metadata: dict[str, Any] = {}


class CardPayload(BaseModel):
    card_type: str
    schema_version: str
    source_execution_id: str
    status: Literal["success", "failed", "pending"]
    data: dict[str, Any]


class ChatMessage(BaseModel):
    message_id: str
    turn_id: str
    sequence: int
    participant: ParticipantRole
    kind: RenderKind
    text: str | None = None
    card: CardPayload | None = None
    source_refs: list[str]
    required: bool
    semantic_block_id: str
    style_profile_version: str | None = None
    style_artifact_id: str | None = None
~~~

约束：

- system_assistant 消息可以是 text 或 card，但不能进入教练 Style Agent。
- kind=card 时 participant 必须是 system_assistant。
- coach 当前只允许 text，且通常是可选消息。
- 所有事实必须有 source_refs；来源可以是工具执行、营养分析结论、结构化档案或本轮用户输入。
- 语义块和最终文案分开保存，便于审查是否改变了事实。
- user 消息不由模型创建，系统消息不能伪装成 user。

## 5. 总体架构

群聊路由位于专家/工具产出语义结果和最终发送之间：

~~~text
Turn Harness
  ├─ 创建并持久化 Turn
  ├─ 输入安全检查
  ├─ 召回档案、领域记录、长期记忆、工作记忆和 RAG
  ├─ 冻结模型、工具、Prompt 和教练风格版本
  ├─ Core Agent 调用工具/专家 Agent
  ├─ 生成 SemanticResponsePlan
  ├─ 群聊参与者路由器（模型提议 + 规则校验）
  ├─ 系统助手文本渲染 / 卡片投影
  ├─ Style Agent（只接收教练候选）
  ├─ Style Validator / 共享审查
  ├─ OutputComposer（合并、排序、去重）
  ├─ 持久化 ChatMessage 和 Trace
  └─ 交给发送渠道
~~~

Core Agent 可以编排其他 Agent 和工具，但不能绕过参与者路由器直接发送任意群聊消息。模型提出的 proposed_participant 只是候选，不是权限。

教练和系统助手不需要两套 Harness。安全、鉴权、上下文冻结、工具调用、幂等和发送仍由 Turn Harness 负责。

## 6. 参与者路由算法

### 6.1 路由不是关键词意图分类

路由解决的是：已经确定的语义块由哪个可见参与者负责表达。它不维护一份覆盖所有场景的关键词枚举。

~~~text
事实/工具结果/分析结论
  -> 大模型提出消息分配建议
  -> 规则检查操作状态、待确认、安全、证据和重复度
  -> 生成系统助手消息与教练候选消息
  -> 仅教练候选进入 Style Agent
~~~

默认拒绝：无法证明适合教练表达的内容由系统助手表达；无法安全表达的内容转为系统澄清或错误说明。

### 6.2 大模型：提出候选，不拥有最终决定权

大模型可以结合输入、Turn 上下文、工具结果和专业分析，提出结构化消息建议：

~~~json
{
  "proposals": [
    {
      "proposed_participant": "coach",
      "semantic_type": "meal_evaluation",
      "content_plan": "对本次晚餐搭配做一句简短认可",
      "source_refs": [],
      "required": false
    },
    {
      "proposed_participant": "system_assistant",
      "semantic_type": "record_status",
      "content_plan": "告知晚餐记录成功",
      "source_refs": ["tool-exec-123"],
      "required": true
    }
  ]
}
~~~

确定性路由器必须重新校验：

- 是否有未解决的 PendingTask；
- 是否有菜品、餐次或日期不确定性；
- 是否涉及工具状态、安全边界或医疗限制；
- 教练候选是否有批准的营养/健康语义；
- 是否包含或泄露 RAG 原文；
- 是否重复系统助手文本或卡片；
- 每条教练候选是否有独立的语义来源，是否与其他教练候选重复；
- 是否与待确认任务、系统操作状态或安全结论冲突。

校验后才生成真正的 ChatMessage。大模型不可以自行创建“工具成功”状态。

### 6.3 系统助手必需消息

以下内容必须由系统助手承担：

- 成功、失败、待确认、未执行等当前操作状态；
- 用户必须回答的业务澄清问题；
- 图片或文本中的关键不确定性；
- 安全边界、风险提示、医疗限制和错误恢复；
- 工具结果、记录摘要和系统卡片；
- RAG 原始资料、引用、证据等级或用户明确要求的依据说明。

系统助手可以用自然语言表达这些内容，不要求全部使用卡片。

### 6.4 教练候选准入

只有满足以下条件，才允许生成教练候选：

1. 事实已确认，或内容是纯关系性表达；
2. 不包含工具成功/失败承诺、不负责解决业务澄清；
3. 营养/健康判断已经形成 approved_semantic_content，并通过安全检查；
4. 不包含 RAG 原文、文档标题、章节、引用编号或检索片段；
5. 不重复系统助手已经完整表达的事实或卡片；
6. 具有独立的语义来源，不是同一语义块或同一工具执行的重复候选；
7. 不与待确认任务、系统操作状态或安全结论冲突。

教练候选是可选消息。系统不设置每轮教练消息数量上限，也不设置跨轮时间冷却；系统只做语义去重、相同来源合并、冲突抑制和幂等控制。系统助手必需消息不能因教练失败而缺失。

如果同一轮有多个互不重复的教练候选，可以分别发送，也可以由 OutputComposer 按语义相关性合并成一条。合并是编排优化，不是数量限制。

## 7. “评价晚餐”的处理

“评价晚餐”不是固定归入卡片，也不是固定归入系统助手，要看评价包含的语义。

### 7.1 关系性评价：教练

~~~text
用户：这是我的晚餐，帮我记一下。（图片）
识别结果：煎三文鱼、藜麦沙拉，置信度足够
工具：record_meal -> success

教练：行，这顿搭配挺好。
系统助手：[已记录今日晚餐] 煎三文鱼、藜麦沙拉
~~~

“这顿搭配挺好”是教练的自然语言认可，不是卡片，也不需要把 RAG 证据交给教练。

### 7.2 事实或操作评价：系统助手

~~~text
系统助手：已记录今日晚餐，包含煎三文鱼和藜麦沙拉。
~~~

这里重点是记录状态和事实，不是关系性表达。

### 7.3 有营养结论的评价：先分析，再由教练表达

~~~text
营养分析：本次餐食蛋白质来源充足，蔬菜量偏少；结论已通过安全检查
教练：鱼肉不错，蔬菜再多加一点就更好了。
系统助手：已记录今日晚餐。
~~~

教练只收到允许表达的结论，不收到 RAG 原始证据。用户追问“为什么”时，由系统助手或专业回答模块解释依据。

### 7.4 混合内容必须拆块

如果候选同时包含“记录成功”和“搭配不错”，必须拆成：

~~~text
教练：这顿搭配不错。
系统助手：已记录今日晚餐。
~~~

不能把整句交给教练 Style Agent。

同一语义块在模型重试、网络重放或多个内部 Agent 重复提议时，只保留一个候选。去重依据是 semantic_block_id、source_refs、工具执行 ID 和幂等键，而不是教练消息数量。

## 8. RAG、营养分析与教练表达边界

RAG 是后台知识来源，不是可见发言者：

~~~text
RAG 检索原始资料和片段
  -> 营养/健康分析模块提炼结构化结论
  -> 证据、适用范围和安全检查
  -> approved_semantic_content
  -> 系统助手解释或教练口语化表达
~~~

教练接收：

~~~json
{
  "approved_semantic_content": "本餐蛋白质来源充足，但蔬菜量还可以增加",
  "allowed_claims": ["蛋白质来源充足", "建议增加蔬菜"],
  "forbidden_claims": ["医学诊断", "绝对禁忌"],
  "source_refs": ["nutrition-claim-789"]
}
~~~

Style Agent 只使用允许表达的语义，不读取 RAG 原始文本。source_refs 只用于 Trace 和审查，不自动出现在教练消息中。用户明确询问依据时，由系统助手输出简化来源解释或引用。

## 9. 待确认任务与群聊行为

待确认任务是状态对象：

~~~python
class PendingTask(BaseModel):
    task_id: str
    task_type: str                 # meal_identity / meal_time / date / confirmation
    turn_id: str
    original_input_refs: list[str]
    known_facts: dict[str, Any]
    unresolved_fields: list[str]
    question_text: str
    expected_answer_schema: dict[str, Any]
    expires_at: datetime | None
    status: Literal["open", "resolved", "cancelled", "expired"]
~~~

用户下一条消息先由 PendingTaskResolver 尝试填充答案：

- 能填充：合并原始图片/文本与补充内容，恢复原任务；
- 不能填充：系统助手继续追问，教练不插入确认问题；
- 用户取消：系统助手关闭任务并说明未记录。

示例：

~~~text
用户：这是我的晚餐。（图片）
系统助手：我还不能确认图片里的菌菇是什么，请问是香菇、口蘑还是其他？
教练：不发言

用户：香菇。
系统助手：收到，我按香菇继续记录。
系统助手：[已记录今日晚餐]
~~~

## 10. 工具结果与系统卡片

ToolEffectLevel 描述副作用，不决定是否展示。新增独立的 PresentationPolicy：

~~~python
class PresentationPolicy(str, Enum):
    NEVER = "never"
    ON_SUCCESS = "on_success"
    ON_FAILURE = "on_failure"
    ON_RESULT = "on_result"
    ON_USER_REQUEST = "on_user_request"
~~~

推荐默认值：

| 工具/结果 | 默认可见形式 |
|---|---|
| 记录餐食、体重、运动 | 系统助手成功卡片；失败使用系统自然语言或错误卡片 |
| 查询趋势、历史记录 | 用户请求时由系统助手展示卡片或文本 |
| 图像识别中间结果 | 默认不可见；不确定时由系统助手追问 |
| RAG 检索、营养证据 | 默认不可见；需要依据时由系统助手解释 |
| Mem0/工作记忆提取 | 不展示，仅进入 Trace/后台 |
| 安全检查、权限判断 | 拦截或需要行动时由系统助手说明 |

卡片投影必须满足：

- source_execution_id 对应真实、已验证的工具执行；
- success 卡片只能在工具真实提交成功后生成；
- 以执行 ID 或幂等键去重，Turn 重放不会创建第二张卡片；
- 卡片 schema 版本未知时，前端降级为系统助手文本。

## 11. 教练 Style Agent 与共享审查

Style Agent 只处理 participant=coach 的语义候选：

~~~json
{
  "participant": "coach",
  "semantic_text": "对本次晚餐搭配做一句简短认可",
  "allowed_claims": [],
  "forbidden_claims": ["记录成功", "RAG原文", "医学诊断"],
  "source_refs": [],
  "style_profile_version": "doctor_v4"
}
~~~

输出：

~~~json
{
  "text": "行，这顿搭配挺好。",
  "preserved_claims": [],
  "added_claims": [],
  "participant": "coach",
  "style_profile_version": "doctor_v4"
}
~~~

系统助手文本、系统卡片和 RAG 原文不进入教练 Style Agent。

风格训练器和在线教练 Style Agent 共享 StyleReviewService：

- 在线：检查是否保留批准语义、是否新增 claim、是否越权或重复系统助手；
- 离线：比较 Guide、示例和候选教练文案，输出风格匹配、语义忠实、表达适宜等指标。

审查不负责修改文案。失败时最多有限重试，并把结构化原因传给下一次尝试；仍失败则丢弃教练消息，保留系统助手必需消息。

## 12. 持久化、API 与移动端

### 12.1 后端持久化

建议新增或演进：

~~~text
chat_messages
  id
  turn_id
  sequence
  participant              user | coach | system_assistant
  render_kind              text | card | image
  text
  card_type
  card_payload_json
  semantic_block_id
  source_refs_json
  required
  style_profile_version
  style_artifact_id
  delivery_status          pending | sent | failed
  created_at
~~~

历史单段 assistant.text 消息迁移为 participant=system_assistant。不要把历史系统操作文案自动当作教练素材。

### 12.2 移动端 API

新客户端以 messages 为准，旧客户端暂时保留 text：

~~~json
{
  "request_id": "req-1",
  "turn_id": "turn-1",
  "text": "行，这顿搭配挺好。",
  "messages": [
    {
      "id": "msg-coach-1",
      "participant": "coach",
      "kind": "text",
      "text": "行，这顿搭配挺好。",
      "sequence": 1
    },
    {
      "id": "msg-system-1",
      "participant": "system_assistant",
      "kind": "card",
      "card": {
        "type": "meal_record",
        "status": "success",
        "data": {"meal": "晚餐", "foods": ["煎三文鱼", "藜麦沙拉"]}
      },
      "sequence": 2
    }
  ]
}
~~~

重放相同 request_id 时返回已持久化消息，不重新调用工具或 Style Agent。

### 12.3 前端群聊展示

- 用户消息使用用户气泡；
- 教练消息显示教练头像、名称和当前风格名称（当前为医生）；
- 系统助手消息显示 SlimGuard 系统助手名称；
- 系统助手卡片使用系统卡片样式，不套教练气泡；
- 同一 Turn 可以按 sequence 交错展示教练和系统助手消息；
- Trace 和后台按参与者展示原始语义、Style 前后文案、工具/RAG 来源和最终发送状态。

## 13. Trace 与可观测性

每个群聊消息记录：

~~~text
semantic_plan_created
participant_proposal_created
participant_assigned
participant_suppressed(reason_code)
system_text_rendered
card_projected(source_execution_id)
coach_style_started
coach_style_rejected(reason_codes)
coach_style_retried(attempt)
chat_message_persisted
chat_message_delivered
~~~

后台按以下链路展示：

~~~text
用户消息
  -> 语义结果
  -> 参与者分配建议
  -> 规则校验
  -> 教练 Style Agent（如有）
  -> 系统助手文本/卡片
  -> 最终群聊消息
~~~

推荐抑制原因码：

~~~text
pending_clarification
missing_approved_semantic_content
operation_status_owned_by_system_assistant
duplicate_system_content
unsafe_or_uncertain_claim
rag_raw_evidence_not_for_coach
duplicate_coach_semantic
same_source_duplicate
conflicts_with_pending_clarification
style_validation_failed
~~~

## 14. 安全与错误处理

- 路由器、卡片投影器和 Style Validator 采用 fail-closed；失败时保留系统助手必需消息，丢弃可选教练消息。
- Style Agent 超时或结构化输出失败，不阻塞系统助手文本或卡片。
- 工具成功卡片必须由真实后端执行结果生成，禁止从模型回复反向解析“已成功”。
- 用户输入、图片识别和 RAG 内容不能通过提示词注入改变 participant、工具状态或卡片权限。
- RAG 原始文本和内部检索片段不进入教练消息。
- 发送失败时保留 delivery_status=failed 和可重放 ID；重试不得重复写入业务记录或卡片。

## 15. 测试策略

### 15.1 单元测试

- 清晰餐食记录成功：可以产生教练自然语言评价和系统助手成功卡片。
- 仅有客观记录结果：系统助手发送文本或卡片，教练可以不出现。
- 菜品不确定：只有系统助手澄清，无教练消息、无成功卡片。
- 待确认回复“香菇”：恢复原任务并合并来源，不创建无关 Turn。
- 记录失败：系统助手说明失败，禁止成功卡片和模糊状态的教练安慰。
- 有批准营养结论：允许教练表达，但不得看到或输出 RAG 原文。
- 无批准结论的健康判断：教练候选被拒绝并记录原因码。
- 教练不能声明工具成功，系统助手不能进入教练 Style Agent。
- 未知参与者、路由异常默认转系统助手。
- Style 前后允许 claim 集一致，新增 claim 被拒绝。
- 同一执行 ID 重放不会重复卡片。

### 15.2 集成测试

- Harness -> Core -> 专家/工具 -> 参与者路由 -> Style -> API 的完整 Turn；
- 图像不确定、用户补充、确认后成功记录的两轮流程；
- 工具失败、Style 超时、卡片 schema 不兼容时的降级；
- 旧客户端读取 text，新客户端按 messages 渲染；
- Trace 显示用户实际收到的最终群聊消息。

### 15.3 人工验收

至少覆盖清晰餐食、模糊餐食、普通查询、营养建议、记录失败、医疗边界和用户取消待确认任务。重点不是教练出现越多越好，而是三个参与者责任清晰、消息自然且不越权。

## 16. 迁移与实施顺序

### 阶段 1：群聊契约

- 增加 ParticipantRole、RenderKind、ChatMessage、PendingTask；
- 将现有单一助手文本适配为 system_assistant 消息，保持旧 API 的 text；
- 增加消息级 source_refs、semantic_block_id 和 Trace 事件。

### 阶段 2：参与者路由与卡片

- 实现模型提议格式和确定性 ParticipantRouter；
- 实现 PendingTaskResolver 和 CardProjector；
- 给工具注册 PresentationPolicy。

### 阶段 3：教练 Style Agent

- Style Agent 只接收 participant=coach 的候选；
- 风格训练素材只保留教练表达，排除系统助手文本和系统卡片；
- 在线和离线共同使用 StyleReviewService。

### 阶段 4：API、移动端和 Trace

- ChatResponse 增加 messages，客户端优先使用群聊消息序列；
- 新增用户、教练、系统助手三种气泡和系统卡片；
- 后台按参与者展示路由、Style 前后文本、卡片来源和最终发送状态。

### 阶段 5：历史数据迁移

- 历史单段助手消息统一标记为 system_assistant/legacy；
- 不把历史系统操作文案自动写入教练素材库；
- 兼容期后移除只服务于单字符串助手输出的旧逻辑。

## 17. 验收标准

1. 群聊中明确显示用户、教练和系统助手三个参与者。
2. 系统需要确认菜品时，系统助手单独追问，教练不抢话。
3. 工具成功后，系统助手的文本或卡片准确反映真实结果；教练可以补一句经过批准的自然语言评价。
4. 系统助手既能发卡片，也能发普通自然语言。
5. RAG 原始证据不会进入教练消息；需要依据时由系统助手或专业回答模块处理。
6. 大模型可以提出参与者建议，但规则校验拥有最终决定权，无法安全判断时默认系统助手。
7. 教练风格只改变教练文本，不改变事实、状态、参与者或系统卡片。
8. 工具失败、状态不确定或安全拦截时，系统助手说明不会被教练风格覆盖或稀释。
9. 用户实际收到的每条消息都能在 Trace 中定位到语义块、工具执行或知识引用。
10. 重放、失败重试和客户端刷新不会重复执行业务工具或发送成功卡片。
