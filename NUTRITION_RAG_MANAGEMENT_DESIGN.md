# SlimGuard 营养知识 RAG 与可视化管理设计

状态：待评审  
版本：v1.0  
日期：2026-09-10  
适用范围：营养证据检索 Agent、饮食建议 Agent、Reviewer 与管理后台

## 1. 为什么需要重新设计

当前代码已经具备资料导入、哈希、人工审核、发布状态、引用校验和 Trace 展示，但检索本身仍是过渡实现：

- 每次搜索先把最多 5,000 个已发布 Chunk 读到应用内，再逐条计算字符级词法分数；
- `embedding_json` 只是预留字段，线上没有真实 Embedding 生成、向量索引和向量召回；
- 所谓 `rerank_score` 实际只是词法分数与可选向量分数的线性相加，不是真正的重排模型；
- 中文单字命中也可能形成候选，资料增加后容易出现貌似相关、实际无关的结果；
- Agent 当前构造的查询信息偏少，可能只有菜名，没有充分利用烹饪方式、目标和适用条件；
- 资料只能通过 CLI 管理，管理后台只能在 Trace 里看检索结果，不能导入、审核、发布、调试和回滚；
- “单份资料发布”直接决定是否可检索，缺少可复现的整库版本，无法安全比较新旧资料集合。

因此，当前能力应定义为“带治理和引用的词法检索骨架”，不能作为长期生产 RAG 的完成状态。本设计将其升级为常规、可扩展、可评测、可回滚的生产级 Hybrid RAG。

## 2. 设计目标

### 2.1 必须达到

1. 从 3 篇资料扩展到数千篇资料时，不扫描全库，不改变管理流程。
2. 使用真实 Embedding、pgvector 向量索引、中文词法检索、RRF 融合和模型重排。
3. 只有人工审核通过且被纳入“当前启用资料版本”的内容才能供 Agent 使用。
4. 管理员可在网页完成上传、解析检查、Chunk 检查、审核、建立版本、评测、启用和回滚。
5. 每条被采用的证据可追溯到资料版本、章节、Chunk、内容哈希、检索版本和本次 Invocation。
6. 检索不确定、模型不可用或没有合格证据时，系统明确返回“证据不足”，不能用模型常识补齐。
7. 资料、Embedding 模型、切片器、检索参数升级时，新旧版本可以并存和 A/B 比较。

### 2.2 本期不做

- 不根据照片估算克重、热量或宏量营养素；
- 不自动抓取互联网并直接发布；
- 不把 RAG 当作菜品实体库，规范菜名、别名、典型特征和硬规则仍放结构化数据库；
- 不允许 Agent 修改资料、审核结果或启用版本；
- 不把用户 Memory/Mem0 与营养知识库混在一起。

## 3. 核心决策

| 主题 | 决策 | 原因 |
| --- | --- | --- |
| 向量存储 | SlimGuard 主 PostgreSQL 使用 `pgvector` | 可与资料状态、Release 和引用做事务一致的过滤与关联 |
| 与 Mem0 的关系 | 严格分库，绝不复用 `mem0-db` | 用户记忆与公共营养知识的权限、生命周期和故障域不同 |
| Embedding | 首个 Profile 使用智谱 `embedding-3`，1024 维 | 已有同一供应商配置；中文支持好；精度与存储成本平衡 |
| 重排 | 首个 Profile 使用智谱 `/paas/v4/rerank` | 是真正的查询—候选相关性重排，不再伪造 `rerank_score` |
| 检索 | Dense + 中文 Lexical + Phrase，RRF 融合后 Rerank | 同时覆盖语义改写、精确术语、菜名和指南条款 |
| 中文词法 | 版本化分词词典 + PostgreSQL FTS；`pg_trgm` 做短语/模糊补充 | 不依赖全库应用内扫描，并能解释关键词为何命中 |
| 资料管理 | 自建治理层，不使用供应商托管知识库作为权威存储 | 保留审核、版本、Hash、Trace、回滚和前端可视化控制 |
| 线上启用单位 | 不再是单份 Source，而是不可变 Corpus Release | 可复现、可整体评测、可一键回滚 |
| 后台任务 | PostgreSQL 租约队列 + 独立 Worker | 当前单机部署足够稳健；以后可横向扩 Worker，无需先引入 Redis/Celery |
| 原始文件 | 私有对象存储接口；首版为 Docker 私有 Volume | 避免把大 PDF 塞入数据库，并为以后迁移 S3/OSS 保留接口 |

### 3.1 为什么不直接使用智谱托管知识库

智谱托管知识库也提供向量、关键词、混合检索和重排，但 SlimGuard 已经有自己的资料审核、Agent 引用校验、Trace 和发布流程。若把权威知识状态放到托管知识库，会出现两套状态机，且难以保证：

- 某个 Source Review 与供应商侧具体切片严格对应；
- 一次 Agent 调用可复现当时的整库版本；
- 回滚时供应商索引与本地状态同时原子切换；
- 管理后台能够解释 Dense、Lexical、RRF、Rerank 每一阶段。

因此只调用 Embedding 和 Rerank 模型 API，索引、治理和引用仍由 SlimGuard 掌握。

### 3.2 为什么现在不引入 Elasticsearch 或独立向量数据库

