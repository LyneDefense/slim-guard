# 医生表达风格：准备、审核、评估和灰度

本流程只提取表达方式，不微调模型，不把群聊当医学知识，不模仿真人身份。
原始 HTML、候选上下文和人审材料不进入用户 Memory、营养 RAG 或普通管理台。

## 当前真实数据状态

2026-09-08 已在本地解析用户指定的 HTML：1,195 条消息，365 条风格源消息，
分成 215 个回复片段。16 组文字上下文可定位；199 组待核对；11 组排除
（待核对和排除可以重叠）。可定位不等于相关、隐私通过或表达可用。

当前可用文件：

- `data/style-assets/doctor_strict_v1/prepared.v2.json`：自动脱敏、本地待审材料。
- `data/style-assets/doctor_strict_v1/preparation-status.json`：真实执行状态，不含聊天正文。
- `style_assets/doctor_strict_v1/profile.draft.json`：根据本地观察归纳的表达草案，未批准。

`data/` 已忽略，输出文件权限为 0600，不覆盖已有文件。初版 `prepared.json` 已被
`prepared.v2.json` 取代，保留便于核对，后续不要导入初版。
图片和语音没有被识别，不凭前后位置猜测回复对象。不足的例子保持缺失，不造训练数据。

真实模型提取、真实 A/B、人工评分及发布尚未完成：本次环境没有模型凭据，未外发群聊，
也没有代替用户提交任何人工批准。单元测试使用合成数据，不是实际风格验收结果。

## 1. 本地准备与隐私核对

```sh
uv run python -m slim_guard.tools.prepare_style_export \
  --input /absolute/path/to/index.html \
  --output data/style-assets/doctor_strict_v1/prepared.new.json
```

必须核对姓名、称谓、健康史、亲属关系及特殊事件等可识别信息。自动脱敏只是第一遍处理。
`eligible_for_judgment` 只表示关联可定位；`privacy_review_required` 仍为真。
原始消息 ID 转为稳定假名，source SHA 和消息引用可在本地追溯；不保存姓名反查表。

## 2. 模型相关性判断与表达抽取

先在本机安全配置 `STYLE_CORPUS_API_KEY`、`STYLE_CORPUS_MODEL`，可选
`STYLE_CORPUS_BASE_URL`。也可显式加全局 `--use-project-model` 使用项目 ZHIPU 配置。
不要把密钥写入命令示例、聊天、语料或 Git。

以下确认参数只可在实际核对隐私并同意将筛选文本交给模型后使用。

```sh
uv run python -m slim_guard.tools.manage_style_corpus \
  --database data/style-assets/doctor_strict_v1/corpus.sqlite \
  --output data/style-assets/doctor_strict_v1/import-result.json \
  import-prepared --input data/style-assets/doctor_strict_v1/prepared.v2.json \
  --max-pairs 40 --confirm-redacted-inputs
```

只有可定位且未排除的文本送模型。模型判断是否真正相关、沟通行为、去事实的表达例子及规则。
相同来源、片段、模型和判断提示词重复导入复用候选，不重复调用模型。
结果仍为待人工审核，不自动批准，也不补造缺失沟通行为。

## 3. 真正的人审与精确资产构建

使用 `manage_style_corpus ... review` 在受限本地终端列候选；该列表有脱敏上下文，
不应复制进普通管理台。审核人逐项确认或拒绝，必要时修订 `example_text` / `tone_rules`。
审批 JSON 的 actor 必须是实际审核人，不能使用模型运行人冒充：

```json
{
  "actor": "ACTUAL_REVIEWER",
  "decision": "approve",
  "note": "实际审核理由，不是预填通过",
  "privacy_confirmed": true,
  "expression_only_confirmed": true
}
```

```sh
uv run python -m slim_guard.tools.manage_style_corpus \
  --database data/style-assets/doctor_strict_v1/corpus.sqlite \
  review --candidate-id CANDIDATE_ID --review data/style-assets/doctor_strict_v1/review.json
```

先审核并另存风格规范为 `profile.reviewed.json`，明确认可短句、直接督促、肯定和安抚的比例。
不得把具体饮食、剂量、身体指标或诊断当风格示例。医生资产要求 acknowledge、correct、
remind、encourage、explain、ask 六种行为的受审示例齐全；缺失时继续整理/审核，不可伪造。

```sh
uv run python -m slim_guard.tools.manage_style_corpus \
  --database data/style-assets/doctor_strict_v1/corpus.sqlite \
  --output data/style-assets/doctor_strict_v1/bundle.json \
  build --profile-id doctor_strict --version doctor_strict_v1 \
  --display-name '直接督促型教练' \
  --profile-spec data/style-assets/doctor_strict_v1/profile.reviewed.json
```

`--display-name` 必须与规范完全一致。`propose` 只提供早期未批准预览，不能混用其评估结果发布。
批准后的 `build` 才是后续 A/B 的精确版本。重新审核、改规则或例子后必须重新生成与评估。

## 4. 真实 A/B 与自动评估

