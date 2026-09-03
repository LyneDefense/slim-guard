# Mem0 语义记忆集成

> 腾讯云上的 Mem0 现由 [单机生产部署说明](./SERVER_DEPLOYMENT.md) 中的统一生产 Compose 管理。
> 下文保留工作原理和验证边界；服务器不要再单独运行 Mem0 的开发 Compose。

SlimGuard 把 PostgreSQL 中的结构化记忆作为唯一可信来源。Mem0 OSS 是可选的语义索引：它帮助找出
与当前消息可能相关的候选，但不能覆盖数据库值，也不能绕过 Memory Tool 的证据、单位和版本校验。

## 运行链路

```text
用户原话 → Memory Ingestion 模型 → Memory Tool → PostgreSQL 权威事实
                                                   ↓ 同事务 Outbox
                                            后台同步 Mem0

当前消息 → Mem0 语义候选 + PostgreSQL 权威候选 → Recall 模型筛选 → Core Agent
```

Mem0 超时或不可达时，聊天不会失败。Recall 模型仍可直接筛选有界的 PostgreSQL 候选；Recall 模型也
不可用时，系统会保守带入有界权威事实并在 Trace 中标记为降级。

## 部署 Mem0 OSS

SlimGuard 的统一生产 Compose 运行 Mem0 API Server。它不部署 Dashboard，也不把 Mem0 和 pgvector
端口发布到宿主机；SlimGuard 只通过内部服务名 `http://mem0:8000` 访问它。生产环境始终启用
Mem0 API Key。

官方部署说明：<https://docs.mem0.ai/open-source/setup>

腾讯云服务器不再使用跨 Compose external network，也不要为 Mem0 增加公网 Nginx location。

## SlimGuard 配置

默认只启用模型召回，不启用 Mem0：

```dotenv
MEMORY_RECALL_ENABLED=true
MEMORY_RECALL_SEARCH_LIMIT=12
MEMORY_RECALL_MAX_SELECTED=8
MEMORY_SEMANTIC_ENABLED=false
```

Mem0 API 和 embedding 验证通过后再启用：

```dotenv
MEMORY_SEMANTIC_ENABLED=true
MEM0_BASE_URL=http://mem0:8000
MEM0_API_KEY=m0sk_replace_me
MEM0_NAMESPACE=slim_guard
MEM0_HTTP_TIMEOUT_SECONDS=10
MEMORY_INDEX_SYNC_INTERVAL_SECONDS=5
MEMORY_INDEX_SYNC_BATCH_SIZE=20
MEMORY_INDEX_SYNC_MAX_ATTEMPTS=10
```

启动时会自动为尚未同步的 active 记忆建立幂等 Outbox 任务；不需要手工重发旧用户资料。
发送给 Mem0 的用户标识会使用 namespace 加 SHA-256 派生，避免暴露 SlimGuard 内部用户 ID，也避免
多个应用共用同一 Mem0 时发生命名冲突。修改已上线的 namespace 会形成一套新索引，不应随意变更。

## SlimGuard 升级

```bash
./deploy.sh
```

迁移会新增 `memory_index_outbox`。关闭 Mem0 时只需设置
`MEMORY_SEMANTIC_ENABLED=false` 并重启，PostgreSQL 权威记忆和模型召回仍然工作。

## 验证

1. 在管理后台进入一个用户的“记忆”页面。
2. active 记忆应显示“已进入语义索引”；失败任务会显示错误类型和重试次数。
3. 打开该用户的一条输出链路，应看到“模型选择本轮相关记忆”和“筛选本轮相关记忆”。
4. 技术详情中的 Mem0 候选数量只用于排障；最终带给 Core Agent 的值必须来自 PostgreSQL。

测试阶段可以暂不启用 Mem0 API 认证，但生产环境不要使用 `AUTH_DISABLED=true`。
