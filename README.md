# SlimGuard

SlimGuard 是一个 Python 编写的企业微信“微信客服”减脂助手。

仓库现在也包含基于 Expo/React Native 的 iOS 与 Android 客户端。它复用同一套 Harness
Agent、健康记录和记忆数据库，提供手机号登录、对话与饮食图片、今日概览、趋势、目标与记忆、
本地提醒、离线可靠发送、微信身份绑定和账号删除。开发入口见
[`mobile-app/README.md`](mobile-app/README.md)，服务器与双端构建步骤见
[`MOBILE_APP_DEPLOYMENT.md`](MOBILE_APP_DEPLOYMENT.md)，交付范围见
[`MOBILE_MVP_REPORT.md`](MOBILE_MVP_REPORT.md)。

腾讯云服务器统一使用 [单机生产部署说明](./SERVER_DEPLOYMENT.md)：宿主机 Nginx、单一
`slim-guard-prod` Compose、一份 `deploy/.env.server` 和日常一键命令 `./deploy.sh`。仓库根目录
`compose.yaml` 只用于本地开发，不再作为服务器生产部署入口。

当前 Harness 版本支持普通微信用户用自然语言或图片记录体重、体脂、饮食和运动，调用智谱
GLM 完成理解与回复，并把业务事实按用户隔离、幂等地保存。Agent 每轮只读取紧凑的用户
资料、当前有效的个性化记忆、近期权威记录、最近有限对话和当天打卡状态，不依赖无限增长的
原始聊天历史。
用户明确表达身高、目标、运动习惯或偏好后，独立的 model-first 记忆摄取阶段会在回复前自动理解、
核对数据库并新增或更新长期记忆，不要求用户额外说“请记住”；这些记忆可以查询、更新或逐条撤销，
也不会从单次打卡或图片中自动猜测。

通道已经包含企业微信会话状态管理：新会话会从“未处理”自动认领为“智能助手
接待”，避免误入人工接待后 API 无法回复。若历史或误操作导致会话处于人工接待状态，
且客户消息在指定时间内没有人工回复，SlimGuard 会结束该人工会话并发送提示；客户再次
发信后会重新由智能助手接待。

## 本地启动

推荐 Python 3.13，代码和测试兼容 Python 3.11+。