当前部署是一台服务器，资料规模远未达到必须拆分搜索集群的程度。PostgreSQL + pgvector + GIN/GiST 已能支持数十万级 Chunk 的常规场景，也显著减少备份、权限和一致性成本。检索层会通过 Repository/Gateway 抽象；只有真实容量和延迟数据证明需要时，再替换为独立搜索服务。

### 3.3 新增资料不是“重新训练模型”

以后增加第 4 篇、第 100 篇资料时，不需要训练或微调大模型。系统会对新资料执行解析、切片和 Embedding，
然后建立一个包含新资料的候选 Corpus Release。候选版本通过检索评测和人工批准后再启用。只有更换
Embedding/Rerank 模型或 Chunker Profile 时，才需要为受影响资料重新生成索引；旧 Profile 和旧 Release
仍保留到新版本验收完成。

## 4. 总体架构

```text
管理员浏览器
  │
  ├─ 上传/URL/粘贴文本
  ▼
Admin API ──→ Raw Object Store（私有 Volume，内容哈希寻址）
  │
  ├─ 创建 Ingestion Job
  ▼
Nutrition Knowledge Worker
  解析 → 规范化 → 章节识别 → Parent/Child 切片 → 中文词法索引 → Embedding
  │
  ▼
PostgreSQL 16 + pgvector
  Source Version / Chunk / Embedding / Review / Release / Eval / Receipt
  │
  ├─ 管理员检查、审核、创建 Release、运行离线评测、启用
  ▼
Active Corpus Release
  │
  ▼
Retrieval Agent
  Query Plan → Dense / Lexical / Phrase → RRF → Rerank → Evidence Selection
  │
  ▼
DishEvidenceBundle → Diet Guidance Agent → Style Agent → Reviewer → 用户
```

运行时只有 Retrieval Service 能读取向量和正文。其他 Agent 只能接收校验后的 `DishEvidenceBundle` 或 Citation，不能自行访问数据库或拼接 SQL。

## 5. 资料生命周期

### 5.1 单份资料版本

```text
uploaded
  → parsing
  → indexing
  → review_ready
  ├─ rejected
  └─ approved
       → retired
```

- 原始文件和规范化正文一经生成即不可修改。
- 修正文档、元数据、切片设置或正文时，创建新的 Source Version，而不是覆盖旧版本。
- `rejected` 必须填写原因；`approved` 可以填写备注，但必须完成内容、适用范围和使用权确认项。
- `approved` 只表示“允许进入 Release”，并不自动进入 Agent 当前使用的知识库。
- `retired` 不删除历史数据，也不影响引用过往 Trace。

### 5.2 整库 Release

```text
draft
  → indexing_check
  → evaluating
  → review_ready
  ├─ rejected
  └─ approved
       → active
       → retired
```

一个 Release 固定以下内容：

- Source Version 集合；
- Chunker/Profile 版本；
- Embedding Profile；
- Lexical Analyzer Profile；
- Retrieval Profile；
- 离线评测数据集版本和结果 Hash。

同一时刻只有一个 Nutrition Corpus Release 为 `active`。启用新 Release 是一次数据库事务；回滚等价于重新激活一个历史 approved Release，不重建文件和向量。

### 5.3 “发布”在页面上的含义

为避免混淆，页面不用一个“发布”按钮包办两层动作：

- Source 页面：按钮叫“批准入库”；
- Release 页面：按钮叫“批准版本”；
- Runtime 页面：按钮叫“全量启用”；
- 历史 Release：按钮叫“回滚到此版本”。

## 6. 数据模型

以下是目标模型。现有 `nutrition_knowledge_*` 表保留历史数据，通过迁移扩展或映射，不做破坏性重建。

### 6.1 原始文件与导入任务

```text
nutrition_knowledge_assets
  id
  storage_key
  original_filename
  media_type
  byte_size
  sha256
  source_method: upload | url | pasted_text | legacy_manifest
  source_url
  created_by
  created_at

nutrition_knowledge_jobs
  id
  job_type: ingest | reparse | embed | build_release | evaluate
  subject_type / subject_id
  status: queued | running | retry_wait | succeeded | failed | cancelled
  stage
  completed_items / total_items
  attempt_count / max_attempts
  lease_owner / lease_expires_at
  idempotency_key
  error_code / safe_error_message
  created_by / created_at / started_at / finished_at
```

任务由 Worker 使用 `FOR UPDATE SKIP LOCKED` 领取，并带租约、幂等键、指数退避和最大重试次数。进度必须来自真实完成数量，前端不能显示伪进度。

### 6.2 Source Version 与 Chunk

```text
nutrition_knowledge_sources
  id
  source_key
  version
  supersedes_source_id
  asset_id
  title / publisher / publication_date / source_url / language
  normalized_content_sha256
  parser_profile_id / chunker_profile_id
  status
  review_readiness
  created_by / created_at / retired_at

nutrition_knowledge_sections
  id / source_id
  parent_section_id
  heading_path[]
  page_from / page_to
  ordinal
  content_text / content_sha256

nutrition_knowledge_chunks
  id / source_id / section_id
  parent_chunk_id
  chunk_kind: retrieval_child | context_parent | table | qa
  ordinal / page_from / page_to
  content_text / content_sha256
  lexical_text
  lexical_terms
  lexical_vector tsvector
  token_count / char_count
  metadata_json
  created_at
```

