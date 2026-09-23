# 通用跨轮任务收口（2026-09-23）

本次承接 composition review 中最后未定案的一项。用户已确认实施：用通用任务替代单例 handoff，保留既有 PendingAction 操作授权，通过 task_id 关联。这里只修改本地代码与测试数据库，没有修改服务器或重启 Expo。

## 实际行为

- 一个用户可同时有多个任务，创建任务不再自动“完成”前一个任务。
- 模型决定是否需要任务、还缺哪些信息、用户补充解决了什么以及下一步；没有“确认菜品→记录→营养评价”的固定步骤。
- 任务保存 objective、unresolved、status、revision、原消息和图片引用、逐轮用户引文及模型解释。解释与引文分开；之前回答不会被新一轮覆盖。
- 每次更新绑定当前 Turn 的真实用户输入；检查用户/会话所有权、引文匹配、允许的状态转换、乐观锁版本与幂等操作标识。纯图片输入可作为来源，不要求伪造用户文字。
- 任务可以 open、resolved、cancelled、expired；终态不被静默重开。未结束任务的有效期由独立 tasks 配置管理，更新后续期。
- Task 解决、取消或与授权关联都不会批准/拒绝/执行任何操作。PendingAction 仍冻结具体工具参数，保留已有授权与恢复路径。
- Core 工具：save_conversation_task、get_conversation_task、list_conversation_tasks、link_task_authorization。查询支持分页，超出最近对话窗口后仍可取回任务依据和原附件引用。
- 上下文自动携带当前未完成任务；可核验的用户引文可供 Core 和营养证据包使用。任务摘要/模型解释不会被冒充用户事实。
- 删除餐食工具中“从最近 50 条结果找图片、要求本轮包含所有候选菜名”的专用检查与 visual_confirmation 参数。餐食记录仍受参数、来源身份、权限和写入幂等保护；语义判断交给模型及统一任务上下文。
- 后台用户页展示多个待办，Trace 展示任务操作和当前未完成项。

## 数据与配置迁移

- 新迁移：`20260923_01_conversation_tasks`。
- `memory_handoffs` 转入 `conversation_tasks`：保留 ID、归属、原消息来源、目标与时间；active 改为 open。读取已有消息补充可核对引文与附件引用，迁移后删除旧表。
- 新增 `conversation_task_operations` 记录幂等操作与结果快照；`pending_actions` 增加可空的 `task_id`，不改变现存有效操作的授权状态或参数。
- 删除已退役工具 `confirm_dishes` 对应的旧 PendingAction，不再在每次加载上下文时隐藏它们。
- `MEMORY_HANDOFF_TTL_DAYS` 被 `TASK_CLARIFICATION_TTL_DAYS` 替代，默认仍是 14 天。这是待办有效期，不是图片保存期限。
- 迁移代码会在未来正常部署启动时运行；本次只在隔离测试库运行，未清理服务器数据。删除的旧源码可从 Git 历史恢复。

## 逐文件说明

