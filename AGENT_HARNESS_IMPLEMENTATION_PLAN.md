# SlimGuard Agent Harness 设计与实施计划

> 版本：v0.1  
> 日期：2026-08-27  
> 状态：实施基线  
> 适用范围：从当前单轮智谱回复升级为可持久化、可评测、可演进的减脂 Agent

## 1. 文档目标

本文档整理 SlimGuard Agent 的最终技术方向，并把实现工作严格拆成：

```text
模块
  → 具体步骤
      → 更细步骤
          → 交付物和验收条件
```

本文档只描述 Agent Harness、Memory、工具、安全、Eval 和进化系统。企业微信协议细节、产品范围和通用部署继续以现有文档为准：

- [TECHNICAL_DESIGN.md](./TECHNICAL_DESIGN.md)
- [PRODUCT_DESIGN.md](./PRODUCT_DESIGN.md)
- [PHASE1_PYTHON_CHANNEL_SPIKE.md](./PHASE1_PYTHON_CHANNEL_SPIKE.md)

## 2. 已确定的架构原则

### 2.1 Model-first

文字、图片、上下文、否定、纠错、指代、多目标表达和工具选择统一交给大模型理解。

主流程不设置：

- 前置正则语义解析器；
- 封闭式 Intent Router；
- 强制单意图分类；
- 为每个认知步骤单独调用一次模型；
- 默认 OCR → 正则 → 分类的图片流水线。

规则只负责系统能够确定的内容：Schema、权限、幂等、事务、单位换算、精确计算、业务不变量、安全红线和发送限制。

### 2.2 Harness-first

模型负责理解、推理、选择工具和表达；Harness 负责：

- 构建模型上下文；
- 暴露可用工具和 Skill；
- 执行模型—工具循环；
- 控制权限、预算和停止条件；
- 持久化 Thread、Turn 和 Item；
- 暂停、恢复和失败重试；
- 记录完整 Trace；
- 运行验证和评测。

### 2.3 Open understanding, bounded effects

用户表达和问题不做穷举，但系统副作用必须是有限、声明式、可校验的工具集合。

模型可以理解任何输入；不能映射到工具的请求仍可自然回复。模型不得获得通用 SQL、任意 HTTP、文件系统或绕过业务服务的写权限。

### 2.4 Database is the source of truth

体重、饮食、运动、提醒、确认和风险状态以数据库为准，不依赖模型回忆。模型必须通过查询工具观察权威状态，通过受控写工具改变状态。

### 2.5 Eval-first evolution

模型、Prompt、Tool Schema、Skill、Context、Memory 和安全策略都必须版本化。任何候选版本先经过固定回归集、隐藏集、安全集和真实流量 Shadow，才能进入 Canary 或生产。

“自我进化”定义为：Agent 自动发现问题、提出改进、运行实验并提供证据；生产升级仍由独立门禁控制。

## 3. 当前基线与目标差距

### 3.1 当前已经具备

- 企业微信回调验签、解密和消息同步；
- 入站消息去重；
- 企业微信会话状态管理；
- 用户与 `external_userid` 映射；
- 客户资料同步；
- 文字和图片下载；
- 智谱文本/视觉模型单轮回复；
- 出站消息记录和固定失败降级；
- 基础日志与自动化测试。

### 3.2 当前缺失

- 模型 Function Calling 循环；
- Thread、Turn、Item 和 Pending Action；
- 体重、饮食、运动等权威业务记录；
- 工具注册、权限和执行网关；
- Context Compiler；
- 分层 Memory；
- Skill 渐进式加载；
- Runtime Verification；
- Agent/Prompt/Tool 统一版本；
- Eval Runner、数据集和发布门禁；
- Shadow、Canary、失败挖掘和改进 Agent。

### 3.3 迁移策略

不进行一次性重写。保留当前 `ReplyAgentProtocol` 作为降级路径，通过运行模式逐步迁移：

```text
legacy   当前单轮回复
harness  新 Harness 正常执行并回复
shadow   legacy 回复用户，Harness 只模拟运行和评测
```

## 4. 总体架构

```text
企业微信 / H5 / Scheduler / 人工后台
                    │
                    ▼
              Channel Adapters
                    │
                    ▼
              Thread Manager
                    │
                    ▼
┌──────────────── SlimGuard Harness ────────────────┐
│                                                   │
│  Context Compiler                                 │
│       │                                           │
│       ▼                                           │
│  GLM-5.2 Core Agent                               │
│       │                                           │
│       ├── final response ─────────→ Output Guard  │
│       │                                           │
│       └── tool calls                              │
│               │                                   │
│               ▼                                   │
│          Tool Gateway                             │
│     Schema / Policy / Confirmation / Idempotency  │
│               │                                   │
│               ▼                                   │
│          Domain Services                          │
│               │                                   │
│               ▼                                   │
│       Tool Result / Observation ───→ 回到模型      │
│                                                   │
└──────────────────────┬────────────────────────────┘
                       ▼
               Delivery Policy + Outbox
                       │
                       ▼
                    企业微信

横向能力：
Thread/Event Store · Memory · Skills · Trace · Eval · Evolution
```

