# composition.py 人工 Review 记录

日期：2026-09-20
状态：讨论结论与实施进度；用户已授权继续完成剩余项，不再逐项等待审核。本轮通用任务已实施，验证范围详见第 23 节。

本文记录围绕 `AgentRuntimeDefinition` 及其关联实现的讨论。第 2–21 节保留历史讨论和分批实施记录；当前进度以第 23 节为准；历史阶段中的待办描述不代表最新状态，本地完成不代表线上已验收。

## 1. Review 原则

- 按模块组织配置和实现，不把所有参数堆在一个运行配置类里。
- 保留一套当前认可的架构，清理旧执行路径、无用开关及其关联实现，不只删除配置字段。
- 业务判断和下一步路由由模型负责；Harness 负责权限、预算、状态、契约和幂等保护，不写死业务步骤顺序。
- 最新授权：继续完成本文剩余项，不再逐文件或逐功能等待审核；完成后汇总改动与验证结果。尚未定案、确需产品选择的边界明确记录，不以猜测扩展范围。当前阶段不提交、不部署、不重启 Expo。

## 2. AgentRuntimeDefinition 按模块拆分

### 修改方向

不仅拆出 limits 和 memory，其他配置也按实际职责归类。各模块拥有自己的配置类型，组装层只引用并组合，不重复铺开所有字段。

建议分组（具体类型和目录在实施前确定）：

- 模型：Provider、文本/视觉模型及调用参数。
- 执行：整轮预算、各 Agent 调用预算、超时。
- 记忆：预加载、工作记忆窗口、召回、未完成事项。
- 营养知识：实际需要的检索配置。
- 表达风格：实际需要的改写与共享审查配置。
- 附件存储：保存、访问、保留与删除策略，不属于 Agent 业务编排配置。
- 部署元数据：与行为配置在概念上区分；`code_revision` 本轮暂不改动。

不为了分组新增没有实际用途的参数或空壳模块。

## 3. limits 与执行预算

### 当前事实

- `src/slim_guard/harness/limits.py` 定义默认模型调用 6 次、工具调用 8 次、累计 Token 64,000。
- `harness/loop.py` 的这些检查主要覆盖 Core 模型—工具循环，不是所有 Agent 共用的完整 Turn 总预算。
- 其他 Agent 调用另有调用次数、Token 和截止时间限制。
- 最终统计汇总不等于执行时共享预算；Token 调用后检查也不意味着绝对不会超出阈值。
- `main.py` 当前创建运行定义时没有显式传入 `limits`，该路径使用默认值。

### 整理方向

- 共用预算类型，各 Agent 使用独立配置实例，不通过继承为每个 Agent 定义预算。
- 区分整轮共享预算与单次 Agent 调用预算；一次调用应同时受到两层限制。
- 保留必要的技术预算保护，不把它误认为业务路由硬逻辑。
- 具体预算数值、扣减方式和超限行为仍需在后续 review/实施设计中明确。

## 4. 版本标识与功能开关

### 暂时保留

- `code_revision`：按本轮讨论暂时不动。它记录执行代码版本并参与运行清单标识，不负责选择旧代码执行；生产 Compose 已通过 release tag 注入。
- `agent_specialist_timeout_seconds`：本轮暂时不动。当前实际用于营养咨询截止时间，并非所有 Agent 的统一超时。

### 清理方向

- `multi_agent_graph_version` 当前是记录标识，不是按版本选择编排方案的路由开关；不再要求人工维护多套编排版本。移除/收敛它时要检查清单和 Trace 的关联引用。
- 真正维持不同执行路径的是功能开关及代码分支，包括回复流水线接入、群聊路径、旧整段风格路径和审查路径。
- 清理重点包括 `multi_agent_mode`、`group_chat_enabled`、`style_render_all_normal_replies`，以及固定能力不再需要的营养、RAG、审查开关。
- RAG 是否需要独立的无知识库部署模式尚未具体确定；不要把“没有可用证据”与“切换旧架构”混为一谈。
- 清理必须同步覆盖环境变量、服务器配置示例、组装代码、旧路径、专用适配、测试和文档，检查其他失去调用方的代码。

### 职责边界

- 系统组装固定提供当前架构能力。
- Core/聊天 Agent 根据本轮需求和各自职责选择能力及表达角色。
- 必须满足的发送、授权、事实和风格忠实性契约不能由模型任意跳过。
- 不以“简化开关”为由改成另一套固定业务流水线。

## 5. validate_nutrition_rag

### 当前事实

该方法检查开关组合：RAG 依赖营养 Agent、RAG 不允许关闭引用要求、回复审查依赖风格路径、多 Agent 模式依赖回复审查。它不是运行时 RAG 内容校验，名称也不能覆盖其全部检查内容。

### 修改方向

- 相关开关和旧分支清理后，删除失去意义的组合校验。
- 配置合法性和必要依赖检查仍属于配置/组装阶段；运行时权限、预算、结果契约由相应执行组件负责。
- 不把旧的“内容审查必须依赖整段风格路径”等关系保留为新架构约束。

## 6. 图片属于聊天消息附件

### 当前事实

- `agent/runtime.py` 保存运行图片资产时设置 `expires_at`。
- `image_retention_seconds` 默认 7 天，影响资产读取、最近图片查询和后台过期清理。
- 查到的删除操作针对数据库图片资产，不能据此宣称 COS 原图生命周期已统一管理。

### 已明确的修改方向

- 用户图片是聊天记录的一部分，生命周期由附件存储及后续删除策略负责，不由 AgentRuntime 决定。
- 区分原始聊天附件与 Agent 使用的解析结果/缓存；缓存失效不得让原始聊天图片消失。
- 实施前检查现有聊天附件、数据库资产和 COS 的真实关联，不能仅增大 TTL 代替职责调整。

## 7. 图片解析回填消息上下文

### 当前事实

- 最近对话和最近图片当前分别读取；`memory_recent_image_count` 默认最多 3 张未过期图片。
- 图片上下文是资产引用、时间、已有识别信息等，不是每轮重新提交原图做视觉识别。
- `memory/working.py` 从最近工具结果提取图片观察，并没有把解析结果作为原消息附件语义统一加载。
- 两个独立窗口可能产生重复或错位，不能据此直接断言某次线上问题的根因。

### 用户明确的表达方式

用户原消息：

> 这是我的午餐。（图片 A）

附件解析的上下文描述应类似：

> 用户说了这是我的午餐，并发送了一张图片。图片里包含了 xxx、xxx，但是部分内容不能确定，比如 xxx、xxx，需要用户确认。

这是一段描述性的图片语义补充，不是把“已识别 / 不确定”的结构化任务状态直接当成消息正文。

### 修改方向

