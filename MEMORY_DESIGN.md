# SlimGuard 用户记忆模块设计

> 版本：v1.0
> 日期：2026-08-31
> 状态：已完成（Increment 1～4）
> 适用范围：SlimGuard Agent Harness 的 Profile、Working、Domain 与 Episodic Memory

实施进度：

- [x] Increment 1：Profile Memory 基础闭环；
- [x] Increment 2：Goal 与 Constraint；
- [x] Increment 3：Working Memory 与 Handoff；
- [x] Increment 4：隐私生命周期与完整验收。

## 1. 结论

SlimGuard 的记忆不应等同于“把聊天记录都发给模型”，也不应允许模型自由写一个通用
`memory` 字段。记忆模块采用以下结构：

```text
企业微信消息
    │
    ├── 当前 Turn 输入 ──────────────── Working Memory（短期、有限）
    │
    ├── 用户明确表达 ─→ 语义工具 ───── Profile Memory（长期、可撤销）
    │                              └── Goal / Constraint
    │
    ├── 体重、饮食、运动、提醒 ─────── Domain Memory（现有领域表，唯一事实源）
    │
    └── 已完成 Turn ─→ 受控交接摘要 ── Episodic Handoff（有来源、有期限）

每轮开始
    └── Memory Recall Policy
          ├── 必需的稳定资料
          ├── 与本轮有关的偏好、目标和约束
          ├── 最近少量对话与未解决事项
          └── 现有权威领域事实
                    ↓
              Context Compiler
```

第一版不引入向量数据库，不做后台“自动猜测用户画像”，不保存模型推断出的疾病、性格或
动机。长期记忆必须能回答四个问题：谁说的、何时说的、现在是否仍有效、用户怎样删掉它。

## 2. 目标与非目标

### 2.1 目标

- 用户下一次出现时，Agent 能可靠记住其明确确认的称呼、交流方式、目标、偏好和约束；
- 同一事实只有一个权威来源，记忆模块不复制体重、饮食、运动和提醒数据；
- 每条长期记忆具有用户归属、类型、来源、版本、有效状态和时间边界；
- 用户可以自然语言查看、纠正、撤销某条记忆或清空全部个性化记忆；
- 召回内容有确定性预算，不会随聊天历史无限增长；
- 模型不能跨用户读取、不能从定时任务偷偷写入、不能把推测升级为事实；
- 所有写入、召回、替换和遗忘都可审计，但审计记录不重复保存敏感正文。

### 2.2 非目标

- 不把完整聊天历史当作长期上下文；
- 不建立通用的 `write_memory(key, value)` 工具；
- 不用用户健康记录训练模型或建立全局知识库；
- 不在第一版引入 Embedding、向量数据库或跨用户相似度；
- 不让记忆替代体重、饮食、运动、日程等领域仓储；
- 不从昵称、头像、图片或模型猜测中推断健康状态、年龄、职业、性格和减脂动机。

## 3. 当前基线与缺口

当前生产 Harness 已具备：

- `AgentThreadRecord`、`AgentTurnRecord`、`AgentItemRecord`，可重建运行轨迹；
- `AuthoritativeContextDataProvider`，每轮预加载昵称、首次出现时间、最近 7 条体重、最近
  10 条饮食、最近 10 条运动、提醒计划和当天打卡状态；
- 体重、饮食、运动、提醒设置的领域仓储与查询工具；
- Pending Action，可恢复等待确认的工具调用；
- Agent Manifest 中的 `memory_policy_version="domain-records-v1"`；
- 完整 Context Snapshot 和 Trace。

当前缺口：

- 没有用户目标、长期偏好、约束等结构化 Profile Memory；
- 新 Turn 不读取前几轮对话，缺少 Working Memory；
- 没有跨 Turn 的未解决事项和交接摘要；
- `compaction_policy_version` 仍为 `none-v1`；
- 没有“我记得什么”“忘掉这件事”的用户控制工具；
- 原始用户消息和完整 Context Snapshot 当前可进入 Agent Item，尚未定义正文保留、擦除和
  Snapshot 去敏策略。实现记忆模块前必须一起收紧这一边界。

## 4. 记忆分层与事实权威