## 5. 核心运行模型

### 5.1 Thread

一个用户与 SlimGuard 的长期 Agent 容器。Thread 可创建、暂停、恢复、停止和归档，但不把全部历史永久塞进模型上下文。

### 5.2 Turn

一次外部事件触发的 Agent 工作单元。触发类型至少包括：

```text
user_message
user_confirmation
daily_reminder
daily_review
weekly_review
human_review_completed
delivery_failed
```

### 5.3 Item

Turn 中的 append-only 原子事件：

```text
user_message
image_attachment
context_snapshot
model_message
tool_call
tool_result
approval_request
approval_result
memory_compaction
agent_message
error
```

### 5.4 Inner Loop

```text
调用模型
  ├─ 返回工具调用 → 校验 → 执行 → 结果追加到上下文 → 再次调用模型
  ├─ 等待用户确认 → 持久化 Pending Action → 暂停 Turn
  ├─ 等待人工审核 → 创建 Review Request → 暂停 Turn
  └─ 返回最终回复 → Output Guard → Outbox → 结束 Turn
```

### 5.5 Outer Loop

Outer Loop 由企业微信消息、用户确认、调度任务、人工审核和补偿任务触发。跨天目标通过数据库状态和新 Turn 继续，不依赖一个永远增长的模型会话。

## 6. 模块依赖和实施顺序

| 顺序 | 模块 | 依赖 | 阶段结果 |
|---|---|---|---|
| 0 | 契约、版本与迁移开关 | 现有代码 | 新旧 Runtime 可并存 |
| 1 | Thread / Turn / Item 持久化 | 模块 0 | Agent 运行可追溯、可恢复 |
| 2 | 减脂领域状态 | 模块 0 | 数据库成为事实源 |
| 3 | Model Gateway | 模块 0 | 标准化智谱文本、视觉和工具调用 |
| 4 | Tool Environment | 模块 1、2、3 | 模型可安全观察和改变环境 |
| 5 | Harness Core | 模块 1、3、4 | 完成 model → tool → result 循环 |
| 6 | Context、Memory 与 Skills | 模块 1、2、5 | 跨轮连续性和按需上下文 |
| 7 | 多模态感知 | 模块 3、4、5 | 图片作为 Agent 工具进入主循环 |
| 8 | Guardrails、确认和人工审核 | 模块 2、4、5 | 敏感动作可暂停、确认和恢复 |
| 9 | Delivery 与 Outer Loop | 模块 5、8 | 提醒、复盘和微信限制统一管理 |
| 10 | Observability 与 Trace | 模块 1～9 | 每次运行可解释、可归因 |
| 11 | Eval Harness | 模块 0～10 | 可以比较任意 Agent 版本 |
| 12 | Evolution System | 模块 11 | 自动发现问题、提出并验证改进 |

## 7. 模块 0：契约、版本与迁移开关

### 目标

先定义稳定边界，使旧回复和新 Harness 可以并存，并确保后续所有实验可归因。

### 步骤 0.1：定义 Agent 版本清单

#### 0.1.1 定义 `AgentManifest`

至少包含：

```text
text_model
vision_model
model_parameters
system_prompt_version
skill_versions
tool_schema_versions
tool_description_versions
context_policy_version
memory_policy_version
compaction_policy_version
safety_policy_version
code_commit
```

#### 0.1.2 计算不可变版本 ID

- 对规范化后的 Manifest 计算哈希；
- 相同组件必须得到相同版本 ID；
- 每个 Turn 创建时冻结版本，运行中不得静默切换。

#### 0.1.3 保存版本快照

- 新增 `agent_versions`；
- 保存完整 Manifest，而不是只保存模型名；
- Agent Run、Eval Run 和线上流量都引用同一个版本 ID。

### 步骤 0.2：定义核心 Protocol

#### 0.2.1 模型接口

定义 `ModelGateway`、`ModelRequest`、`ModelResponse`、`NormalizedToolCall` 和统一错误类型。

#### 0.2.2 存储接口

定义 `ThreadStore`、`TurnStore`、`ItemStore`、`PendingActionStore`。

#### 0.2.3 工具接口

定义 `ToolRegistry`、`ToolGateway`、`ToolContext`、`ToolResult`。

#### 0.2.4 环境接口

注入 `Clock`、`IdGenerator`、`DeliveryGateway`、`ObjectStorage`，避免 Eval 依赖真实时间和外部服务。

### 步骤 0.3：加入运行模式

#### 0.3.1 配置

新增 `AGENT_RUNTIME_MODE=legacy|harness|shadow`，默认保持 `legacy`，避免未完成时影响生产。

#### 0.3.2 调用边界

让企业微信同步服务只依赖统一 `TurnHandlerProtocol`；旧 `ReplyAgent` 和新 Harness 分别作为实现。

#### 0.3.3 Shadow 行为

Shadow 版本允许读取生产快照，但写工具、提醒和发送全部进入隔离环境。

### 验收条件