| 文件 | 改动 |
| --- | --- |
| [tasks/contracts.py](../src/slim_guard/tasks/contracts.py) | 定义任务、逐轮依据、更新命令及冲突错误；没有业务场景枚举。 |
| [tasks/config.py](../src/slim_guard/tasks/config.py) | 独立管理澄清任务 TTL。 |
| [tasks/ports.py](../src/slim_guard/tasks/ports.py) | 对外提供任务读写与授权关联端口。 |
| [tasks/repository.py](../src/slim_guard/tasks/repository.py) | 多任务存储、累积依据、分页、过期、版本冲突与幂等。 |
| [tasks/sources.py](../src/slim_guard/tasks/sources.py) | 校验当前及历史来源、绑定消息附件，按所有权读取原消息。 |
| [tasks/tools.py](../src/slim_guard/tasks/tools.py) | 面向 Core 的四个任务工具，返回可供模型纠错的失败信息。 |
| [tasks/presentation.py](../src/slim_guard/tasks/presentation.py) | 有界任务上下文投影，明确截断与来源。 |
| [tasks/context.py](../src/slim_guard/tasks/context.py) | 只投影任务中经来源校验的用户引文，不提升模型解释的权威性。 |
| [tasks/migration.py](../src/slim_guard/tasks/migration.py) | 迁移旧 handoff、增加授权关联列、清除退役菜品授权与旧表。 |
| [tasks/__init__.py](../src/slim_guard/tasks/__init__.py) | 明确新模块职责。 |
| [agent/assembly/tasks.py](../src/slim_guard/agent/assembly/tasks.py) | 在组装层连接任务仓储、授权仓储与任务工具。 |
| [agent/assembly/memory.py](../src/slim_guard/agent/assembly/memory.py) | 移除 handoff 的创建和工具注入。 |
| [agent/assembly/business.py](../src/slim_guard/agent/assembly/business.py) | 上下文改为引用任务端口。 |
| [agent/assembly/tools.py](../src/slim_guard/agent/assembly/tools.py) | 合并独立的任务工具执行器；沿用授权与恢复协调器。 |
| [agent/composition.py](../src/slim_guard/agent/composition.py) | 连接任务组件，不再自己创建授权仓储。 |
| [agent/definition.py](../src/slim_guard/agent/definition.py) | 引用独立 tasks 配置。 |
| [agent/tool_catalog.py](../src/slim_guard/agent/tool_catalog.py) | 注册通用任务工具。 |
| [agent/prompt.py](../src/slim_guard/agent/prompt.py) | 简短说明任务连续性、来源边界与授权区别，不规定业务执行顺序。 |
| [harness/context_data.py](../src/slim_guard/harness/context_data.py) | 加载任务列表及待授权操作的 task_id，删除旧 handoff 和菜品过滤。 |
| [harness/context.py](../src/slim_guard/harness/context.py) | 将任务用户引文纳入可引用来源，取消上下文中“一有歧义就必须问”的笼统要求。 |
| [harness/pending_actions.py](../src/slim_guard/harness/pending_actions.py) | 增加经过验证的任务关联与查询，关联操作不变更授权状态。 |
| [harness/safety.py](../src/slim_guard/harness/safety.py) | 更新任务写工具名称，仍检查失败后虚称成功。 |
| [orchestration/evidence.py](../src/slim_guard/orchestration/evidence.py) | 营养证据包接收任务用户引文，仍标记为用户自述而非医学事实。 |
| [tools/meal.py](../src/slim_guard/tools/meal.py) | 删除旧图片扫描、当前回复菜名匹配及 visual_confirmation 字段。 |
| [tools/memory.py](../src/slim_guard/tools/memory.py) | 删除旧 handoff 参数、定义和处理器。 |
| [tools/__init__.py](../src/slim_guard/tools/__init__.py) | 删除退役工具导出。 |
| [memory/config.py](../src/slim_guard/memory/config.py) | 移除 handoff TTL。 |
| [memory/__init__.py](../src/slim_guard/memory/__init__.py) | 删除旧 handoff 导出。 |
| [memory/repository.py](../src/slim_guard/memory/repository.py) | 更新职责变化后的记忆策略标识。 |
| [memory/working.py](../src/slim_guard/memory/working.py) | 更新工作记忆策略标识，任务不再是独立图片窗口。 |
| [memory/lifecycle.py](../src/slim_guard/memory/lifecycle.py) | 不再直接维护 handoff ORM。 |
| [services/memory_maintenance.py](../src/slim_guard/services/memory_maintenance.py) | 通过任务端口维护任务过期，统计改用 expired_task_count。 |
| [config.py](../src/slim_guard/config.py) | 环境参数改名为 task_clarification_ttl_days。 |
| [main.py](../src/slim_guard/main.py) | 配置注入和维护服务连接任务能力。 |
| [db/models.py](../src/slim_guard/db/models.py) | 替换 handoff 表模型，增加任务操作表和授权关联字段。 |
| [db/migrations.py](../src/slim_guard/db/migrations.py) | 注册本次迁移。 |
| [mobile/platform.py](../src/slim_guard/mobile/platform.py) | 删除账号时处理新的任务数据。 |
| [admin/repository.py](../src/slim_guard/admin/repository.py) | 通过任务端口返回多个待办，不直接读旧 handoff ORM。 |
| [admin/presentation.py](../src/slim_guard/admin/presentation.py) | 更新工具中文名称和任务上下文展示。 |
| [api/admin_routes.py](../src/slim_guard/api/admin_routes.py) | 注入后台任务查询端口。 |
| [frontend/src/features/tasks/types.ts](../frontend/src/features/tasks/types.ts) | 独立的任务展示契约。 |
| [frontend/src/features/tasks/TaskCards.tsx](../frontend/src/features/tasks/TaskCards.tsx) | 展示目标、剩余事项、版本与有效期。 |
| [frontend/src/types.ts](../frontend/src/types.ts) | 用户详情引用任务契约。 |
| [frontend/src/App.tsx](../frontend/src/App.tsx) | 使用任务组件替换单个 handoff 区域。 |

删除：`src/slim_guard/memory/handoff.py`、`tests/unit/test_handoff_repository.py`。旧工具和旧仓储不保留兼容执行路径。

配置说明同步更新 `.env.example`、`deploy/env.server.example`、`README.md`；未编辑实际部署环境文件。

## 验证范围

- `tests/unit/tasks/test_tasks.py`：并存、来源隔离、分轮补充、图片来源、版本冲突、幂等、取消/过期、任务和授权不混淆。
- `tests/unit/tasks/test_migration.py`：旧数据迁移、旧表删除、重复运行、新授权列与原授权状态保留。
- `tests/unit/tasks/test_context.py`：Core 与营养证据引用正确，模型解释不冒充用户事实。
- `tests/unit/tasks/test_runtime_flow.py`：真实运行时/数据库/工具、脚本模型的三轮补充→记录流程；只保留一轮近期对话仍能保留先前回答，业务记录只写一次。
- `tests/unit/tasks/test_account_cleanup.py`：账号删除清理所属任务及快照，不误删其他用户任务。
- 相邻测试同步更新：`test_agent_runtime.py`、`test_agent_prompt.py`、`test_meal_tools.py`、`test_memory_maintenance.py`、`test_db_migrations.py`、`test_settings.py`、`memory/test_config.py`。
- 最终运行数量见 composition review 实施记录。脚本模型验证工程链路，不能据此保证真实模型在所有输入上都正确建任务或正确理解答案；PostgreSQL 实机升级与线上真实模型验收未进行。
