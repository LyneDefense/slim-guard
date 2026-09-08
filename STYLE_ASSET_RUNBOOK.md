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
- `data/style-assets/doctor_strict_v1/REVIEW.md`：首批真实模型结果与待审核表达建议。
- `data/style-assets/doctor_strict_v1/extraction-review.v1.json`：保留实际模型输出，另列助手筛选及建议修订。
- `data/style-assets/doctor_strict_v1/extraction-status.v1.json`：当前实际执行状态；早期 preparation-status 仅记录准备阶段。
- `data/style-assets/doctor_strict_v1/style-direction.approved.v1.json`：用户在本会话接受三条表达建议及语气方向的记录，不是完整资产或上线批准。
- `data/style-assets/doctor_strict_v1/REVIEW-ROUND-2.md`：纠正、鼓励、询问三类补充模板的历史复审材料，现已通过。
- `style_assets/doctor_strict_v1/profile.reviewed.json`：六类表达审核完成后冻结的无身份化风格规范。
- `data/style-assets/doctor_strict_v1/bundle.v3.json`：六类各一条已批准示例组成的固定受审 bundle。
- `data/style-assets/doctor_strict_v1/comparison.v5.json`：固定 bundle 的最终真实模型合成 A/B 报告。
- `data/style-assets/doctor_strict_v1/AB-REVIEW.md`：与最终报告逐字一致的本地人工复审清单。
- `data/style-assets/doctor_strict_v1/export.v1.json`：自动 Eval 通过的精确离线导出，仍为 draft。
- `style_assets/doctor_strict_v2/profile.feedback-derived.json`：根据首轮实名拒绝批注归纳的 v2 规则；
  不冒充真人，不携带批注场景中的食物或专业知识。
- `data/style-assets/doctor_strict_v2/feedback-review.v1.json`：六类批注到修订例句及来源 review ID 的本地审计记录。
- `data/style-assets/doctor_strict_v2/bundle.v1.json`：从 append-only 反馈修订记录构建的 v2 draft bundle。
- `data/style-assets/doctor_strict_v2/comparison.v3.json`：带逐条结构化场景的 v2 最终人工复审输入。

`data/` 已忽略，输出文件权限为 0600，不覆盖已有文件。初版 `prepared.json` 已被
`prepared.v2.json` 取代，保留便于核对，后续不要导入初版。
图片和语音没有被识别，不凭前后位置猜测回复对象。不足的例子保持缺失，不造训练数据。

用户已配置模型凭据，并明确允许处理经隐私复核的脱敏文字。2026-09-08 已用真实模型
处理首批 7 组低隐私风险文字，共两轮、14 次判断；另 9 组因个人健康、亲属或具体方案等原因
保留在本地，未外发。原始 HTML、图片和语音没有上传。

首轮出现英文输出、主观推断及反问误读，已保留原记录并收紧抽取提示词后重跑。
第二轮模型判定 5 组相关、2 组不相关；助手复核建议 1 组送人工审核、2 组修订后再审，
4 组不采用。相关性不代表表达适用；修订建议不是医生原话，也不是人工批准。
确认、解释、提醒三类建议以及后续的纠正、鼓励、询问模板均已由用户明确确认。
六条审核以实际会话审核人写入离线 append-only corpus，均限定为表达方式批准，不包含原聊天事实、
医学知识、真人身份或上线授权。

用户随后确认沿用确认、解释、提醒三条表达建议，以及“简短、直接、不责备”的方向；之后又明确
通过纠正、鼓励、询问三条补充模板。原始提取结果与最终修订模板分别保留，批准不外推到原始事实。

随后从未外发语料中进一步挑选并完全去事实化 4 组低风险表达，补做真实模型判断。
纠正和询问得到相关候选；一条强烈肯定被归为 acknowledge，另补一条明确鼓励候选。
助手把鼓励和询问缩短为安全模板，连同纠正模板交由用户第二轮复审并获得明确通过。
至此六类各有一个正式 corpus 批准示例，受审规范和 bundle 已冻结。

固定 bundle 经过数轮真实合成 A/B 诊断：首先发现真实供应商无法仅凭类型名稳定返回结构化响应，
补入完整 JSON Schema；随后人工发现自动 Judge 漏过生硬纠正前缀和解释类新增动作，因而进一步禁止
强套示例脚手架，并在无上游 Action 时禁止补造下一步。最终 `response-style-v5` 的 12 个 Case 两侧
全部真实生成成功，12 个自动评估全部通过，六类行为齐全。早期 comparison 文件仅作失败审计，
不得导入；后续只使用 bundle SHA-256
`409df5d0ace5b18fde0597f558a213c2f8c5977aa8442edb5860ef053fe0e528` 对应的 `comparison.v5.json`。

最终 A/B 已用 source ID `doctor-strict-review-final-v1` 导入 `.env` 指向的本地 PostgreSQL：
`doctor_strict_v1` 共 12 个 Case，用户已实名评分 12 个，其中接受 1、拒绝 11；风格匹配均分 1.25，
语义忠实均分约 3.08，表达适宜均分 1.25。该版本没有通过人工门槛，资产未发布、未灰度、未启用。
拒绝批注中的示例说法是下一版本的修订输入，不等同于批准一套新的模板或专业内容。