- 保留用户原文及附件关联；解析结果作为模型生成的附件注释回填，不伪装成用户原话。
- 下次加载对话时统一加载消息、关联附件引用和解析描述。
- 不再无条件额外拼接最近 N 张图片，清理/替代 `memory_recent_image_count` 的独立窗口逻辑。
- 需要重新看图时通过附件引用读取原图；未完成任务可继续引用窗口外的原消息与附件。
- “当时识别不确定”是历史观察，不等于“现在仍待确认”；最新解决状态由通用任务机制表达。
- 原始结构化识别产物可按实际需要供工具和追踪使用，不等于必须作为独立图片记忆注入上下文。

## 8. 系统共用的对话任务与确认机制

### 当前事实

- 当前菜品澄清主要依靠最近对话及图片工作记忆；`context_data.py` 明确过滤退役的 `confirm_dishes` 动作。
- `PendingActionRepository` 绑定具体工具调用、参数和执行键，主要处理工具操作授权/审批，不是完整的通用对话待办。
- `handoff` 保存未完成事项摘要，通过模型调用工具创建/解决；不是所有追问都会自动创建。
- `memory_handoff_ttl_days` 当前有效：决定摘要过期时间，过期后不再加载；其未来归属应结合统一任务与记忆结构确定。

### 已明确的修改方向

- 不新增菜品专用确认闭环，整个系统共用任务/待办基础能力。
- 任务关联原消息、附件、待解决事项、状态及解决依据。
- 区分信息澄清和操作授权：可以共享存储和生命周期基础能力，但完成条件不能混用。
- 模型判断是否存在阻塞、是否需要追问、用户回答解决了什么、下一步做什么。
- 通用组件负责持久化和接续；Harness 校验权限、来源、状态更新及执行幂等。
- 不写死“确认菜品 → 记录 → 营养评价”的业务顺序。
- 只有实际需要推进的事项进入待办；不把所有视觉不确定性都强制转为用户问题。
- 与现有 PendingAction、handoff 的复用/合并边界仍需设计，避免再并行增加第三套重复机制。

### 补充结论：优先复用现有确认能力

- `pending_actions` 已经是跨工具的通用授权/审批能力，不是菜品专用实现；后续应整理复用，而不是把它当成不存在后另起一套确认系统。
- 信息澄清是“信息尚不完整，等待补充”；操作授权是“具体操作和参数已明确，等待允许执行”。两者属于统一任务推进体系，但不能都用批准/拒绝表示完成。例如“烧茄子”是在补充事实，不是批准一个已有完整参数的工具操作。
- 优先复用持久化、消息关联、状态恢复、权限和幂等能力，补齐任务、待解决事项、用户补充与工具操作之间的关联和生命周期。
- 上下文提供器能读取待授权操作、业务记录和未完成摘要，不代表已实现跨轮补充信息的合并与任务状态更新。“加载状态”与“推进状态”是不同职责。
- 评估 `handoff` 能否收敛到统一任务体系，避免一个未完成事项同时存在于多份互不关联的状态中。
- 不直接向 `PendingActionRecord` 堆叠大量可空业务字段。是演进现有结构，还是以通用任务关联现有授权记录，需要结合完整实现再确定。

## 9. 通用图片理解，取消前置业务分类

### 当前调用事实

- 对模型暴露的是 `inspect_image`，`_recognize_meal` 是内部方法，不是独立工具。
- `focus` 由 Core 大模型生成工具参数，取值限定为 `auto / weight_scale / meal / exercise`，默认 `auto`。
- 工具说明引导菜品识别或饮食适宜性问题使用 `focus=meal`。
- 仅在 `focus == "meal"` 且视觉 Provider 有 `recognize_dishes` 能力时，进入 `_recognize_meal` 和 `DishRecognitionAgent`。
- `auto` 识别出食物不会自动转入该分支；当前也并非只支持食物图片。
- 专用提示词位于 `src/slim_guard/agents/dish_recognition/prompt.py`，要求菜品候选、不确定原因和建议问题，禁止营养评价等输出。

### 讨论形成的调整方向

- 用户消息中的图片首先是通用附件，不应在理解图片前强制选择业务类别。
- 通用图片理解结合用户原话和必要上下文，描述可见内容、与请求相关的信息和不确定性。
- 将描述关联回原消息，再由 Core 基于完整消息判断回答或调用其他能力。
- 取消 `focus=meal` 等作为通用识别必经的前置业务分类；专用识别只有确有需要时才按需调用，不默认保留无用专用层。
- 图片理解不负责决定业务写入，不把 `suggested_question` 当作必须追问的指令。
- 食物包装日期、错误截图等都应按用户请求理解，不能统一套菜品识别规则。
- 识别入口、解析缓存和专用能力的最终接口在后续 review 中确定；本记录不要求所有图片一律触发同一套多阶段调用。

## 10. build_agent_runtime 按能力模块化

### 当前职责

`build_agent_runtime()` 是总组装入口：创建 Repository、工具定义与执行器、上下文、记忆、营养能力、回复处理组件和 TurnHarness，最后返回 AgentRuntime。创建对象的顺序不是用户请求的业务执行顺序。

目前各模块内部的具体组装细节集中在该函数中，包括待确认处理器的后置绑定、营养 Agent 的工具包装、记忆召回与提取调度，以及不同开关控制的回复路径。

### 已确认的模块化方向

- 保留总组装入口，但让它只表达模块之间的连接，不直接展开每个模块所有 Repository、Handler 和配置参数。
- 记忆模块内部组装结构化资料、长期记忆、召回与提取调度。
- 任务模块内部管理信息澄清、操作授权、恢复及存储；复用并整理现有确认能力。
- 附件模块内部管理原图访问、图片理解及其关联信息。
- 营养模块内部组装知识检索、营养 Agent 和对 Core 暴露的工具。
- 回复模块内部组装聊天、风格、审查与修复能力。
- 总组装层通过各模块公开契约/端口连接能力，不直接访问其他模块内部 Repository。
- 工具定义与工具执行器仍需正确对应；模块化不是只移动其中一方。

结构示意（不是已实现或最终确定的接口）：

```python
def build_agent_runtime(...):
    memory = build_memory_runtime(...)
    tasks = build_task_runtime(...)
    attachments = build_attachment_runtime(...)
    nutrition = build_nutrition_runtime(...)

    tools = build_core_tools(
        memory=memory,
        tasks=tasks,
        attachments=attachments,
        nutrition=nutrition,
        ...
    )
    context = build_context_provider(...)
    responses = build_response_runtime(...)
    harness = build_turn_harness(
        tools=tools,
        context=context,
        responses=responses,
        ...
    )
    return AgentRuntime(...)
```

### 实施约束

- 不仅把长函数切成同一文件中的多个私有函数；实现、配置和公开接口应按能力归属组织。
- 不为每个小对象新增工厂类，不引入不必要的复杂依赖注入框架。
- 先确定唯一执行路径、清理废弃分支，再按能力拆分组装，避免把旧复杂度搬到更多文件。
- 结构移动与行为变化尽量分开提交；具体拆分边界和命名在后续 review 后确定。
- 模块化不改变“模型判断业务、Harness 保障执行合法”的原则，也不把组装顺序写成业务流程。

## 11. build_agent_manifest 的职责与整理

### 当前事实