`Chunk ID` 由 Source 内容 Hash、Chunker Profile 和位置生成；同样的输入与 Profile 必须得到同样的 Chunk ID。

### 6.3 模型与索引 Profile

```text
nutrition_embedding_profiles
  id / profile_key
  provider / model / dimensions / distance
  request_options_json
  status: draft | ready | deprecated
  created_at

nutrition_chunk_embeddings
  chunk_id / embedding_profile_id
  embedding vector
  content_sha256
  status: pending | ready | failed
  provider_request_id
  prompt_tokens / latency_ms
  embedded_at
  PRIMARY KEY(chunk_id, embedding_profile_id)

nutrition_lexical_profiles
  id / profile_key
  analyzer / analyzer_version
  dictionary_sha256
  status

nutrition_retrieval_profiles
  id / profile_key
  embedding_profile_id / lexical_profile_id
  dense_top_k / lexical_top_k / phrase_top_k
  rrf_k / rerank_top_n / final_top_k
  min_rerank_score
  max_context_chars
  query_plan_version
  status
```

Embedding 列使用无固定维度的 `vector` 类型，以允许不同模型 Profile 并存；每个可启用 Profile 建立带维度 Cast 和 `embedding_profile_id` 条件的 HNSW Partial Index。首个索引按 `vector(1024)` + cosine 创建。新增不同维度 Profile 时，必须先后台并发建好对应索引，才允许标为 `ready`。

### 6.4 Review、Release 与运行时

```text
nutrition_knowledge_review_events
  id
  subject_type: source | release | evaluation
  subject_id
  review_type: content | applicability | rights | release_acceptance
  decision: approve | reject | revoke
  attestations_json
  reason
  actor
  subject_sha256
  created_at

nutrition_corpus_releases
  id / version
  status
  embedding_profile_id / lexical_profile_id / retrieval_profile_id
  manifest_sha256 / evaluation_run_id
  created_by / created_at / approved_at / activated_at / retired_at

nutrition_corpus_release_sources
  release_id / source_id
  source_content_sha256
  PRIMARY KEY(release_id, source_id)

nutrition_corpus_runtime
  singleton_key
  active_release_id
  runtime_revision
  updated_by / updated_at

nutrition_corpus_activation_events
  id
  previous_release_id / activated_release_id
  action: activate | rollback
  actor / reason / runtime_revision / created_at
```

Review 和 Activation Event 都是 append-only。管理员身份只从服务端 Session 取得，前端请求不能自报 reviewer。

### 6.5 检索与评测记录

```text
nutrition_retrieval_runs
  id
  invocation_id nullable
  release_id / retrieval_profile_id
  query_plan_json
  query_hash
  safe_query_summary
  status: succeeded | degraded | insufficient | failed
  provider_usage_json
  total_latency_ms
  created_at

nutrition_retrieval_candidates
  retrieval_run_id / chunk_id
  query_variant_id
  dense_rank / dense_score
  lexical_rank / lexical_score
  phrase_rank / phrase_score
  rrf_rank / rrf_score
  rerank_rank / rerank_score
  selection_status / rejection_reason

nutrition_evaluation_datasets
  id / version / manifest_sha256 / status

nutrition_evaluation_cases
  dataset_id / case_id
  query_plan_input_json
  expected_source_keys[]
  expected_chunk_concepts[]
  forbidden_source_keys[]
  expected_outcome: evidence | insufficient

nutrition_evaluation_runs
  id / release_id / retrieval_profile_id / dataset_id
  status / metrics_json / result_sha256
  created_by / created_at / completed_at
```

线上用户查询默认只保存 Hash、结构化标签和脱敏摘要。管理台“检索实验室”可以保存完整测试 Query，但必须明确标记为测试数据，且禁止粘贴真实用户身份信息。

## 7. 数据库与部署设计

### 7.1 主数据库升级

当前主库镜像为 `postgres:16-alpine`，目标改为经过测试并固定 Digest 的 `pgvector/pgvector` PostgreSQL 16 镜像，保持 PostgreSQL Major Version 不变。迁移创建：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

不能因为现有 `mem0-db` 已使用 pgvector 就把营养向量写入 Mem0。营养知识表继续位于 SlimGuard 主库。

### 7.2 上线迁移顺序

1. 对主库做逻辑备份并校验可恢复；
2. 在临时数据库用目标镜像完整恢复一次；
3. 停止应用写入；
4. 保持 PostgreSQL 16 数据卷，切换主库镜像；
5. 创建扩展与新表，但暂不改变线上检索；
6. 启动 Worker，迁移和重新生成 Chunk/Embedding；
7. 运行离线评测与 Shadow；
8. 激活首个 Corpus Release；
9. 保留旧词法实现一个 Release 周期作为紧急只读回退，之后删除全库扫描路径。

一旦表中使用 `vector` 类型，直接退回普通 PostgreSQL 镜像不能视为有效回滚。数据库级回滚依赖迁移前备份；业务级回滚依赖旧 Corpus Release 和 RAG Feature Flag。

### 7.3 备份范围

日常备份必须同时覆盖：

- 主 PostgreSQL；
- `nutrition_source_data` 私有 Volume 中的原始文件；
- 当前生效配置，但不把 API Key 写入备份日志。

恢复演练必须验证 Source Hash、Chunk Hash、Embedding 行数、Active Release 和随机 Citation 回库。

