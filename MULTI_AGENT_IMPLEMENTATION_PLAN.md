# SlimGuard 多 Agent 与可观测管理台实施计划

> 版本：v1.0
> 日期：2026-09-04
> 状态：实施中
> 关联设计：`MULTI_AGENT_ARCHITECTURE.md`

## 1. 目标与交付原则

本计划把多 Agent 运行时、全局回复风格、营养专业能力、专业 RAG、忠实度审查以及现有 React
管理台作为同一个工程目标交付。管理台不是多 Agent 完成后的附属功能：每新增一个运行节点，必须在同一
Increment 中补齐 Trace、白话 Presentation、前端展示和测试，否则该节点不算完成。

最终系统需要做到：

1. 保留现有 Harness 作为唯一控制平面，不另建一套失控的 Agent Runtime；
2. 对话编排 Agent 根据语义和工具观察选择当前需要的能力；
3. 营养专业 Agent 只负责形成有证据的专业分析，并能调用独立只读工具与 RAG；
4. 所有正常用户沟通都经过统一的 Response Style Agent，而不是只风格化营养建议；
5. Style Agent 只改变表达，不改变事实、记录状态、专业结论、置信度、风险和引用；
6. Memory 继续采用共享读取、集中治理、单一长期记忆写入通道；
7. Coordinator 管理一张有条件分支和有限纠错边的状态图，而不是写死单向 `A → B → C`；
8. 每次专业 RAG 的检索、采用和最终引用都可核对到具体资料、版本和章节；
9. 管理员可以从用户列表进入，清楚看到一次回复实际经过了哪些 Agent、为什么返回上游、最终采用了
   哪个版本；
10. 全部新能力支持 `off → shadow → canary → on`，随时可以切回当前单 Agent 链路。

本计划不包含模型微调，也不把 Agent 拆成独立微服务。第一阶段继续使用同一个 Python 应用、同一个
PostgreSQL 和现有 React 管理台。

## 2. 目标运行图

### 2.1 正常路径

```text
用户输入
  → 输入安全预检
  → Memory Ingestion
  → 权威数据加载与 Memory Recall
  → 对话编排 Agent
       ↔ 业务工具 / 图片观察
       ├─ 普通沟通 ───────────────────────────┐
       └─ 需要专业分析 → 营养专业 Agent         │
                              ↔ RAG / 计算工具  │
                              ─────────────────┤
                                               ↓
                                    结构化 ResponsePlan
                                               ↓
                                    Response Style Agent
                                               ↓
                                      忠实度审查 Agent
                                               ↓
                                        Output Guard
                                               ↓
                                           发送
```

所有正常用户可见回复，包括闲聊、确认、追问、纠正、鼓励、资料查询、专业建议和主动提醒，都经过统一
Style Profile。验证码、权限错误、系统故障和紧急健康安全文本使用专门的安全或操作模板，不强制套用
严格教练风格。

### 2.2 允许的有限返回边

代码保存的是允许转换的图，不是固定调用序列：

```text
REVIEW ── style_drift / changed_meaning ───────→ STYLE
REVIEW ── unsupported_professional_claim ─────→ NUTRITION
REVIEW ── missing_user_evidence ──────────────→ ORCHESTRATOR
NUTRITION ── permitted_context_request ───────→ MEMORY_RECALL → NUTRITION
```

约束如下：

- Agent 不直接互相调用，所有返回都由 Coordinator 校验和执行；
- Style 问题不重新运行 Nutrition；
- 专业问题修复后必须重新生成 Style，并重新审查；
- 每个 Specialist 最多补取一次上下文；
- Style 最多修复一次，Nutrition 最多修复一次；
- 整个 Turn 最多发生两次上游纠错；
- 达到上限后使用中性渲染或保守回复，不无限循环。

## 3. 对现有系统的改造边界

### 3.1 保留不动的核心

- `AgentRuntime` 和渠道无关请求；
- `HarnessTurnRunner` 的 Turn 初始化、安全预检、记忆生命周期和持久化职责；
- Thread / Turn / Item 数据模型；
- `ToolGateway`、Schema、权限、幂等、确认和事务；
- 体重、体脂、饮食、运动、提醒等现有领域服务；
- PostgreSQL 作为用户事实和业务记录的权威来源；
- Mem0 作为用户记忆候选的语义索引，不成为事实源；
- 企业微信、App 和主动消息 Delivery；
- `interaction_traces`、`trace_spans`、按用户查看管理台的现有入口。