人工复审页现先展示合成场景，再展示同一份 ResponsePlan 和两侧输出。新 Case 的场景包含“用户刚刚
发生了什么”“系统已确认的上下文”“这条回复要完成什么”，并以 SHA-256 与 Case、导入清单和最终
发布回执绑定。迁移前已有的 12 条评分及批注保持不变；由于旧报告没有结构化场景字段，只明确标注为
“历史 A/B 用例（未单独记录场景）”，不根据输出倒推或补造上下文。v2 A/B 已写入逐条明确场景。

`doctor_strict_v2` 已使用首轮实名批注中的六类说法修订：普通确认“行”、鼓励“不错，要保持”、
日期追问“你记录的是哪一天的”，并为提醒、纠正和解释增加只使用上游已确认事实的边界。
首轮 v2 真实生成因一条受保护事实被模型改写而降级，第二轮虽 12/12 通过但基线和候选区分不足；
两份报告均保留为失败/诊断审计。最终 `comparison.v3.json` 的两侧 12/12 均真实生成成功、自动评估
12/12 通过，并已导入目标 PostgreSQL：`doctor_strict_v2` 共 12 条，用户已完成第二轮实名评分，
最新结论为接受 7、拒绝 5。它仍未通过整套人工门槛，未发布、未灰度、未启用；拒绝项与后续真实测试
纠正只作为 v3 的输入，不会改写 v2 历史。

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
本批示例复用项目 `.env`，只导入已复核的 7 组；不直接发送原来 16 组自动脱敏候选。

```sh
uv run python -m slim_guard.tools.manage_style_corpus \
  --database data/style-assets/doctor_strict_v1/corpus.sqlite \
  --use-project-model \
  --output data/style-assets/doctor_strict_v1/import-result.json \
  import-prepared --input data/style-assets/doctor_strict_v1/prepared.model-input.v1.json \
  --max-pairs 7 --confirm-redacted-inputs
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
  --use-project-model \
  --actor ACTUAL_OPERATOR --confirm-redacted-inputs \
  --output data/style-assets/doctor_strict_v1/comparison.json
```

默认 12 个明确合成场景：六行为，各有社交表达和含受保护事实的版本。每个报告 Case 都保存场景标题、
用户处境、已确认上下文和回复目标；导入后这些内容显示在两侧输出之前。基线/候选都真实调用
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
评分前先核对场景、已确认上下文与回复目标；它们是合成审核语境，不是微信群原文，也不代表新增用户事实。
审核人由登录会话确定；改评分追加记录，不覆盖审计历史。模型分数与人类评分明确分离。

管理台另提供“风格纠正”（`/admin/style-feedback`），用于真实开发测试中记录场景、已脱敏用户消息、
当前 Agent 回复、期望回复和可选说明。沟通行为允许留空，因此可以收集默认 12 个合成 Case 之外的场景。
每次提交必须重新确认已脱敏且只学习表达，actor 取自登录会话；记录及内容 Hash 均 append-only。
这些反馈不会即时修改运行中 Profile，不进入用户 Memory 或营养知识库。构建下一版本时按反馈 ID/Hash
冻结选中集合，将规则和例句去事实化，再重新执行本章的 A/B、自动评估和实名人评。

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

生产环境后续经用户明确批准灰度，再在部署配置中选择：

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

当前单人开发阶段采用已明确批准的直接全量策略：只有某一候选版本的精确 Case 集全部人工接受、完成
上述 publish 并成为 evaluated 资产后，跳过 Style Canary，把 `DEFAULT_STYLE_PROFILE` 直接改成该精确版本，
清空 `STYLE_CANARY_PROFILE` / `STYLE_CANARY_USER_IDS`，再重启服务。v3 尚未全部验收前不得提前切换。
回退时把 `DEFAULT_STYLE_PROFILE` 改回 `slimguard_default_v1` 并重启；这不会删除已发布版本或反馈历史。
该开发便利不取消自动评估、实名 A/B 人评、精确 Hash 绑定或启动时的已发布版本校验，也不自动延伸到生产。

## 实现验证

2026-09-08 当前后端完整回归：`uv run pytest -q` 共收集并通过 722 项；`ruff check src tests`、
`mypy src/slim_guard`（174 个源文件）及编译检查通过。数据库审核与认证 API 使用临时 SQLite
验证，自动化测试中的模型响应全部是显式合成测试夹具。风格纠正表及 append-only 触发器迁移已应用到
本地 `.env` 指向的 PostgreSQL，管理 API 也用实际管理员会话完成只读连通性验证；没有写入示例反馈。
前端 `npm run check`、`npm run build` 和 `npm test`（25 项 SSR/契约回归）通过。
最终固定 bundle 的 12 组合成 A/B 均由真实模型生成，独立自动评估 12/12 通过；这与代码回归是
两组不同证据。首轮人工 A/B 仅接受 1/12，证明自动评估不能替代真实风格验收；该版本没有发布。
