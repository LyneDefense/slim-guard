# SlimGuard

SlimGuard 是一个 Python 编写的企业微信“微信客服”减脂助手。

当前 Harness 版本支持普通微信用户用自然语言或图片记录体重、饮食和运动，调用智谱
GLM 完成理解与回复，并把业务事实按用户隔离、幂等地保存。Agent 每轮只读取紧凑的用户
资料、当前有效的个性化记忆、近期权威记录、最近有限对话和当天打卡状态，不依赖无限增长的
原始聊天历史。
用户明确表达后，Agent 可以跨轮记住其偏好称呼、回复风格、饮食偏好和运动偏好；这些记忆
可以查询、更新或逐条撤销，不会从单次打卡或图片中自动猜测。

通道已经包含企业微信会话状态管理：新会话会从“未处理”自动认领为“智能助手
接待”，避免误入人工接待后 API 无法回复。若历史或误操作导致会话处于人工接待状态，
且客户消息在指定时间内没有人工回复，SlimGuard 会结束该人工会话并发送提示；客户再次
发信后会重新由智能助手接待。

## 本地启动

推荐 Python 3.13，代码和测试兼容 Python 3.11+。

```bash
cp .env.example .env
uv sync --dev
uv run uvicorn slim_guard.main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：

```text
GET http://localhost:8000/health/live
GET http://localhost:8000/health/ready
```

未填写企业微信配置时，`live` 返回 200，`ready` 返回 503，这是预期行为。

## 企业微信配置

在企业微信管理后台创建或选择一个微信客服账号，并配置通过 API 管理。将以下内容写入 `.env`：

```dotenv
WECOM_CORP_ID=企业ID
WECOM_KF_SECRET=微信客服Secret
WECOM_OPEN_KF_ID=客服账号ID
WECOM_CALLBACK_TOKEN=回调Token
WECOM_CALLBACK_AES_KEY=EncodingAESKey

ZHIPU_API_KEY=智谱 API Key
```

Agent Runtime 默认使用 `harness`：企业微信文字和图片消息会进入新版 Harness，并可调用
图片检查、体重、饮食、运动、纠错和提醒日程工具。图片作为用户隔离的短期资产默认保留
7 天，可通过 `AGENT_IMAGE_RETENTION_SECONDS` 调整；后台默认每 6 小时物理清理过期图片。
`shadow` 尚未开放，设置后会拒绝启动：

```dotenv
# harness：新版 Agent Harness；legacy：仅供回滚的旧版单次回复
AGENT_RUNTIME_MODE=harness
# 部署流水线可以写入 Git commit；未设置时为 development
AGENT_CODE_REVISION=development
ASSET_MAINTENANCE_INTERVAL_SECONDS=21600
```

智谱可选配置：

```dotenv
ZHIPU_TEXT_MODEL=glm-5.2
ZHIPU_VISION_MODEL=glm-5v-turbo
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4
ZHIPU_HTTP_TIMEOUT_SECONDS=45
ZHIPU_MAX_OUTPUT_TOKENS=1024
AGENT_REPLY_MAX_CHARS=1500

# 模型或图片下载失败时发给用户的降级提示
AGENT_FALLBACK_REPLY_TEXT=抱歉，我刚才没有成功分析这条记录，请稍后再发一次。
```

文字消息由 `glm-5.2` 处理；图片消息由智谱 5 系列视觉模型
`glm-5v-turbo` 处理。两者默认都关闭深度思考，以缩短微信回复时间。不要把 Key 提交到 Git。
模型能力和参数以智谱官方的
[`GLM-5.2`](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2) 和
[`GLM-5V-Turbo`](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5v-turbo) 文档为准。

会话状态机可以使用以下可选配置，默认值通常无需修改：

```dotenv
# 人工接待收到客户消息后，多少秒无人回复则自动结束人工服务
WECOM_HUMAN_IDLE_TIMEOUT_SECONDS=600

# 后台检查超时人工会话的间隔
WECOM_SESSION_WATCHDOG_INTERVAL_SECONDS=30

# 恢复进程退出前未完成的普通回复；使用原 platform msgid 防止重复
WECOM_OUTBOX_RECOVERY_INTERVAL_SECONDS=30
WECOM_OUTBOX_SEND_STALE_SECONDS=120