## 8. 导入与切片流水线

### 8.1 支持入口

首个可用版本支持：

- 上传 PDF、Markdown、TXT、HTML；
- 输入公开网页 URL；
- 粘贴经过人工整理的文本；
- 兼容现有 Manifest/CLI，CLI 改为调用同一 Application Service。

后续增加 DOCX/XLSX 时不改变后续审核与发布流程。

URL 导入必须防 SSRF：只允许 `http/https`，解析和重定向后的地址都不能落入内网、Loopback 或云元数据地址；限制下载大小、跳转次数、Content-Type 和总超时。

### 8.2 解析

```text
原始文件
  → MIME 与大小校验
  → SHA-256 去重
  → 文本/页面/标题/列表/表格提取
  → 页眉页脚与重复导航清理
  → Unicode 与空白规范化
  → 解析质量检查
  → Sections
```

扫描 PDF 若文本密度低于阈值，状态进入 `needs_ocr`，不能生成空洞 Chunk。OCR 通过 Provider 接口实现，首个实现可以配置本地或云端方案，但无 OCR 能力时必须在页面明确要求管理员补充可检索文本，不能静默“解析成功”。

### 8.3 Parent/Child 切片

- 优先按标题、段落、列表、表格和页码切分，不先按固定字符硬切；
- `retrieval_child` 默认目标 300–700 个中文字符，用于召回与 Citation；
- `context_parent` 默认目标 800–1,800 个中文字符，用于给 Agent 补足上下文；
- 相邻 Child 只在语义边界需要时保留少量重叠；
- 表格整块保留，并在超长时按表头重复方式拆分；
- 标题路径、页码、适用人群和资料日期进入 Chunk Metadata；
- 所有阈值属于 Chunker Profile，不写死为不可追溯的常量。

Embedding 输入由 `资料标题 + 标题路径 + Child 正文` 组成，既保留局部含义，也减少脱离章节后的歧义。智谱 `embedding-3` 单次支持批量输入；Worker 按上限以内的安全批次生成并逐项核对返回索引。

### 8.4 中文词法索引

`ChineseLexicalAnalyzer` 负责：

- Unicode 规范化、全半角和常见标点处理；
- 使用版本化中文分词器；
- 加载受代码版本管理的营养领域词典，例如“含糖饮料”“全谷物”“低血糖”“食物过敏”；
- 保留菜名、指南术语、数值和单位；
- 生成空格分隔的 `lexical_terms`，再构建 `to_tsvector('simple', ...)`；
- 另保留规范化原文供 `pg_trgm` 的短语相似度与错字补充召回。

单个中文字符命中不能独立进入最终候选；它只能作为其他召回信号的微弱补充。

## 9. 运行时 Hybrid Retrieval

### 9.1 Query Plan

Retrieval Agent 输出严格的 `NutritionRetrievalQueryPlan`，Coordinator 校验后执行：

```text
NutritionRetrievalQueryPlan
  schema_version
  dish_terms[]
    canonical_name
    confirmed_aliases[]
    confirmed_preparation_terms[]
  goal_terms[]
  applicability_tags[]
  confirmed_constraints[]
  query_variants[]
    id
    text
    purpose: dish | meal_balance | constraint
  excluded_assumptions[]
```

Query Variant 最多 3 条，示例：

```text
番茄炒蛋 少油 烹调 调整 减脂 成人
一餐 蔬菜 蛋白质 主食 搭配 成人 体重管理
```

只允许使用本轮已确认菜名、可见/确认烹饪信息、用户明确目标和有效限制。不得把“可能有糖”改写成“高糖”，也不得带用户姓名、OpenID 或原始聊天历史调用 Embedding/Rerank。

### 9.2 四路候选与融合

对每个 Query Variant 并行执行：

1. **Dense**：Query Embedding 与 Active Release Chunk 的 cosine 检索，默认取前 40；
2. **Lexical**：PostgreSQL FTS 术语检索，默认取前 40；
3. **Phrase**：标题、章节和正文的 `pg_trgm`/精确短语检索，默认取前 20；
4. **Structured boost**：Source/Chunk 的 applicability、菜品标签和资料类别做过滤或有限加权。

先过滤 Active Release、Source 状态、Embedding Profile、语言和适用范围，再召回。使用 HNSW 时开启并评测 iterative scan，避免 Metadata Filter 导致返回数量不足。

Dense、Lexical 和 Phrase 的原始分数不可直接相加，因为量纲不同。先按各自排名使用 Reciprocal Rank Fusion：

```text
RRF(chunk) = Σ 1 / (rrf_k + rank_in_list)
```

RRF 只负责产生稳定候选集，不被展示成“相关性概率”。

### 9.3 真正的 Rerank

- 对融合后的前 20–30 个 Child Chunk 调用 Rerank Gateway；
- 请求包含脱敏 Query、资料标题、标题路径和 Chunk 正文；
- 单条候选严格小于供应商长度上限；
- 保存 provider request id、延迟、Token 用量、排名和 relevance score；
- `min_rerank_score` 不照搬示例值，由 SlimGuard 中文营养黄金集校准；
- 同一 Source 的高度重叠 Chunk 去重，避免前几名都来自同一段；
- 最终默认选择不超过 4 个 Child Citation，并按上下文预算补充对应 Parent 内容。

### 9.4 证据采用规则