```bash
cp .env.example .env
docker compose up -d postgres
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
图片检查、体重、体脂、饮食、运动、纠错和提醒日程工具。图片作为用户隔离的短期资产默认保留
7 天，可通过 `AGENT_IMAGE_RETENTION_SECONDS` 调整；后台默认每 6 小时物理清理过期图片。
多 Agent 使用独立开关，默认 `off`，不会改变当前生产回复；`shadow` 会运行无写权限的候选工作流并
在管理台展示对比，但候选不会发送：

Shadow 候选的所有正常沟通会经过版本化 `slimguard_default_v1` Style Profile。Style Agent 只能调整
表达，required 内容块、数字、记录状态、专业结论、风险与引用均由代码校验；模型或校验失败时使用
中性渲染器，安全与操作模板明确绕过普通风格层。

```dotenv
# harness：新版 Agent Harness；legacy：仅供回滚的旧版单次回复
AGENT_RUNTIME_MODE=harness
# 部署流水线可以写入 Git commit；未设置时为 development
AGENT_CODE_REVISION=development
MULTI_AGENT_MODE=off
MULTI_AGENT_CANARY_USER_IDS=
MULTI_AGENT_GRAPH_VERSION=typed-supervisor-v1
MULTI_AGENT_SHADOW_TIMEOUT_SECONDS=20
DEFAULT_STYLE_PROFILE=slimguard_default_v1
NUTRITION_AGENT_ENABLED=false
NUTRITION_RAG_ENABLED=false
NUTRITION_REQUIRE_RAG_CITATIONS=true
RESPONSE_REVIEWER_ENABLED=false
ASSET_MAINTENANCE_INTERVAL_SECONDS=21600
```

`MULTI_AGENT_MODE` 支持 `off → shadow → canary → on`。首次部署保持 `off`；完成 Shadow 验证前不要
直接进入 Canary 或全量。关闭该开关不影响现有 Harness、用户 Thread 或已经写入的健康记录。
Canary/on 要求启用 Style 和 Reviewer，失败沿用本轮原回复而不重复执行工具。名单使用内部 user_id，
不是显示名；配置变更需重启服务。量化门槛、只读验收命令和回退步骤见
[Multi-Agent 发布手册](MULTI_AGENT_ROLLOUT.md)。默认保持 `off`，本仓库不自动放量。

离线表达语料使用独立 SQLite 文件，不连接用户 Memory 或营养 RAG。通用准备工具已提供，但
`doctor_strict_v1` 必须等待真实语料授权、风格需求评审和人工隐私审核，当前没有发布或激活。
导入支持 UTF-8 JSON 消息数组（`sender/text/conversation_id`）或 `sender<TAB>text` 文本，
并非任意微信导出格式；必须用 JSON 显式映射所有 sender。模型只收到自动脱敏后的候选，
但自动脱敏无法保证识别全部个人信息，调用模型前应先完成源文件隐私检查，并补充 `--private-terms`。
真实微信 HTML 整理、医生风格草案、精确版本 A/B、人审发布和回滚流程见
[STYLE_ASSET_RUNBOOK.md](STYLE_ASSET_RUNBOOK.md)。医生资产当前仍待真实模型评估及人工审核，不默认启用。

离线模型单独配置 `STYLE_CORPUS_API_KEY`、`STYLE_CORPUS_MODEL` 和可选 `STYLE_CORPUS_BASE_URL`。

```bash
uv run python -m slim_guard.tools.manage_style_corpus --database ./offline-style.sqlite import --input ./export.json --format json --sender-mapping ./senders.json --private-terms ./private-terms.json
uv run python -m slim_guard.tools.manage_style_corpus --database ./offline-style.sqlite review
uv run python -m slim_guard.tools.manage_style_corpus --database ./offline-style.sqlite review --candidate-id CANDIDATE_ID --review ./human-review.json
uv run python -m slim_guard.tools.manage_style_corpus --database ./offline-style.sqlite eval --profile-id PROFILE_ID --version VERSION --display-name DISPLAY_NAME --cases ./style-eval-cases.json --actor REVIEWER
uv run python -m slim_guard.tools.manage_style_corpus --database ./offline-style.sqlite export --profile-id PROFILE_ID --version VERSION --display-name DISPLAY_NAME
```

人工审核 JSON 包含 `actor`、`decision`（approve/reject）、`note`；批准时必须明确设置
`privacy_confirmed=true` 和 `expression_only_confirmed=true`，可覆写 `example_text`、`tone_rules`。
Eval 输入必须是实际 `response_plan` 与 `styled_response` 配对，并覆盖所有已批准示例的沟通行为。
导出只产生 `draft`，要求当前精确版本的最新评估同时通过表达、忠实度和隐私检查；修改或撤销审核后
旧评估失效。评审记录 append-only，离线数据库和导出文件应限权存放，不提交仓库或暴露给普通管理员。

Nutrition RAG 使用与用户 Memory 完全分离的数据库命名空间。资料必须先离线导入为 `draft`，再经过
“审核”和“发布”两个独立操作，才会被新 Turn 检索；资料退休后立即退出新检索，历史 Artifact 中已经
冻结的 Citation 仍可审计。线上 Nutrition 工具全部只读，也不会自动把网页写入知识库。

导入清单可以是 JSON 数组，也可以是包含 `documents` 数组的对象。每项至少包含 `source_key`、
`version`、`title`、`publisher` 和 `content`；也可用 `content_path` 代替 `content`，引用相对清单文件
的 UTF-8 文本或 Markdown 文件。运维命令如下：

```bash
uv run python -m slim_guard.tools.manage_nutrition_knowledge import ./knowledge-manifest.json --actor importer@example
uv run python -m slim_guard.tools.manage_nutrition_knowledge approve SOURCE_ID --reviewer reviewer@example
uv run python -m slim_guard.tools.manage_nutrition_knowledge publish SOURCE_ID --reviewer publisher@example
uv run python -m slim_guard.tools.manage_nutrition_knowledge search "成年人膳食多样性" --limit 5
uv run python -m slim_guard.tools.manage_nutrition_knowledge retire SOURCE_ID --reviewer reviewer@example --reason "资料已被新版本替代"
uv run python -m slim_guard.tools.manage_nutrition_knowledge show SOURCE_ID
```

完成资料审核后，才在 Shadow 模式打开 `NUTRITION_AGENT_ENABLED=true` 和
`NUTRITION_RAG_ENABLED=true`。RAG 打开时不能关闭 `NUTRITION_REQUIRE_RAG_CITATIONS`；每条知识性
Claim 都必须保留当前 Nutrition invocation 的 Citation，并完整通过 Style 渲染。

`RESPONSE_REVIEWER_ENABLED=true` 开启候选回复审查，要求同时开启 Style 渲染。
审查将风格问题返回 Style、无依据专业结论返回 Nutrition、缺少用户信息返回 Orchestrator 询问。
每个目标最多修复一次，全 Turn 最多两次返回；每次专业修复都重新渲染并审查。
拒绝、预算耗尽或审查失败会生成保守候选；Shadow 阶段仍交付原 Harness 回复。
管理台展示审查结果、返回边、版本关系和过去 7 天的拒绝/修复/降级率。

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

生产和 Compose 部署使用 PostgreSQL 16，SQLite 只保留为快速单元测试后端。主库保存同步
cursor、消息 ID、消息类型、用户身份与客户资料、会话状态、权威打卡记录、Agent
运行轨迹、个性化记忆、跨轮 Handoff、日程 Job 和出站回复。Harness 运行轨迹中的用户/助手正文、
模型可见上下文、Tool 参数和结果默认保留 30 天，之后由后台维护任务原位替换为不可逆 SHA-256
哈希和必要审计元数据；领域记录与消息幂等账本不依赖这些正文。下载图片会加密链路传输并作为用户隔离的短期数据库资产保存，
默认七天后不可读取并可清理。新增表由版本化迁移幂等创建。PostgreSQL 首次部署、空库建表、备份
和密码变更见 [PostgreSQL 部署说明](./POSTGRESQL_DEPLOYMENT.md)。Mem0 的 pgvector 数据库只是可重建的
语义索引，不替代这个权威主库。

## 用户记忆

当前 Memory 保存用户明确表达的长期资料：身高、运动习惯、偏好称呼、回复风格、饮食与运动偏好、
目标体重、目标体脂、行为目标，以及用户自述的饮食、运动和健康约束。每条记忆分别绑定本次操作来源和
用户原话证据，旧值被替换后不会继续进入上下文。目标体重和目标体脂只是用户自述目标，不会写成一次测量，
也不表示系统认可其医学安全性；健康约束始终标记为用户自述，180 天后提示复核而不会变成诊断。
自然语言中明确说的是体重但省略单位时默认使用 kg，明确说的是身高但省略单位时默认使用 cm；
模型负责理解数字属于当前测量、目标还是个人资料，工具只负责默认单位、范围、来源和幂等校验。
每个用户消息进入 Harness 后，会先经过独立的记忆摄取模型；摄取模型只提出有用户原话证据的结构化
写入，Repository 再与数据库 active 记忆比较：不存在则新增、相同则幂等复用、当前明确新值则版本化
替换。随后回复 Agent 重新读取数据库，因此数据库结果而不是 3 轮聊天窗口是长期事实权威。摄取阶段
还会把数据库本轮写入回执交给回复 Agent，明确区分“新保存”“从旧值更新”和“原值未变”，避免把
刚更新后的值误说成旧记录。最近 20 条用户原话只用于为升级前尚未结构化的近期事实做渐进回填。
用户可以直接问“你记得我什么”，也可以要求忘记某一条明确记忆。每轮还会加载最近最多 3 个
已完成 Turn 的用户和最终助手可见文本，合计默认不超过 1500 字；其中历史用户原话带有仅限本轮使用的
证据引用，模型可在用户说“保存上次那个”时直接调用记忆工具，后端验证同用户、原文、数值、可见范围和
新旧冲突，不要求用户复述固定句式。助手文本没有证据引用，工具参数、模型草稿或内部 Context Snapshot
也不会被当成事实证据。最近最多 3 张仍在保留期内的用户图片会以用户隔离的真实
`asset_id` 和非权威视觉观察进入 Working Memory，供核心模型理解“刚才那张”；代码不按关键词
匹配指代，也不允许模型编造图片 ID。用户明确说“下次接着做”时，Agent 可以保存一个临时 Handoff，
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
MEMORY_RECENT_IMAGE_COUNT=3
MEMORY_HANDOFF_TTL_DAYS=14
MEMORY_INGESTION_ENABLED=true
MEMORY_INGESTION_HISTORY_COUNT=20
MEMORY_INGESTION_HISTORY_MAX_CHARS=6000
MEMORY_RECALL_ENABLED=true
MEMORY_RECALL_SEARCH_LIMIT=12
MEMORY_RECALL_MAX_SELECTED=8
MEMORY_SEMANTIC_ENABLED=false
MEM0_BASE_URL=http://mem0:8000
MEM0_API_KEY=
MEM0_NAMESPACE=slim_guard
MEM0_HTTP_TIMEOUT_SECONDS=10
MEMORY_INDEX_SYNC_INTERVAL_SECONDS=5
MEMORY_INDEX_SYNC_BATCH_SIZE=20
MEMORY_INDEX_SYNC_MAX_ATTEMPTS=10
AGENT_TRANSCRIPT_BODY_RETENTION_DAYS=30
MEMORY_REVOKED_VALUE_RETENTION_DAYS=30
MEMORY_MAINTENANCE_INTERVAL_SECONDS=21600
```