# 微信昵称、头像等客户资料的刷新间隔，默认24小时
WECOM_CUSTOMER_PROFILE_REFRESH_SECONDS=86400

# 允许下载的微信图片大小上限，默认10 MiB
WECOM_MEDIA_MAX_BYTES=10485760

# 自动结束人工会话后，通过事件响应接口发送给客户的提示
WECOM_HUMAN_TIMEOUT_MESSAGE=人工服务暂时没有响应，已结束人工接待。请再发送一次刚才的内容，SlimGuard 减脂助手会继续为你服务。
```

提醒和晚间复盘只会在用户明确设置后启用。例如用户可以直接说“每天早上 8 点提醒我称重，
晚上 9 点复盘”。后台调度器使用持久化 Job 和发送账本，服务重启不会重复生成或发送同一条
日程消息。微信客服主动消息仍受平台窗口和额度约束；默认只使用最多 3 条主动消息，为正常
对话保留余量：

```dotenv
ROUTINE_SCHEDULER_ENABLED=true
ROUTINE_SCHEDULER_INTERVAL_SECONDS=30
ROUTINE_JOB_LEASE_SECONDS=120
ROUTINE_SEND_RETRY_SECONDS=120
ROUTINE_MAX_LATENESS_SECONDS=7200
ROUTINE_AGENT_TIMEOUT_SECONDS=45
ROUTINE_MAX_ATTEMPTS=3
WECOM_PROACTIVE_ACTIVE_WINDOW_HOURS=48
WECOM_PROACTIVE_MAX_MESSAGES=3
```

超过客户最后发言后的配置窗口、额度不足、当天已经完成对应打卡、任务迟到超过两小时或
企业微信会话不在智能助手接待状态时，任务会记录明确的跳过原因，不会强行发送。

`REPLY_DELIVERY_MODE` 默认为 `automatic`。代码已经预留 `internal_review` 模式：回复草稿
进入 SlimGuard 自己的 `pending_review` 队列，批准后仍通过微信客服 API 发出，全程不把
企业微信会话切换到状态 3。当前尚未提供审核管理页面，因此部署环境请保持
`REPLY_DELIVERY_MODE=automatic`。

首次开通时可以分两次填写：先配置 `WECOM_CORP_ID`、`WECOM_CALLBACK_TOKEN` 和
`WECOM_CALLBACK_AES_KEY`，启动服务并让企业微信完成回调 URL 验证；验证完成、后台显示
Secret 后，再补充 `WECOM_KF_SECRET` 和 `WECOM_OPEN_KF_ID`。补全前 `/health/ready` 返回 503
属于预期行为。

拿到 Corp ID 和微信客服 Secret 后，可以运行下面的命令查询真正的 `open_kfid`：

```bash
uv run python -m slim_guard.tools.list_kf_accounts
```

把输出中对应客服账号的 `wk...` 值填入 `WECOM_OPEN_KF_ID`。

将回调地址设置为：

```text
https://你的公网域名/callbacks/wecom/kf
```

公网地址必须为 HTTPS。服务器出口 IP 还需要满足企业微信后台的可信 IP 要求。

## 数据

默认 SQLite 文件位于：

```text
data/slim_guard.sqlite3
```

保存同步 cursor、消息 ID、消息类型、用户身份与客户资料、会话状态、权威打卡记录、Agent
运行轨迹、个性化记忆、跨轮 Handoff、日程 Job 和出站回复。Harness 运行轨迹中的用户/助手正文、
模型可见上下文、Tool 参数和结果默认保留 30 天，之后由后台维护任务原位替换为不可逆 SHA-256
哈希和必要审计元数据；领域记录与消息幂等账本不依赖这些正文。下载图片会加密链路传输并作为用户隔离的短期数据库资产保存，
默认七天后不可读取并可清理。新增表会在启动时自动创建，已有 SQLite 文件无需删除。删除 SQLite
文件会丢失用户、记录、记忆、日程、去重和会话状态，真机环境不要随意删除。

## 用户记忆

当前 Memory 保存用户在当前消息中明确表达的长期资料：偏好称呼、回复风格、饮食与运动偏好、
目标体重、行为目标，以及用户自述的饮食、运动和健康约束。每条记忆都绑定当前用户、来源 Turn
和原文证据，旧值被替换后不会继续进入上下文。目标体重只是用户自述目标，不会写成一次测量，
也不表示系统认可其医学安全性；健康约束始终标记为用户自述，180 天后提示复核而不会变成诊断。
用户可以直接问“你记得我什么”，也可以要求忘记某一条明确记忆。每轮还会加载最近最多 3 个
已完成 Turn 的用户和最终助手可见文本，合计默认不超过 1500 字；不会把工具参数、模型草稿或
内部 Context Snapshot 当成对话。用户明确说“下次接着做”时，Agent 可以保存一个临时 Handoff，
用于之后理解“上次那个继续”；任务完成、用户取消或默认 14 天到期后不再召回。
用户要求清空全部个性化记忆时，系统会先冻结清空范围并要求再次确认；确认后只批量撤销
Profile、Goal 和 Constraint，不删除体重、饮食、运动或消息幂等记录。被撤销值立即停止召回，
默认在 30 天宽限期后从事实表物理清空，只保留状态、来源引用和不可逆哈希。

模型每轮最多预加载 30 条当前有效记忆，可调整：

```dotenv
MEMORY_PRELOAD_MAX_FACTS=30
MEMORY_HEALTH_REVIEW_DAYS=180
MEMORY_RECENT_TURN_COUNT=3
MEMORY_RECENT_DIALOGUE_MAX_CHARS=1500
MEMORY_HANDOFF_TTL_DAYS=14
AGENT_TRANSCRIPT_BODY_RETENTION_DAYS=30
MEMORY_REVOKED_VALUE_RETENTION_DAYS=30
MEMORY_MAINTENANCE_INTERVAL_SECONDS=21600
```

Working Memory 和 Handoff 是非权威承接上下文，当前消息始终优先；语义由核心模型判断，代码只
负责来源、用户隔离、幂等、容量和期限约束，不通过关键词规则决定“刚才那个”或确认回复的
含义。后台维护任务在启动后立即执行一次并周期运行，服务重启不会重置保留期。完整边界见
[用户记忆模块设计](./MEMORY_DESIGN.md)。

## 用户与客户资料

首次收到一个新的 `external_userid` 时，SlimGuard 会：

1. 创建一个内部 UUID 用户，写入 `users`；
2. 将 `(channel_id, external_userid)` 与内部用户关联，写入 `channel_identities`；
3. 调用微信客服客户基础信息接口，保存昵称、头像、性别和可用的 `unionid`；
4. 后续同一身份的消息继续更新同一个用户的 `last_seen_at`。

`external_userid` 不会写入日志或 CLI 输出。服务器上可以用下面的命令查看用户，命令只显示
哈希后的 `external_ref`：

```bash
docker compose exec app python -m slim_guard.tools.list_users
```

没有绑定符合要求的公众号或小程序微信开发者帐号时，`unionid` 为空属于正常情况，不影响
SlimGuard 使用 `external_userid` 区分客户。客户资料接口暂时失败也不会阻止 Agent 回复，
后续消息会再次尝试同步。

正常运行时可关注以下日志：

```text
wecom_session_claimed_by_agent
wecom_agent_reply_accepted
slim_guard_agent_reply_failed
wecom_reply_deferred_by_service_state
wecom_human_session_ended_after_timeout
wecom_customer_profiles_synced
slim_guard_reply_pending_internal_review
routine_message_accepted
routine_job_skipped
routine_job_attempt_failed
wecom_outbox_recovered
wecom_outbox_recovery_failed
```

## 测试与检查

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

测试会模拟企业微信服务器，不需要真实 Corp ID 或 Secret。

## Docker

```bash
docker compose up --build
```

Compose 会读取当前目录的 `.env`，并把 SQLite 数据保存在 Docker 命名卷 `slim_guard_data`。
默认只把服务绑定到宿主机的 `127.0.0.1:18083`，供同机 Nginx 反向代理，不直接暴露公网端口。

## 设计文档

- [Phase 1 实现设计](./PHASE1_PYTHON_CHANNEL_SPIKE.md)
- [完整技术设计](./TECHNICAL_DESIGN.md)
- [Agent Harness 设计与实施计划](./AGENT_HARNESS_IMPLEMENTATION_PLAN.md)
- [用户记忆模块设计](./MEMORY_DESIGN.md)
- [MVP 产品范围](./MVP_0_WECHAT_BRIDGE.md)