候选只有同时满足以下条件才能成为 `KnowledgeCitation`：

- 属于检索开始时读取到的 Active Release Revision；
- Source 已批准，且未被撤销或退休；
- Chunk 内容 Hash、Embedding 对应内容 Hash 均一致；
- 适用人群与当前请求不冲突；
- 通过 Rerank 阈值；
- 不与排名更高的候选重复；
- 引用绑定本次 Invocation ID。

候选不等于证据。未采用候选只能在 Trace/检索实验室展示，不能进入 Nutrition Agent Prompt，也不能出现在面向用户的 Citation 中。

### 9.5 故障与降级

| 故障 | 行为 |
| --- | --- |
| Query Embedding 失败 | RAG 标记 `degraded`；不自动采用词法候选，结构化菜品规则仍可工作 |
| Rerank 失败 | 保留候选供 Trace 排障，但 `adopted_citations=[]` |
| Active Release 不存在 | 返回 `corpus_unavailable`，不得检索所有 published Source 兜底 |
| 部分 Chunk 缺 Embedding | Release 不允许激活；已激活 Release 出现损坏时 fail closed 并告警 |
| 无候选达到阈值 | 返回 `insufficient`，Guidance Agent 只能提问或说明资料不足 |
| Provider 超时 | 受统一 Deadline 约束，不无限等待；记录安全错误码 |

这里选择安全优先：不能把“向量或重排挂了”静默变成低质量但看起来正常的专业建议。

## 10. 与现有 Agent 的编排

```text
Dish Recognition Agent
  → 用户确认不确定菜名
  → Retrieval Agent 生成 Query Plan
  → Coordinator 执行 Hybrid Retrieval
  → DishEvidenceBundle
      ├─ 结构化 dish_entities / traits / diet_rules
      └─ Active Release Knowledge Citations
  → Diet Guidance Agent
  → Style Agent
  → Reviewer
```

需要修改现有契约：

- `DishEvidenceBundle` 增加 `corpus_release_id`、`retrieval_profile_id`、`retrieval_run_id`；
- `KnowledgeCitation` 增加 `section_path`、`page_from/page_to`、`source_version`、`release_id`、`chunk_sha256`；
- Agent 的 Claim 只能引用 `adopted_citations`；
- Reviewer 除校验 Citation 存在，还要校验 Release、Hash、适用范围和 Claim 语义是否被证据支持；
- Style Agent 不能新增、删除或弱化关键限制，也不能自行补充营养知识。

结构化菜品规则优先级高于 RAG 解释性文字；两者冲突时不让模型自行裁决，而是返回数据冲突，进入人工修复队列。

## 11. 管理后台信息架构

侧栏新增一级入口“营养知识库”，进入后使用五个 Tab：

1. **资料库**：Source 列表、状态、审核和详情；
2. **导入任务**：上传、URL 导入、解析/Embedding 进度、失败重试；
3. **检索实验室**：逐阶段观察 Dense/Lexical/RRF/Rerank；
4. **版本发布**：创建、评测、批准、启用和回滚 Corpus Release；
5. **评测集**：管理黄金 Query、预期证据和历史指标。

### 11.1 总览

```text
┌ 当前启用 Release: nutrition-rag-v3  [查看] [回滚]
│ Source 28 · Child Chunk 1,426 · Embedding 100% · 最近评测 通过
└──────────────────────────────────────────────────────────
  待解析 2   待审核 4   Embedding 失败 1   待批准 Release 1

  [上传资料] [从 URL 导入] [新建 Release] [打开检索实验室]
```

任何红色异常卡都可点击进入已带过滤条件的列表，而不是只显示数字。

### 11.2 资料列表与详情

列表筛选：

- 状态、资料类型、发布机构、标签、适用范围、语言；
- Parser/Chunker/Embedding Profile；
- 是否存在失败任务；
- 是否被 Active Release 使用；
- Source Key、标题和内容 Hash 搜索。

详情采用三栏或可切换视图：

```text
原始文件/页面 | 规范化正文与章节 | Child Chunk 与索引状态
```

管理员点击 Chunk 时，同时高亮原始页面/正文位置，并看到：

- 标题路径、页码、字符数、Token 数；
- 内容 Hash；
- 分词结果；
- Embedding Profile、状态和失败原因；
- 当前 Source 的全部 append-only Review 历史；
- 被哪些 Release 引用。

页面不允许直接修改已生成正文。若解析有误，选择“基于此资料创建新版本”，修改解析设置或上传修正版。

### 11.3 导入向导

步骤明确显示：

1. 选择上传、URL 或粘贴文本；
2. 填写标题、Source Key、版本、发布机构、发布日期、来源链接；
3. 选择资料类别、适用人群和标签；
4. 创建任务并显示 `解析 → 切片 → 词法索引 → Embedding → 待审核`；
5. 检查解析质量和抽样 Chunk；
6. 提交审核。

重复文件按 SHA-256 提示现有 Source，不重复生成数据。相同 Source Key + Version 但内容不同必须报冲突。

### 11.4 审核界面

批准前必须确认：

- 正文与原文一致，关键表格/列表没有丢失；
- 适用人群、资料日期和来源元数据正确；
- 当前用途具备相应使用依据；
- 抽样 Chunk 没有标题丢失、跨义切分或页码错误。