### 3.2 需要新增或演进的部分

```text
src/slim_guard/
├── agents/
│   ├── contracts.py
│   ├── structured_runner.py
│   ├── nutrition/
│   ├── style/
│   └── review/
├── orchestration/
│   ├── coordinator.py
│   ├── graph.py
│   ├── artifacts.py
│   ├── evidence.py
│   ├── routing.py
│   └── fallback.py
├── knowledge/
│   ├── contracts.py
│   ├── repository.py
│   ├── retrieval.py
│   ├── ingestion.py
│   └── citations.py
└── harness/
    ├── runner.py              # 接入 Coordinator
    ├── loop.py                # 保留兼容，并抽象为单节点 Runner
    ├── context.py             # 演进为角色化 Context Compiler
    ├── trace.py               # 增加 invocation/artifact/transition 事件
    └── manifest.py            # 增加 Graph Manifest

frontend/src/
├── types.ts                   # 多 Agent Trace API 类型
├── api.ts
├── App.tsx                    # 工作流总览和路由入口
└── components/trace/
    ├── WorkflowGraph.tsx
    ├── AgentInvocationCard.tsx
    ├── EvidencePanel.tsx
    ├── CitationPanel.tsx
    ├── RepairHistory.tsx
    └── ShadowComparison.tsx
```

上述目录是目标组织方式，实施时优先保持小提交；不为移动文件而一次性重写现有代码。

## 4. 核心数据契约

### 4.1 Agent 调用与 Artifact

每个 Agent 节点使用统一调用信封：

```text
AgentInvocation
  invocation_id
  trace_id / turn_id / graph_version
  agent_role / agent_version / attempt
  parent_invocation_id
  input_artifact_ids
  allowed_tools / privacy_scopes
  deadline / model-call / tool-call / token limits

AgentArtifact
  artifact_id / turn_id
  producer_role / artifact_type / schema_version
  parent_artifact_ids
  payload_sha256 / payload
```

Artifact 完成后不可原地修改。修复产生新 Artifact，并通过 `parent_artifact_ids` 指向旧版本。管理台据此
展示第一次结果、审查问题、修复结果以及最终采用版本。

### 4.2 ResponsePlan

Style Agent 的输入不是一段可随意改写的长文，而是结构化内容计划：

```text
ResponsePlan
  communication_act
  requested_detail
  content_blocks[]
    block_id
    kind: fact / claim / action / question / risk / uncertainty / social_act
    text
    source_refs[]
    required
  citation_refs[]
  prohibited_transformations[]
```

`social_act` 用于没有专业事实的日常交流，例如确认、接住情绪或自然追问。它仍由 Orchestrator 根据
对话语义产生，Style Agent 不能借此新增用户事实和专业判断。

### 4.3 ProfessionalAssessment 与引用

每条专业 Claim 都要标记依据类型：

```text
user_evidence
visual_observation
deterministic_calculation
model_prior
rag_evidence
```

实际使用 RAG 的 Claim 必须引用 `KnowledgeCitation`：

```text
citation_id / source_id / chunk_id
title / publisher / published_at / version
section_or_page / source_url
applicability / review_status
retrieved_in_invocation_id
```

没有知识库资料时，检索工具明确返回 `corpus_status=empty`，不能伪造来源。高风险、疾病治疗、用药和
精确剂量等内容不得仅依据 `model_prior` 生成。

## 5. 管理台目标体验

现有入口保持为：

```text
用户列表 → 用户详情 → 输出链路列表 → 单次链路详情
```

单次链路详情由五个区域组成。

### 5.1 本轮摘要

- 用户输入和最终回复；
- 生成与投递状态；
- Legacy / Shadow / Canary / Multi-Agent 模式；
- Graph、各 Agent 和 Style Profile 版本；
- 总耗时、模型调用、工具调用和 Token；
- 是否使用 RAG、是否修复、是否降级。

### 5.2 工作流图

按实际运行节点展示，不显示没有执行的伪步骤。节点显示状态、耗时、尝试次数和工具数量；边显示正常
前进、补充上下文、返回修复或降级。点击节点进入对应 Invocation 详情。

### 5.3 Agent 分组时间线

