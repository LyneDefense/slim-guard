# 工具失败反馈与显式操作恢复

状态：本地实现，尚未提交、推送或部署。模型网关网络重试见 `model_request_retry.md`，与这里的业务恢复独立。

## 本次边界

- 模型依据工具 Observation 决定修正参数、读取状态、恢复已有操作、发起新的业务操作或询问用户。
- Harness 不因 `retryable=false` 撤掉整轮工具，也不指定错误之后必须调用哪个工具。
- 执行层控制权限、并发执行身份、不可变命令以及不确定写入的安全边界。
- 不修改 Conversation Agent、教练出现策略或风格迁移策略；不增加 Agent / Token 累计预算。

## 失败反馈

`ToolFailure` 新增：

- `field_errors`：字段路径、错误码和错误说明；不重复返回 Pydantic 原始 input/context。
- `effect_status`：本次尝试明确没有应用副作用（`not_applied`），或无法确定（`unknown`）。

网关参数校验、权限拒绝发生在执行前，明确标记未执行。业务工具只在可证明执行前失败或事务回滚的路径标记未执行。超时、未分类异常、无法确认的碰撞仍保持未知。

`retryable` 是“相同命令是否适合再次尝试”的提示，不再控制 Core 是否继续使用工具。恢复接口要求同时有明确未执行状态和可重试标记，不能只凭一个 `retryable=true` 重做写入。

若失败指向另一笔操作，`failure.effect_status` 描述当前调用的副作用；`operation.status` 描述被引用操作的状态。例如新调用被阻止，可以是“本次未执行、原操作结果未知”。

## 两种身份

| 身份 | 含义 | 保存位置 |
|---|---|---|
| `tool_call_id` / 工具执行键 | 一次模型工具调用及其结果 | 原 `tool_executions` 与 Trace |
| `operation_id` | 一笔被接受的业务写入 | 新 `tool_operations` |

操作 ID 由执行层生成。保存用户、会话、来源 Turn/消息、执行模式、原工具及版本、验证后的参数、原调用上下文、开始时间、当前状态和尝试序号。

业务处理器收到的 `execution_idempotency_key` 改为操作 ID；餐食、体重、体脂和运动记录继续利用业务表的唯一键防止同一操作重复插入。恢复复用原来源和原开始时间，不能让“默认当前时间”在恢复时漂移。

原工具执行键仍可用于已有确认/审批机制；恢复时使用当前可信权限重新检查原命令对应的授权，不把操作 ID 本身视为授权。

## 模型可用接口

- `list_unsettled_tool_operations`：列出当前用户、当前会话、当前执行模式下的未完成操作；同时按原工具权限过滤。
- `get_tool_operation(operation_id)`：读取原操作，支持的业务工具还会查权威数据库核实结果。查询工具成功，不代表被查询的写入成功。
- `resume_tool_operation(operation_id)`：不接收业务参数；成功则返回原结果，明确未执行且可重试时原样执行，未知/仍在运行时拒绝盲目再次写入。

当前轮上下文还携带未完成操作的 ID、原工具、状态和来源引用。是否查看、恢复仍由模型决定。

恢复校验包括用户、会话、执行模式、当前原工具权限、原工具版本、原命令授权与当前操作状态。重试抢占使用数据库条件更新；并发恢复只有一个调用获得执行权，各次模型调用另有执行记录。

## 不确定写入

在同一用户、会话、执行模式和工具能力范围内，尚有运行中/结果未知的操作时，暂不接受该能力的另一笔新写入。数据库唯一约束保证并发新调用也不能绕开这一点。

这是防止未知写入被换调用 ID、换参数再次执行的安全互锁，**不是把两条请求判定成同一个业务操作**。读工具、其他能力不因此被全局关闭。

餐食、体重、体脂、运动提供 `lookup_result`：按用户和操作 ID 查已提交记录。查到结果后持久化成功状态，恢复返回已存在的记录，而不是再调用写处理器。

查不到记录不证明没有提交或没有在执行。其他暂未提供查账适配器的能力也不能假装已核实；结果未知时保持不重做。取消或进程中断留下的 running 操作同样不能因时间过去就自动获得重试权限。

## 没有声称解决的问题