| 层 | 内容 | 权威存储 | 默认召回 | 生命周期 |
|---|---|---|---|---|
| Transcript | 用户输入、模型消息、工具轨迹 | Thread / Turn / Item | 否 | 正文默认 30 天，元数据按审计策略保留 |
| Working | 最近少量对话、Pending Action、未解决问题 | Turn + Handoff | 是，严格限额 | 7～14 天或解决即失效 |
| Profile | 称呼、回复风格、饮食/运动偏好 | Memory Fact | 是，按相关性 | 用户撤销前有效，可设置复核时间 |
| Goal | 用户自述目标及目标日期 | Memory Fact | 是 | 被替换、完成或撤销 |
| Constraint | 用户自述的饮食限制、运动限制和健康注意事项 | Memory Fact | 是，带 `user_reported` 标签 | 撤销前有效，定期提示复核 |
| Domain | 体重、饮食、运动、提醒、当日状态 | 现有领域表 | 是，摘要或按需工具查询 | 按领域规则 |
| Episodic | 当前计划、未解决事项、关键记录引用 | Handoff | 相关时召回 | 默认 14 天，可延长或提前解决 |
| Knowledge | 经审核的通用营养与运动知识 | 未来独立知识库 | 按需 | 不属于用户记忆 |

权威优先级固定为：

```text
当前用户明确陈述
  > 当前有效的领域记录
  > 当前有效且有来源的 Memory Fact
  > Episodic Handoff 摘要
  > Transcript 中的历史陈述
  > 模型推测（不得作为事实）
```

发生冲突时不得静默选择旧记忆。当前表达明确时用新表达替换旧值；表达含糊时询问用户，
在确认前保持旧值有效。

## 5. V1 允许保存的记忆

记忆键由代码中的版本化 `MemorySchemaRegistry` 注册。模型只能调用语义工具，不能自造键。

| Key | 类型 | 基数 | 示例 | 默认召回 | 敏感级别 |
|---|---|---:|---|---|---|
| `identity.preferred_name` | string | single | “叫我阿杰” | 总是 | normal |
| `coaching.response_style` | enum | single | concise / detailed / gentle / direct | 总是 | normal |
| `food.preference` | object | set | `{item: "香菜", stance: "dislike"}` | 饮食相关 | normal |
| `exercise.preference` | object | set | `{activity: "游泳", stance: "like"}` | 运动相关 | normal |
| `goal.target_weight` | object | single | `{grams: 65000, target_date: null}` | 体重/复盘相关 | health |
| `goal.behavior` | object | set | `{kind: "weekly_exercise", target: 3}` | 运动/复盘相关 | normal |
| `constraint.dietary` | object | set | 过敏、宗教或用户明确限制 | 饮食相关 | health |
| `constraint.exercise` | object | set | 用户自述的膝部运动限制 | 运动相关 | health |
| `constraint.health_context` | object | set | 用户主动要求记住的健康注意事项 | 安全相关 | restricted |

V1 不保存：

- 模型推断的疾病、心理状态、年龄、收入、职业或家庭情况；
- “用户可能缺乏自律”等评价性标签；
- 图片推断出的身份或健康信息；
- 精确住址、证件、支付信息、第三方秘密；
- 已由领域表管理的体重、单餐、单次运动和提醒时间副本。

时区仍以 `UserRoutinePreference` 为权威；昵称仍区分企业微信同步昵称与用户明确设置的
`preferred_name`，后者只覆盖 Agent 称呼方式，不反写企业微信资料。

## 6. 持久化模型

### 6.1 `user_memory_facts`

```text
id                       UUID PK
user_id                  FK users.id, required
kind                     profile | goal | constraint
memory_key               versioned registry key
slot_key                 normalized conflict slot
value_json               nullable canonical JSON；active 时必填，撤销清除后可为空
value_hash               SHA-256(canonical value)
status                   active | superseded | revoked | expired
assertion                 user_explicit | user_confirmed | imported
sensitivity              normal | health | restricted
supersedes_id            nullable FK user_memory_facts.id
source_turn_id           FK agent_turns.id
source_item_id           FK agent_items.id
evidence_item_id         FK agent_items.id；事实对应的用户原话，可与本轮操作来源不同
source_tool_call_id       required
valid_from                timezone-aware datetime
expires_at               nullable
review_after              nullable
created_at                datetime
ended_at                  nullable datetime
```

设计约束：