- 现有测试全部通过；
- `legacy` 行为不变；
- 任意一次回复都能查到完整 Agent 版本；
- 测试可以替换 Clock、Model 和 Delivery。

## 8. 模块 1：Thread、Turn、Item 和恢复

### 目标

建立 Harness 的持久化骨架，使一个 Turn 中的模型调用、工具调用、暂停和错误都可重建。

### 步骤 1.1：数据库模型

#### 1.1.1 新增 `agent_threads`

字段包含 `user_id`、状态、创建时间、最后活动时间和当前摘要版本。

#### 1.1.2 新增 `agent_turns`

字段包含 Thread、触发类型、状态、Agent 版本、step 数、deadline、开始和完成时间。

#### 1.1.3 新增 `agent_items`

保存顺序号、类型、生命周期状态、脱敏 payload、关联 Tool Call 和时间。

#### 1.1.4 新增 `pending_actions`

保存待确认命令、原始来源、过期时间、确认策略和最终处理结果。

### 步骤 1.2：事件写入规则

#### 1.2.1 Append-only

Item 不原地覆盖业务内容。状态变化通过完成事件或明确状态字段推进。

#### 1.2.2 顺序与并发

- 每个 Thread 内分配单调递增序号；
- 同一用户同时只允许一个可写 Turn；
- 新消息到达运行中 Turn 时，进入待处理事件，不并发修改同一用户状态。

#### 1.2.3 数据最小化

模型原文、健康信息和图片引用按敏感数据处理；日志只保存 ID 和摘要。

### 步骤 1.3：暂停和恢复

#### 1.3.1 恢复点

每次 Tool Result、Approval Request 和最终回复后保存 checkpoint。

#### 1.3.2 恢复规则

- 已成功的副作用不重复执行；
- 未知发送结果通过 Outbox 状态判断；
- Pending Action 使用原参数恢复，不重新理解为另一条命令。

### 验收条件

- 服务在工具执行后崩溃，重启不会重复写入；
- 用户确认后可以恢复原 Pending Action；
- 后台可以按顺序查看一个 Turn 的所有 Item；
- 同一用户并发消息不会产生交叉写入。

## 9. 模块 2：减脂领域状态与业务服务

### 目标

把聊天内容转化为可查询、可纠错、可审计的权威业务状态，为 Agent 工具提供稳定环境。

### 步骤 2.1：记录模型

#### 2.1.1 体重

新增 `weight_records`，保存标准 kg、原始值、原始单位、测量时间、测量条件、来源 Item 和状态。

#### 2.1.2 饮食

新增 `meal_records`、`meal_items`，区分用户明确描述、视觉观察和模型估算。

#### 2.1.3 运动

新增 `activity_records`，保存类型、时长、距离、步数、设备报告热量和来源。

#### 2.1.4 候选与确认

新增 `record_candidates`，仅用于不能安全直接写入的候选事实。

### 步骤 2.2：纠错和版本

#### 2.2.1 不静默覆盖

纠错产生新版本，旧记录标记 `superseded`，保留来源和纠错关系。

#### 2.2.2 撤销和删除

区分用户撤销、业务失效和隐私删除，所有动作产生审计事件。

### 步骤 2.3：确定性计算

#### 2.3.1 标准化

在领域服务中完成单位换算、用户时区和日期归属，不让模型计算最终存储值。

#### 2.3.2 趋势

提供最近记录、日差、7 日趋势、数据完整度等查询对象。

#### 2.3.3 每日状态

从权威记录推导 `pending|partial|complete|reviewed`，不让模型直接修改汇总状态。

### 验收条件

- 同一来源不能重复产生有效记录；
- 用户纠错后只有一个 active 版本；
- 所有数值趋势由代码计算；
- 所有记录能追溯到 Thread、Turn、Item 和 Tool Call。

## 10. 模块 3：Model Gateway

### 目标

统一智谱模型调用、Function Calling、视觉调用、错误分类、超时和用量记录，使 Harness 不依赖厂商响应结构。

### 步骤 3.1：文本模型适配

#### 3.1.1 Function Calling

支持 `tools`、`tool_calls`、assistant tool-call message 和 tool result message 的完整多轮协议。

#### 3.1.2 标准化响应

统一为：

```text
assistant_text
tool_calls
finish_reason
usage
provider_request_id
```

#### 3.1.3 错误分类

区分网络、限流、超时、认证、无效响应、上下文超限和不可重试错误。

### 步骤 3.2：视觉模型适配

#### 3.2.1 图片输入

支持二进制或短期 URL，校验 MIME 和大小，不把长期外部 URL写入模型上下文。

#### 3.2.2 视觉结果

返回模型观察文本、可见数值、未知项和引用的 asset ID，不直接写业务表。

### 步骤 3.3：调用预算

#### 3.3.1 Turn 预算

限制最大模型调用数、最大工具步数、总时间和输出 token。

#### 3.3.2 重试

只对明确可重试错误重试；模型格式错误有限重试并把错误反馈给模型。

### 步骤 3.4：Fake Model

提供脚本化响应的 Fake Gateway，用于确定性测试 Tool Loop、错误和恢复。