时间线按 Invocation 分组，而不是把所有 `model_message` 平铺在一起。每组默认用白话说明：

- 这个 Agent 收到了哪些类别的信息；
- 为什么被调用；
- 调用了哪些工具；
- 产出了什么结构化结论；
- 下一步交给谁；
- 是否发生失败、修复或降级。

原始 JSON、供应商 Request ID、Token 和精确参数继续放在“技术详情”中。

### 5.4 Evidence 与 Claim 映射

分开展示用户原话、Working Memory、长期记忆、数据库记录、图片观察、计算结果和专业资料。每条专业
Claim 能展开查看其 Evidence 和 Knowledge Citation；无依据 Claim 显示为错误而不是正常结果。

### 5.5 RAG 与 Shadow

RAG 面板区分“检索候选”和“最终采用”。只有被最终 Claim 使用的资料进入用户回复引用；所有候选仍在
管理台保留审计信息。

Shadow 页面并排显示现有线上结果和新工作流候选结果，并明确标记新结果“未发送、无业务写入”。

管理台不会保存或展示模型隐藏思维链，只展示可验证的输入摘要、决策结果、工具观察、结构化输出、
证据、引用和修复原因。

## 6. 分阶段实施

实施进度：Increment 0 已于 2026-09-04 完成，Increment 1 已于 2026-09-05 完成，
Increment 2 和 Increment 3 已于 2026-09-06 完成，Increment 4 已于 2026-09-07 完成，
Increment 5 已于 2026-09-08 完成；
Increment 6 已扩展到真实 HTML 本地整理、医生风格规范草案、受审版本和示例接入、A/B 及人审发布工具；
六类去事实化表达示例及规范已经用户逐项确认，固定资产的 12 组合成 A/B 已完成真实模型生成且自动
风格/忠实度评估通过，并已导入本地目标应用数据库；首轮独立人工 A/B 已完成，结果为接受 1、拒绝 11，
因此 `doctor_strict_v1` 明确不发布。拒绝批注作为下一版本的修订输入保留，A/B Case 现会绑定并展示
合成场景、已确认上下文和回复目标，避免脱离语境评分；基于批注形成的 `doctor_strict_v2` 已完成
12 组真实生成和自动评估并导入管理台，正在等待带场景的第二轮人工评分；默认资产未改变
（见 STYLE_ASSET_RUNBOOK.md）；
Increment 7 的 Canary/on 运行时、运营指标、筛选和只读放量检查已实现；
真实线上任务固定集、人评、真实失败样本复测及线上放量尚未执行，默认保持 off。

### Increment 0：设计冻结、契约和 Trace 规范

#### 后端

- 根据最新评审把架构文档升级为 v1.2；
- 新增 Invocation、Artifact、Directive、ResponsePlan、Assessment、Citation、StyledResponse 和
  ReviewerVerdict Pydantic 类型；
- 定义 Graph 节点、允许边、错误分类和循环预算；
- 给 `ModelPurpose` 增加 orchestrator、nutrition、style、reviewer 等明确用途；
- 给 Model Gateway 增加结构化 JSON 响应契约，并保留本地 Pydantic 校验；
- 定义 Graph Manifest，冻结每个 Agent 的模型、Prompt、工具、Schema 和版本。

#### Trace 与管理台

- 定义统一 Trace Event Schema：`invocation_started/result`、`artifact_created`、
  `workflow_transition`、`response_adopted`、`response_degraded`；
- 扩展 `frontend/src/types.ts`，但暂不改变现有页面行为；
- 给 `admin/presentation.py` 增加新操作的白话 Presentation；
- 明确敏感字段默认脱敏策略。

#### 验收

- 所有 Schema 和允许边有单元测试；
- 非法边、伪造 Artifact 引用和越权工具被拒绝；
- 旧 Trace API 与前端保持兼容；
- 当前生产回复完全不变。

### Increment 1：Shadow Coordinator 与工作流管理台

#### 后端

- 实现 `AgentWorkflowCoordinator` 和通用 `StructuredAgentRunner`；
- 在 `HarnessTurnRunner` 完成现有记忆和 Context 阶段后启动 Shadow Graph；
- Shadow 节点不获得写工具权限，不发送候选回复；
- 新旧链路共享 `trace_id/turn_id`，但 Invocation 和 Artifact 独立；
- 实现节点 Deadline、调用预算、取消和中性终止。