- 该函数生成 Agent 运行版本清单，不执行模型、工具或业务流程，也不根据版本号加载旧实现。
- 先单独构建工具注册表，再把 `registry.versions` 写入清单；营养工具是否包含在内受 `nutrition_agent_enabled` 控制。
- 清单包含模型及参数、主提示词版本及内容指纹、工具版本、策略标识和代码版本。规范化序列化后生成清单版本标识。
- 清单用于识别配置和组件变化，不保证能够完整重现历史回答。
- `context_policy_version`、`compaction_policy_version`、`safety_policy_version` 等在这里是版本标记，不是策略实现。修改字符串不会改变实际行为，代码变动而标记未更新也可能造成误导。
- 当前清单未直接记录 limits、记忆窗口等实际配置，以及所有回复处理组件的提示词和配置，因此不能称为整个系统所有行为的完整快照。

### 已确认的整理方向

1. **工具集合只保留一个来源。** `build_agent_runtime()` 和 `build_agent_manifest()` 当前重复枚举工具定义，存在一处修改、另一处遗漏的风险。运行时与清单应共享实际组装得到的工具注册结果，而不是维护两份列表。
2. **不要把清单比较误当成工具集合一致性检查。** `active_manifest != expected_manifest` 比较清单对象，不能直接发现另一份运行时工具列表与清单发生偏差。
3. **策略信息归所属模块。** 确有用途的策略版本由对应模块提供，不在 composition.py 中硬写其内部版本细节；不为了完整而给每个小模块新增人工版本号。
4. **明确记录边界。** 后续检查 AgentManifest 与其他运行清单、配置快照、Trace 的分工，避免重复记录又遗漏关键配置。不要未经梳理就把所有字段继续堆入清单。
5. **延续之前的清理决定。** 删除无实际用途的人工版本体系和旧开关关联逻辑；`code_revision` 本轮仍暂时不动。

接口方向示意（非最终签名）：

```python
registry = build_core_tool_registry(...)
manifest = build_agent_manifest(definition=definition, registry=registry)
# 运行时继续使用同一份 registry。
```

## 12. 删除无实际消费者的 AgentGraphManifest

### 当前 review 事实

- `build_agent_graph_manifest()` 手工列出角色的模型、提示词指纹、输出结构名称、工具名、数据范围和预算，再附加整体策略标识。
- 它没有定义节点间的边、调用顺序或转移条件，不是可执行的编排图。
- 当前源码中查到的用途是启动时构建并检查清单字段、提供内容哈希标识，以及挂载到 `application.state.agent_graph_manifest`；没有查到后续通过该清单驱动 Agent 组装、路由、实际权限/预算、输入隔离或后台流程图展示的消费路径。
- 实际运行参数在其他位置另行构建，已经存在偏差：营养节点清单的 `max_tool_calls=0`，实际营养调用对象声明为 8；风格节点清单未包含实际调用声明的 `recent_dialogue` 数据范围。这些是声明不一致，不能据此断言实际调用次数或隔离效果。
- 部分节点无条件列入清单，而实际组件受开关控制；清单构建器还强制要求特定角色。清单合法不代表实际组装一致。
- 函数重复调用 `build_agent_manifest()`，并另行创建营养工具注册表以取得版本信息，继续维护了与实际运行组件分离的描述。

### 用户已确认的决定

**删除 `AgentGraphManifest`，不继续为这份缺少实际消费者的静态清单补字段或维护兼容壳。**

- 删除专用构建函数 `build_agent_graph_manifest()`、启动构建及 `application.state` 挂载。
- 核查并清理清单类型、仅服务于该清单的节点类型、导入/导出、测试、文档和其他失去用途的引用。
- 如发现新的实际消费者，先核实其职责并处理依赖，不凭名称批量删除。
- 不要误删 `AgentManifest`：它是另一份模型、提示词、工具及代码版本记录，按第 11 节单独整理。
- 不要误删真实运行中的权限、预算、输入保护、调用记录和 Trace；清单内的声明不等于这些实际执行机制。
- 各处名为 `graph_version` 的字段也不一概视为清单专用，需按实际引用结合第 4 节决定清理范围。
- 后台展示“本轮实际调用了哪些 Agent、如何调度”应使用真实执行记录，不依赖手工维护的静态角色清单。

以上为 review 时的删除决定；清单删除的实施情况见第 14 节。

## 13. 后续 review 检查项

- composition.py 剩余组装代码及调用关系。
- 固定当前架构后，哪些开关和分支真正失去用途。
- 整轮预算与各 Agent 预算的实际扣减范围。
- 原消息—附件—解析产物的存储与上下文投影关系。
- 通用任务机制与 PendingAction、handoff 的职责收敛。
- 清理字段时同步核对调用方、配置、测试、文档和必要的数据迁移；行为清理与结构整理分开审核。

## 14. 已实施的结构整理（2026-09-20）

### 14.1 目录与职责

```text
src/slim_guard/
  agent/
    composition.py          连接能力组件，组装 TurnHarness 和 AgentRuntime
    definition.py           组合运行配置，暂保留旧开关及其组合校验
    manifest.py             构建实际使用的 AgentManifest
    tool_catalog.py         Core 工具定义集合的唯一来源
    assembly/
      memory.py             记忆存储、召回、工具及提取任务调度的依赖组装
      business.py           共享业务仓储，组装上下文提供器和业务工具
      nutrition.py          营养 Specialist、知识服务和 Core 工具适配器组装
      tools.py              工具网关、待确认/审批操作与恢复协调器组装
      responses.py          聊天、风格、审查和返工组件的依赖组装
  agent_models/config.py    ModelRuntimeConfig
  memory/config.py          MemoryRuntimeConfig
  runtime/budget_config.py  ExecutionBudgetConfig（Core 与单次 Invocation 分开）
  harness/pending_config.py PendingActionConfig（工具确认与审批有效期）
```

`assembly` 是应用组装层，不是新增业务层。它可以连接具体仓储和 Provider；业务模块不得反向依赖它。这些构建函数创建依赖，不调用模型或执行业务工具，其调用顺序不是用户对话的业务路由。

### 14.2 已完成

- 删除无实际消费者的 `AgentGraphManifest`、节点清单、构建函数、启动挂载、导出及专用测试；保留实际使用的 `AgentManifest`。
- Core 工具定义只维护一份，运行时构建的工具注册表同时用于清单和上下文编译，不再在两处分别枚举工具。
- 模型配置、记忆配置分别归属对应模块；同步迁移应用入口和测试调用方，不保留旧平铺配置字段的兼容壳。环境变量名称和取值未改变。
- 执行预算归入 `ExecutionBudgetConfig`：`budgets.core` 仍使用原 `HarnessLimits`，`budgets.invocation_max_total_tokens` 仍传给营养与回复处理组件。只归类配置，没有新增跨 Agent 共享扣减或改变任何预算值。
- 工具确认/审批有效期归入 `PendingActionConfig`，通过 `pending_actions` 引用并交给工具组装模块。默认值、边界校验和实际过期行为不变，不将其冒充为通用信息澄清任务配置。
- `AgentRuntimeDefinition` 与清单构建器移出 composition；调用方使用各自模块。`agent` 包级公开入口保留。
- 按能力拆分具体依赖组装，仍共享同一份上下文、状态仓储和待确认操作仓储；工具恢复绑定保持不变。
- 删除 composition 重复声明的营养知识 Protocol，使用营养工具已有的知识访问端口；未改变检索或来源读取实现。