### 验收条件

- 单元测试可以模拟连续两次 Tool Call 后返回最终文本；
- 每次调用记录模型、版本、延迟、token 和错误类别；
- 供应商异常不会泄漏 API Key 或完整敏感输入到日志。

## 11. 模块 4：Tool Environment

### 目标

把 SlimGuard 的数据库和业务服务变成模型可观察、可行动但不能越权的环境。

### 步骤 4.1：Tool 定义

#### 4.1.1 元数据

每个工具声明名称、描述、JSON Schema、版本、effect level、是否幂等、是否需要确认和超时。

#### 4.1.2 Effect Level

```text
read
reversible_write
sensitive_write
external_effect
prohibited
```

#### 4.1.3 工具发现

核心工具保持稳定顺序；扩展工具通过 Skill 或工具目录按需发现，避免一次暴露所有能力。

### 步骤 4.2：Tool Gateway

#### 4.2.1 参数解析

模型参数必须经过 Pydantic/JSON Schema，拒绝未知字段和不合法类型。

#### 4.2.2 授权

检查 Agent 版本、用户授权、当前风险状态和工具 effect。

#### 4.2.3 幂等

由 `turn_id + tool_call_id + canonical_arguments` 生成幂等键。

#### 4.2.4 事务执行

写操作通过领域服务和数据库事务执行，不能在工具内部任意拼 SQL。

#### 4.2.5 执行后验证

重新读取权威状态，并把实际结果而非 `success=true` 返回模型。

### 步骤 4.3：第一批观察工具

```text
get_user_profile
get_today_checkin
get_recent_weight_trend
get_meals_by_date
get_recent_activities
get_pending_actions
```

### 步骤 4.4：第一批行动工具

```text
record_weight
record_meal
record_activity
correct_record
skip_checkin
update_reminder
stop_service
create_review_request
```

### 验收条件

- 模型无法访问未注册能力；
- 任意写工具重复调用不会重复生效；
- Tool Result 包含 canonical state 和来源 ID；
- 工具失败后模型可以看到结构化错误并自行调整或询问。

## 12. 模块 5：Harness Core

### 目标

实现真正的模型—工具—观察循环，替换当前“一次模型调用直接回复”的核心路径。

### 步骤 5.1：Turn 初始化

#### 5.1.1 获取或创建 Thread

按内部 `user_id` 获取长期 Thread，不使用昵称或外部 ID 作为主键。

#### 5.1.2 创建 Turn

冻结 Agent 版本、触发类型、时间、deadline 和运行预算。

#### 5.1.3 写入输入 Item

把聚合后的文字、图片 asset、引用关系写成 Item。

### 步骤 5.2：执行 Inner Loop

#### 5.2.1 编译上下文

调用 Context Compiler 生成模型输入和允许工具。

#### 5.2.2 调用模型

保存 Model Call 和 assistant Item。

#### 5.2.3 处理工具调用

逐个或安全并行执行 Tool Call，把 Tool Result 追加到上下文。

#### 5.2.4 继续循环

直到模型返回最终文本、需要暂停或触发终止条件。

### 步骤 5.3：终止条件

```text
final_response
waiting_user_confirmation
waiting_human_review
max_model_calls
max_tool_calls
deadline_exceeded
safety_suspended
fatal_error
```

### 步骤 5.4：接入现有微信链路

#### 5.4.1 Harness 模式

在现有消息去重和会话状态判断之后调用 Harness，最终回复仍进入现有 Outbox。

#### 5.4.2 Legacy 降级

Harness 的模型或持久化临时不可用时，根据错误类别决定模板降级或不回复，不能盲目重复执行。

### 验收条件

- 一条消息可连续调用多个工具后只发送一条最终回复；
- 一个 Turn 的所有步骤可在后台重建；
- 达到预算时安全结束，不无限循环；
- Tool 失败后不会错误声称执行成功。

## 13. 模块 6：Context、Memory 与 Skills

用户记忆的分层、数据模型、写入与召回策略、遗忘和隐私生命周期以
[用户记忆模块设计](./MEMORY_DESIGN.md) 为实施细则；本节保留总体架构约束。

### 目标

让模型获得完成当前任务所需的最小、正确上下文，并支持跨轮连续性，不把所有聊天记录粗暴塞入窗口。

### 步骤 6.1：Context Compiler

#### 6.1.1 稳定前缀

包含 Agent 身份、服务边界、安全原则、工具使用原则和不可信输入规则。

#### 6.1.2 动态环境

包含当前时间、用户时区、渠道、发送预算、待确认状态和本次输入。

#### 6.1.3 最小预加载

默认只加入稳定 Profile、今天摘要和少量相关对话；更深历史交给查询工具。

#### 6.1.4 Append-only

同一 Turn 中新模型消息和 Tool Result 只追加，避免修改早期前缀。

### 步骤 6.2：Memory 分层

#### 6.2.1 Transcript Memory

Thread/Turn/Item 是完整可审计历史。

#### 6.2.2 Working Memory

当前上下文中的最近相关消息、Tool Result 和未解决事项。

