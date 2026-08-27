# SlimGuard 持续实现日志

> 开始日期：2026-08-27  
> 记录规则：每个增量在提交前追加一节，使用“文件 → 职责 → 本次作用”的格式。  
> 当前目标：完成 SlimGuard MVP；按约定暂不实现 Eval Harness 和 Evolution System。

## MVP 完成范围

- 企业微信文字与图片消息进入 Agent Harness；
- 体重、饮食和运动成为可追溯、幂等的权威记录；
- Agent 能查询近期记录并给出基于事实的反馈；
- 支持缺卡提醒和每日复盘，同时遵守微信客服主动发送限制；
- 生产装配、失败降级、重启恢复、配置和运维说明完整；
- 所有增量通过 Ruff、Mypy 和自动化测试。

## 暂不包含

- Eval 数据集、评分器、版本对比和发布门禁；
- Evolution/改进 Agent；
- H5 页面和完整运营后台 UI；
- Redis、Celery 和对象存储的分布式部署替换。

## 提交日志

### `docs: establish continuous implementation log`

- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 定义今晚的 MVP 完成范围、明确暂缓项，并建立后续每个提交统一使用的文件级记录格式。

### `feat: persist user-scoped image assets`

- `src/slim_guard/db/models.py` → SQLAlchemy 权威数据模型 → 新增短生命周期图片资产表，保存用户归属、来源消息、内容哈希、字节、类型和过期时间。
- `src/slim_guard/domain/assets/contracts.py` → 图片资产领域契约与确定性校验 → 识别真实图片格式，拒绝 MIME 欺骗、无来源配对和无时区过期时间。
- `src/slim_guard/domain/assets/errors.py` → 图片资产领域错误 → 定义来源幂等冲突错误，防止同一微信消息被替换成另一张图片。
- `src/slim_guard/domain/assets/repository.py` → 图片资产持久化边界 → 实现幂等保存、按用户隔离读取、过期后不可读和批量清理。
- `src/slim_guard/domain/assets/__init__.py` → 图片资产模块公共出口 → 统一暴露领域契约和仓储。
- `tests/unit/test_image_asset_repository.py` → 图片资产仓储测试 → 覆盖幂等、跨用户隔离、内容冲突、过期清理和格式欺骗。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: add controlled vision inspection tool`

- `src/slim_guard/agent_models/vision.py` → 供应商无关的视觉模型契约 → 定义带图片字节、模型、Prompt、用量和请求 ID 的标准检查请求与响应。
- `src/slim_guard/agent_models/zhipu_vision.py` → 智谱视觉模型适配器 → 调用 GLM 视觉接口并规范化超时、网络、HTTP 和无效响应错误。
- `src/slim_guard/agent_models/__init__.py` → 模型模块公共出口 → 暴露视觉协议和智谱实现。
- `src/slim_guard/tools/image.py` → Harness 图片检查工具 → 按当前用户读取短期资产，根据体重秤、饮食或运动关注点调用视觉模型，并返回非权威观察结果。
- `src/slim_guard/tools/__init__.py` → Tool 模块公共出口 → 暴露图片工具定义、参数和执行器。
- `tests/unit/test_zhipu_vision_gateway.py` → 智谱视觉适配器测试 → 验证多模态请求序列化、响应解析和供应商错误归一化。
- `tests/unit/test_image_tools.py` → 图片 Tool 测试 → 验证资产所有权隔离、视觉请求内容和安全观察结果。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: connect image messages to agent runtime`