- 使用 partial unique index 保证 `user_id + slot_key + status=active` 只能存在一条；
- single 键的 `slot_key` 等于键名，如 `goal.target_weight`；
- set 键的 `slot_key` 等于键名加规范化实体哈希，如
  `food.preference:<cilantro-hash>`；
- 相同来源、键和值重复执行时返回原记录，不新增副本；
- 替换采用一个事务：旧行变为 `superseded`，新行引用 `supersedes_id`；
- active 行的 `value_json` 只接受 Registry 对应 Pydantic Schema 的规范化结果；revoked 行在
  宽限期后允许清空值但保留 `value_hash`；
- 不使用数据库内的任意动态 JSON 查询作为业务规则，读写都经 Repository。

### 6.2 `user_memory_events`

```text
id                       UUID PK
memory_id                FK user_memory_facts.id
user_id                  FK users.id
event_type               created | recalled | superseded | revoked | expired | reviewed
turn_id                  nullable FK agent_turns.id
item_id                  nullable FK agent_items.id
policy_version           required
detail_json               不含用户正文，只保存 reason code、字段名和引用 ID
created_at                datetime
```

召回量较大时，同一 Turn 的多条 `recalled` 合并为一个 `memory_recall` Agent Item，保存
`memory_ids`、策略版本、命中原因和被预算截断的数量，避免事件表膨胀。

### 6.3 `memory_index_outbox`

权威事实与可选 Mem0 语义索引之间通过事务 Outbox 投影。新增、替换、撤销和全量清除与事实变更
同事务入队；后台使用租约、幂等 operation key、指数退避和最大尝试次数同步。Mem0 不可用不会回滚
PostgreSQL 事实，也不会阻塞用户回复。

### 6.4 `memory_handoffs`

```text
id                       UUID PK
user_id                  FK users.id
thread_id                FK agent_threads.id
status                   active | resolved | expired
objective                nullable, max 300 chars
unresolved_json           最多 5 个结构化未解决事项
related_memory_ids_json   只保存引用
related_record_ids_json   只保存引用
source_turn_ids_json      必须非空
created_at                datetime
expires_at               默认 14 天
resolved_at               nullable datetime
```

Handoff 是带来源的临时摘要，不得覆盖 Profile 或 Domain Fact。摘要中的数值事实必须通过记录 ID
引用权威数据；没有引用的自由文本只能表示“待处理事项”，不能表示健康事实。

### 6.4 不新增副本的内容

- 最近体重、饮食、运动继续从现有 Repository 读取；
- 时区与提醒时间继续从 `UserRoutinePreference` 读取；
- Pending Action 继续使用现有表；
- 原始 Transcript 继续属于 Agent Item，但增加独立保留和擦除策略。

## 7. 写入流程

### 7.1 基本流程

```text
当前用户消息 + 有界的近期用户原话 + 数据库 active 记忆
  → 独立 Memory Ingestion 模型判断明确、未来仍有价值的表达
  → 调用语义明确的 Memory Tool
  → Tool Gateway 注入 user_id / turn_id / source_item_id / tool_call_id
  → MemorySchemaRegistry 校验类型、范围、基数和敏感级别
  → Source Validator 分别确认本轮操作来源与事实证据属于当前用户
  → Memory Policy 判断直接写入、要求确认或拒绝
  → Repository 幂等创建 / 替换 / 撤销
  → Tool 返回实际生效结果
  → Context Provider 重新读取数据库 active 记忆
  → Core Agent 基于数据库结果回复
```

摄取模型提供来自用户原话的 `evidence_excerpt` 和同一条消息的 `evidence_ref`。Harness 只授权
摄取模型实际看见的当前用户消息引用，执行器验证引用属于同一用户、原文包含该片段、数值与单位一致
且不会用旧证据覆盖更新事实。当前操作仍绑定当前 Turn 和用户消息，长期表另存
`evidence_item_id`，不重复保存 excerpt。语义映射由模型完成，代码只负责结构、来源和状态约束。

这条摄取链路不依赖 Core Agent 是否记得调用工具，也不要求用户显式说“记住”。数据库已有同槽事实
时，Repository 进行最终仲裁：同值不新建版本，较新的明确用户事实替换旧值，较旧冲突证据被拒绝。
摄取完成后才编译回复上下文，所以 Core Agent 看到的是写入后的数据库状态。

### 7.2 允许直接写入