#### 6.2.3 Profile Memory

用户明确确认的时区、目标、语气、提醒和长期偏好；每条记录保存来源、版本和撤销状态。

#### 6.2.4 Domain Memory

体重、饮食、运动和每日状态是权威事实，模型按需通过工具查询。

#### 6.2.5 Episodic Handoff

上下文压缩时只保存当前目标、未解决问题、Pending Action、关键 Tool Result ID 和相关记录 ID。

#### 6.2.6 Knowledge Memory

未来的营养知识和经审核内容独立存储；不把用户健康记录当向量事实库。

### 步骤 6.3：Memory 写入策略

#### 6.3.1 禁止自由写入

模型不能调用通用 `write_memory`；只能调用语义明确的偏好或目标工具。

#### 6.3.2 来源与撤销

长期 Memory 必须有来源、有效期和 `active|superseded|revoked` 状态。

### 步骤 6.4：Skills

#### 6.4.1 Skill 清单

初期包含 `weight_checkin`、`meal_checkin`、`record_correction`、`daily_review`、`reminder_management`、`health_safety`。

#### 6.4.2 渐进式加载

主上下文只展示名称和简短描述；模型需要时加载完整 Skill。

#### 6.4.3 Skill 版本

Skill 指令、示例、允许工具和验证要求都进入 Agent Manifest。

### 验收条件

- 模型不会声称记得数据库中不存在的事实；
- 用户修改偏好后旧值可追溯且不再生效；
- 上下文压缩后 Pending Action 仍能恢复；
- Skill 不成为单意图分类器，可同时加载多个或一个都不加载。

## 14. 模块 7：多模态感知

### 目标

把图片作为主 Agent 可调用的感知能力，而不是固定前置 OCR 流水线。

### 步骤 7.1：媒体资产

#### 7.1.1 资产元数据

保存 asset ID、MIME、大小、来源 Item、生命周期和短期存储位置。

#### 7.1.2 上下文表示

文本主模型看到“当前 Turn 有可检查图片 asset_x”，不看到永久下载 URL。

### 步骤 7.2：视觉工具

#### 7.2.1 通用接口

实现 `inspect_image(asset_id, question)`，由主 Agent 决定观察目标。

#### 7.2.2 专用接口

通过 Eval 决定是否增加 `inspect_scale_image`、`inspect_meal_image`、`inspect_activity_screenshot`；没有证据前不预设复杂流水线。

#### 7.2.3 视觉结果

返回可见内容、明确数值、未知项和观察限制；结果只是 Observation，主 Agent 决定后续工具。

### 步骤 7.3：图片安全和降级

- 限制类型和大小；
- 图片文字视为不可信用户内容；
- 视觉调用失败时允许主 Agent 询问用户，不猜测；
- 媒体按保留策略清理。

### 验收条件

- 单独发送图片也能进入 Agent Loop；
- 图片看不清时不会自动写入猜测数据；
- 同一 asset 重试不会产生重复记录；
- 视觉模型可以单独替换和评测。

## 15. 模块 8：Guardrails、确认和人工审核

### 目标

不限制模型开放理解，但严格约束数据、副作用、健康安全和人工介入。

### 步骤 8.1：Tool Guardrails

#### 8.1.1 Schema 和业务不变量

检查参数、用户隔离、日期、单位、重复、纠错目标和当前记录状态。

#### 8.1.2 Effect Policy

根据工具 effect、来源清晰度、用户授权和风险状态返回 `allow|confirm|deny|review`。

### 步骤 8.2：用户确认

#### 8.2.1 创建 Pending Action

保存完整 canonical command、来源和过期时间。

#### 8.2.2 恢复

用户确认后执行原命令；用户纠正则创建新的 Agent Turn，不直接修改原参数。

#### 8.2.3 幂等

重复“确认”只能执行一次。

### 步骤 8.3：健康安全

#### 8.3.1 风险信号

模型可以发现开放式风险信号；确定性策略决定允许工具、回复类型和是否暂停常规建议。

#### 8.3.2 高风险边界

医疗诊断、危险减重方式、紧急症状、未成年人等使用硬门禁，不允许平均 Eval 分数抵消。

### 步骤 8.4：内部人工审核

#### 8.4.1 Review Request

保存原因、必要上下文、Agent 草稿和允许的人工操作。

#### 8.4.2 统一发送

人工批准或修改后仍通过 SlimGuard Outbox 发送，不切换企业微信原生人工状态 3。

### 步骤 8.5：Output Guard

检查：

- 工具失败却声称成功；
- 回复数值无法从 Tool Result 或权威事实追溯；
- 把估算表达为事实；
- 禁止医疗表达；
- 超长、多重行动和与风险状态冲突。

### 验收条件

- 模糊敏感动作可暂停并跨消息恢复；
- 高风险输入不会进入常规减脂点评；
- 模型不能越过 Tool Gateway 写数据；
- 内部审核全过程不依赖企业微信人工会话。

## 16. 模块 9：Delivery 和 Outer Loop

### 目标

统一用户主动消息、提醒、复盘、人工审核和失败补偿，同时遵守微信客服窗口与额度。