- `src/slim_guard/agent/runtime.py` → 渠道无关的 Agent 运行入口 → 接收文字或图片，将原始图片先保存为当前用户的短期资产，再把不透明 `asset_id` 写入 Turn。
- `src/slim_guard/agent/composition.py` → Agent 依赖装配模块 → 将图片工具、资产仓储和可选视觉网关加入 Registry、Gateway 与版本 Manifest。
- `src/slim_guard/agent/prompt.py` → 版本化 Agent 行为说明 → 要求图片必须先调用视觉工具，低清、冲突或不确定结果必须向用户确认而不是写入猜测值。
- `src/slim_guard/services/harness_reply_agent.py` → 企业微信到 Harness 的渠道适配器 → 把下载后的图片字节和 MIME 类型传给 Runtime，不再走图片 fallback。
- `src/slim_guard/main.py` → FastAPI 生产装配与生命周期入口 → 创建并关闭智谱视觉客户端，并把图片保留期和视觉输出预算注入 Runtime。
- `src/slim_guard/config.py` → 环境配置契约 → 新增可配置的短期图片保留秒数。
- `tests/unit/test_agent_runtime.py` → Agent Runtime 闭环测试 → 更新冻结工具集合，确认图片工具进入模型上下文。
- `tests/unit/test_harness_reply_agent.py` → 企业微信渠道适配测试 → 验证图片字节和类型完整进入 Runtime。
- `tests/unit/test_settings.py` → 应用配置和 Manifest 测试 → 验证 Harness 版本包含图片工具。
- `tests/integration/test_callback_flow.py` → 企业微信端到端测试 → 验证图片回调依次经过媒体下载、资产隔离、视觉检查、体重写入、趋势查询和最终回复。
- `.env.example` → 部署配置模板 → 增加图片保留期配置示例。
- `README.md` → 部署与运行说明 → 说明 Harness 已支持图片及默认七天保留策略。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: persist authoritative meal records`

- `src/slim_guard/db/models.py` → SQLAlchemy 权威数据模型 → 新增餐次、食物清单、份量描述、时间、状态和来源链完整的饮食记录表。
- `src/slim_guard/domain/source.py` → 业务记录共用来源校验器 → 统一验证 Turn 属于当前用户且 Item 属于该 Turn，供体重、饮食和后续运动记录复用。
- `src/slim_guard/domain/weight/repository.py` → 体重权威仓储 → 改用共用来源校验器，保持原有隔离语义并消除重复实现。
- `src/slim_guard/domain/meal/contracts.py` → 饮食领域契约 → 定义餐次、食物与份量、发生时间、状态和可审计写入命令，不保存伪精确热量。
- `src/slim_guard/domain/meal/errors.py` → 饮食领域错误 → 区分来源不可信和幂等冲突。
- `src/slim_guard/domain/meal/repository.py` → 饮食权威持久化边界 → 实现来源验证、幂等写入、冲突保护和近期记录查询。
- `src/slim_guard/domain/meal/__init__.py` → 饮食领域公共出口 → 暴露领域契约和仓储。
- `tests/unit/test_meal_repository.py` → 饮食仓储测试 → 覆盖幂等、时间排序、用户隔离和内容冲突保护。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: expose meal recording tools`

- `src/slim_guard/tools/meal.py` → 饮食 Tool 定义和受控处理器 → 提供饮食写入与近期查询工具，解析可靠时间并把 Harness 身份、来源和幂等键注入领域命令。
- `src/slim_guard/tools/__init__.py` → Tool 模块公共出口 → 暴露饮食工具常量、参数、处理器和构造函数。
- `src/slim_guard/agent/composition.py` → Agent 依赖装配模块 → 将饮食工具和仓储加入固定 Registry、执行 Gateway 与 Agent Manifest。
- `src/slim_guard/agent/prompt.py` → 版本化 Agent 行为说明 → 规定文字和图片饮食的保存边界，禁止臆测食物、配料、精确热量或不可靠时间。
- `tests/unit/test_meal_tools.py` → 饮食 Tool 测试 → 验证权威写入、近期读取、来源 ID 返回以及缺少 Gateway 执行身份时拒绝写入。
- `tests/unit/test_agent_runtime.py` → Agent Runtime 闭环测试 → 验证饮食工具出现在核心模型的冻结工具列表。
- `tests/unit/test_settings.py` → 应用配置和 Manifest 测试 → 验证生产 Harness Manifest 固定饮食工具版本。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: persist authoritative exercise records`

- `src/slim_guard/db/models.py` → SQLAlchemy 权威数据模型 → 新增开放运动名称、时长、步数、距离、设备报告消耗、时间和完整来源链的运动记录表。
- `src/slim_guard/domain/exercise/contracts.py` → 运动领域契约 → 使用开放活动名称承接多样运动表达，并对可选量化指标设置合理范围和时区校验。
- `src/slim_guard/domain/exercise/errors.py` → 运动领域错误 → 区分来源不可信和幂等冲突。
- `src/slim_guard/domain/exercise/repository.py` → 运动权威持久化边界 → 实现来源验证、幂等写入、冲突保护和近期运动查询。
- `src/slim_guard/domain/exercise/__init__.py` → 运动领域公共出口 → 暴露领域契约和仓储。
- `tests/unit/test_exercise_repository.py` → 运动仓储测试 → 覆盖幂等、时间排序、用户隔离和内容冲突保护。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: expose exercise recording tools`