### 14.3 当时尚未实施的事项（后续进度见第 22 节）

- 清除其余旧功能开关及执行分支；营养启用、审查启用、重复风格路径及引用要求开关分别按第 15—18 节清理，`validate_nutrition_rag` 的组合校验已全部删除。
- 继续检查策略标识的用途；按第 19 节已删除人工编排版本配置，保留有实际持久化/Trace 消费者的记录字段。
- 统一任务澄清、待授权操作与 handoff 的生命周期。
- 通用图片理解、原消息附件与解析结果回填、独立近期图片窗口和图片保存策略的调整。
- 整轮共享预算与单次调用预算的实际扣减范围调整；现有预算配置已归类，其余配置按后续能力边界继续整理。图片保留期和营养超时本轮未改变。

第 10 节曾提出先清理旧执行路径再拆分。实际执行按用户最新审核方式调整为：先完成不改变行为的结构整理，旧分支不借拆分之机悄悄删除；行为清理另行审核。此次没有改 Prompt、路由判断、审查策略、预算数值、确认规则或数据表。

### 14.4 验证范围

- 两批定向测试共 **100 项通过**：配置与应用入口、工具目录和组装连接、运行入口、记忆提取/摄取/召回、营养工具、回复处理、回退、上下文、handoff、待确认操作恢复与幂等性。
- 新增组装连接测试，核对工具定义与执行器匹配、清单与编译器共用注册表、待确认恢复共用状态，以及配置参数和现有功能分支的传递。
- 后续预算/确认配置归类后另跑 **95 项定向测试，全部通过**：新增配置校验、应用启动参数传递、组装连接、Agent 运行、营养与回复处理、待确认恢复、Harness 循环和 TurnHarness；此次数量不与前一批累加，覆盖有重叠。相关 Ruff、Mypy 和差异格式检查通过。
- 这些是本地定向自动化测试，不代表全量测试或真实模型线上联调；没有提交、部署、修改线上数据库或重启 Expo。

## 15. 固定提供营养咨询工具（2026-09-20）

本节属于经授权的行为修改，不属于第 14 节的纯结构整理。

- 删除 `nutrition_agent_enabled` 运行定义字段、Settings 字段、组装分支，以及“RAG 必须先打开营养 Agent 开关”的校验。
- `tool_catalog.py` 固定注册 `consult_nutrition_specialist`，营养组装固定提供对应执行器；独立清单构建和真实运行共用该目录。不再保留只为有条件注入营养工具存在的 `specialist_tools` 参数。
- Core 总能看见营养咨询工具，但只有模型选择调用它才执行营养咨询；没有新增“记录后必须营养评价”等固定业务顺序。
- RAG 开关、RAG 引用要求、凭据校验、营养超时和预算、回复处理开关及 Prompt 均未改变。未开启 RAG 不再意味着营养工具不可用，也不代表放宽原有专业结论校验。
- 同步清理本地/服务器配置示例及部署说明中的旧开关；没有读取或修改真实服务器配置，没有修改本地私有 `.env`。Settings 原有 `extra="ignore"` 保持不变：旧环境文件若仍有该变量，会被忽略，不再能关闭营养工具。
- 原先关闭营养工具的运行清单会因工具集合变化产生新标识，这是实际工具能力变化的记录；不迁移或伪造历史清单。

### 本次逐文件说明

| 文件 | 修改内容 |
|---|---|
| `src/slim_guard/agent/tool_catalog.py` | 固定加入营养工具定义，删除可选 Specialist 注入接口。 |
| `src/slim_guard/agent/composition.py` | 无条件构建完整工具目录，不再传营养启用开关。 |
| `src/slim_guard/agent/assembly/nutrition.py` | 固定提供营养工具执行器，保留 RAG、超时和预算参数。 |
| `src/slim_guard/agent/manifest.py` | 独立清单构建也使用完整工具目录。 |
| `src/slim_guard/agent/definition.py` | 删除营养启用字段及其 RAG 依赖校验。 |
| `src/slim_guard/config.py` | 删除对应 Settings 字段。 |
| `src/slim_guard/main.py` | 删除旧开关的启动检查和参数传递，保留引用与凭据检查。 |
| `tests/unit/agent/test_tool_catalog.py` | 验证有/无 RAG 时营养工具均注册；替换失效的可选注入测试。 |
| `tests/unit/agent/test_runtime_assembly.py` | 验证不同回复模式下目录和执行器均包含营养工具且匹配。 |
| `tests/unit/test_nutrition_agent_tool.py` | 验证默认配置下可主动咨询，以及普通对话不会自动调用营养。 |
| `tests/unit/test_response_finalization.py` | 删除旧配置参数，原营养/风格/审查回归仍保留。 |
| `tests/unit/test_agent_runtime.py` | 更新实际可用工具集合预期，保留体重操作闭环断言。 |
| `tests/unit/test_settings.py` | 更新默认清单；验证 RAG 引用、凭据检查及可正常创建应用。 |
| `.env.example` | 删除旧环境变量示例。 |
| `deploy/env.server.example` | 删除旧服务器环境变量示例。 |
| `README.md` | 说明营养工具固定可用、调用按需，更新配置与启用步骤。 |
| `MULTI_AGENT_ROLLOUT.md` | 启用 RAG 的步骤不再要求额外打开营养 Agent 开关。 |
| `MULTI_AGENT_IMPLEMENTATION_PLAN.md` | 删除配置清单中的旧开关。 |
| `NUTRITION_RAG_IMPLEMENTATION.md` | 删除配置示例中的旧开关。 |
| `review/review_composition_py_result.md` | 记录本项行为边界和逐文件改动，更新审核方式。 |

### 验证结果

- 本项定向回归 **63 项通过**，覆盖工具目录、组装、配置/启动清单、营养调用、普通对话不自动咨询、业务记录、回复处理和回退。
- Ruff、相关 15 个源码文件的 Mypy、`git diff --check` 通过；源码、测试与有效配置示例已无旧开关引用（本文保留历史 review 记录和删除说明）。
- 使用本地脚本模型与测试数据库，不属于真实模型线上验收；未提交、推送、部署或重启 Expo。

## 16. 回复审查固定装配（2026-09-20）

### 行为边界