接受时评分说明/备注不必填；拒绝时原因必填。每次操作都新增 Review Event，不覆盖历史记录。

### 11.5 检索实验室

输入区：

- 自然语言测试问题；
- 可选的已确认菜名、烹饪方式、目标和适用条件；
- 选择 Corpus Release 与 Retrieval Profile；
- “运行检索”与“与当前线上版本比较”。

结果区按阶段展示：

```text
Query Plan
  原始测试输入 → 结构化字段 → 3 条 Query Variant

Dense 40 | Lexical 40 | Phrase 20
  每条显示 rank、原始 score、命中词和过滤原因

RRF Top 30
  显示来自哪些列表，不把 RRF 当概率

Rerank Top 8
  显示 relevance score、阈值、是否采用和拒绝原因

最终上下文与 Citation
  展示实际交给 Agent 的 Parent Context、Child 引用、总字符数
```

支持将一次结果追加为评测 Case：管理员选择预期 Source/Chunk 概念，或标记“应该无结果”。测试 Query 和结果不可混入线上用户 Trace。

### 11.6 Release 页面

创建 Release 时从 approved Source Version 中选择，系统自动检查：

- 是否同时包含同一 Source Key 的多个版本；
- Source Review 是否完整；
- Chunk/Embedding 是否 100% ready；
- Profile 是否 ready；
- 是否存在已知数据冲突或撤销；
- 与当前 Active Release 的新增、替换和移除差异。

“批准版本”前必须有完成的评测 Run。“全量启用”弹窗展示版本差异、核心指标、失败 Case、回滚目标和确认文本。启用后页面显示 Runtime Revision 和操作人。

### 11.7 前端实现约束

- 沿用现有 React、React Query、Session、CSRF 与审计机制；
- 列表全部服务端分页、筛选和排序，不一次取回所有 Source/Chunk；
- Job 进度首版用 2 秒条件轮询，任务终态后停止；以后可替换 SSE；
- 大正文分页/按 Section 加载；向量数值绝不下发浏览器；
- 所有危险操作都显示精确对象、影响和可回滚性；
- UI 文案区分“自动评测”“人工批准”“当前启用”，不能混成一个绿色状态。

## 12. Admin API

统一前缀：`/api/admin/nutrition-knowledge`。所有写接口要求 Admin Session、CSRF 和 Audit Event。

### 12.1 Dashboard 与 Source

```text
GET  /dashboard
GET  /sources
POST /sources/imports                 multipart 或 JSON URL
GET  /sources/{source_id}
GET  /sources/{source_id}/sections
GET  /sources/{source_id}/chunks
POST /sources/{source_id}/reviews
POST /sources/{source_id}/new-version
POST /sources/{source_id}/retire
```

### 12.2 Job

```text
GET  /jobs
GET  /jobs/{job_id}
GET  /jobs/{job_id}/events
POST /jobs/{job_id}/retry
POST /jobs/{job_id}/cancel
```

Job Events 只返回安全阶段、计数和错误码，不把 URL 凭据、API Key 或完整 Provider 响应暴露给前端。

### 12.3 Release 与 Runtime

```text
GET  /releases
POST /releases
GET  /releases/{release_id}
POST /releases/{release_id}/evaluate
POST /releases/{release_id}/reviews
POST /releases/{release_id}/activate
POST /runtime/rollback
GET  /runtime
```

创建、评测和激活都要求 Idempotency Key，避免双击产生重复版本或重复任务。

### 12.4 Lab 与 Eval

```text
POST /retrieval-lab/runs
GET  /retrieval-lab/runs/{run_id}
POST /retrieval-lab/runs/{run_id}/evaluation-cases
GET  /evaluation-datasets
POST /evaluation-datasets
GET  /evaluation-runs
GET  /evaluation-runs/{run_id}
```

## 13. 评测体系

### 13.1 黄金集内容

不能只围绕现有 3 篇资料写 12 条“顺利命中”样例。至少包含：

- 同义问法、口语、错字和短查询；
- 中国菜名、烹饪方式和外食场景；
- 一餐搭配、蔬菜/主食/蛋白质、油盐糖等一般原则；
- 适用人群过滤；
- 同一术语在不同资料中的近似段落；
- 应该无结果的问题；
- 容易被单字或宽泛语义误召回的负例；
- 已退休 Source 和未批准 Source 的泄漏测试；
- 必须提问而不是直接建议的缺信息场景。

第一阶段建立不少于 100 个 Retrieval Case，其中至少 25% 是负例或应返回 insufficient 的场景。之后每次管理员在检索实验室发现问题，都可以一键追加 Case。

### 13.2 指标

| 指标 | 含义 | 首次全量启用门槛 |
| --- | --- | --- |
| Recall@5 / Recall@10 | 预期证据是否被召回 | 由首批黄金集基线校准，目标不低于 0.90 / 0.95 |
| MRR / nDCG@10 | 正确证据排序质量 | 新版本不得显著回退 |
| Insufficient Precision | 应无结果时是否拒绝乱引 | 不低于 0.95 |
| Unpublished leakage | 草稿、拒绝、退休或非 Release 内容泄漏 | 必须为 0 |
| Citation integrity | Source/Chunk/Hash/Release/Invocation 可回库 | 必须为 100% |
| Context duplication | 最终上下文重复比例 | 设基线后持续监控 |
| P95 latency | 完整检索耗时 | Shadow 实测后设门槛，不在设计阶段拍脑袋 |
| Provider failure behavior | 超时/失败是否 fail closed | 必须为 100% |