- `src/slim_guard/tools/exercise.py` → 运动 Tool 定义和受控处理器 → 提供运动写入与近期查询工具，确定性换算米/公里/英里并注入可信来源和执行身份。
- `src/slim_guard/tools/__init__.py` → Tool 模块公共出口 → 暴露运动工具常量、参数、处理器和构造函数。
- `src/slim_guard/agent/composition.py` → Agent 依赖装配模块 → 将运动工具和仓储加入固定 Registry、执行 Gateway 与 Agent Manifest。
- `src/slim_guard/agent/prompt.py` → 版本化 Agent 行为说明 → 保持开放活动名称，只允许保存用户或设备明确报告的量化指标，禁止模型估算运动消耗。
- `tests/unit/test_exercise_tools.py` → 运动 Tool 测试 → 验证权威写入、近期读取、确定性距离换算和领域范围保护。
- `tests/unit/test_agent_runtime.py` → Agent Runtime 闭环测试 → 验证运动工具出现在核心模型的冻结工具列表。
- `tests/unit/test_settings.py` → 应用配置和 Manifest 测试 → 验证生产 Harness Manifest 固定运动工具版本。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: compile authoritative cross-turn context`

- `src/slim_guard/harness/context_data.py` → 跨轮次权威事实提供器 → 并行读取用户资料以及近期体重、饮食和运动记录，裁剪内部 ID 与来源字段后生成有界、可序列化的模型上下文。
- `src/slim_guard/harness/context.py` → Harness 上下文编译器 → 将权威用户事实作为独立系统消息放在本轮不可信输入之前，并拒绝不可序列化的上下文。
- `src/slim_guard/harness/runner.py` → 单轮 Agent 编排入口 → 在初始化 Turn 后加载用户事实，将其注入编译器，并把无效上下文转成可审计的终止状态。
- `src/slim_guard/agent/composition.py` → Agent 依赖装配模块 → 复用体重、饮食和运动仓储组装生产上下文提供器，并升级冻结的 Context/Memory 策略版本。
- `tests/unit/test_context_data.py` → 权威上下文提供器测试 → 验证用户昵称、体重、饮食和运动能跨 Turn 汇总成紧凑事实。
- `tests/unit/test_context_compiler.py` → 上下文编译器测试 → 验证权威事实位于用户输入之前且保持结构化 JSON。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: persist configurable checkin routines`

- `src/slim_guard/db/models.py` → SQLAlchemy 权威数据模型 → 新增用户级时区、体重提醒、饮食提醒和晚间复盘时间配置表。
- `src/slim_guard/domain/routine/contracts.py` → 日程偏好领域契约 → 校验 IANA 时区、分钟精度时间、启停语义和非空修改命令。
- `src/slim_guard/domain/routine/repository.py` → 用户日程持久化边界 → 实现按用户读取、部分更新、单项关闭和启用日程扫描。
- `src/slim_guard/domain/routine/__init__.py` → 日程领域公共出口 → 统一暴露提醒类型、配置契约和仓储。
- `src/slim_guard/tools/routine.py` → 日程配置 Tool → 允许 Agent 在用户明确要求后分别设置、查询或关闭体重、饮食和每日复盘日程。
- `src/slim_guard/tools/__init__.py` → Tool 模块公共出口 → 暴露日程 Tool 的定义、参数和处理器。
- `src/slim_guard/agent/composition.py` → Agent 依赖装配模块 → 把日程 Tool 和用户日程仓储加入固定 Registry、Gateway 与 Agent Manifest。
- `src/slim_guard/agent/prompt.py` → 版本化 Agent 行为说明 → 约束提醒必须由用户主动开启、按本地时区配置且不得保证微信平台一定送达。
- `src/slim_guard/harness/context_data.py` → 跨轮次权威事实提供器 → 将当前用户已经配置的日程加入后续 Turn 上下文。
- `tests/unit/test_routine_preferences.py` → 日程领域和 Tool 测试 → 覆盖非法时区、缺失时间、部分更新、关闭单项和用户隔离。
- `tests/unit/test_agent_runtime.py` → Agent Runtime 闭环测试 → 验证日程 Tool 出现在核心模型的冻结工具列表。
- `tests/unit/test_settings.py` → 应用配置和 Manifest 测试 → 验证生产 Harness Manifest 固定日程 Tool 版本。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: add durable routine job ledger`

- `src/slim_guard/db/models.py` → SQLAlchemy 权威数据模型 → 新增按用户、任务类型和本地日期唯一的日程 Job 表，记录执行状态、次数、租约、结果 Turn 和终止原因。
- `src/slim_guard/domain/routine/jobs.py` → 日程 Job 状态机与规划器 → 按用户时区生成当天到期任务，通过原子抢占和过期租约实现并发防重与重启恢复，并限制终止状态转换。
- `src/slim_guard/domain/routine/__init__.py` → 日程领域公共出口 → 暴露 Job 状态、引用、仓储和规划器。
- `tests/unit/test_routine_jobs.py` → 日程 Job 状态机测试 → 验证未到时间不规划、同日规划幂等、租约期内不重复、租约过期可恢复以及只能完成一次。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: run scheduled agent turns`

- `src/slim_guard/harness/events.py` → Harness 事件类型定义 → 新增体重提醒和饮食提醒的明确 Trigger，使定时任务不再依赖含糊意图分类。
- `src/slim_guard/agent/runtime.py` → 渠道无关的 Agent 运行入口 → 新增无用户输入的定时 Turn 命令，只允许体重提醒、饮食提醒和每日复盘，并在运行时禁用全部 Tool 写入。
- `src/slim_guard/agent/prompt.py` → 版本化 Agent 行为说明 → 定义三类定时 Turn 的生成边界，要求依据权威事实判断缺卡、客观复盘且禁止编造和调用工具。
- `tests/unit/test_agent_runtime.py` → Agent Runtime 闭环测试 → 验证定时 Turn 无需伪造用户消息、没有可用 Tool、保留明确 Trigger 并正常持久化输出。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。
