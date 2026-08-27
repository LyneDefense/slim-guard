# SlimGuard

SlimGuard 是一个 Python 编写的企业微信“微信客服”减脂助手。

当前版本支持单轮 AI 回复：普通微信用户发送文字或图片后，SlimGuard
会调用智谱 GLM API 进行减脂相关的识别和点评。图片可以是体重秤、食物或
运动截图。这一版不传历史消息，不使用 memory、工具调用或复杂 Agent 框架。

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

Agent Runtime 当前保持兼容模式。`harness` 和 `shadow` 会在对应运行时实现后逐步开放，
现阶段设置其他值会拒绝启动，避免配置看似生效但实际仍走旧路径：

```dotenv
AGENT_RUNTIME_MODE=legacy
# 部署流水线可以写入 Git commit；未设置时为 development
AGENT_CODE_REVISION=development
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

# 微信昵称、头像等客户资料的刷新间隔，默认24小时
WECOM_CUSTOMER_PROFILE_REFRESH_SECONDS=86400

# 允许下载的微信图片大小上限，默认10 MiB
WECOM_MEDIA_MAX_BYTES=10485760

# 自动结束人工会话后，通过事件响应接口发送给客户的提示
WECOM_HUMAN_TIMEOUT_MESSAGE=人工服务暂时没有响应，已结束人工接待。请再发送一次刚才的内容，SlimGuard 减脂助手会继续为你服务。
```

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

只保存同步 cursor、消息 ID、消息类型、用户身份与客户资料、会话状态和出站回复，不保存
入站文本正文或下载的图片。图片仅在当次识别的内存中使用。新增表会在启动时自动创建，
已有 SQLite 文件无需删除。删除 SQLite 文件会
丢失用户、去重和会话状态，真机环境不要随意删除。

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
- [MVP 产品范围](./MVP_0_WECHAT_BRIDGE.md)
