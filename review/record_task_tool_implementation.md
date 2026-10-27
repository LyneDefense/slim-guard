# 记录、任务与工具重构实施状态

依据：[记录与跨轮任务重构设计](../RECORD_TASK_TOOL_REFACTOR_DESIGN.md)。

更新日期：2026-10-26。

## 当前结论

此前的实施说明停留在“领域契约阶段”，已与代码不符。主要的 1–4 项能力已经进入实际 Harness 和移动端链路；本轮补齐旧记录迁移中“未知时间”和“旧字段证据来源”的标注，并修正过时的迁移/恢复测试。下文记录当前实现，不代表线上部署、PostgreSQL 并发或真实模型语义已验收。

## 已实现的主链路

### 1. 记录契约、持久化与事务回执

- `src/slim_guard/features/health_records/` 定义统一的记录视图、领域内容、发生时间、字段来源、识别问题和局部修改契约；菜品有稳定子项 ID。
- `persistence/store.py` 在同一事务中写记录、revision、字段问题清理、操作回执，并在请求要求时提交关联任务更新。
- 更新使用记录行锁和 `expected_revision` 比较；同一操作 ID 重放原始回执，操作 ID 对应不同命令则冲突。
- 写入前校验用户、Turn、来源消息和字段证据归属；失败时不留下半写入的记录、任务或回执。
- `persistence/migration.py` 保留记录 ID，为旧菜品生成稳定 ID；旧时间为空时迁移为 `precision=unknown/basis=unknown`，不会伪造时间。旧字段被标为 `source_kind=legacy` 且不虚构来源引用；可验证的旧用户消息关联会保留。

### 2. 查询、修改工具与 Agent 注册

- `features/health_records/tools.py` 通过统一 `RecordService` 提供记录创建、分页搜索、详情读取、字段更正和状态修改工具。
- `agent/tool_catalog.py` 将这些能力注册给 Core；Tool Adapter 只做参数/来源绑定和领域服务调用，不选择业务流程或要求固定的确认步骤。
- `persistence/queries.py` 提供按日期、来源、餐次、状态和文本筛选的游标分页；查询按用户隔离。
- 更正使用 typed patch 和当前 revision，支持菜名替换、否定、认可、份量、顺序和移除等明确语义；过期识别问题随对应字段修订清理。
- 对状态变化采用作废/恢复，不物理删除记录；不能将旧 superseded 记录错误恢复为当前记录。

### 3. 跨轮请求与用户后续回答

- `tasks/record_requests.py` 将待解决记录请求保存为结构化 `RecordRequest`，包含候选目标、已知更改、未解决事项及来源。
- `tasks/repository.py` 提供带 revision 的任务写入、读取和列表；不确定性不等于授权，也不要求每个识别问题都先阻塞写入。
- `tasks/record_changes.py` 将已经完成的记录更正和对应任务关闭放入同一记录事务。用户后续回答可以引用原始附件/记录，不要求重新上传或重复确认已明确的信息。
- `tests/unit/test_agent_runtime.py` 覆盖“图片识别有不确定字段时先保存记录，后续用户补充后修改同一记录”的受控模型场景。

### 4. 旧授权链退出与移动端历史

- `20260924_02_retire_approval_waits` 将旧的等待确认 Turn 标为失败并写入“流程已退役、未执行”事件，然后删除旧 `pending_actions` 表；它不会批准或执行冻结的旧命令。已经发布的迁移保留为历史迁移，不改写迁移版本。
- 当前 Core Tool Catalog 不包含 `link_task_authorization`、`resolve_pending_user_action` 或批量清空记忆工具；相应的旧 Agent Tool Adapter、公开导出和过期后台标签已移除。底层存储管理方法仍留在 memory persistence 层，供后续明确授权的设置接口复用。
- 运行时工具恢复只恢复明确引用的、可验证操作，不依赖用户确认队列。
- `api/mobile_record_routes.py` 与 `mobile/record_projection.py` 提供记录历史/进度读取；`mobile-app/src/features/record-history/RecordHistory.tsx` 在首页展示饮食、运动历史，支持展开、分页加载、识别问题提示及刷新。

## 本轮实际修正

1. 旧记录没有发生时间时，迁移原来会保留默认 `datetime`，读取时可能因缺少时间戳而校验失败。现在按是否有真实时间/日期分别迁移为 `datetime`、`date` 或 `unknown`。
2. 历史记录此前没有字段级来源标注。迁移现在以 `legacy` 明确表示来源细节不可得，不会把旧值误称为用户口述或图片识别。
3. 有可信旧消息外键的记录，会保留该来源消息 ID；没有外键则不编造引用。
4. 增加历史记录迁移回归用例，验证未知时间、字段证据、稳定菜品 ID、来源消息及 revision。
5. 更新旧的跨轮任务迁移测试，使其验证审批表已退役，而不是要求把它留在新数据库里。
6. Tool Operation 测试删除已不存在的“工具确认授权”假设，改为验证恢复操作时仍检查当前工具能力授权。普通工具授权和隔离策略仍由 Harness 强制执行。
7. 清理不可达的批量清空记忆 Agent Adapter，以及要求旧确认队列继续工作的过期回调测试；工具 manifest 验收改为当前真实 Tool Catalog。

## 尚需独立验收的范围

- **PostgreSQL 并发与提交中断**：本轮使用 SQLite 定向测试；没有用 PostgreSQL 实例验证 `FOR UPDATE`、并发 revision 冲突和进程崩溃后的提交结果。
- **真实模型语义**：受控模型测试证明链路和数据库行为，不证明模型在所有自然语言、模糊图像、纯否定或多轮补充下都会选对目标；需要真实模型验收记录。
- **手机 UI**：本轮检查了前端组件/API 结构，但没有在实体设备上验收不同日期/时区、下拉分页和刷新体验。
- **线上状态**：没有连接服务器、执行迁移、重启服务或部署。

## 本轮验证

测试命令：

```bash
uv run pytest tests/unit/features/health_records tests/unit/tasks tests/integration/test_mobile_api.py tests/unit/test_agent_runtime.py tests/unit/test_db_migrations.py tests/unit/test_tool_gateway.py tests/unit/test_tool_registry.py tests/unit/tool_operations/test_recovery.py tests/unit/test_settings.py tests/unit/test_memory_tools.py tests/unit/test_long_term_memory_tools.py tests/integration/test_callback_flow.py -q --tb=short
```

结果：**162 passed，2 warnings**，耗时 105.05 秒。覆盖记录存储、任务、迁移、Harness/Runtime、操作恢复、Tool Catalog、记忆工具与移动端 API/回调，不是全量测试。两条警告是现有 Starlette 413 常量弃用提示。

静态/前端检查：相关 Python 文件 `ruff check` 通过；移动端 `npm run typecheck` 通过。没有运行全项目 Mypy、全量测试或 Expo 双端打包。