- “以后叫我阿杰”；
- “回复尽量短一点”；
- “我不喜欢香菜”；
- “我更喜欢游泳，不爱跑步”；
- 用户清晰陈述并要求记住的目标或限制。

### 7.3 必须确认

- 存在多个合理候选的模糊指代：“把上次那个记住”；唯一且有用户原话证据的候选可直接写入；
- 与现有 single 记忆冲突但用户没有清楚表达替换意图；
- 模型需要从上下文推导而不是直接读取的目标值；
- 涉及第三方的信息；
- 用户的玩笑、假设、引用别人话语或否定范围不清；
- restricted 信息没有明确“这是我的情况/请记住”的表达。

确认继续复用现有 Pending Action 和 Tool Call Coordinator，不再造第二套暂停/恢复机制。

### 7.4 永远拒绝

- scheduled Turn、assistant message、视觉观察单独触发的长期写入；
- 模型推断的诊断、人格或用户动机；
- 试图指定另一个 `user_id`、另一个 Turn 或任意来源 ID；
- 不在 Registry 中的键；
- 把单次体重、饮食或运动写入 Memory Fact；
- 来源已过期、已擦除或不属于当前用户。

## 8. Tool 设计

不暴露通用 `write_memory`。V1 提供：

```text
set_coaching_profile
  preferred_name? / response_style? / evidence_excerpt / evidence_ref?

upsert_food_preference
  item / stance(like|dislike|avoid) / reason? / evidence_excerpt / evidence_ref?

upsert_exercise_preference
  activity / stance(like|dislike|avoid) / evidence_excerpt / evidence_ref?

set_weight_goal
  value / unit(kg|jin|lb) / target_date? / evidence_excerpt / evidence_ref?

set_behavior_goal
  kind / target / period / evidence_excerpt / evidence_ref?

record_user_constraint
  category(dietary|exercise|health_context) / user_wording / evidence_excerpt / evidence_ref?

list_user_memories
  kind? / include_stale=false

forget_user_memory
  memory_id 或受限 category + normalized entity
```

规则：

- 所有写工具的身份和来源参数均由 Gateway 注入，模型不可见也不可覆盖；
- `user_wording` 限长并进行控制字符、提示注入标记和日志转义，不把它当系统指令；
- `list_user_memories` 只返回当前用户数据，默认不返回 revoked/superseded；
- `forget_user_memory` 对明确 ID 可直接撤销；范围不明确时先列出候选并询问；
- “忘掉我的所有偏好/全部记忆”使用专门的批量服务并要求一次确认；
- Tool Result 返回稳定 reason code，Agent 不得在失败时声称记住或忘记。

## 9. 召回与 Context 预算

### 9.1 每轮候选与筛选

每轮先并行获得有界候选：

1. PostgreSQL 中当前用户的 active Profile、Goal 和 Constraint；
2. Mem0 按同一 `user_id` 返回的语义候选及分数；
3. 未解决 Handoff；
4. 最近最多 3 个已完成 Turn 的用户/助手可见文本；
5. 现有 Authoritative Domain Context。

专用 Recall 模型根据当前消息语义和指代，从 PostgreSQL 候选中选出少量本轮相关事实。Mem0 分数
只是候选提示，模型不能创建候选之外的 ID，最终值始终重新取自 PostgreSQL。Core Agent 不接收全部
长期记忆。若本轮摄取执行了写入，Core Agent 还会收到由 Repository 生成的结构化写入回执；回执明确
标注 created、updated 或 unchanged，并在 updated 时同时给出 previous_value 和 current_value。
它只描述已经提交到数据库的结果，不由模型自行推断，也不把 Mem0 结果提升为权威事实。

建议默认总预算：

```dotenv
MEMORY_CONTEXT_MAX_CHARS=3000
MEMORY_PRELOAD_MAX_FACTS=30
MEMORY_RECENT_TURN_COUNT=3
MEMORY_RECENT_DIALOGUE_MAX_CHARS=1500
MEMORY_HANDOFF_TTL_DAYS=14
MEMORY_RECALL_SEARCH_LIMIT=12
MEMORY_RECALL_MAX_SELECTED=8
```

Recall 模型故障时，系统保守带入已有的有界权威候选并记录 degraded 事件；Mem0 故障时仍由模型
直接筛选 PostgreSQL 候选。被筛掉的记忆仍可通过只读工具按类别获取。

