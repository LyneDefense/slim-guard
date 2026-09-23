# composition 清理改动清单

本清单汇总这轮 review 的本地改动，包含此前逐批已完成的结构拆分。通用任务的后续实现见 [逐文件清单](task_continuity_changes.md)，最新验收边界以 Review 第 23 节为准；本地修改不代表已部署。

## 运行配置与组装

| 文件 | 内容 |
| --- | --- |
| [src/slim_guard/agent/composition.py](../src/slim_guard/agent/composition.py) | 连接能力组件、统一模型预算包装；不再选择旧回复路径。 |
| [src/slim_guard/agent/definition.py](../src/slim_guard/agent/definition.py) | 按 models / budgets / memory / tasks / pending_actions 引用配置，删除退役开关与图片保留期。 |
| [src/slim_guard/agent/manifest.py](../src/slim_guard/agent/manifest.py) | 复用唯一工具注册表，策略标识从所属模块导入。 |
| [src/slim_guard/agent/tool_catalog.py](../src/slim_guard/agent/tool_catalog.py) | Core 工具定义的唯一来源，营养咨询能力固定注册。 |
| [src/slim_guard/agent/assembly/attachments.py](../src/slim_guard/agent/assembly/attachments.py) | 新增附件资产与通用图片理解工具组装。 |
| [src/slim_guard/agent/assembly/business.py](../src/slim_guard/agent/assembly/business.py) | 组装业务记录与上下文；移出图片资产/识别职责。 |
| [src/slim_guard/agent/assembly/memory.py](../src/slim_guard/agent/assembly/memory.py) | 集中记忆、召回与后台提取依赖。 |
| [src/slim_guard/agent/assembly/nutrition.py](../src/slim_guard/agent/assembly/nutrition.py) | 接入外部提供的知识端口，不擅自回退旧知识服务。 |
| [src/slim_guard/agent/assembly/responses.py](../src/slim_guard/agent/assembly/responses.py) | 固定创建 Conversation、风格、审查与责任返工组件。 |
| [src/slim_guard/agent/assembly/tools.py](../src/slim_guard/agent/assembly/tools.py) | 同一工具注册表连接执行器、授权与幂等恢复。 |
| [src/slim_guard/agent_models/config.py](../src/slim_guard/agent_models/config.py) | 模型配置模块；视觉输出限制随模型配置。 |
| [src/slim_guard/memory/config.py](../src/slim_guard/memory/config.py) | 记忆配置模块；去掉独立近期图片窗口。 |
| [src/slim_guard/harness/pending_config.py](../src/slim_guard/harness/pending_config.py) | 既有授权/审批有效期，尚未充当通用信息澄清任务配置。 |
| [src/slim_guard/config.py](../src/slim_guard/config.py) | 删除旧能力开关、图片 TTL 与独立图片窗口的 Settings 字段。 |
| [src/slim_guard/main.py](../src/slim_guard/main.py) | 同步新配置、单一路径及 RAG Provider 依赖；v1 引擎必须显式选择。 |

## Harness 与共享预算

| 文件 | 内容 |
| --- | --- |
| [src/slim_guard/runtime/budget_config.py](../src/slim_guard/runtime/budget_config.py) | 分离整轮与 Core 预算，保留 Invocation 局部 Token 配额。 |
| [src/slim_guard/runtime/turn_budget.py](../src/slim_guard/runtime/turn_budget.py) | 新增 ContextVar 隔离的整轮用量；模型尝试、视觉和工具调用共用额度。 |
| [src/slim_guard/harness/runner.py](../src/slim_guard/harness/runner.py) | 建立和释放 Turn 预算作用域，上下文快照记录实际限制。 |
| [src/slim_guard/harness/failures.py](../src/slim_guard/harness/failures.py) | 将整轮模型预算耗尽转换为稳定错误码。 |
| [src/slim_guard/tools/gateway.py](../src/slim_guard/tools/gateway.py) | 在工具执行前申请整轮额度。 |
| [src/slim_guard/agents/nutrition/tools.py](../src/slim_guard/agents/nutrition/tools.py) | 专业只读工具同样扣减整轮工具额度。 |
| [src/slim_guard/harness/manifest.py](../src/slim_guard/harness/manifest.py) | 删除无消费者的 GraphManifest；澄清 Core Manifest 不是完整回放快照。 |
| [src/slim_guard/harness/safety.py](../src/slim_guard/harness/safety.py) | 安全策略标识由安全模块自己提供，未修改安全判断规则。 |

## 图片与上下文