```sh
uv run python -m slim_guard.tools.evaluate_style_asset \
  --bundle data/style-assets/doctor_strict_v1/bundle.json \
  --database data/style-assets/doctor_strict_v1/corpus.sqlite \
  --actor ACTUAL_OPERATOR --confirm-redacted-inputs \
  --output data/style-assets/doctor_strict_v1/comparison.json
```

默认 12 个明确合成场景：六行为，各有社交表达和含受保护事实的版本。基线/候选都真实调用
同一 Style Agent，再独立判断风格、忠实度、隐私、无冒充/羞辱及无新增专业知识。
报告记录模型、用例、示例 ID、调用次数及失败原因。模型中性降级不算风格成功；基线降级也不算
完整 A/B。没有凭据会明确失败，不生成虚假的 passed 报告。

在线现阶段将业务基线全文作为受保护事实块，因此风格主要影响衔接、节奏及社交表达，
不是任意改写专业建议。这个忠实度约束不因追求“更像”而放松；合成 social-only 场景不能
代替真实线上任务效果验收。

## 5. 普通管理台只接收合成 A/B 并由真人评分

显式指定目标应用数据库，不要误用离线 corpus 数据库。以下 `STYLE_REVIEW_DATABASE_URL`
应在本机配置为管理台实际使用的 `DATABASE_URL`（可为 PostgreSQL 或测试用 SQLite）；
不能导入另一份新建数据库后期待管理台自动看到结果。

```sh
uv run python -m slim_guard.tools.manage_style_assets \
  --database-url "$STYLE_REVIEW_DATABASE_URL" \
  --actor ACTUAL_OPERATOR --confirm-reviewed-inputs \
  import-ab --input data/style-assets/doctor_strict_v1/comparison.json \
  --bundle data/style-assets/doctor_strict_v1/bundle.json --source-id doctor-strict-review-1
```

导入校验 bundle/evaluation/cases 精确哈希、版本、示例选择和六行为覆盖。
两侧任一降级或非合成输入不能进入普通管理员 A/B 界面。失败的自动评估不因人工评分而变通过。
在管理台导航“风格 A/B 人评”（`/admin/style-ab`）逐项评分：风格匹配、语义忠实度、
适当性（1–5），接受/拒绝及理由。
审核人由登录会话确定；改评分追加记录，不覆盖审计历史。模型分数与人类评分明确分离。

## 6. 发布、灰度与回滚

先从离线库导出与当前批准集合完全一致且真实 Eval 通过的资产：

```sh
uv run python -m slim_guard.tools.manage_style_corpus \
  --database data/style-assets/doctor_strict_v1/corpus.sqlite \
  --output data/style-assets/doctor_strict_v1/export.json \
  export --profile-id doctor_strict --version doctor_strict_v1 \
  --display-name '直接督促型教练' \
  --profile-spec data/style-assets/doctor_strict_v1/profile.reviewed.json

uv run python -m slim_guard.tools.manage_style_assets \
  --database-url "$STYLE_REVIEW_DATABASE_URL" \
  --actor ACTUAL_OPERATOR --confirm-reviewed-inputs \
  publish --input data/style-assets/doctor_strict_v1/export.json --confirm-evaluation-reviewed
```

发布还必须找到应用数据库中同一 bundle/evaluation/version 的合格人工 A/B 审核记录。
布尔确认参数不能代替这条数据库审核证据。发布仅生成 evaluated 版本，不自动激活或切流。

后续经用户明确批准灰度，再在部署配置中选择：

```dotenv
DEFAULT_STYLE_PROFILE=slimguard_default_v1
STYLE_CANARY_PROFILE=doctor_strict_v1
STYLE_CANARY_USER_IDS=INTERNAL_USER_UUID_1,INTERNAL_USER_UUID_2
```

必须使用内部 user ID，不使用群昵称、微信号或外部联系人 ID。
workflow 本身仍遵守既有 `MULTI_AGENT_MODE` / `MULTI_AGENT_CANARY_USER_IDS` 采用规则；style 名单不扩大
workflow canary 名单。先 shadow 检查，再经批准使用 canary，不能只改 style 名单就认为已发给用户。
启动时拒绝未发布的配置版本；运行中解析异常可观察地回退默认风格。
每轮只取一次版本快照，修复/Reviewer/渲染复用同一版本，按行为选择至多五个例子。

快速回滚：清空 `STYLE_CANARY_PROFILE` 并按既有流程重启，保持默认 slimguard_default_v1。
名单可保留。停止整个新工作流则使用既有 `MULTI_AGENT_MODE=off`。
本流程不运行任何部署、切流或自动批准命令。

## 实现验证

2026-09-08 后端完整回归：`uv run pytest` 为 694 passed；`ruff check src tests`、
`mypy src/slim_guard`（172 个源文件）及编译检查通过。数据库审核与认证 API 使用临时 SQLite
验证，模型响应全部是显式合成测试夹具。PostgreSQL 触发器已实现，但本次没有在真实
PostgreSQL 实例执行迁移；部署前仍应在目标数据库的测试副本验证。
前端 `npm run check`、`npm run build` 和 `npm test`（21 项 SSR/契约回归）通过。
这些结果证明代码链路的测试状态，不等同于医生风格的真实模型效果或人工验收。
