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

## 最终交付状态（2026-08-27）

本文件定义的 MVP 范围已经完成。最终验证结果：

- Ruff 全仓检查通过；
- Mypy strict 检查通过（96 个源码文件）；
- Pytest 185 个测试全部通过；
- Python `compileall` 通过；
- sdist 与 wheel 构建成功，并在全新 Python 3.11 虚拟环境安装、导入成功；
- Git 对象检查通过，工作树干净；
- 本机 Docker daemon 未启动，因此没有在本机执行最终 `docker build`。Dockerfile 的 Python
  包构建路径已由 wheel 冷安装验证；服务器部署时仍应执行 README 中的 Compose 构建与健康检查。

服务器更新时执行：

```bash
git pull
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 app
curl -i https://enceladus.online/health/ready
```

`.env` 至少确认 `AGENT_RUNTIME_MODE=harness`、企业微信五项配置和 `ZHIPU_API_KEY` 存在。新增
调度、Outbox 恢复、图片清理与主动消息额度配置均有代码默认值，不补写也可启动。

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

### `feat: guard proactive wecom delivery`

- `src/slim_guard/db/models.py` → SQLAlchemy 权威数据模型 → 新增与日程 Job 一对一的主动消息账本，保存稳定平台消息 ID、路由、内容、发送状态、尝试次数和错误。
- `src/slim_guard/domain/routine/status.py` → 当日打卡状态读取器 → 按用户配置时区确定本地自然日边界，并从权威记录统计体重、饮食和运动完成情况。
- `src/slim_guard/services/proactive_delivery.py` → 微信主动发送策略与持久化边界 → 解析用户最新微信客服路由，检查 48 小时窗口，以默认三条主动消息上限预留平台额度，并提供内容防篡改、原子抢占和超时重试。
- `tests/unit/test_proactive_delivery.py` → 主动发送策略测试 → 覆盖有效路由、额度阻断、同 Job 幂等、发送租约重试和内容碰撞保护。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: execute reminder and review jobs`

- `src/slim_guard/services/routine_scheduler.py` → 日程任务执行服务 → 周期规划并抢占到期 Job，确定性跳过已打卡或过期任务，在微信窗口与客服状态允许时运行定时 Agent、发送消息并落终态；异常尝试由租约恢复且次数受限。
- `src/slim_guard/services/proactive_delivery.py` → 微信主动发送策略与持久化边界 → 持久化首次生成内容、来源 Turn 和当时会话窗口，支持重启后复用同一内容与消息 ID，不重复调用模型。
- `src/slim_guard/db/models.py` → SQLAlchemy 权威数据模型 → 为主动消息增加来源 Turn 与客户最近发言时间，形成从 Job、Agent Turn 到平台发送的完整审计链。
- `tests/unit/test_routine_scheduler.py` → 日程执行闭环测试 → 验证缺卡时生成并发送一次、已打卡时不调用模型、同日不重复，以及进程在准备发送后崩溃仍能复用冻结消息恢复。
- `tests/unit/test_proactive_delivery.py` → 主动发送策略测试 → 更新来源 Turn 契约并继续验证内容碰撞保护。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: activate production routine scheduler`