- 删除 `response_reviewer_enabled` / `RESPONSE_REVIEWER_ENABLED`，不再需要额外开关才能启用审查。
- 回复处理组件要求提供 Reviewer；缺失依赖在组装时直接报错，不留到运行后跳过。
- 群聊路径有教练候选时执行既有审查；只输出系统助手时，不新增风格/教练审查调用，也不强迫教练发言。
- 群聊审查拒绝或结构化输出失败时，仍丢弃教练候选并保留系统助手消息及工具卡片。Style 内部自检/重试保持不变。
- 旧整段回复路径仍按原条件，仅在有专业评估时执行独立 Reviewer；其风格、Core、营养返工路径保持不变。本项不声称所有消息都经过内容审查。
- 删除两条依赖旧审查开关的配置校验，保留“回复处理开启时不能关闭其风格路径”的合法性检查，防止 `on` 实际不装配回复处理的静默状态。
- `multi_agent_mode`、`group_chat_enabled`、`style_render_all_normal_replies` 及旧整段回复路径仍未删除：旧路径承载的返工机制必须在后续收口时明确处理，不能当作无用代码直接连带删除。
- Prompt、模型业务路由、权限、预算和数据库未改。仅同步示例和说明，不修改真实 `.env` 或服务器配置。

### 本次逐文件说明

| 文件 | 修改内容 |
|---|---|
| `src/slim_guard/response_pipeline/finalizer.py` | Reviewer 改为必需依赖；删除开关跳过分支，保留原触发范围和失败处理。 |
| `src/slim_guard/response_pipeline/stages.py` | 组装时拒绝缺失 Reviewer；将聊天结果从 `object` 修正为已有 `RoutingResult` 类型。 |
| `src/slim_guard/agent/assembly/responses.py` | 删除审查开关入参，继续固定构造 Reviewer。 |
| `src/slim_guard/agent/composition.py` | 删除审查开关传递。 |
| `src/slim_guard/agent/definition.py` | 删除旧字段和相关组合校验；保留回复处理开启所需的路径完整性校验。 |
| `src/slim_guard/config.py` | 删除对应 Settings 字段。 |
| `src/slim_guard/main.py` | 删除启动参数传递。 |
| `tests/unit/agent/test_required_response_review.py` | 新增缺失依赖、仅系统消息、审查拒绝、审查格式损坏四项测试。 |
| `tests/unit/agent/test_runtime_assembly.py` | 删除旧参数，验证回复处理启用时必有 Reviewer。 |
| `tests/unit/test_settings.py` | 验证无需额外审查开关即可启动，并保留非法回复路径组合测试。 |
| `tests/unit/test_response_finalization.py` | 删除旧参数；原群聊成功、专业审查和各责任 Agent 返工回归继续运行。 |
| `tests/unit/test_multi_agent_rollback.py` | 删除旧参数与恢复设置，保留重启及业务数据不重复验证。 |
| `.env.example` | 删除旧变量并更新开关注释。 |
| `deploy/env.server.example` | 删除旧变量示例。 |
| `README.md` | 更新固定审查依赖说明，并明确群聊和旧整段路径的真实审查/返工差异。 |
| `MULTI_AGENT_ROLLOUT.md` | 删除审查开关示例。 |
| `MULTI_AGENT_IMPLEMENTATION_PLAN.md` | 删除审查开关示例。 |
| `NUTRITION_RAG_IMPLEMENTATION.md` | 删除审查开关示例。 |
| `DISH_RECOGNITION_DIET_GUIDANCE_DESIGN.md` | 删除审查开关示例。 |
| `STYLE_MANAGEMENT.md` | 删除审查开关示例。 |
| `review/review_composition_py_result.md` | 记录本次修改、尚未删除的旧路径及验证范围。 |

### 验证结果

- **53 项定向测试通过**：应用配置、组件组装、群聊/营养、审查通过/拒绝/格式失败、风格与 Core/营养返工、关闭回复处理后的记录恢复。
- Ruff、相关 22 个源码文件的 Mypy、差异格式检查通过。使用本地脚本模型与测试数据库，未运行线上真实模型验收；未提交、部署或重启 Expo。

## 17. 删除重复的风格路径开关（2026-09-20）

### 事实与修改范围

- 清理前，`multi_agent_mode=on` 与 `style_render_all_normal_replies=False` 的组合已被运行定义校验拒绝；`off` 时则不会构建回复处理组件，不受后者取值影响。因此后者没有独立的合法执行模式。
- 删除 `style_render_all_normal_replies` / `STYLE_RENDER_ALL_NORMAL_REPLIES` 字段、条件和组合校验；回复处理是否装配暂时只由 `multi_agent_mode` 决定。
- 对此前合法配置，模型调用顺序、风格改写范围、审查与返工行为保持不变。没有将系统消息一律改为教练风格，也没有强制每轮产生教练消息。
- `validate_nutrition_rag` 现在仅剩 RAG 引用要求检查；该检查本轮保留。
- `multi_agent_mode`、`group_chat_enabled` 及旧整段回复路径仍待后续收口。旧路径的有限返工不能不加区分地删除，本轮尚未合并返工路径。
- 旧环境文件中的该变量由 Settings 原有的忽略多余字段行为忽略；没有新增兼容层。未修改真实 `.env`、服务器配置或数据库。

### 本次逐文件说明

| 文件 | 修改内容 |
|---|---|
| `src/slim_guard/agent/composition.py` | 回复处理装配条件简化为 `multi_agent_mode == "on"`。 |
| `src/slim_guard/agent/definition.py` | 删除重复风格开关及其组合校验。 |
| `src/slim_guard/config.py` | 删除对应 Settings 字段。 |
| `src/slim_guard/main.py` | 删除开关参数传递。 |
| `tests/unit/test_settings.py` | 删除旧字段默认值断言和失效的开关组合测试，清理无用导入。 |
| `tests/unit/agent/test_runtime_assembly.py` | 验证回复处理启用时风格组件仍固定装配。 |
| `.env.example` | 删除旧开关示例。 |
| `deploy/env.server.example` | 删除旧开关示例。 |
| `MULTI_AGENT_IMPLEMENTATION_PLAN.md` | 删除旧开关示例。 |
| `MULTI_AGENT_ROLLOUT.md` | 删除旧开关示例。 |
| `DISH_RECOGNITION_DIET_GUIDANCE_DESIGN.md` | 删除旧开关示例。 |
| `STYLE_MANAGEMENT.md` | 删除旧开关示例。 |
| `review/review_composition_py_result.md` | 记录本次清理依据、逐文件变化和剩余范围。 |

### 验证结果

- **45 项定向测试通过**：Agent 配置与组装、应用配置、回复处理、审查与返工、关闭回复处理后的记录恢复。
- Ruff、相关 15 个源码文件的 Mypy、`git diff --check` 通过；源码、测试及有效配置示例已无该开关引用，本文保留历史说明。
- 使用本地脚本模型与测试数据库，不属于线上真实模型验收；未提交、推送、部署或重启 Expo。

## 18. 删除引用要求开关与剩余组合校验（2026-09-20）

### 事实与修改范围