#### Trace 与管理台

- 增加工作流总览图；
- 增加按 Agent Invocation 折叠的时间线；
- 增加 Shadow 对比面板；
- 显示节点未运行、跳过、失败和降级原因；
- 支持按 Agent 角色、状态、是否修复筛选。

#### 验收

- Shadow 不产生第二次体重、饮食或记忆写入；
- 新 Agent 故障不影响当前线上回复；
- 管理台能还原实际节点和父子关系；
- 页面不会把 Shadow 候选标记成已发送。

### Increment 2：全局 Response Style Agent

#### 后端

- 实现 `StyleProfileRepository`、`StyleContextCompiler` 和 `ResponseStyleAgent`；
- 首先发布接近现有体验的 `slimguard_default_v1`，不立即启用医生风格；
- Orchestrator 的所有正常响应路径都生成 `ResponsePlan`；
- 所有正常用户回复进入 Style Agent；
- 安全和操作性文本使用明确的旁路策略；
- Style 失败时使用中性渲染器表达相同内容；
- 对 required block、事实、数字、记录状态和引用做确定性完整性检查。

#### Trace 与管理台

- 展示 Style Profile 名称和版本；
- 展示进入 Style 的内容块类型和来源，不默认暴露敏感全文；
- 展示风格渲染结果、降级结果和最终采用版本；
- 展示“为什么本轮绕过风格层”。

#### 验收

- 闲聊、打卡、追问、提醒和专业回复的表达人格一致；
- Style 不能把工具失败改成保存成功；
- Style 不能增加新的体重、食物、疾病或专业建议；
- Style 服务故障不影响记录和安全回复。

### Increment 3：Nutrition Agent 与 Evidence Plane

#### 后端

- 实现 Evidence Builder 和 `NutritionContextCompiler`；
- 实现 Nutrition Agent 的独立只读 Tool Registry；
- 首批加入 BMI、趋势和打卡完成度等确定性计算工具；
- 加入空实现的知识检索接口，知识库为空时显式返回空状态；
- 生成结构化 `ProfessionalAssessment`；
- 检查 Claim → Evidence、Action → Claim 的引用完整性；
- 先 Shadow，不能直接改变线上专业回复。

#### Trace 与管理台

- 增加 Evidence 来源面板；
- 展示每条专业 Claim、置信度和来源类型；
- 展示图片观察与用户自述的权威等级；
- 对证据不足、模型一般知识和确定性计算使用不同视觉标识。

#### 验收

- Nutrition Agent 无写库、发送和用户全库读取权限；
- 图片不确定性不会变成确定结论；
- 知识库为空时不出现虚假文章；
- Nutrition 失败时仍能完成原始记录确认。

### Increment 4：Nutrition Knowledge Plane 与 RAG 引用

#### 后端

- 新增知识源、分块、导入批次和审核记录的数据表；
- 实现离线导入、去重、文档哈希、分块、索引和停用；
- 建立关键词 + 向量候选、Metadata Filter 和 Rerank 接口；
- 实现 `search_nutrition_knowledge` 与 `get_nutrition_source`；
- 实现 Citation Coverage/Integrity/Applicability Validator；
- 知识库与用户 Mem0 使用不同存储边界和命名空间；
- 未审核、已退休或不适用资料不能支撑线上 Claim。

#### Trace 与管理台

- 增加 RAG 检索候选列表；
- 增加最终采用引用列表；
- 展示标题、机构、版本、章节、链接、审核状态和适用范围；
- 展示每个引用支撑了哪条 Claim；
- 用户最终文本使用紧凑引用，管理台保留完整审计信息。

#### 验收

- 使用 RAG 的知识性 Claim 引用覆盖率为 100%；
- 不存在跨 Invocation 伪造 Citation；
- Style Agent 不会删除或错配引用；
- 资料停用后新 Turn 不再检索到，但历史 Trace 仍可审计原版本。

### Increment 5：Reviewer 与有限返回边

#### 后端

- 实现 Response Reviewer；
- 输出 `verdict + repair_target + issue_type + reason_summary`；
- 根据问题类型返回 Style、Nutrition 或 Orchestrator；
- 专业修复后强制重新渲染和审查；
- 实现每节点和全 Turn 循环预算；
- 达到上限后中性或保守降级。