### 步骤 9.1：最终回复交付

#### 9.1.1 Final Response Item

模型最终文本先持久化为 Item，再创建 Outbox，不在模型循环中直接请求企业微信。

#### 9.1.2 Delivery Policy

发送前重新检查会话状态、48 小时窗口、额度、静默时间、优先级和重复发送。

### 步骤 9.2：Scheduler 事件

#### 9.2.1 到期扫描

Scheduler 只创建领域事件或 Turn，不直接调用模型和微信。

#### 9.2.2 提醒 Turn

Harness 读取当天权威状态和发送预算，自主生成一条合并提醒或选择不发送。

#### 9.2.3 复盘 Turn

Harness 使用不可变 Daily Snapshot 和查询工具生成复盘。

### 步骤 9.3：失败补偿

- 发送失败进入明确状态；
- 仅对平台允许重试的错误重试；
- 窗口关闭后不绕过平台；
- 业务操作成功、回复失败时不重复业务操作。

### 验收条件

- 同一回复最多产生一次平台发送；
- 已打卡后到期提醒会被抑制；
- 主动额度优先保留给当前用户问题和必要确认；
- 外部发送失败不破坏内部业务状态。

## 17. 模块 10：Observability、Trace 和错误归因基础

### 目标

让每次 Agent 行为可解释，并为 Eval 和 Evolution 提供统一输入。

### 步骤 10.1：Trace

#### 10.1.1 Trace 关联

从企业微信回调到 Outbox 使用同一 `trace_id`，关联 Thread、Turn、Item、Model Call 和 Tool Call。

#### 10.1.2 Span

至少包含 Context Build、Model Call、Tool Gateway、Domain Transaction、Output Guard 和 Delivery。

### 步骤 10.2：指标

```text
turn_success_rate
model_call_latency
model_call_count_per_turn
tool_call_count_per_turn
tool_rejection_rate
confirmation_rate
invalid_model_response_rate
delivery_success_rate
token_usage
fallback_rate
```

### 步骤 10.3：隐私

- 默认不把完整 Prompt、回复、图片或外部用户 ID 写入普通日志；
- Trace 内容采集可配置并脱敏；
- Eval 数据复制有单独权限和保留期。

### 步骤 10.4：初步错误归因

优先用确定性错误码区分模型、Prompt、Tool Schema、Tool 实现、Memory、Policy、视觉、企业微信和未知错误。

### 验收条件

- 可以从一次用户消息追踪到最终平台结果；
- 可以区分“模型错”和“微信没发出去”；
- 日志中不出现 Secret、access token 和明文外部 ID；
- Eval Runner 可以直接消费标准 Trace。

## 18. 模块 11：Eval Harness

### 目标

建立可重复运行的评测环境，客观判断更换模型、Prompt、Tool、Skill、Context 或 Memory 是否改善系统。

### 步骤 11.1：Agent Variant

#### 11.1.1 Baseline

指定当前生产 Agent Manifest 为 Baseline。

#### 11.1.2 Candidate

Candidate 可以只修改一个组件用于归因，也可以修改完整组合用于整体比较。

#### 11.1.3 冻结运行环境

每次 Eval 保存代码 commit、数据集版本、Clock、模型参数和随机种子（供应商支持时）。

### 步骤 11.2：Scenario DSL

#### 11.2.1 输入

支持初始 Thread、初始领域状态、用户文字、图片 fixture、时间、企业微信状态和触发事件。

#### 11.2.2 预期

支持必须副作用、禁止副作用、最终状态、回复 Rubric、最大模型调用、最大工具调用、延迟和成本。

#### 11.2.3 结果优先

默认评测最终状态和禁止副作用，不要求唯一 Tool 顺序，也不恢复 Intent 分类。

### 步骤 11.3：隔离环境

#### 11.3.1 Fake 外部依赖

提供 Fake Clock、Fake WeCom、隔离数据库、测试媒体存储和 Tool Recorder。

#### 11.3.2 真实 Harness

Eval 必须运行生产相同的 Harness Core、Context、Tools 和 Guardrails，只替换外部环境。

### 步骤 11.4：评测维度

#### 11.4.1 感知

文字/图片关键数值、单位、日期、明确内容和不确定性。

#### 11.4.2 工具

是否需要调用、是否误调用、工具参数、敏感工具和不必要调用。

#### 11.4.3 状态

最终数据库状态、重复、纠错、用户隔离和业务不变量。

#### 11.4.4 回复

事实一致性、安全、问题覆盖、自然度、同理心、简洁和行动质量。

#### 11.4.5 Journey

首次记录、图片、纠错、查询、偏好修改、跨天和复盘等多轮流程。

#### 11.4.6 运行效率

模型调用、工具调用、token、延迟、费用和失败率。

### 步骤 11.5：Evaluator

#### 11.5.1 确定性断言

数据库、工具、权限、数值、发送、安全硬规则和成本优先用代码判断。

#### 11.5.2 Blind Judge

主观质量使用独立上下文的 Judge；随机 A/B 顺序，不向 Judge 暴露 Baseline/Candidate 和改进假设。