- `nutrition_require_rag_citations` 只参与配置组合检查和启动参数传递，没有控制运行时引用校验。启用 RAG 时它必须为真；关闭 RAG 时它不改变行为。
- 删除该字段、`NUTRITION_REQUIRE_RAG_CITATIONS` 示例和启动检查；删除 `AgentRuntimeDefinition.validate_nutrition_rag`，不再保留失去用途的方法。
- 保留 `ProfessionalClaim` 的来源契约以及 `NutritionAssessmentValidator` / `NutritionCitationValidator` 的既有运行时检查：RAG claim 必须有可解析、属于本次调用的有效引用，不能伪造来源。
- 删除的是配置开关，不是引用约束，也不是要求每次咨询必须使用 RAG。允许模型通识的低风险判断仍按原有来源与风险规则校验。
- `nutrition_rag_enabled` 与必要的凭据检查暂时保留；无知识库部署模式的取舍不在本项扩展。群聊/旧回复路径、预算、Prompt、数据库与真实服务器配置均未修改。

### 本次逐文件说明

| 文件 | 修改内容 |
|---|---|
| `src/slim_guard/agent/definition.py` | 删除引用要求字段、剩余组合校验及无用导入。 |
| `src/slim_guard/config.py` | 删除对应 Settings 字段。 |
| `src/slim_guard/main.py` | 删除重复启动检查和旧参数传递；保留 RAG 凭据检查。 |
| `tests/unit/test_settings.py` | 删除旧字段断言和已失效的配置组合测试。 |
| `tests/unit/test_nutrition_agent.py` | 增加未填写引用、引用 ID 没有对应 Citation 两种情况的契约拒绝测试；沿用真实来源校验回归。 |
| `.env.example` | 删除旧环境变量示例。 |
| `deploy/env.server.example` | 删除旧服务器环境变量示例。 |
| `MULTI_AGENT_IMPLEMENTATION_PLAN.md` | 删除旧配置示例。 |
| `README.md` | 说明引用校验是固定契约，区分 RAG 来源与允许使用的模型通识。 |
| `review/review_composition_py_result.md` | 更新剩余项并记录本项修改边界与验证结果。 |

### 验证结果

- **66 项定向测试通过**，覆盖配置与组装、RAG 启动依赖、营养咨询、引用缺失/伪造拒绝、有效引用接受、低风险模型通识判断和回复处理。
- 两种缺引用情况均在产物契约层被拒绝；营养运行时校验继续核对引用是否来自本次检索，未因删除开关而放宽。
- Ruff、相关 15 个源码文件的 Mypy、`git diff --check` 通过。源码、测试和有效配置示例无旧字段及组合校验引用；本文保留历史 review 说明。
- 使用本地脚本模型与测试数据库，未进行线上真实模型验收。未提交、推送、部署、修改真实环境文件或重启 Expo。

## 19. 收敛人工编排版本配置（2026-09-20）

### 事实与修改范围

- `multi_agent_graph_version` 没有驱动方案选择，其值传给 Core、营养和回复处理生成的 Invocation；`app.state` 上的同名字段没有其他源码消费者。
- 与已删除的 AgentGraphManifest 不同，Invocation 的 `graph_version` 有实际用途：写入 `AgentInvocationRecord`，供后台 Trace 展示与筛选。不能把这些消费者描述成不存在。
- 删除 Settings、AgentRuntimeDefinition、启动传参与 `app.state` 中的人工配置入口。组装代码统一提供现有标识 `core-primary-v1`，标识不是模型路由策略，不会切换执行实现。
- 本轮只清理人工维护入口，没有删除 Invocation/数据库/API 中的 `graph_version` 字段，也没有替换旧数据或新增兼容层。已有历史标签仍能读取、筛选；新应用调用记录使用固定标识。
- `code_revision` 按约定保留，仍随 AgentManifest 记录实际执行代码版本。实际存在的 `multi_agent_mode` / `group_chat_enabled` 分支尚未合并，不能据此声称已经完全统一执行路径。
- 不修改真实环境文件、服务器或数据库。旧环境变量按 Settings 原有的多余字段忽略策略处理，不能改变运行标识。

### 本次逐文件说明

| 文件 | 修改内容 |
|---|---|
| `src/slim_guard/agent/definition.py` | 删除人工编排版本字段。 |
| `src/slim_guard/config.py` | 删除对应 Settings 字段。 |
| `src/slim_guard/main.py` | 删除旧参数传递和无消费者的 app.state 字段。 |
| `src/slim_guard/agent/composition.py` | 使用代码内单一记录标识连接 Core、营养和回复组件，明确它不选择编排方案。 |
| `tests/unit/test_settings.py` | 删除旧配置默认值与 app.state 断言。 |
| `tests/unit/agent/test_runtime_assembly.py` | 验证三处组装使用同一标识，覆盖现有模式组合。 |
| `.env.example` | 删除人工版本环境变量。 |
| `deploy/env.server.example` | 删除人工版本环境变量。 |
| `README.md` | 删除变量并区分架构记录标识、代码版本与实际运行开关。 |
| `MULTI_AGENT_ROLLOUT.md` | 删除旧环境变量示例。 |
| `MULTI_AGENT_IMPLEMENTATION_PLAN.md` | 删除仍指向旧 supervisor 名称的人工配置示例。 |
| `NUTRITION_RAG_IMPLEMENTATION.md` | 删除旧环境变量示例。 |
| `review/review_composition_py_result.md` | 更新进度、消费者事实与本次范围。 |

### 验证结果

- **65 项定向测试通过**：配置与组装、回复处理、营养咨询、TurnHarness、Invocation 持久化、后台 Trace 筛选。
- Ruff、相关 15 个源码文件的 Mypy、`git diff --check` 通过；源码、测试和有效配置示例无已删除的人工配置字段引用。
- 本地脚本模型与测试数据库验证，不属于线上真实模型验收；未提交、推送、部署或重启 Expo。

## 20. 分离群聊输出，准备收敛旧回复路径（2026-09-20）

### 事实与修改范围

- 旧整段回复路径仍有有效行为：根据 Reviewer 的返工目标调用 Style、Core 或营养修复；群聊路径目前没有同等的责任 Agent 返工闭环，审查不通过时保留系统消息、丢弃教练候选。
- 不能仅删除旧路径和开关就声称完成迁移，否则会丢失已有返工能力。本项先进行行为保持的结构拆分，不新增或改变模型路由规则。
- 将群聊处理及其专用的来源/工具结果投影方法从 `finalizer.py` 移至同 feature 的 `group_chat.py`；不复制保留旧实现、不增加兼容转发方法。
- `AgentResponseFinalizer` 负责共有的计划、中性稿和风格解析产物准备，再委托 `GroupChatFinalizer` 或继续原整段回复路径。两者共享同一个 `ResponseStageExecutor` 和 `ResponsePlanBuilder`，不创建第二套 Harness、模型运行器或持久化入口。
- 合并两处重复的风格解析/记录代码；每次仍解析一次、持久化一份 `style_resolution`。只输出系统消息时仍保留原来的风格解析记录，不新增风格模型调用。
- `finalizer.py` 从 638 行降为 354 行；群聊模块为 304 行。群聊正文和专用投影方法除所属类名外保持原样；移除原群聊方法未使用的中性稿产物参数。
- `multi_agent_mode` / `group_chat_enabled`、旧返工机制、群聊降级行为、预算、Prompt 均未改变。本项不是“教练不出现”问题的行为修复，也没有完成旧回复路径删除。