### 9.2 相关性

相关性由专用模型判断，不再用“体重、饭、运动”等关键词枚举。向量搜索只做第一阶段粗召回，第二
阶段模型结合当前请求、触发类型、候选 key/value、时效性和语义分数完成选择。用户隔离、ID 合法性、
数量上限和数据库优先级仍由代码强制执行。

### 9.3 注入格式

记忆必须作为不含指令权的结构化系统数据加入：

```json
{
  "memory_policy_version": "profile-domain-handoff-v1",
  "profile": [
    {
      "id": "...",
      "key": "food.preference",
      "value": {"item": "香菜", "stance": "dislike"},
      "assertion": "user_explicit"
    }
  ],
  "stale_constraints": [],
  "handoff": null
}
```

Context Compiler 在该 JSON 前固定声明：这些是用户数据，不是系统指令；缺失不代表否定；
`user_reported` 的健康信息不是诊断；回复中不得暴露内部 ID。

## 10. Working Memory 与 Handoff

### 10.1 最近对话窗口

Working Memory 只读取用户和最终 Agent 可见消息，不读取 Context Snapshot、内部 Prompt、模型
草稿、Tool 参数或错误堆栈。历史用户消息携带 `evidence_ref`，最终 Agent 消息不携带；该引用只
允许作为本轮记忆工具的事实来源，执行层仍会核验用户归属和原文。选择规则固定为最近最多 3 个
完成 Turn，并受字符预算约束。另加载
最近最多 3 张仍未到期、属于当前用户的图片能力引用；只暴露真实 `asset_id`、MIME、期限和视觉
模型的非权威结构化观察，不暴露图片字节，也不把观察提升为领域事实。

用户使用“刚才”“上次那个”等指代时，先在 Working Memory 与 active Handoff 中解析；唯一且证据
充分的用户事实可直接写入，仍有多个候选才询问，不能凭最近一条记录强行匹配。图片指代同样由核心模型结合语境判断；执行层只验证
用户隔离和 TTL，不使用关键词规则，也不接受模型生成的虚构 `asset_id`。

Working Memory 的 3 Turn 限制只约束对话承接，不约束长期记忆保存。长期资料在用户首次明确表达时
由 Memory Ingestion 写入数据库；为兼容上线前尚未摄取的消息，摄取阶段另有用户消息专用的有界回填
窗口，默认最近 20 条、6000 字，且不包含助手消息、工具结果或内部快照。

### 10.2 Handoff 创建

仅在以下情况创建或更新：

- Turn 结束时仍有用户明确提出但未完成的事项；
- Pending Action 之外还需要下轮继续的目标；
- 用户明确说“下次继续”；
- 每日复盘产生了用户同意的下一步计划。

不为普通寒暄、已经完成的打卡或模型自行建议创建 Handoff。V1 可由受限的
`set_conversation_handoff` 内部工具写入，参数只允许 objective、unresolved item 和当前消息原文
证据；完成或取消后由 `resolve_conversation_handoff` 关闭。两个工具都不对 scheduled Turn 开放。

Handoff 被完成、用户取消、创建后 14 天无活动时失效。新 Handoff 不自动生成永久 Profile Fact。

## 11. 纠正、遗忘与生命周期

### 11.1 纠正

- single：创建新版本并 supersede 旧版本；
- set：相同实体更新 stance 时替换旧版本；
- 含糊纠正：先列出当前有效值，不猜 ID；
- 旧版本保留审计元数据，但永不再召回。

### 11.2 遗忘

用户说“别再记得我不吃香菜”时：

1. 匹配当前用户 active `food.preference`；
2. 唯一匹配则立即标记 `revoked`；
3. 多个匹配则询问；
4. 从下一次 Context 构建开始立即不可见；
5. 后台维护任务在宽限期后擦除 `value_json`，只保留不可逆哈希和审计 reason code。

批量清空 Profile/Goal/Constraint 必须确认。清空个性化记忆不默认删除法定/运维所需的消息
幂等元数据，也不自动删除体重、饮食和运动领域记录；Agent 必须向用户说明范围。删除领域记录
继续走各领域的撤销或未来的数据导出/删除服务。

### 11.3 过期与复核

