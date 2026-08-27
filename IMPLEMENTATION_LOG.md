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