### 本次逐文件说明

| 文件 | 修改内容 |
|---|---|
| `src/slim_guard/response_pipeline/finalizer.py` | 移出群聊实现，合并风格解析准备；保留原整段回复及有限返工路径。 |
| `src/slim_guard/response_pipeline/group_chat.py` | 新增群聊输出模块，集中聊天决策调用、教练改写/审查、消息组合及专用上下文投影。 |
| `src/slim_guard/response_pipeline/__init__.py` | 修正包描述，不再将当前存在的两条路径称为单路径。 |
| `tests/unit/test_response_finalization.py` | 验证风格解析产物唯一、群聊中性稿来源、Conversation→Style→Reviewer 父子链及 Style 输入引用完整；原返工测试继续保留。 |
| `tests/unit/agent/test_required_response_review.py` | 在系统独答、审查拒绝/格式失败场景验证解析产物不重复；系统独答不创建教练中性稿。 |
| `review/review_composition_py_result.md` | 记录拆分依据、保持不变的行为和后续返工迁移缺口。 |

### 验证结果

- **77 项定向测试通过**：组装与配置、群聊消息、风格/审查、旧路径 Style/Core/营养返工、模式关闭后的记录恢复、TurnHarness。
- Ruff、相关 21 个源码文件的 Mypy、`git diff --check` 通过；另外逐段比对搬移的群聊正文与原实现，除类名引用外一致。
- 使用本地脚本模型与测试数据库，未做线上真实模型验收；未提交、推送、部署、修改数据库结构或重启 Expo。

## 21. 群聊风格返工与完整审查反馈（2026-09-20）

### 行为变化与边界

- 群聊不再把 Reviewer 的所有非通过结果都直接当成最终拒绝：当有效判决为 `repair` 且目标为 `response_style` 时，执行一次风格返工与复审。返工目标仍由审查模型提出，没有新增按用户关键词/场景判断的路由。
- 新增共享 `repair_style_once`，群聊和旧整段回复路径共用；删除旧路径内重复的“改写→复审”实现。不新建模型运行器、Harness 或业务工具调用。
- 修复旧反馈缺口：Style 原本只收到错误类别，现接收结构化 `ReviewerVerdict`，包括 `reason_summary`、问题片段、具体解释及目标；通过 JSON 数据传入，不拼入系统指令。
- 返工继续使用同一语义计划、风格快照和请求截止时间，并连接首轮 Style、Reviewer 产物。继续由已有执行组件检查每次调用预算，调用次数/Token 计入总统计；本项未实现整轮共享预算。
- 群聊只重写教练候选，不重新生成系统助手文本，不重跑 Core/Conversation 或记录工具。通过复审才采用；复审拒绝、再次要求返工或结构化失败时停止，不循环第三次，保留系统助手文本和工具结果卡片。
- 每次 Style 调用内部原有自检/有限重试保持不变。“外层一次返工”不等于“总共只调用模型两次”。
- 群聊的 Core/营养返工尚未迁移；这些目标仍按现有方式抑制教练候选。旧路径的 Core/营养行为未变，其反馈扩充也留待对应迁移，不把本项宣称为全套返工完成。

### 本次逐文件说明

| 文件 | 修改内容 |
|---|---|
| `src/slim_guard/response_pipeline/style_repair.py` | 新增同 feature 共享的一次风格返工操作，返回既有 StyleStage/ReviewStage。 |
| `src/slim_guard/response_pipeline/group_chat.py` | 接入模型指定的 Style 返工，累计用量，记录返工标志与终止原因，保留系统消息及卡片。 |
| `src/slim_guard/response_pipeline/finalizer.py` | 旧路径复用共享操作，删除重复调用代码，保留原采用/降级策略。 |
| `src/slim_guard/response_pipeline/stages.py` | 风格调用的反馈参数改为 ReviewerVerdict，传递完整审查结论。 |
| `src/slim_guard/agents/style/agent.py` | 改写请求携带结构化 review_feedback，替代仅错误类别的旧字段，不改变系统 Prompt。 |
| `tests/unit/response_pipeline/test_group_style_repair.py` | 新增四种复审结果的完整本地运行时测试，验证具体原因传入、同快照、Trace 关联、用量统计、卡片保留和业务记录不重复。 |
| `README.md` | 同步群聊可返工与未迁移的责任目标说明。 |
| `review/review_composition_py_result.md` | 记录本次真实行为变化及剩余范围。 |

### 验证结果

- **81 项定向测试通过**，包含新增四种群聊复审结果：通过、拒绝、再次要求修复、结构化失败；另覆盖原风格自检、内容审查、旧路径返工、记录恢复和 TurnHarness。
- 新增测试使用真实运行时、工具执行与 SQLite，模型响应为脚本：记录只写一次、卡片保留、风格快照/语义不变、失败原因进入请求、Trace 父子关系与 Token 统计均通过。
- Ruff、相关 26 个源码文件的 Mypy、`git diff --check` 通过。没有做线上真实模型验收；未提交、推送、部署、修改数据库结构或重启 Expo。

## 22. 剩余明确项的集中实现（2026-09-20）

用户授权不再逐文件审核。本批只修改本地工作区与测试数据库，未提交、推送、部署或重启 Expo。

### 22.1 已完成的行为与清理