#### 11.5.3 人工校准

定期抽样校准 Judge，重点覆盖风险、用户纠错和 A/B 分歧。

### 步骤 11.6：数据集分层

```text
development
regression
hidden_holdout
safety
rolling_production
```

Improvement Agent 不得读取隐藏集和安全门禁答案。

### 步骤 11.7：Release Gate

#### 11.7.1 硬门禁

跨用户访问、未授权操作、危险建议、关键风险漏检、错误删除、虚构成功和关键回归任一失败即拒绝。

#### 11.7.2 核心正确性

任务成功、记录准确、纠错、图片数值和错误写入不得相对 Baseline 退化。

#### 11.7.3 体验与成本

在硬门禁通过后比较质量、延迟和成本，不使用一个总分抵消安全错误。

### 步骤 11.8：CLI 与 CI

提供：

```text
eval run --variant ... --dataset ...
eval compare --baseline ... --candidate ...
eval report --run ...
```

Pull Request 运行小型 smoke/regression；定时任务运行完整图片、Journey 和 Judge 套件。

### 验收条件

- 同一数据集可比较任意两个 Agent Manifest；
- 报告能指出改善、退化、成本和失败案例；
- 修改 Prompt 或模型时自动运行回归；
- 安全失败不能被平均分掩盖；
- Eval 不依赖真实用户写操作和真实微信发送。

## 19. 模块 12：Agent Evolution System

### 目标

在 Eval Harness 之上建立自动发现失败、生成候选、验证、Shadow、Canary 和回滚的进化闭环。

### 步骤 12.1：Failure Miner

#### 12.1.1 信号采集

收集用户纠错、“你理解错了”、重复提问、Tool 拒绝、虚构成功、Fallback、人工修改、停止服务和风险事件。

#### 12.1.2 自动生成候选案例

把生产 Trace 脱敏后转成待审核 Eval Case，不直接自动进入隐藏集。

#### 12.1.3 聚类

按感知、Tool 选择、参数、Memory、Context、Policy、视觉、Delivery 等失败模式聚类。

### 步骤 12.2：Failure Attribution

#### 12.2.1 确定性优先

HTTP、数据库、平台错误和 Schema 错误用真实错误码归因。

#### 12.2.2 Analysis Agent

只分析无法确定的模型、Prompt、Tool 描述、Context 和 Memory 问题，并输出证据和置信度。

### 步骤 12.3：Improvement Agent

#### 12.3.1 提案范围

允许提出模型参数、Prompt、Tool 描述、Tool Schema、Skill、Context 和 Memory 策略候选。

#### 12.3.2 提案协议

每个提案包含失败簇、假设、具体 diff、预期改善、潜在回归和 Candidate Manifest。

#### 12.3.3 禁止范围

不得自动修改或放宽用户授权、数据隔离、删除权限、健康安全红线、微信限制和人工审核权限。

### 步骤 12.4：实验编排

#### 12.4.1 单变量实验

只改变一个组件，用于回答“换模型/换 Prompt 是否有帮助”。

#### 12.4.2 组合实验

运行完整 Candidate，用于判断整体版本能否替换生产 Baseline。

#### 12.4.3 自动淘汰

依次运行 development → regression → safety → hidden holdout，任一硬门禁失败立即停止。

### 步骤 12.5：Shadow

#### 12.5.1 双运行

生产 Agent 正常回复；Candidate 读取相同输入和状态快照，在隔离环境模拟工具和回复。

#### 12.5.2 禁止副作用

Shadow 不写生产数据库、不发送微信、不创建真实提醒和审核任务。

#### 12.5.3 对比

比较工具选择、参数、模拟最终状态、回复、延迟和成本。

### 步骤 12.6：Canary 和回滚

#### 12.6.1 风险分层

首批 Canary 不包含健康高风险、删除、投诉和敏感长期设置。

#### 12.6.2 监控

监控用户纠错、Tool 拒绝、Fallback、确认、人工请求、延迟和错误写入。

#### 12.6.3 回滚

生产 assignment 使用版本指针，触发阈值时立即恢复 Baseline；历史 Turn 继续引用原版本。

### 步骤 12.7：自治等级

#### Level 1

自动发现、提案、Eval 和报告，人工批准发布。首个生产版本只实现 Level 1。

#### Level 2

自动运行 Shadow 并筛选候选，人工批准 Promotion。

#### Level 3

仅低风险变更允许自动 Canary 和 Promotion，必须满足完整门禁和即时回滚。

### 验收条件

- 系统能自动回答某次换模型或 Prompt 是否改善、退化及原因；
- Improvement Agent 看不到隐藏集答案；
- Candidate 无法修改评测器和安全门禁；
- 自动提案不能直接进入生产；
- 所有 Promotion 和 Rollback 都有审计记录。

## 20. 目标代码结构