| 文件 | 内容 |
| --- | --- |
| [src/slim_guard/agent/runtime.py](../src/slim_guard/agent/runtime.py) | 保存原消息与附件时不再施加 Agent 默认 TTL。 |
| [src/slim_guard/tools/image.py](../src/slim_guard/tools/image.py) | 通用图片理解、原文/查看问题传入、保存描述性注释；删除食物前置分类。 |
| [src/slim_guard/agent_models/vision.py](../src/slim_guard/agent_models/vision.py) | 删除不再使用的菜品专用 Provider 协议与返回类型。 |
| [src/slim_guard/agent_models/zhipu_vision.py](../src/slim_guard/agent_models/zhipu_vision.py) | 只保留通用 inspect 入口。 |
| [src/slim_guard/agent_models/__init__.py](../src/slim_guard/agent_models/__init__.py) | 清理菜品专用模型契约导出。 |
| [src/slim_guard/agents/dish_recognition/__init__.py](../src/slim_guard/agents/dish_recognition/__init__.py) | 只公开仍被历史后台更正 API 消费的契约。 |
| [src/slim_guard/agents/dish_recognition/contracts.py](../src/slim_guard/agents/dish_recognition/contracts.py) | 删除无调用方的 AgentResult，保留实际使用的识别产物契约。 |
| [src/slim_guard/domain/assets/contracts.py](../src/slim_guard/domain/assets/contracts.py) | 原图可长期保存，新增原文关联。 |
| [src/slim_guard/domain/assets/repository.py](../src/slim_guard/domain/assets/repository.py) | 按用户隔离保存和读取描述，显式过期资产仍支持清理。 |
| [src/slim_guard/domain/assets/migration.py](../src/slim_guard/domain/assets/migration.py) | 资产有效期可空、历史字节保留、旧合法工具观察回填。 |
| [src/slim_guard/db/models.py](../src/slim_guard/db/models.py) | 调整资产字段，不创建第二张重复图片表。 |
| [src/slim_guard/db/migrations.py](../src/slim_guard/db/migrations.py) | 登记附件保留与描述回填迁移。 |
| [src/slim_guard/memory/working.py](../src/slim_guard/memory/working.py) | 按消息读取附件及描述，不再另取最近 N 张图片。 |
| [src/slim_guard/harness/context_data.py](../src/slim_guard/harness/context_data.py) | 统一对话附件上下文，移除独立图片窗口参数。 |
| [src/slim_guard/orchestration/evidence.py](../src/slim_guard/orchestration/evidence.py) | 从消息附件取历史视觉观察，不把旧观察直接当成当前待办。 |
| [src/slim_guard/admin/presentation.py](../src/slim_guard/admin/presentation.py) | 在所属消息下显示图片注释，明确是模型描述。 |
| [src/slim_guard/tools/meal.py](../src/slim_guard/tools/meal.py) | 先修正旧检查类型防御；9 月 23 日进一步删除菜品专用确认检查，改用通用任务依据。 |

## 群聊与返工

| 文件 | 内容 |
| --- | --- |
| [src/slim_guard/response_pipeline/finalizer.py](../src/slim_guard/response_pipeline/finalizer.py) | 仅准备中性稿、计划与风格快照，再委托群聊处理；删除旧整段回复路径。 |
| [src/slim_guard/response_pipeline/group_chat.py](../src/slim_guard/response_pipeline/group_chat.py) | 统一群聊编排、有限责任返工、计数与降级；不重复业务写入。 |
| [src/slim_guard/response_pipeline/repair.py](../src/slim_guard/response_pipeline/repair.py) | 责任 Agent 内容修复与表达修复分离；使用完整原始计划。 |
| [src/slim_guard/response_pipeline/style_repair.py](../src/slim_guard/response_pipeline/style_repair.py) | 一次带完整审查反馈的风格返工和复审。 |
| [src/slim_guard/response_pipeline/stages.py](../src/slim_guard/response_pipeline/stages.py) | 持久化父子调用链、完整失败反馈、失败用量；删除旧 safe_fallback。 |
| [src/slim_guard/response_pipeline/planning.py](../src/slim_guard/response_pipeline/planning.py) | 区分教练必须表达的语义和供审核参考的专业依据。 |
| [src/slim_guard/agents/style/agent.py](../src/slim_guard/agents/style/agent.py) | 改写收到完整 ReviewerVerdict，不只收到错误类别。 |
| [src/slim_guard/agents/nutrition/repair.py](../src/slim_guard/agents/nutrition/repair.py) | 验证同轮原始评估和 Reviewer 产物，沿用冻结检索输入。 |
| [src/slim_guard/agents/nutrition/agent.py](../src/slim_guard/agents/nutrition/agent.py) | 专业返工接收结构化完整判决。 |
| [src/slim_guard/agents/reviewer/agent.py](../src/slim_guard/agents/reviewer/agent.py) | 明确 supporting_assessment 是审核依据，不要求教练重复整个评估。 |
| [src/slim_guard/agents/reviewer/context.py](../src/slim_guard/agents/reviewer/context.py) | 编译教练内容与专业审核依据。 |
| [src/slim_guard/agents/reviewer/contracts.py](../src/slim_guard/agents/reviewer/contracts.py) | 新增有类型的 supporting_assessment。 |
| [src/slim_guard/response_pipeline/__init__.py](../src/slim_guard/response_pipeline/__init__.py) | 更新包职责说明。 |
| [src/slim_guard/agent/__init__.py](../src/slim_guard/agent/__init__.py) | 同步模块化入口，移除 GraphManifest 导出。 |
| [src/slim_guard/harness/__init__.py](../src/slim_guard/harness/__init__.py) | 移除退役清单导出。 |

