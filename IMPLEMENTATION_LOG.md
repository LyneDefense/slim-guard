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