阈值由冻结黄金集评测确定，不用供应商文档中的演示阈值直接上线。

### 13.3 Release 对比

每个候选 Release 必须与当前 Active Release 在同一 Dataset 上比较：

- 新增通过、修复和回退的 Case；
- 每项指标差值；
- Query Plan、召回阶段或 Rerank 导致变化的具体原因；
- 成本和延迟变化。

存在未接受的严重回退时，页面不能启用 Release。

## 14. 安全、隐私与治理

- 公开营养资料和用户健康数据分离；Source/Chunk 没有 `user_id`；
- 发给 Embedding/Rerank 的 Query 去标识化，只包含完成检索所需的最小饮食语义；
- Provider API Key 只在后端 Secret/环境变量中，数据库仅保存 Provider 名、Model、Request ID 和用量；
- 原始上传文件在私有 Volume，不由 Nginx 静态暴露；下载必须经过 Admin API 授权和审计；
- Source URL 不能携带持久凭据；发现 Query Token 时拒绝保存或先清理；
- Rights Review、Content Review 和 Applicability Review 分开记录，不能依赖可过期的自定义 Metadata 布尔值；
- 被撤销资料不会从历史 Trace 消失，但立即禁止进入新检索；
- Prompt Injection 清理不是靠删除文字，而是把资料内容标为不可执行数据，并在 Coordinator/Reviewer 校验工具与指令边界。

## 15. 配置

敏感值留在服务器 `deploy/.env.server`：

```text
NUTRITION_RAG_ENABLED=true
NUTRITION_RAG_ENGINE=v2
NUTRITION_KNOWLEDGE_WORKER_ENABLED=true
NUTRITION_KNOWLEDGE_STORAGE_BACKEND=local
NUTRITION_KNOWLEDGE_STORAGE_PATH=/var/lib/slim-guard/nutrition-sources

NUTRITION_EMBEDDING_PROVIDER=zhipu
NUTRITION_EMBEDDING_MODEL=embedding-3
NUTRITION_EMBEDDING_DIMENSIONS=1024
NUTRITION_RERANK_PROVIDER=zhipu
NUTRITION_RERANK_MODEL=rerank
```

复用现有 `ZHIPU_API_KEY`，不新增一份重复 Secret。Top-K、RRF、阈值和上下文预算不散落在环境变量中，而由数据库中不可变的 Retrieval Profile 管理，便于评测和复现。

`NUTRITION_RAG_ENGINE=v1|v2` 只用于迁移期 Shadow 对比；v2 全量稳定一个 Release 周期后删除 v1 和全库应用内扫描代码。

## 16. 现有 3 篇资料如何迁移

现有资料不是特殊硬编码样例，而是第一批真实 Source Version：

1. 把现有规范化正文登记为 `legacy_manifest` Asset；
2. 使用新 Parser/Chunker Profile 重建 Section 和 Parent/Child Chunk；
3. 使用 `embedding-3@1024` 生成向量，并建立词法索引；
4. 将现有 approve/publish 历史迁移为 Content Review 事件；
5. 对当前 Metadata 中 `license_reviewed=false`、`publish_eligible=false` 与数据库已发布状态的不一致做显式修复：旧布尔值只保留为“采集当时状态”，Rights Review 重新在页面确认；
6. 建立 `nutrition-corpus-v1` 候选 Release；
7. 用至少 100 条黄金集运行 Hybrid Retrieval Eval；
8. 人工检查失败 Case；
9. 通过后仅在 Multi-Agent Shadow 使用；
10. 端到端建议和 Reviewer 也通过后，再全量启用。

迁移不会声称 3 篇资料足以覆盖全部饮食建议。它只证明标准流程可工作；此后新增第 4 篇或第 400 篇资料都走相同入口。

## 17. 实施拆分

### RAG-0：纠正完成状态与兼容边界

- 把当前检索标为 v1 transitional；
- 文档不再声称“代码入口完整、只缺数据”；
- 保持线上 Shadow，不扩大用户流量。

验收：当前能力和缺口在文档、管理台状态中一致。

### RAG-1：pgvector 与目标 Schema

- 升级主数据库镜像，完成备份/恢复演练；
- 增加 vector、pg_trgm、Profile、Job、Section、Embedding、Release、Eval 表；
- 保留现有表和 Trace 兼容读取。

验收：迁移前后现有业务测试通过；扩展、索引和回滚演练有记录。

### RAG-2：导入 Worker 与 Source 管理 API

- 实现私有 Raw Object Store；
- 实现 PDF/MD/TXT/HTML 解析、质量检测、Parent/Child 切片；
- 实现中文词法分析、Embedding 批处理、幂等重试；
- 完成 Source/Job Admin API。

验收：同一文件重复导入不重复计费；失败可重试；Hash 和进度正确。

### RAG-3：真正的 Hybrid Retrieval

- 实现 Query Plan；
- 实现 pgvector Dense、FTS、Phrase；
- 实现 RRF、智谱 Rerank、去重、Parent Context 和严格采用规则；
- 删除把线性加权称作 Rerank 的逻辑。

验收：SQL 不做全库应用内扫描；每阶段排名可解释；故障 fail closed。

