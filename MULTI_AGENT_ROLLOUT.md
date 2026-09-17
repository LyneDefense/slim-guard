# Core Agent 主路径启用与回退手册

> 适用于 `core-primary-v1`。旧版 shadow/canary 双轨工作流已经删除。

## 运行边界

`AGENT_RUNTIME_MODE` 固定为 `harness`。Core Agent 始终是唯一主业务路径，可调用普通业务工具和
Nutrition Agent Tool；系统不会预先生成 Legacy baseline，也不会并行生成候选回复。

`MULTI_AGENT_MODE` 目前只控制最终回复管线：

| 模式 | Core 与业务/专业工具 | Style + Reviewer | 最终回复 |
| --- | --- | --- | --- |
| `off` | 正常运行 | 关闭 | Core 的安全中性稿 |
| `on` | 正常运行 | 开启 | 通过医生风格和必要审查的最终稿 |

启用 `on` 时必须同时配置：

```dotenv
AGENT_RUNTIME_MODE=harness
MULTI_AGENT_MODE=on
MULTI_AGENT_GRAPH_VERSION=core-primary-v1
AGENT_SPECIALIST_TIMEOUT_SECONDS=20
MULTI_AGENT_INVOCATION_MAX_TOTAL_TOKENS=32000
STYLE_RENDER_ALL_NORMAL_REPLIES=true
RESPONSE_REVIEWER_ENABLED=true
DEFAULT_STYLE_PROFILE=doctor_strict_v3
```

`DEFAULT_STYLE_PROFILE` 必须是数据库中已经发布并启用的精确版本。配置修改后需要重新部署或重启服务。

## 建议启用顺序

1. 保持 `MULTI_AGENT_MODE=off`，验证 Core 的记录、查询、图片观察和 Nutrition Agent Tool。
2. 确认已发布的 Nutrition RAG Release 能返回可靠引用；需要 RAG 时启用
   `NUTRITION_AGENT_ENABLED=true` 与 `NUTRITION_RAG_ENABLED=true`。
3. 在管理台完成医生 Style Profile 的 A/B 人评、发布和全量启用。
4. 用测试账户验证 Trace 中能看到中性稿、风格稿、Reviewer 判决和最终采用。
5. 设置 `MULTI_AGENT_MODE=on` 并重新部署。

系统仍处于开发阶段，不保留复杂的名单灰度或影子候选机制。需要降低风险时，应在测试账户上验收后直接
启用；出现问题时关闭最终管线即可，Core、用户 Thread、RAG Release 和已经写入的健康记录不受影响。

## 验收重点

- 一条用户消息只产生一条主业务生成链；
- 业务写工具只执行一次；
- Nutrition Agent 的每次检索绑定当前 Invocation 和冻结的 RAG Release；
- 专业 Claim 有可追溯 Citation，资料不足时不生成肯定结论；
- 风格前后数字、事实、风险、不确定性和引用保持一致；
- Reviewer 只判决，不直接改文案；返工交回 Style、Nutrition 或 Core；
- 风格/审查管线失败时使用已生成的 Core 中性稿，不再次调用 Core；
- Trace 首屏能读出用户输入、实际路线、工具、记忆、RAG、风格变化和最终回复。

建议验证命令：

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q tests/unit/test_agent_runtime.py \
  tests/unit/test_nutrition_agent_tool.py \
  tests/unit/test_response_finalization.py \
  tests/unit/test_harness_loop.py
npm --prefix frontend test
npm --prefix frontend run build
```

这些自动化测试验证代码契约，不替代真实模型、真实资料和测试账户的人工验收。

## 回退步骤

1. 在 `deploy/.env.server` 设置 `MULTI_AGENT_MODE=off`。
2. 保持 `AGENT_RUNTIME_MODE=harness`，不要切回已删除的 legacy/shadow 路径。
3. 通过 `ssh me` 登录服务器，在项目目录执行 `./deploy.sh`。
4. 打开 `https://enceladus.online/admin/`，确认新 Turn 仍由 Core 执行，最终输出使用中性稿。
5. 保存失败 Trace、Graph/Profile/RAG Release 版本和错误码，修复后用测试账户重新验收再开启。

回退只影响重启后的新 Turn；已经进入 Outbox 或已经发送的消息不会自动撤回。