Working Memory 和 Handoff 是非权威承接上下文，当前消息始终优先；语义由核心模型判断，代码只
负责来源、用户隔离、幂等、容量和期限约束，不通过关键词规则决定“刚才那个”或确认回复的
含义。视觉模型会结构化标记 clear/uncertain 和是否需要用户澄清；存在未解决歧义时，核心模型
必须基于当前用户原话确认后才能写入饮食记录。后台维护任务在启动后立即执行一次并周期运行，
服务重启不会重置保留期。完整边界见
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
slim_guard_tool_call_failed
slim_guard_tool_failure_circuit_opened
```

## 测试与检查

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

测试会模拟企业微信服务器，不需要真实 Corp ID 或 Secret。

## Docker（仅本地开发）

```bash
docker compose up --build
```

根目录 Compose 只用于本地开发。腾讯云生产环境不要执行这组命令，应使用
[`SERVER_DEPLOYMENT.md`](./SERVER_DEPLOYMENT.md) 中的 `./deploy.sh`。

本地 Compose 会读取当前目录的 `.env`，并把 PostgreSQL 数据保存在 Docker 命名卷
`slim_guard_postgres_data`。数据库只绑定宿主机 `127.0.0.1:15432`，应用只绑定
`127.0.0.1:18083`，供本机维护和 Nginx 反向代理，不直接暴露公网端口。

## 用户级 Trace 管理后台

项目包含一个独立的 React + TypeScript 前端。默认入口 `/admin/users` 先展示用户列表，进入
用户后可以查看该用户独立的输出 Trace、模型和工具时间线、最终投递、记忆、健康记录及提醒。
无法归属用户的运行故障仍保留在结构化服务日志中，不会错误关联给某个用户。

后台认证统一由 FastAPI 负责：登录页提交 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 后，后端签发
短时、签名的 `HttpOnly` Session Cookie；Nginx 不保存密码，也不需要 htpasswd。React 容器
默认绑定 `127.0.0.1:18084`，API 继续使用 `127.0.0.1:18083`。完整的首次部署、PostgreSQL 备份、
迁移、Nginx 配置和验证步骤见 [管理后台部署说明](./ADMIN_WEB_DEPLOYMENT.md)。宿主机 Nginx
的版本化 location 片段见
[`deploy/nginx/slim-guard.locations.conf`](./deploy/nginx/slim-guard.locations.conf)。

数据库升级由版本化迁移账本管理。生产部署脚本会在启动应用前自动、幂等地执行迁移；下面的命令
只用于本地开发环境：

```bash
docker compose run --rm app python -m slim_guard.db.migrate
```

## 设计文档

- [PostgreSQL 部署说明](./POSTGRESQL_DEPLOYMENT.md)
- [Phase 1 实现设计](./PHASE1_PYTHON_CHANNEL_SPIKE.md)
- [完整技术设计](./TECHNICAL_DESIGN.md)
- [Agent Harness 设计与实施计划](./AGENT_HARNESS_IMPLEMENTATION_PLAN.md)
- [用户记忆模块设计](./MEMORY_DESIGN.md)
- [MVP 产品范围](./MVP_0_WECHAT_BRIDGE.md)