#### Trace 与管理台

- 在工作流图中可视化返回边；
- 显示审查问题、目标节点和修复次数；
- 并排展示旧 Artifact、修复 Artifact 和最终采用 Artifact；
- 增加审查拒绝率、修复率和降级率。

#### 验收

- 风格漂移只返回 Style；
- 无依据专业结论返回 Nutrition；
- 缺少用户证据返回 Orchestrator 或询问用户；
- 二次失败不会无限循环；
- 管理台能说明为什么回退以及最终采用什么。

### Increment 6：`doctor_strict_v1` 风格资产

2026-09-08 已按用户授权开始医生语料处理及 Increment 6 实现。HTML 本地整理、表达规范草案、
受审版本/示例接入、真实 A/B 工具及人工评分/发布门槛已补齐。模型凭据和脱敏文字处理授权已具备，
首批 7 组已完成两轮真实提取，后续 4 组完全去事实化补充文本已形成后三类待审模板。
六类表达模板和不冒充真人、不迁移知识的规范已由用户确认并录入 append-only 审核；固定 bundle 的
12 组合成 A/B 已用真实模型生成，自动风格、忠实度、隐私、无冒充和无新增专业主张评估全部通过。
最终 12 个合成 Case 已导入目标应用数据库。用户已完成首轮实名 A/B 人工评分：接受 1、拒绝 11；
批注中的建议说法仅作为 `doctor_strict_v2` 的修订输入，不能倒推为对新模板的批准。
`doctor_strict_v1` 未通过人工关卡，未发布、未灰度、未启用。后续 A/B Case 必须同时展示并哈希绑定
合成场景、已确认上下文和回复目标，避免审核人凭空判断。
`doctor_strict_v2` 已根据实名批注收紧为短句、直接、少铺垫的表达，并修正含糊的合成 ResponsePlan；
最终 12 组真实 A/B 生成完整、自动评估 12/12 通过，已导入为 12 条待人工评分 Case。该自动结果
不代表用户已批准 v2，发布和启用仍被阻断。
执行步骤和真实数据状态见 `STYLE_ASSET_RUNBOOK.md`。

#### 后端与数据

- 解析微信导出并识别章之文消息；
- 合并连续消息，模型判断上下文与回复是否真实相关；
- 自动脱敏并进入人工审核队列；
- 生成 Style Spec 和按 communication act 分类的示例；
- 建立 Style Asset 版本、Eval、Canary 和回滚；
- 原始聊天不进入用户 Memory、营养 RAG 或线上 Prompt。

#### 管理台

- 展示本轮 Profile 版本、示例 ID 和沟通行为；
- 在两侧输出之前展示合成场景、系统已确认上下文和回复目标，并把场景摘要纳入 Case 完整性校验；
- 不向普通管理员展示原始群聊和其他群友隐私；
- 增加风格 A/B 结果和人工评分入口。

#### 验收

- 日常沟通与专业建议保持同一人格；
- 严格但不羞辱、不恐吓、不冒充真人医生；
- 不复制医生的专业知识、私人信息或身份化自称；
- 风格 Eval 和语义忠实度同时通过。

### Increment 7：Canary、全量和运营指标

#### 发布顺序

```text
off → shadow → test1~test5 → 小比例真实用户 → 全量 on
```

#### 管理台

- 链路列表增加运行模式、Agent 失败、RAG、修复和降级筛选；
- 增加 p50/p95 延迟、Token、节点失败率、引用覆盖率和无效引用率；
- 增加 Legacy 与 Multi-Agent 结果质量对比；
- 支持按 Graph/Agent/Profile 版本定位回归。

#### 验收

- 每个阶段都有可量化放量阈值；
- 环境变量可以快速回到 Legacy；
- 回退不会丢失数据库记录或破坏用户 Thread；
- 全量前完成固定回归集、安全集和真实失败样本复测。

## 7. 配置与发布开关

第一阶段计划增加以下配置，具体名称实施时以 `Settings` 规范为准：