- `src/slim_guard/main.py` → FastAPI 生产装配与生命周期入口 → 在 Harness、企业微信和模型均可用时启动日程后台任务，注入持久化 Job、当日状态、主动发送策略与会话状态机，并在停机时有序收束。
- `src/slim_guard/config.py` → 环境配置契约 → 增加调度间隔、Job 租约、发送重试、最大迟到、模型超时、最大尝试以及微信主动窗口和额度上限配置。
- `src/slim_guard/harness/context_data.py` → 跨轮次权威事实提供器 → 按用户时区把当天体重、饮食和运动计数注入定时复盘上下文。
- `src/slim_guard/agent/composition.py` → Agent 依赖装配模块 → 为生产 Runtime 注入当日打卡状态读取器。
- `tests/unit/test_daily_checkin_status.py` → 当日状态测试 → 验证 Asia/Shanghai 本地日边界、有效状态过滤以及三类记录计数。
- `tests/unit/test_settings.py` → 应用配置测试 → 验证默认 48 小时窗口、主动消息额度预留和非法上限拒绝。
- `.env.example` → 部署配置模板 → 给出调度与主动发送策略的安全默认值。
- `README.md` → 部署与运维说明 → 更新当前 Harness 能力、数据保留事实、用户开启提醒方式、平台限制、配置项与关键日志。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: enforce health safety output guard`

- `src/slim_guard/harness/safety.py` → 健康安全硬门禁与最终回复校验器 → 确定性识别明确急症、自伤、高风险减重和未成年人信号，禁止此类 Turn 调用 Tool，并替换诊断、处方、危险建议或失败写入却声称成功的输出。
- `src/slim_guard/harness/runner.py` → 单轮 Agent 编排入口 → 在上下文编译前评估本轮风险，高风险时清空可用 Tool 并把不可覆盖的安全状态加入系统上下文。
- `src/slim_guard/harness/loop.py` → 有界 Model-Tool 内循环 → 在最终回复落库前执行 Output Guard，并将实际交付文本作为 Turn 结果。
- `src/slim_guard/harness/trace.py` → Harness 可重建运行轨迹 → 仅在门禁修改回复时追加不含敏感正文的 Guard 事件和原因码。
- `src/slim_guard/harness/events.py` → Harness 事件类型定义 → 新增 `output_guard` 审计事件。
- `src/slim_guard/agent/composition.py` → Agent 依赖装配模块 → 在生产 Runtime 启用 SlimGuard Output Guard 并升级冻结安全策略版本。
- `tests/unit/test_safety_guard.py` → 安全策略单元测试 → 覆盖明确急症、未成年人、普通打卡以及诊断处方输出替换。
- `tests/unit/test_agent_runtime.py` → Agent Runtime 闭环测试 → 验证急症输入无法调用任何 Tool、错误模型回复被安全升级信息替换且 Guard 事件可追溯。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: support reversible record corrections`