## 删除的旧实现

- `src/slim_guard/agents/dish_recognition/agent.py`、`prompt.py`：不再使用的专用食物识别执行路径。
- `tests/unit/test_dish_recognition_agent.py`、`test_agent_graph_manifest.py`：仅服务于删除实现的测试。
- `tests/unit/test_multi_agent_rollback.py`：旧运行开关回退测试；以 [test_runtime_restart.py](../tests/unit/test_runtime_restart.py) 验证同一运行方案重启后的数据读取。
- Provider 专用方法、旧整段回复路径及其 fallback 已从原文件删除，不另留兼容层。

删除的本地受 Git 管理文件仍可从 Git 历史恢复；本批没有清空服务器数据库。

## 测试与说明文件

- [共享预算测试](../tests/unit/runtime/test_turn_budget.py)：跨 Agent 用量、错误尝试、Token 超额后阻止、并发 Turn 隔离。
- [旧附件升级测试](../tests/unit/agent/test_attachment_migration.py)：NOT NULL 旧表重建、图片内容与索引保留、旧描述回填及跨用户隔离。
- [群聊返工测试](../tests/unit/response_pipeline/test_group_style_repair.py)：Style/Core 目标与复审 pass/reject/repair/invalid，卡片和写入只发生一次。
- [回复完整链路](../tests/unit/test_response_finalization.py)：营养、聊天、风格、审查与 Nutrition 返工，校验专业依据与完整失败原因。
- [证据投影](../tests/unit/test_evidence_plane.py)：历史图片描述保持观察级来源，不再用真假确认标志推断当前任务状态。
- [Core 运行时](../tests/unit/test_agent_runtime.py)、[上下文](../tests/unit/test_working_memory.py)、[图片工具](../tests/unit/test_image_tools.py)、[Provider](../tests/unit/test_zhipu_vision_gateway.py)、[数据库迁移](../tests/unit/test_db_migrations.py)、[企业微信回调](../tests/integration/test_callback_flow.py)：更新新契约及相邻回归。
- `tests/unit/agent/`、`agent_models/`、`harness/`、`memory/`、`runtime/test_execution_budget_config.py`、`test_settings.py`：模块配置与装配约束。
- `tests/unit/test_memory_ingestion.py`、`test_memory_extraction.py`、`test_nutrition_agent.py`、`test_nutrition_agent_tool.py`：更新固定 Conversation 路径和完整返工反馈。
- [SystemConversationScript](../tests/conversation_script.py)：仅供聚焦业务工具的测试隔离聊天响应脚本；仍执行真实回复管线。实际教练路径有独立测试，不以此模拟线上成功。
- `.env.example`、`deploy/env.server.example`、`README.md`、`MULTI_AGENT_ROLLOUT.md`、`STYLE_MANAGEMENT.md`、`NUTRITION_RAG_IMPLEMENTATION.md`、`MULTI_AGENT_IMPLEMENTATION_PLAN.md`：清理旧参数和操作说明。
- `DISH_RECOGNITION_DIET_GUIDANCE_DESIGN.md`：标明是历史设计，不能按其中旧开关和食物预分类指导部署。

工作区中原有的 `AGENTS.md`、`IMPLEMENTATION_LOG.md` 及移动端配置/资料页等改动予以保留，不计为本批新实现。