```text
MULTI_AGENT_MODE=off|shadow|canary|on
MULTI_AGENT_CANARY_USER_IDS=
MULTI_AGENT_GRAPH_VERSION=typed-supervisor-v1
DEFAULT_STYLE_PROFILE=slimguard_default_v1
STYLE_RENDER_ALL_NORMAL_REPLIES=true
NUTRITION_AGENT_ENABLED=false
NUTRITION_RAG_ENABLED=false
NUTRITION_REQUIRE_RAG_CITATIONS=true
RESPONSE_REVIEWER_ENABLED=false
```

配置只决定产品启用范围和安全策略，不能用关键词配置代替模型对用户语义的理解。

## 8. 测试与 Eval

### 8.1 自动化测试

- Schema 和序列化单元测试；
- Graph 合法/非法转换测试；
- Agent 工具权限测试；
- PostgreSQL Artifact、知识库和历史版本测试；
- Citation 完整性、适用性和停用测试；
- Admin API、Presentation 与 React 组件测试；
- Shadow 无副作用集成测试；
- 超时、模型错误、Mem0 错误、RAG 错误和数据库错误注入；
- Legacy 回退测试。

### 8.2 固定业务回归集

至少覆盖：

- 当前体重和目标体重同时出现；
- 当前体脂和目标体脂同时出现；
- 重复消息和幂等写入；
- “把刚才那个保存”“下次接着做”；
- “我刚量了一下，身高应该是178”；
- 清晰、模糊、多图和图片指代；
- 普通闲聊和拒绝回答；
- 一周体重没有变化；
- 用户自述健康背景；
- RAG 为空、资料冲突、资料失效和引用伪造；
- Style 改变事实、不确定性或建议强度；
- Reviewer 返回不同上游节点；
- 紧急健康输入绕过普通风格。

### 8.3 管理台回归

每个后端行为必须验证：

- 页面白话说明准确；
- Agent、Invocation 和尝试次数正确；
- 工作流图与实际 Trace 一致；
- Shadow 与真实发送状态不会混淆；
- Claim、Evidence 和 Citation 可以互相跳转；
- 修复历史与最终采用版本清楚；
- 默认不展示隐藏思维链、完整 Prompt 和未脱敏健康隐私；
- 技术详情仍能用于排障。

## 9. 专业资料准备清单

Nutrition Agent 框架可以在没有资料时先完成，但 RAG 上线前需要为每份资料准备：

- PDF、网页或结构化原文；
- 标题、发布机构、发布日期和版本；
- 原始稳定链接；
- 适用地区和适用人群；
- 是否允许内部保存、分块和展示短摘录；
- 当前是否有效；
- 专业审核人和审核时间；
- 与其他资料冲突时的优先级说明。

未经审核的资料只能进入 `draft`，不能支撑线上专业 Claim。医生聊天记录只用于风格资产，永远不作为
Nutrition Knowledge Source。

## 10. 每个 Increment 的完成定义

一个 Increment 只有同时满足以下条件才可以提交并进入下一阶段：

1. 后端能力和失败降级已实现；
2. Trace 字段完整且可以关联到同一个用户、Turn 和 Invocation；
3. `admin/presentation.py` 已提供准确白话解释；
4. Admin API 已返回对应结构；
5. React 管理台已能清楚展示；
6. 单元、集成、前端和固定 Eval 通过；
7. 没有记录隐藏思维链或扩大敏感数据暴露；
8. Shadow/Canary/回退开关经过验证；
9. `IMPLEMENTATION_LOG.md` 记录了变更、测试和未完成风险；
10. 形成一个边界清楚、可单独回滚的 Git Commit。

## 11. 推荐的实际开工顺序

```text
第一批：Increment 0
  契约 + 状态图 + Trace 规范 + 前端类型

第二批：Increment 1
  Shadow Coordinator + 工作流图 + Agent 分组日志

第三批：Increment 2
  全局 Style 基础设施，先保持现有语气

第四批：Increment 3
  Nutrition Agent + Evidence Plane，继续 Shadow

第五批：Increment 4
  专业知识库 + RAG + 引用前后端闭环

第六批：Increment 5
  Reviewer + 有限返回边 + 修复历史

第七批：Increment 6
  完成风格评审后接入 doctor_strict_v1

第八批：Increment 7
  测试账号 → Canary → 全量
```

优先从 Increment 0 开始，不先接医生语料，也不先切换线上回复。Increment 1 完成后，管理台必须已经
可以清楚看到新工作流的 Shadow 运行结果；从那以后，每一个 Agent 后端改动都与管理台同步提交。
