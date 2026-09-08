# Multi-Agent 发布与回退手册

本手册对应通用 `slimguard_default_v1`。代码支持 Canary/on，不等于本仓库已经完成真实用户放量。
`doctor_strict_v1` 仍等待风格需求评审和真实授权语料；不能用合成测试结果替代医生资产验收。

## 运行边界

`AGENT_RUNTIME_MODE=harness` 保持不变，只调整 `MULTI_AGENT_MODE`：

| 模式 | 执行范围 | 最终回复 |
| --- | --- | --- |
| off | 原 Harness | 原回复 |
| shadow | 全部正常输入的只读候选图 | 原回复，候选不投递 |
| canary | `MULTI_AGENT_CANARY_USER_IDS` 精确匹配的内部 user_id | 合格候选或原回复 |
| on | 全部正常输入 | 合格候选或原回复 |

Canary/on 必须同时打开 `STYLE_RENDER_ALL_NORMAL_REPLIES=true` 和 `RESPONSE_REVIEWER_ENABLED=true`。
名单不是微信昵称、手机号或 test1 等显示名；应在受控测试环境确认对应内部 ID，再配置逗号分隔的名单。
空名单不会采用任何候选。无须为了回退而切换旧版 `AGENT_RUNTIME_MODE=legacy`。

真实采用发生在业务工具执行、原 Harness 文本生成及 OutputGuard 检查之后。随后刷新权威数据库
快照并加入本轮结构化工具回执，生成 Plan → Style → Reviewer 候选；专业路径另经 Nutrition。
只有成功状态、同 Turn 最终候选、完整 Payload 哈希和精确候选血缘上的最新 pass Verdict，以及
最终 OutputGuard 都成立时，才记 `response_adopted.final=true` 并保存一条最终 Agent 消息。
采用不是渠道送达，WeCom Outbox 仍负责投递。紧急输入或被 OutputGuard 修正的原回复不走普通风格。

超时、模型故障、证据刷新失败、返修耗尽、审查拒绝或总预算不足时，保留已生成的安全原回复。
不重新运行记录工具、记忆摄取或整个 Turn。`MULTI_AGENT_SHADOW_TIMEOUT_SECONDS` 同时限制候选图，
并受原 Turn 剩余 Deadline 约束；Canary/on 各节点及修复共享扣除 Harness 已用量后的调用/Token 预算。
默认每 Turn 最多 6 次模型调用，因此复杂工具轮次可能没有足够余量完成图，届时明确降级。
Token 总量由供应商返回的 usage 核算：响应使额度超限时拒绝采用并阻止后续调用，不能预知供应商计费。

## 放量顺序与量化门槛

依次进行 off 基线 → shadow → test1～test5 测试账户 → 小名单真实用户 → on。
每阶段保持同一 Graph/Agent/Profile 版本采样；版本变化后重新验收，不混入旧版本的好样本。
部署时将 `AGENT_CODE_REVISION` 固定到实际 commit，不能用默认 `development` 代替可复现发布标识。
初始门槛保存在 `slim_guard.rollout`，调整须通过代码评审，不能由模型自行降低。

| 目标阶段 | 最少已审工作流 | 最少人工配对评分 |
| --- | ---: | ---: |
| test_canary | 20 | 20 |
| small_canary | 100 | 50 |
| on | 500 | 100 |

所有阶段要求：拒绝率 ≤ 2%、实际返修率 ≤ 15%、降级率 ≤ 5%、各节点失败率 ≤ 2%、
p95 端到端延迟 ≤ 60 秒、p95 Token ≤ 64,000；知识 Claim 引用覆盖率 100%，无效引用、
高危安全失败和重复业务写入均为 0。启用 RAG 时必须有实际 RAG Claim 样本。
这些是首版运营门槛，不是已观测到的性能承诺。

人工对同一输入的 Legacy/Candidate 采用 1～5 分量表评价事实准确、必要信息完整、表达与下一步，
候选均分不得低于 Legacy。保存具名评分记录引用，不把隐私回复正文放入公开仓库。管理台提供候选、
实际采用及版本对比；它不把修复事件或模型自评冒充人工质量评分。
报告需列出固定回归项的通过结果与测试日志/受控 Trace 引用；真实失败样本必须全部复测通过，
没有观测到失败时如实记录 0，不能编造样本。报告时间必须在过去 7 天内。

```bash
uv run python -m slim_guard.tools.check_workflow_rollout --schema
uv run python -m slim_guard.tools.check_workflow_rollout /secure/reviewed-rollout-report.json
```

报告 Schema 要求 Graph/Agent/Profile 版本、actor、metrics_ref、quality_review_ref、采样计数和
`regressions`（`case_id/passed/evidence_ref`）。固定 case_id 以 `slim_guard.rollout.REQUIRED_REGRESSIONS`
为准；缺项即不通过。退出码 0 为报告满足门槛，1 为门槛未通过，2 为报告格式或读取错误。
命令只读，不自动查询隐私材料、不替操作者确认输入真实性、不改配置、不部署；通过报告后仍由
负责发布的人确认目标环境、名单、窗口和批准。全局管理台指标不可直接当作某个版本/名单的验收样本。

## 固定回归与运行验证

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run python -m compileall -q src
npm --prefix frontend run check
npm --prefix frontend run build
```

自动化回归使用合成数据及确定性模型替身，覆盖契约、只读权限、幂等写入、视觉不确定性、
引用治理、风格忠实度、有限返回边、候选采用和故障回退。真实模型质量、真实失败样本和线上性能
必须另做受控复测，不能以单元测试通过代替。尚未得到的验收数据应使发布停在前一阶段。

管理台列表支持运行模式、节点失败、RAG、实际修复、降级及 Graph/Agent/Profile 版本筛选；
详情保持真实返回边和采用状态。全局 7 天指标显示各自分母，缺样本不是 0% 成功证明。
Reviewer 拒绝/返修率与节点失败率衡量不同对象，不应直接混比。

## 回退步骤

1. 设置目标环境 `MULTI_AGENT_MODE=off`，按既有发布流程重启/滚动更新服务；配置不是热更新。
2. 确认新 Turn 不再启动多 Agent invocation，仍正常执行原 Harness 和 Outbox。
3. 复核同一用户 Thread、原已保存记录和工具幂等键仍在；不要删数据、重放输入或手工再发已入 Outbox 的回复。
4. 保存 Graph/Agent/Profile 版本和失败 Trace 引用，修复后回到 Shadow 重新验收。

回退仅影响使用新配置的 Turn；已在执行的 Turn 和已经排队的消息不会被撤销，渠道已发送消息也不会
被自动收回。若问题要求撤回或暂停既有队列，需要单独的运维处置授权，不能把切换开关当成撤回保证。