- 只装配一条群聊回复处理路径：Core → Conversation 的角色表达 → 教练专用风格与审查。删除旧整段回复实现、专用 fallback 及失去消费者的导出，不保留运行开关作为兼容入口。模型仍决定本轮是否调用专业工具、是否产生教练候选；本项不是保证每轮出现教练。
- 删除 `multi_agent_mode`、`group_chat_enabled`、`nutrition_rag_enabled` 和其 Settings、组装、示例环境变量及现行操作指南引用。之前已删除的营养/审查/风格开关和组合校验不恢复。
- 群聊补齐 Core、Nutrition 返工。审查模型决定责任目标，原始完整判决通过不可变 Artifact 传入责任 Agent；只允许一次外层返工，重新交给 Conversation 后仍使用同一风格快照。复审失败保留系统必要消息及真实工具卡片，不重跑业务写工具。
- Reviewer 收到教练对应的中性语义及 `supporting_assessment`：可核对专业依据，但不会要求教练重复系统警告、整个评估或原始 RAG 证据。原始评估引用保留在父产物链上。
- 通用 `inspect_image` 不再接收 `focus`，结合原消息和可选查看问题理解任意图片。删除不再调用的 DishRecognitionAgent、专用 Prompt、Provider 方法/返回类型和仅服务于它们的测试。仍被后台历史识别更正接口使用的产物契约没有误删。
- 原图是聊天附件，Agent 不再设置默认 7 天 TTL。新增可空有效期、原文关联、模型注释；保存注释不覆写用户原文。近期对话统一携带消息附件和描述，删除 `memory_recent_image_count` 及独立 recent_images 加载窗口；后台相应展示消息关联的注释。
- 新迁移 `20260920_01_chat_attachment_retention` 保留现存资产 ID、字节、来源及索引，取消原 Agent 施加的有效期，并从合法历史工具结果回填描述（按用户所有权约束）。已物理删除的图片无法恢复。显式过期的临时资产仍由存储清理服务处理，不把缓存有效期重新塞回 Agent 配置。
- `budgets.turn` 新增整轮共享额度，`budgets.core` 与 Invocation 局部额度继续独立生效。文本模型、视觉、记忆召回模型、专业调用与专业工具共享本轮计数；RAG answerability 的文本模型也接入。上下文快照记录整轮与 Core 的实际配置。
- 预算默认值为工程保护上限：整轮 32 次模型尝试、16 次工具尝试、128,000 Token；Core 仍为 6 / 8 / 64,000，单次专业调用 Token 默认 32,000。失败请求计入尝试，工具重放尝试也计入。Provider 在调用后返回 Token，单次请求仍可能超额，后续调用才被阻止；不声称是严格计费上限。外部 Mem0 服务内部调用、Embedding/Rerank 和后台离线训练不冒充已纳入这个文本/视觉模型额度。
- 附件存储和视觉工具另设 `assembly/attachments.py`，不混入业务记录仓储组装。策略版本由实际上下文、工作记忆和安全模块提供；AgentManifest 明确只是 Core 身份记录，不是全系统可重放快照。没有恢复无消费者的 GraphManifest。
- RAG 能力固定存在，缺 Provider 是知识不可用而不是自动换旧资料：v2 缺少 Provider 不静默降级读取 v1；只有显式选择现有 v1 检索引擎时才使用其词法服务。

### 22.2 当时未完成项与边界（现已由第 23 节承接）

**通用信息澄清任务与 PendingAction/handoff 的统一仍未实现。** 原文第 8 节明确留待设计复用边界；本批已向用户提出选择：将 handoff 演进为多任务澄清模块，保留 PendingAction 处理操作授权，通过 task_id 关联。尚未收到确认，因此未更改这部分存储和生命周期，也没有宣称菜品反复确认已经得到完整修复。

相关的任务模块拆分、handoff 配置归属及旧菜品确认保护向通用任务的迁移，都应在同一个闭环内完成，不能只改名称或删保护。当前图片观察的不确定性不能作为任务已解决/未解决的权威状态；现有业务工具中的确认检查尚未统一。

保留 `code_revision` 和 `agent_specialist_timeout_seconds`，遵守前面的明确约定。后台 Worker、外部 Provider、存储引擎与用户功能开关不是同一类配置，本批未无差别删除所有部署参数。

### 22.3 逐文件说明与验证

逐文件说明见 [本批改动清单](composition_cleanup_changes.md)。

- 扩大回归：`pytest tests/unit tests/integration -o addopts='' -q -p no:cacheprovider --tb=short`，**607 项通过**（277.55 秒）。有 2 条既有 Starlette HTTP 413 常量弃用警告，不是测试失败。
- 最后调整历史图片证据投影后，另跑 `test_evidence_plane.py`、`test_nutrition_agent_tool.py`、`test_settings.py`、`test_response_finalization.py`、`agent/test_attachment_migration.py`：**39 项定向测试通过**，包含新补充的历史观察不携带当前确认状态用例。该范围与扩大回归重叠，不能相加作为独立用例数。
- 旧表迁移验证包括原图字节、来源、可空有效期、索引和跨用户观察隔离；使用 SQLite。PostgreSQL 的 DROP NOT NULL 分支尚未做实机迁移验收。
- Ruff、**255 个源码文件**的 Mypy、`git diff --check` 通过。
- 不把脚本模型响应说成真实模型效果验收；未证明线上教练出现、RAG 命中或所有澄清场景已正确。未提交、推送、部署、修改服务器数据库或重启 Expo。


## 23. 通用跨轮任务与操作授权关联（2026-09-23）

用户已确认继续实施第 22.2 节。通用任务这一剩余项已在本地实现；第 22 节的“等待确认”仅为历史记录。

### 23.1 本次完成

- 以 conversation_tasks 替代单个 active handoff，多个事项并存，支持逐轮更新、解决、取消和过期。任务版本冲突与幂等保护由存储层执行。
- 保留原消息与附件引用，累积每轮用户引文；模型解释单独保存，不当成用户原话。查询支持分页，最近对话窗口不再决定能否恢复待办。
- Core 通过通用工具决定任务推进；不枚举菜品/体重/目标场景，不规定业务步骤顺序。
- PendingAction 继续负责冻结参数、授权与恢复，通过 task_id 关联。完成澄清不会获得操作授权，关联任务不会改变待授权操作的状态或参数。
- 删除餐食工具中扫描旧图片、要求本轮包含全部候选菜名的专用代码及 visual_confirmation 参数；保留记录工具自身的契约、来源身份、权限与幂等检查。
- 任务里的用户引文可进入 Core 可引用来源与营养证据包。同一原消息的不同引文会保留，不把模型解释提升成用户事实。
- 配置和组装单独归属 tasks；后台显示多个待办。旧 handoff 工具、模块和导出删除，无兼容运行路径。
- 数据迁移保留旧任务 ID/归属/来源后删除 memory_handoffs；pending_actions 增加 task_id，同时清理已退役 confirm_dishes 授权。账号删除也清理任务及幂等快照。

逐文件说明、迁移和边界见 [通用任务改动清单](task_continuity_changes.md)。

### 23.2 验证与未执行事项

- 本次跨配置、Harness、工具、迁移与后台，执行了扩大回归：`pytest tests/unit tests/integration -o addopts='' -q -p no:cacheprovider --tb=short`，**617 项通过、1 项失败**（610.58 秒）。失败为旧测试仍断言 Prompt 版本 v30；已更新为本次 v31，并复测该测试组 **3 项通过**。没有将这次原始全量运行说成全绿。
- 最后运行 `tests/unit/tasks`、`test_evidence_plane.py`、`test_agent_prompt.py`，**31 项定向测试通过**（24.41 秒），覆盖新增任务、逐轮补充、原附件关联、任务/授权隔离、迁移、账号删除、多轮运行链路及营养证据投影；包含已修正的版本断言。与扩大回归有重叠，不相加作为独立用例数。
- Ruff、Mypy（265 个源码文件）、前端 TypeScript 检查及生产构建通过。2 条既有 Starlette HTTP 413 弃用警告不属于本次改动。
- 多轮测试使用真实 Runtime/Harness、工具和 SQLite，模型回复为脚本；验证状态/来源/工具执行链路，不保证真实模型对每种表达都判断正确。
- 未在 PostgreSQL 实机执行本次迁移，未做线上真实模型验收；未提交、推送、部署、清理服务器数据库或重启 Expo。