- `src/slim_guard/domain/records/service.py` → 跨记录类型的用户纠错服务 → 按当前用户所有权将体重、饮食或运动记录标记为 voided 或恢复为 active，保留历史且重复操作幂等。
- `src/slim_guard/domain/records/__init__.py` → 记录纠错领域公共出口 → 暴露记录类型、动作、结果与服务。
- `src/slim_guard/tools/records.py` → 记录状态 Tool → 允许 Agent 在先查询到确切 record_id 后撤销错误记录或恢复已撤销记录，拒绝跨用户访问和 superseded 状态冲突。
- `src/slim_guard/tools/__init__.py` → Tool 模块公共出口 → 暴露记录纠错 Tool 的定义、参数和处理器。
- `src/slim_guard/agent/composition.py` → Agent 依赖装配模块 → 把记录纠错 Tool 加入固定 Registry、Gateway 与 Agent Manifest。
- `src/slim_guard/agent/prompt.py` → 版本化 Agent 行为说明 → 要求纠错先查精确 ID、只做软撤销、正确新事实另行保存且逐步如实反馈。
- `tests/unit/test_record_status_tools.py` → 记录纠错服务和 Tool 测试 → 覆盖撤销、重复撤销、恢复以及跨用户不可见。
- `tests/unit/test_agent_runtime.py` → Agent Runtime 闭环测试 → 验证纠错 Tool 进入核心模型冻结工具列表。
- `tests/unit/test_settings.py` → 应用配置和 Manifest 测试 → 验证生产 Harness Manifest 固定纠错 Tool 版本。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: harden production lifecycle defaults`

- `src/slim_guard/services/maintenance.py` → 短期资产维护任务 → 启动后立即并周期性物理删除过期图片字节，单次失败记录日志但不终止服务。
- `src/slim_guard/main.py` → FastAPI 生产装配与生命周期入口 → 在 Harness 可用时启动图片维护任务，并在应用关闭前有序停止。
- `src/slim_guard/config.py` → 环境配置契约 → 将完成后的 Harness 设为默认 Runtime，并增加图片清理周期配置。
- `src/slim_guard/api/routes.py` → HTTP 健康与企业微信回调入口 → Harness 模式缺少智谱 Key 时 readiness 返回 503，避免静态降级服务被误判为生产就绪。
- `tests/unit/test_maintenance.py` → 图片维护任务测试 → 验证到期图片会从数据库物理删除且不可再次读取。
- `tests/unit/test_settings.py` → 应用配置和 Manifest 测试 → 更新默认 Runtime 为 Harness，同时保留显式 legacy 回滚测试。
- `.env.example` → 部署配置模板 → 默认启用 Harness 并给出六小时图片清理周期。
- `README.md` → 部署与运维说明 → 说明 Harness 默认值、legacy 回滚用途和短期图片自动清理行为。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `feat: recover interrupted wecom outbox sends`

- `src/slim_guard/db/repositories.py` → 企业微信消息与 Outbox 持久化边界 → 支持使用同一平台 msgid 原子重抢过期 sending 消息，并只扫描超过安全等待期的 planned/sending 回复，避免与正常生成流程竞争。
- `src/slim_guard/services/fixed_reply.py` → 企业微信同步、Agent 生成与发送服务 → 新增 Outbox 周期恢复器，直接复用数据库中已冻结的回复内容，不重新调用 Agent，并记录恢复数量与失败。
- `src/slim_guard/main.py` → FastAPI 生产装配与生命周期入口 → 启动和有序停止企业微信 Outbox 恢复后台任务。
- `src/slim_guard/config.py` → 环境配置契约 → 增加 Outbox 恢复扫描周期和发送租约过期时间。
- `tests/unit/test_sync_service.py` → 企业微信同步与发送服务测试 → 模拟进程在内容冻结后、平台发送前退出，验证恢复器不重新生成且只发送一次。
- `.env.example` → 部署配置模板 → 给出 Outbox 恢复和过期抢占的安全默认值。
- `README.md` → 部署与运维说明 → 说明 Outbox 恢复配置和关键日志。
- `IMPLEMENTATION_LOG.md` → 无人值守开发的持久交接日志 → 记录本次提交的文件职责与作用。

### `docs: finalize MVP implementation handoff`

- `IMPLEMENTATION_LOG.md` → 无人值守开发的最终交接记录 → 标记 MVP 完成，汇总静态检查、185 个测试、包冷安装和 Git 审计结果，记录 Docker daemon 未启动这一环境限制，并给出服务器更新与健康检查命令。

### `feat: add durable profile memory`

- `MEMORY_DESIGN.md` → 用户记忆模块实施设计 → 定义 Profile、Goal、Constraint、Working、Domain 与 Episodic 分层，给出来源、召回、遗忘、隐私和四阶段实施边界。
- `src/slim_guard/db/models.py` → SQLAlchemy 权威数据模型 → 新增用户记忆 Fact 与 Event 表，通过 active slot 唯一约束、来源链和版本引用保证同一记忆槽只有一个生效值。
- `src/slim_guard/memory/` → 记忆领域与持久化边界 → 实现版本化 Schema Registry、规范化值、用户隔离、精确来源证据、幂等写入、替换、查询和撤销。
- `src/slim_guard/tools/memory.py` → 受控记忆 Tool → 提供称呼/回复风格、饮食偏好、运动偏好写入，以及当前记忆查询和单条遗忘；不暴露通用自由写入接口。
- `src/slim_guard/agent/composition.py` → 生产 Agent 装配 → 注册五个记忆 Tool、Repository 和 Context Provider，并升级冻结的 Context/Memory 策略版本。
- `src/slim_guard/harness/context_data.py` → 跨轮权威上下文 → 在确定性数量预算内注入当前用户 active Profile Memory，排除已替换和已撤销值。
- `src/slim_guard/agent/prompt.py` → 版本化 Agent 行为说明 → 规定只记忆当前用户明确表达、证据必须逐字来自当前消息、不得从打卡或图片推断，并支持查看和遗忘。
- `src/slim_guard/harness/safety.py` → 最终回复真实性门禁 → 记忆写入或撤销失败时阻止模型错误声称已经记住或忘记。
- `src/slim_guard/config.py`、`.env.example` → 配置契约与模板 → 增加每轮 Profile Memory 最大预加载数量。
- `tests/unit/test_memory_repository.py` → 记忆仓储测试 → 覆盖版本替换、幂等、集合记忆、来源证据、跨用户隔离、撤销和并发单槽写入。
- `tests/unit/test_memory_tools.py` → 记忆工具测试 → 验证 Harness 身份注入、结构化输出和缺少可信来源时拒绝写入。
- `tests/unit/test_agent_runtime.py` → 跨轮运行测试 → 验证第一轮保存的称呼和回复风格在下一轮进入模型上下文，并验证失败写入真实性门禁。
- `README.md`、`AGENT_HARNESS_IMPLEMENTATION_PLAN.md` → 使用与设计索引 → 说明当前 Profile Memory 能力、数据保存事实、后续增量和详细设计入口。
- 最终验证 → Ruff 全仓、Mypy strict（102 个源码文件）、Pytest 194 项和 Python compileall 全部通过。

### `feat: remember user goals and constraints`

- `src/slim_guard/memory/contracts.py`、`registry.py` → 记忆类型与版本化 Schema → 新增目标体重、行为目标、饮食约束、运动约束和健康背景，约束带 180 天复核时间及 health/restricted 敏感级别。
- `src/slim_guard/memory/repository.py` → 记忆持久化边界 → 支持可注入时钟和 `review_after`，延续单槽版本替换、来源证据、幂等与用户隔离语义。
- `src/slim_guard/tools/memory.py` → Goal/Constraint 语义工具 → 新增目标体重、有限行为目标和用户自述约束写入；数字、单位、主体和 statement 必须来自当前消息证据。
- `src/slim_guard/harness/context_data.py` → 记忆召回上下文 → 注入 kind、sensitivity 和确定性 stale 标记，使待复核约束只能用于保守提醒。
- `src/slim_guard/agent/prompt.py` → Agent 记忆规则 → 明确目标不是测量或医学认可、约束不是诊断，禁止模型替用户制定后擅自保存。
- `src/slim_guard/harness/safety.py` → 记忆写入真实性门禁 → 把三个新增写工具纳入失败后不得声称成功的硬校验。
- `tests/unit/test_memory_tools.py` → Goal/Constraint 工具测试 → 覆盖克单位规范化、行为目标、饮食约束、复核时间及统一查询/遗忘。
- `tests/unit/test_context_data.py` → stale 召回测试 → 验证超过 180 天的健康约束仍带用户自述来源且被标记为待复核。
- `tests/unit/test_agent_runtime.py` → 跨轮目标闭环 → 验证目标体重进入下一轮上下文但不会生成权威体重测量记录。
- `README.md`、`MEMORY_DESIGN.md` → 能力与进度说明 → 标记 Increment 2 完成并说明目标、约束和剩余实施边界。
- 最终验证 → Ruff 全仓、Mypy strict（102 个源码文件）、Pytest 196 项和 Python compileall 全部通过。

### `feat: add bounded working memory and handoff`

- `src/slim_guard/memory/working.py` → 最近对话窗口 → 只读取当前用户最近已完成 Turn 的用户消息和最终助手消息，按 Turn 数及字符数确定性截断，排除工具、模型草稿和内部快照。
- `src/slim_guard/db/models.py`、`memory/handoff.py` → 临时交接持久化 → 实现单用户唯一 active Handoff、当前消息来源校验、幂等替换、跨用户隔离、解决和 14 天到期。
- `src/slim_guard/tools/memory.py` → Handoff 语义工具 → 新增显式留待下次和完成/取消两个受控操作；模型负责语义理解，执行器只校验证据、身份与状态。
- `src/slim_guard/harness/context_data.py`、`context.py` → 跨轮上下文 → 把 Working Memory 与权威领域事实分开注入，明确其只用于指代承接且当前消息优先。
- `src/slim_guard/agent/composition.py`、`config.py`、`.env.example` → 生产装配与配置 → 接入最近 Turn 数、对话字符预算和 Handoff TTL，并升级冻结的 Prompt、Context、Memory 与 Compaction 策略版本。
- `tests/unit/test_working_memory.py`、`test_handoff_repository.py`、`test_agent_runtime.py` → Increment 3 验证 → 覆盖可见文本筛选、确定性预算、跨用户隔离、幂等、替换、解决、过期，以及“刚才那个继续”和“下次接着做”的跨轮 Runtime 上下文。
- 最终验证 → Ruff 全仓、Mypy strict（104 个源码文件）、Pytest 202 项和 Python compileall 全部通过。

### `feat: complete memory privacy lifecycle`

- `src/slim_guard/db/models.py` → 增量兼容的隐私审计模型 → 新增 Agent Item 擦除账本和批量记忆操作账本，不给已有表强加迁移列，现有 SQLite 可由 `create_all` 安全补表。
- `src/slim_guard/memory/lifecycle.py`、`services/memory_maintenance.py` → 隐私保留执行器 → 到期后擦除用户/助手正文、Context、模型轨迹、Tool 参数和结果，保留哈希、引用与 reason code；同时清空 revoked value、标记到期 Fact/Handoff，并在启动及周期任务中幂等执行。
- `src/slim_guard/memory/repository.py`、`tools/memory.py` → 批量遗忘 → 事务性撤销当前用户全部 Profile/Goal/Constraint，使用持久化操作账本保证零条结果也可幂等重放，明确排除领域记录和消息幂等数据。
- `src/slim_guard/tools/pending.py`、`harness/pending_actions.py` → 跨 Turn 用户确认 → 把待确认操作作为非权威 Working Memory 注入，由模型理解当前确认/拒绝语义，再以当前消息证据解决冻结操作；不使用确认关键词解析器。
- `src/slim_guard/services/harness_reply_agent.py` → 微信确认提示 → Harness 暂停等待用户确认时交付明确提示，不再误走生成失败降级回复。
- `src/slim_guard/config.py`、`.env.example`、`main.py` → 生命周期配置与生产任务 → 增加 Transcript、revoked value 保留期和维护间隔；隐私任务不依赖模型是否在线，服务每次启动都会立即补做过期维护。
- `tests/unit/test_memory_maintenance.py`、`test_agent_runtime.py`、`tests/integration/test_callback_flow.py` → 完整验收 → 验证正文不可恢复、哈希审计与 Tool 幂等仍有效、领域记录不受影响、维护重复执行安全，以及企业微信确认提示和跨 Turn 批量清空闭环。
- 最终验证 → Ruff 全仓、Mypy strict（107 个源码文件）、Pytest 206 项和 Python compileall 全部通过。

### `fix: harden multimodal meal recording`

- `src/slim_guard/tools/meal.py` → 饮食工具真实 JSON 边界 → 把模型 JSON array 参数改为原生 list、领域层再转 tuple；视觉模型要求澄清时，确认状态由核心模型基于当前用户消息给出，代码只验证当前 Turn 来源，不解析用户措辞。
- `src/slim_guard/harness/loop.py` → 模型工具循环可靠性 → 同一工具连续两次返回相同不可重试错误后移除工具能力，由模型生成最终说明；可重试错误不受影响，并增加无参数、无正文的结构化失败日志。
- `src/slim_guard/agent_models/vision.py`、`zhipu_vision.py`、`tools/image.py` → model-first 视觉证据 → 视觉模型输出 category、summary、逐项 clear/uncertain 和 requires_user_confirmation，代码只严格解析结构，不按食物关键词判断确定性。
- `src/slim_guard/memory/working.py`、`harness/context_data.py` → 跨轮图片 Working Memory → 注入当前用户最近未过期图片的真实 asset_id、期限和非权威观察，让核心模型理解自然语言指代；不加载图片字节、不跨用户、不做关键词匹配。
- `src/slim_guard/harness/state_repository.py`、`trace.py` → 运行可观测性 → Turn step_count 写入实际模型调用数与工具调用数之和，等待、完成和异常终止均可审计。
- `tests/unit/test_meal_tools.py`、`test_agent_runtime.py`、`test_working_memory.py`、`test_harness_loop.py` → 生产故障回归 → 覆盖真实 JSON Gateway、不可重试熔断、图片用户隔离与过期、餐图跨三轮确认后唯一写入，以及脱敏失败日志。
- 最终验证 → Ruff 全仓、Mypy strict（107 个源码文件）、Pytest 213 项、Python compileall 与 Git diff check 全部通过。

### `fix: preserve model-first onboarding facts`

- `src/slim_guard/tools/memory.py`、`memory/registry.py` → 结构化资料与目标 → 体重目标省略单位时默认 kg、身高省略单位时默认 cm，并新增目标体脂和运动习惯档案；模型判断字段语义，执行层只验证数字、显式单位、来源、范围和幂等。
- `src/slim_guard/domain/body_fat/`、`db/models.py`、`db/migrations.py` → 权威体脂领域 → 新增用户隔离、可撤销、幂等的体脂测量与趋势记录，以及已有 SQLite 的增量建表迁移。
- `src/slim_guard/tools/body_fat.py`、`agent/composition.py` → 体脂 Tool 与生产装配 → 提供当前体脂写入和近期趋势读取，并冻结到 Agent Manifest。
- `src/slim_guard/agent/prompt.py` → model-first 行为边界 → 明确当前值、目标值、身高和运动习惯由模型理解；不把“目前不运动”误写成身体限制，不对用户自述代谢问题给出武断临床结论。
- `src/slim_guard/harness/safety.py` → 最终回复真实性保护 → 只有同类写操作全部失败却声称成功时才替换回复，保留模型对部分成功、部分失败的逐项如实说明。
- `src/slim_guard/agent/prompt.py` → 默认对话风格 v15 → 普通打卡采用一到三句的微信口语对话，多项写入自然合并确认，避免客服套话、逐字段报账和未请求的通用长建议；用户明确保存的 `response_style` 仍优先。
- `memory/working.py`、`tools/memory.py`、`memory/repository.py` → 跨轮历史事实写入 → 只为模型当前可见的同用户历史原话提供 `evidence_ref`，模型负责理解“保存上次那个”，执行层验证原文、用户归属、可见范围和新旧冲突；证据充足时直接写入，不要求用户重复数值。
- `src/slim_guard/admin/`、`frontend/` → 管理后台 → 用户统计、健康记录和 Trace 上下文来源增加体脂与新记忆类型。
- `tests/` → 故障回归 → 覆盖截图中的整条输入、默认 kg/cm、体脂记录及目标、运动习惯分类、部分成功回复、迁移与体脂软撤销。

### `feat: ingest durable memories before agent replies`

- `src/slim_guard/memory/ingestion.py` → 独立 model-first 记忆摄取 → 每条用户消息先由专用模型阶段结合数据库 active 记忆和有界用户原话回填窗口判断长期事实，再复用受控 Memory Tool 写入；不靠 Core Agent Prompt 是否恰好调用工具。
- `src/slim_guard/harness/runner.py`、`agent/composition.py` → 回复前数据库对账 → 摄取完成后才重新加载权威上下文；缺失新增、同值幂等、新的明确值版本化替换，Core Agent 读取数据库结果回复。
- `src/slim_guard/memory/working.py` → 用户证据回填窗口 → 只加载同用户、已完成、用户本人发送的最近消息，排除助手文本和当前 Turn，使升级前近期未结构化事实可以渐进写入。
- `src/slim_guard/admin/presentation.py` → 可视化链路 → 将 `memory_ingestion` 模型调用单独展示为“模型提取需要写入的长期记忆”，并继续显示实际 Memory Tool 及数据库结果。
- `tests/unit/test_memory_ingestion.py` → 端到端回归 → 覆盖“我身高179”首次自动入库、重复同值不建版本、“我身高180”替换旧值，以及原话滑出 3 Turn 后仍从近期证据回填。

### `feat: add model-ranked recall with optional Mem0 projection`

- `memory/engine.py` → Mem0 OSS HTTP 适配层 → 强制按 `user_id` 查询；只投影 PostgreSQL 权威事实，Mem0 不成为第二份真相；超时和响应错误不泄露 API Key 或上游正文。
- `memory/recall.py`、`harness/runner.py` → model-first Recall → 删除关键词筛选，先获取语义候选，再由独立模型从数据库候选中选出本轮需要的少量事实；非法 ID 被丢弃，模型故障时保守降级。
- `memory/index_sync.py`、`memory_index_outbox` → 可靠同步 → 权威记忆新增、替换、撤销与清空在同事务写入 Outbox，后台幂等同步、租约恢复、指数退避并在启动时回填历史 active 记忆。
- `admin/`、`frontend/` → 召回可视化 → Trace 白话展示候选数、入选数、Mem0 状态和降级原因；记忆页显示权威值、用户证据、有效期和语义索引状态。
- `MEM0_INTEGRATION.md` → 部署与回滚 → Mem0 只走服务器内网，无需新增 Nginx 入口；关闭语义索引不影响 PostgreSQL 记忆与聊天。

### `fix: preserve current-turn memory mutation semantics`

- `memory/contracts.py`、`memory/repository.py` → 权威写入回执 → 每个冲突槽明确返回 created、updated 或 unchanged；updated 同时保留前值和当前值，混合写入中的同值字段不再生成无意义版本。
- `memory/ingestion.py`、`harness/runner.py` → 回复前事实交接 → 记忆摄取完成后把经过类型校验、去除内部 ID 的本轮回执加入 Core Agent 权威上下文，避免将刚写入的新值描述成历史旧值。
- `harness/trace.py`、`admin/presentation.py`、`frontend/` → 可观察链路 → 独立记录“本轮记忆变更”事件，并以“身高从 179 cm 更新为 178 cm”等白话展示；回执正文沿用追踪保留期自动脱敏。
- `tests/` → 故障回归 → 覆盖首次新增、同值幂等、混合写入、179 cm 更新为 178 cm、Core Agent 上下文回执、后台白话展示与隐私清理。

### `ops: unify single-server production deployment`

- `deploy/compose.production.yaml` → 单一生产编排 → 用 `slim-guard-prod` 统一管理后端、管理前端、SlimGuard PostgreSQL、Mem0 和 pgvector；只向宿主机回环地址发布 18083/18084，数据库与 Mem0 不再暴露端口。
- `deploy/env.server.example`、`.gitignore` → 唯一服务器配置 → 将两个数据库、Mem0、模型、企业微信、后台和移动端配置收口到不入库的 `deploy/.env.server`，并校验重复键、占位值、密钥长度与文件权限。
- `deploy/mem0/` → 固定第三方生产镜像 → 固定 `mem0ai==2.0.19`、关闭 reload 和启动时重装；用临时白名单上下文构建，避免把旧 `.env` 密钥或 history 数据发送给 Docker 或烘进镜像，并持久化、一致性导出 Mem0 change history。
- `deploy.sh`、`deploy/scripts/` → 一键部署与安全切换 → 提供首次 cutover、日常发布、备份、状态、日志、应用回滚和旧架构精确清理；镜像在旧服务在线时预构建，切换失败恢复旧容器，日常健康失败恢复上一应用镜像，绝不删除数据卷。
- `deploy/nginx/slim-guard.locations.conf` → 宿主机入口片段 → Nginx 只负责现有域名的 HTTPS 与反向代理，后台认证继续完全由 FastAPI 负责，不引入 Basic Auth 或第二套密码。
- `SERVER_DEPLOYMENT.md` 及各部署文档 → 唯一生产操作手册 → 明确上传版 Mem0 无需服务器 clone、现有数据卷复用、Nginx include、首次切换、日常一条命令、回滚与稳定后清理流程；根 Compose 明确只用于本地开发。
- 验证 → Shell 语法、Python 编译与 Ruff、生产 Compose 渲染、后端 Ruff/Mypy、269 项 Pytest 以及管理前端生产构建全部通过。

### `fix: install Mem0 PostgreSQL runtime library`

- `deploy/mem0/Dockerfile` → Mem0 数据库驱动运行环境 → 在 `python:3.12-slim` 中安装 `libpq5`，让 requirements 中的纯 Python `psycopg` 实现可以加载 PostgreSQL 客户端库并执行 Alembic；避免首次统一 cutover 因 `no pq wrapper available` 回退旧容器。

### `fix: bundle Mem0 psycopg binary runtime`

- `deploy/mem0/Dockerfile` → 国内服务器可复现构建 → 改由清华 PyPI 镜像安装固定的 `psycopg[binary]==3.3.5`，不再等待腾讯云连接缓慢的 Debian apt 索引；二进制 wheel 自带 libpq 实现，并与旧 Mem0 当前运行版本一致。
- 首次服务器验证 → 兼容测试环境已初始化数据卷中的短数据库密码，仅在 `APP_ENV=production` 强制数据库密码至少 16 位，避免新配置与旧库内角色密码不一致。