- Handoff：默认 14 天；
- 日常作息类事实：未来加入时默认 90 天；
- health/restricted Constraint：不因时间到达而静默消失；超过 `review_after` 后仍为 active，
  但召回结果带计算字段 `stale=true`，并在相关场景请用户复核；复核前只用于保守提醒，
  不用于具体建议；
- 显式到期事实在 `expires_at` 后由维护任务改为 `expired`；
- 过期和 revoked 事实不进入模型上下文。

## 12. 隐私与安全

### 12.1 数据最小化

- 长期表保存规范化事实，不复制整段聊天；
- Evidence 保存来源 ID 和哈希，不再保存摘录副本；
- 日志只记录 memory kind、reason code、哈希用户引用和数量；
- Context Snapshot 不再永久保存完整 compiled request，改存版本、引用 ID、内容哈希、预算和
  redaction 后的结构；调试正文使用短保留期的受限存储；
- 图片不能直接生成长期 Profile Memory。

### 12.2 Transcript 保留

建议新增：

```dotenv
AGENT_TRANSCRIPT_BODY_RETENTION_DAYS=30
MEMORY_REVOKED_VALUE_RETENTION_DAYS=30
MEMORY_HEALTH_REVIEW_DAYS=180
```

后台维护任务到期后擦除 Agent Item 中的用户/助手正文和完整 Context Snapshot 内容，但保留
Item 类型、状态、时间、来源消息 ID 哈希和运行结果 reason code。领域记录不依赖 Transcript
正文存活。

### 12.3 权限与隔离

- Repository 所有查询必须显式接受 `user_id`；按 ID 更新也必须同时过滤 `user_id`；
- Tool Gateway 从 `HarnessTurnContext` 注入用户身份，模型参数中不存在 `user_id`；
- 来源校验复用现有 `domain/source.py` 模式；
- proactive/scheduled Turn 默认只有 recall 权限，没有长期 memory write 权限；
- 内部审核只能批准候选操作，不能改变候选的 user、key、value 和来源；
- 用户身份合并不能仅凭昵称；未来使用 unionid 合并时需要独立、可审计流程。

### 12.4 健康安全

- 存储用户目标不代表系统认可该目标；
- 未成年人和明确高风险减重信号下，禁止创建减重数值 Goal，继续使用现有 Safety Guard；
- Constraint 始终标记为 `user_reported`，不得在回复中改写成医学诊断；
- 记忆与当前用户表达冲突时，以当前表达为准并询问是否更新；
- Prompt Injection 文本即使被保存为偏好值，也作为数据转义，永远不获得指令优先级。

## 13. 代码边界与接入点

建议新增：

```text
src/slim_guard/memory/
├── __init__.py
├── contracts.py          # Fact、Handoff、状态和命令
├── schemas.py            # 各 memory_key 的 Pydantic value schema
├── registry.py           # 版本化 key/cardinality/sensitivity/recall policy
├── repository.py         # 用户隔离、幂等、替换、撤销和过期
├── policy.py             # 写入许可、确认和召回优先级
├── service.py            # 写入/纠正/遗忘用例
├── recall.py             # 预算内构建 MemoryContext
└── transcript.py         # 最近对话与正文保留/擦除

src/slim_guard/tools/memory.py
src/slim_guard/services/memory_maintenance.py
tests/unit/test_memory_*.py
tests/integration/test_memory_flow.py
```

修改现有边界：

- `db/models.py`：增加 Fact、Event、Handoff 表；尽量只新增表，兼容当前 `create_all` 部署；
- `agent/composition.py`：注册 Memory Tools、Recall Service 和维护任务；
- `harness/context_data.py`：从单一 Domain Provider 变成组合 Provider，分别输出
  `profile_memory`、`working_memory` 和 `domain_context`；
- `harness/context.py`：添加结构化 Memory 区块及不可信数据说明；
- `harness/events.py`：增加 `memory_recall` / `memory_change` 事件类型；
- `agent/prompt.py`：规定何时记忆、何时询问、不得推断和用户遗忘语义；
- `harness/manifest.py`：升级 memory、compaction、context policy version；
- `main.py` / `config.py`：增加保留期、召回预算和维护周期配置。

Manifest 建议升级为：

```text
context_policy_version    authoritative-memory-v2
memory_policy_version     profile-domain-handoff-v1
compaction_policy_version bounded-working-handoff-v1
```

## 14. 可观测性

日志事件不包含值正文：