### RAG-4：管理后台资料与任务页面

- 增加侧栏入口、Dashboard、Source List/Detail、Import Wizard、Job 页面；
- 完成 Chunk/原文联动、Review 历史和失败重试；
- 完成前端单元、集成和权限测试。

验收：管理员不使用 CLI 也能把一份资料推进到 approved。

### RAG-5：检索实验室、Release 与 Eval 页面

- 完成逐阶段检索解释；
- 完成 Evaluation Dataset/Run；
- 完成 Release Diff、批准、启用和回滚。

验收：管理员可在页面创建候选版本、比较当前版本并安全切换。

### RAG-6：Agent 接入与 Shadow

- 扩展 Artifact/Citation/Reviewer；
- 迁移现有 3 篇资料；
- 建立首批 100+ Case；
- 跑 Retrieval 与端到端 Shadow 对比。

验收：未发布泄漏为 0、Citation 完整性 100%、无证据不生成知识性结论。

### RAG-7：Canary、全量与清理

- 通过人工审核后从 Shadow 进入 Canary；
- 演练 Corpus Release 回滚和 `NUTRITION_RAG_ENABLED=false` 紧急降级；
- 全量稳定一个 Release 周期后移除 v1 扫描实现。

验收：页面、Agent、Trace、告警和备份恢复全部通过；v2 成为唯一生产检索路径。

## 18. 测试范围

### 18.1 后端

- Parser、Chunker、中文 Analyzer 的 Golden File；
- Source/Review/Release 状态机与并发幂等；
- Embedding 批次错序、缺项、非法维度和 Provider 重试；
- Dense/FTS/Phrase/RRF/Rerank 的固定样例；
- Active Release 隔离、撤销、退休、Hash 篡改；
- Deadline、Provider 故障和 fail-closed；
- PostgreSQL 真集成测试验证 vector/GIN/HNSW 查询，不用 SQLite 假装覆盖；
- Admin Auth、CSRF、SSRF、上传大小和审计。

### 18.2 前端

- Dashboard 统计和异常跳转；
- 导入向导与真实 Job 轮询；
- 原文、Section、Chunk 联动；
- 接受备注可空、拒绝原因必填；
- Release 差异、评测阻断、启用确认和回滚；
- Retrieval Lab 各阶段分数与拒绝原因；
- 大列表分页、错误和空状态。

### 18.3 端到端

```text
上传资料
  → Worker 成功解析与 Embedding
  → 人工批准 Source
  → 建立候选 Release
  → 运行 100+ Eval
  → 人工批准 Release
  → Shadow Agent 使用
  → Trace 回查 Citation
  → 激活
  → 回滚
```

## 19. 完成定义

只有同时满足以下条件，才能说“营养 RAG 和可视化管理已经完成”：

- 真实 Embedding 已生成并由 pgvector 检索；
- 中文词法索引不依赖把全部 Chunk 拉到应用内；
- RRF 和真实 Rerank 已启用，分数名称准确；
- Active Corpus Release 是线上唯一知识集合；
- 管理员能只通过网页完成完整生命周期；
- 现有 3 篇资料已按标准流程迁移，没有特殊代码路径；
- 至少 100 条 Retrieval Golden Case 和端到端 Shadow 人工评审通过；
- 未批准/非 Active/退休资料泄漏为 0；
- Citation 回库与 Hash 完整性为 100%；
- Provider 故障、备份恢复、Release 回滚和 RAG 紧急关闭均演练通过；
- v1 全库应用内扫描实现已从生产路径移除。

## 20. 外部能力依据

- pgvector 官方文档说明可在 PostgreSQL 中使用 HNSW/IVFFlat、Metadata Filter、iterative scan，并建议 Hybrid Search 使用 PostgreSQL Full Text Search 后通过 RRF 或 Cross-Encoder 融合：<https://github.com/pgvector/pgvector>
- PostgreSQL 16 `pg_trgm` 官方文档提供文本 Trigram 相似度和 GIN/GiST 索引：<https://www.postgresql.org/docs/16/pgtrgm.html>
- 智谱 Embedding API 官方文档说明 `embedding-3` 支持 256/512/1024/2048 维、数组批量输入：<https://docs.bigmodel.cn/api-reference/模型-api/文本嵌入>
- 智谱 Rerank API 官方文档提供 `/paas/v4/rerank`、候选相关性得分和最多 128 条 Documents：<https://docs.bigmodel.cn/api-reference/模型-api/文本重排序>

## 21. 与已有文档的关系

- 本文取代 `DISH_RECOGNITION_DIET_GUIDANCE_DESIGN.md` 中“词法检索先上线、向量按需再接”的技术策略；
- `DISH_RECOGNITION_DIET_GUIDANCE_DESIGN.md` 中“识图只认菜、不估热量”、三 Agent 分工和安全边界继续有效；
- `MULTI_AGENT_ARCHITECTURE.md` 中 Agent 不直接互调、Artifact 不可变和 Reviewer 校验继续有效；
- `DISH_GUIDANCE_DATA_RUNBOOK.md` 的 CLI 作为兼容和应急入口保留，但不再是日常管理主入口；
- `MULTI_AGENT_ROLLOUT.md` 的 `off → shadow → canary → on` 继续适用。

若旧文档与本文在 Nutrition RAG 技术路线、完成状态或管理入口上冲突，以本文为准。