1. 成功后模型再次发起一个“新操作”，是否在业务上重复，不能靠随机操作 ID、参数相同或时间接近自动判断。本次没有加入模糊匹配或自动合并。不同新操作有不同身份，业务级重复判断仍需独立设计。
2. 操作引用与权限校验不能证明模型正确理解了用户意图。修改已保存数据仍是新的业务操作，不能通过 resume 偷换参数；本次没有新增自然语言授权判定器。
3. 没有宣称外部副作用 exactly-once，也没有为不支持权威查询的工具推断“肯定未执行”。
4. 没有把历史执行记录猜测合并成操作。迁移只新增表；启用后创建的操作才有完整恢复快照。
5. 审批要求仍然有效。缺少原命令当前所需授权时，恢复不会绕过它，也不会自动批准。

## 文件职责

- `src/slim_guard/harness/loop.py`：移除失败后强制纯文本与丢弃同批后续调用的分支；继续把失败反馈给模型。
- `src/slim_guard/tools/contracts.py`：字段错误、副作用状态与操作回执契约。
- `src/slim_guard/tools/gateway.py`：执行前结构化失败、普通调用记账；提供权威结果查询端口，区分由操作层管理超时的执行器。
- `src/slim_guard/tool_operations/contracts.py`：操作快照与存储端口。
- `src/slim_guard/tool_operations/models.py`、`repository.py`：操作表、持久化、并发抢占和未知写入互锁。
- `src/slim_guard/tool_operations/service.py`：新操作、核实、恢复、权限检查和冻结参数执行。
- `src/slim_guard/tool_operations/gateway.py`：组合普通工具调用与操作服务；恢复调用独立记账，不使用共享可变授权状态。
- `src/slim_guard/tool_operations/tools.py`：三个模型可调用接口。
- `src/slim_guard/tool_operations/migration.py`、`src/slim_guard/db/migrations.py`：增量迁移 `20260923_03_tool_operations`，不删旧数据。
- `src/slim_guard/tool_operations/lifecycle.py`：快照跟随聊天保留策略清理，用户清理时删除；清理不能解锁未知写入。
- `src/slim_guard/harness/preparation.py`：加载未完成操作的精简引用。
- `src/slim_guard/agent/assembly/tools.py`、`composition.py`、`tool_catalog.py`：接入执行层、上下文与工具目录。
- `src/slim_guard/agent/prompt.py`：增加通用恢复语义，不写固定业务步骤或关键词路由。
- `src/slim_guard/domain/{meal,weight,body_fat,exercise}/repository.py`：按用户与操作 ID 查询已提交记录。
- `src/slim_guard/tools/{meal,weight,body_fat,exercise}.py`：区分写入前参数错误、保留原开始时间、实现结果查询。
- `src/slim_guard/tools/{memory,long_term_memory,records,routine}.py`、`src/slim_guard/tasks/tools.py`：已知拒绝/回滚路径的副作用说明。
- `src/slim_guard/tools/pending.py`：保留底层失败详情及操作引用；授权处理器不另建一笔包裹业务写入的操作。
- `src/slim_guard/tools/execution_repository.py`：旧结果缺少新增字段时按契约默认值比较，保持重复完成校验兼容。
- `src/slim_guard/group_chat/composer.py`：恢复结果仍可投影业务卡片，同一 Turn 同一操作不重复出卡。
- `src/slim_guard/memory/lifecycle.py`、`src/slim_guard/mobile/platform.py`：接入快照保留和用户数据清理。

## 验证说明

测试覆盖格式修正、同批工具响应完整性、成功回放、已知未执行重试、不可修改冻结参数、并发恢复、权限/用户/会话/执行模式隔离、取消后重新查询、真实本地业务写入后丢失响应、查询不到记录时拒绝重做、保留策略和恢复卡片。

完整 Runtime 测试使用脚本模型与真实 Harness、工具、SQLite 和回复流水线：错误的 `foods` 格式返回字段错误，下一次模型调用修正后同一 Turn 写入一餐并生成一张卡片。不把脚本模型测试说成真实线上大模型联调；没有连接服务器或重启 Expo。

实际验证结果：

- 定向回归 **163 passed**：上述工具/操作恢复、Harness、权限与待审批、迁移、领域工具、任务、用户数据清理、卡片，以及移动端 API 和回调集成测试。两条既有 Starlette 弃用警告，不是全量测试。
- 最后调整后复测 **38 passed**：操作恢复与完整 Runtime、记忆工具、Runner 生命周期及 Trace 隐私；与上一组范围重叠，不能相加成独立用例数。
- Ruff、Mypy（287 个源文件）、修改文件格式与 `git diff --check` 通过。
- 数据库、普通工具网关、操作服务、操作网关、运行时装配共五条独立进程导入通过。独立导入检查发现的新端口类型依赖环已通过仅类型导入消除。
- 未修改服务器配置、未执行生产迁移，未提交/推送/部署。