```text
memory_fact_created
memory_fact_idempotent_reuse
memory_fact_superseded
memory_fact_revoked
memory_fact_expired
memory_recall_compiled
memory_recall_truncated
memory_write_rejected
memory_handoff_created
memory_handoff_resolved
memory_transcript_body_scrubbed
```

建议指标：

- 每 Turn 预加载 Fact 数、字符数和截断数；
- 写入成功、确认、拒绝、幂等复用、纠正、遗忘比例；
- stale Constraint 数量；
- 因错误记忆导致用户纠正的比例；
- 跨用户访问拒绝次数；
- Transcript 擦除延迟与失败次数。

## 15. 实施顺序

### Increment 1：Profile Memory 基础闭环

- 新增 Fact/Event 表、Registry、Repository；
- 实现称呼、回复风格、饮食和运动偏好；
- 实现 `list_user_memories`、单条撤销和 Context 预加载；
- 加入用户隔离、来源校验、幂等、替换和预算测试。

完成标准：用户说“以后叫我阿杰，回复短一点”，下一轮生效；用户要求忘记后下一轮立即失效。

### Increment 2：Goal 与 Constraint

- 实现体重/行为目标和用户自述约束；
- 接入 Pending Action、安全门禁、stale/review 机制；
- 在饮食、运动、复盘场景按相关性召回。

完成标准：目标和约束能跨轮生效、可纠正、可撤销，且未成年人/高风险场景不能写入危险目标。

### Increment 3：Working Memory 与 Handoff

- 加载最近有限的用户可见对话；
- 实现 Handoff 创建、解决、过期；
- 处理“刚才那个”“下次继续”等跨 Turn 指代；
- Context 超预算时确定性截断。

完成标准：不用加载完整历史也能继续最近未完成事项；过期 Handoff 不再影响回复。

### Increment 4：隐私生命周期与完整验收

- 实现 Transcript/Snapshot 正文擦除；
- 实现 revoked value 物理擦除、批量清空和维护任务；
- 补充真实微信集成测试、故障恢复和观测指标；
- 更新 README 的数据保留说明。

完成标准：所有保留期可配置，服务重启后仍正确执行；擦除后领域记录、幂等和审计元数据仍可用。

## 16. 验收用例

### 16.1 功能

- “以后叫我阿杰” → 下一轮使用阿杰；
- “别叫我阿杰了” → 唯一匹配时撤销，后续不再使用；
- “回答简短点” → 后续回复风格生效；
- “我不吃香菜” → 后续饮食建议读取偏好；
- “其实我现在可以吃香菜了” → 原偏好被 supersede/revoke；
- “目标 65 kg” → 体重/复盘 Turn 可读取，不复制为体重记录；
- “我膝盖不舒服，运动建议避开跳跃” → 只保存用户自述约束，不生成诊断；
- “刚才那个继续” → 在唯一 Handoff 下继续，有歧义则询问；
- “你记得我什么？” → 只列当前 active 记忆并区分领域记录；
- “清空关于我的个性化记忆” → 确认后批量撤销，说明不含业务记录。

### 16.2 安全与可靠性

- Prompt Injection 不能写任意 key 或另一个用户的记忆；
- revoked、expired、superseded 事实绝不进入 Context；
- 相同微信消息重放不产生重复 Fact；
- 两个并发冲突写入只能留下一个 active single Fact；
- scheduled Turn 尝试写长期记忆被拒绝；
- 来源 Item 不属于当前用户时写入失败；
- Context 达到预算时结果仍确定、可测试，安全 Constraint 不被低优先级偏好挤掉；
- 服务在 supersede 事务中退出时不会出现两个 active 值或没有 active 值的半完成状态；
- Transcript 正文擦除后，Memory Fact 仍有来源哈希，领域记录查询和 Outbox 幂等不受影响。

## 17. 暂缓决策

以下能力在当前实现稳定后再评估：

- 多渠道身份合并后的共享记忆；
- 用户自助记忆管理页面与数据导出；
- 经审核的 Knowledge Memory；
- 基于 Eval 的 Memory 策略自动优化。

Embedding 语义检索已通过可选 Mem0 OSS 投影实现，默认关闭。启用前必须使用私有部署、验证模型与
Embedding 的数据保留策略，并完成删除传播测试；管理后台持续显示同步与召回解释。