```text
src/slim_guard/
├── harness/
│   ├── runtime.py
│   ├── loop.py
│   ├── context.py
│   ├── events.py
│   ├── limits.py
│   ├── termination.py
│   └── recovery.py
├── threads/
│   ├── models.py
│   ├── repository.py
│   └── manager.py
├── agent_models/
│   ├── gateway.py
│   ├── zhipu_text.py
│   ├── zhipu_vision.py
│   └── fake.py
├── tools/
│   ├── registry.py
│   ├── gateway.py
│   ├── policy.py
│   ├── records.py
│   ├── progress.py
│   ├── preferences.py
│   ├── vision.py
│   └── review.py
├── skills/
│   ├── registry.py
│   ├── loader.py
│   └── definitions/
├── memory/
│   ├── transcript.py
│   ├── working.py
│   ├── profile.py
│   ├── compaction.py
│   └── retrieval.py
├── domain/
│   ├── records/
│   ├── checkins/
│   ├── reminders/
│   └── reviews/
├── policies/
│   ├── permissions.py
│   ├── confirmation.py
│   ├── health_safety.py
│   └── delivery.py
├── evals/
│   ├── datasets/
│   ├── scenarios/
│   ├── environment/
│   ├── evaluators/
│   ├── runner/
│   └── reports/
├── evolution/
│   ├── versions/
│   ├── failure_mining/
│   ├── improvement/
│   ├── experiments/
│   ├── shadow/
│   └── promotion/
└── observability/
    ├── logging.py
    ├── tracing.py
    ├── metrics.py
    └── redaction.py
```

目录是目标边界，不要求一次性移动现有文件。每完成一个模块，再迁移对应实现，避免为了目录整洁制造无功能价值的大改动。

## 21. 推荐迭代里程碑

### Milestone 1：最小 Harness

包含模块 0、1、3、4、5 的最小部分：

1. GLM-5.2 Function Calling；
2. Thread、Turn、Item；
3. 一个只读工具和一个测试写工具；
4. model → tool → result → final response；
5. `legacy|harness|shadow` 开关。

完成标准：真实微信文字消息能经过至少一次 Tool Call 后回复，且完整 Trace 可查询。

### Milestone 2：体重闭环

包含模块 2、4、6、8：

1. `record_weight`、`get_recent_weight_trend`、`correct_record`；
2. 用户确认和 Pending Action；
3. Profile、Working、Domain Memory；
4. 工具执行后验证；
5. 体重相关基础 Eval Case。

完成标准：文字体重、查询趋势、纠错和模糊确认形成可靠闭环。

### Milestone 3：多模态和完整记录

包含模块 7 和剩余领域工具：

1. 图片资产；
2. 视觉工具；
3. 饮食和运动记录；
4. 图片 Eval 数据集；
5. 多动作组合消息。

完成标准：图片和文字可以在同一 Turn 中触发多个工具，并正确写入权威状态。

### Milestone 4：提醒、复盘和内部审核

包含模块 8、9：

1. Scheduler 事件化；
2. 提醒和每日复盘 Turn；
3. 微信窗口与额度；
4. 内部人工审核；
5. 跨天 Journey Eval。

完成标准：长期目标由 Outer Loop 维持，所有发送统一经过 Delivery Policy。

### Milestone 5：完整 Eval 门禁

包含模块 10、11：

1. Agent Manifest；
2. Trace；
3. Scenario DSL；
4. Regression、Safety、Hidden Holdout；
5. Baseline/Candidate 报告；
6. CI Release Gate。

完成标准：换模型、Prompt 或 Tool 后能自动给出改善、退化和是否可发布的结论。

### Milestone 6：受控进化

包含模块 12：

1. Failure Miner；
2. Failure Attribution；
3. Improvement Agent；
4. 自动实验；
5. Shadow；
6. 人工 Promotion；
7. Canary 和回滚。

完成标准：系统自动提出候选并提供完整评测证据，但无权绕过发布和安全门禁。

## 22. 每个模块的统一完成定义

一个模块只有同时满足以下条件才算完成：

1. 类型和接口已定义；
2. 数据库迁移或存储方案已完成；
3. 正常路径、失败路径和幂等路径有单元测试；
4. 至少一个集成测试经过真实 Harness 调用链；
5. Trace、指标和脱敏已加入；
6. 对应 Eval Case 已加入；
7. 文档和配置已更新；
8. 旧路径没有被意外破坏；
9. 生产启用有开关和回滚方式。

## 23. 明确不做

当前阶段不做：

- 多 Agent 角色互相讨论；
- 通用自主规划器；
- 用户记录向量化作为事实源；
- 模型直接写数据库；
- 模型直接发送企业微信；
- Agent 自动修改健康安全和权限规则；
- Candidate 自动跳过 Eval 进入生产；
- 为了“规则兜底”复制一套传统 Intent/Entity NLP 系统。

## 24. 最终系统定义

SlimGuard Agent 不是固定回复的升级版，也不是一串分类器，而是：

```text
一个由强模型主导理解和行动、
由 Harness 提供上下文、工具、记忆和反馈、
由数据库保存真实世界状态、
由 Guardrails 控制副作用、
由 Eval 判断版本优劣、
由 Evolution System 持续提出并验证改进的长期健康陪伴系统。
```
