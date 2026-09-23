# Turn Runner 生命周期边界整理

## 范围与确认

本次只整理已讨论的职责边界，不更改 Core 的业务路由、工具权限规则、记忆选择策略、教练出现策略或回复降级策略。

另经确认的两点：

1. Core 生成草稿后结束自身 Invocation，回复模块继续执行；整个 Turn 由 Runner 最后结束。Core 的完成状态不等于回复已生成完毕，更不等于用户已收到消息。
2. 未分类异常先补记失败，再向外抛出原异常，不吞成正常返回。取消仍向外传播，并尽力完成状态记录。

## 文件与职责

| 文件 | 职责 |
|---|---|
| `src/slim_guard/harness/runner.py` | 初始化 Turn、绑定用量归属、协调准备/Core/回复处理、统一收尾。依赖在外部装配，不再自行创建 Core。 |
| `src/slim_guard/harness/preparation.py` | `TurnGrants`、上下文加载/召回/编译的协调、有效工具授权、上下文快照。复用现有安全策略和 Compiler。 |
| `src/slim_guard/runtime/invocation/core.py` | Core Invocation 的创建、开始、完成、失败、取消记录，保存真实 Core 草稿及自身调用统计。工具循环与结构化零工具调用不同，因此不强行复用现有 `InvocationRunner`。 |
| `src/slim_guard/runtime/invocation/core_result.py` | Core 执行结果及携带进度的异常契约；Runner 不通过执行实现导入这些类型，避免包初始化产生循环依赖。 |
| `src/slim_guard/response_pipeline/processor.py` | 原稿输出检查、调用现有 Finalizer、最终输出检查、采用/降级与事件记录。只返回输出，不发送、不结束 Turn。 |
| `src/slim_guard/harness/loop.py` | 模型—工具循环及中间事件；返回执行结果，不再调用回复流水线或 `finish_run`。本次不修改模型选择业务下一步的机制。 |
| `src/slim_guard/agents/core/agent.py` | Core 执行接口移除回复处理与整轮结束回调，向执行层提供本次进度用于异常记录。 |
| `src/slim_guard/agent/composition.py` | 装配准备模块、Core 调用执行模块和回复处理模块，注入 Runner。 |
| `src/slim_guard/runtime/turn/__init__.py`、`src/slim_guard/harness/__init__.py` | 更新 TurnGrants 的公开导出位置。 |

## 执行顺序

```text
Runner 初始化 Turn、绑定用量归属
  → 上下文准备模块生成上下文与权限
  → Core 调用执行层：开始 Invocation → Core 模型/工具循环 → 保存草稿、结束 Invocation
  → 回复处理模块：检查原稿 → 聊天/风格/审查 → 采用或降级
  → Runner 保存最终消息与 Turn 状态，返回结果
  → 外层渠道负责发送与送达记录
```

Core 的调用统计只包含它自身的请求。问答总用量仍以请求级账本为准，不因 Invocation 结束时机变化重复加总。

## 异常与不变项

- 上下文校验失败：不进入 Core，记录失败结果。
- Core 的已分类模型错误：沿用原失败结果和重试属性。
- Core 未分类异常：记录已完成的模型/工具进度和失败类型；Runner 完成整轮失败记录后抛出原异常。错误消息正文不写入结构化失败字段。
- 在上下文、Core 或回复阶段取消：不保存最终用户回复；Turn 使用现有 `suspended` 状态和 `execution_cancelled` 原因码。若 Core 早已成功，不倒改为失败。
- 工具要求用户确认或人工审批：保留原等待状态，不进入回复生成、不误标整轮完成。
- 原稿被输出检查修改时不进入风格流程；最终候选被修改时退回原稿并不采用候选角色消息。这是既有降级策略，本次只集中到回复模块，**没有实现新的逐消息保留或修复策略**。
- 数据库不可用时只能尽力记录。最后一次 Turn 写入异常不会盲目重试，以免重复保存用户可见消息；这不是新增的自动恢复/重试系统。
- 不新增数据库迁移，不改服务配置、不部署、不重启 Expo。

## 验证

新增 `tests/unit/harness/test_runner_lifecycle.py` 验证 Core/Turn 的结束顺序、Core 草稿与最终角色消息的区别、回复异常回退、每阶段审查次数、原异常传播、取消与已完成用量、等待状态，以及收尾写入异常不重复保存消息。

原 Loop 的回复处理测试迁入以上模块边界测试；装配测试更新为校验新依赖。扩大回归覆盖 Harness、Runtime、回复处理、通用待办、用量统计、主动提醒以及移动端和微信回调集成。

另用独立 Python 进程验证各运行时模块可单独导入，防止测试收集顺序掩盖循环依赖。

最终验证结果（2026-09-23）：

- 上述定向扩大回归 **150 项通过**，含新增 12 项生命周期用例及 7 项独立模块导入用例；不是全量测试。
- 两条已有的 Starlette HTTP 413 常量弃用警告，不影响测试结果，本次未扩展修改 API。
- Ruff 检查 `src/slim_guard` 与 `tests`、Mypy 检查 `src/slim_guard`、改动文件格式检查及 `git diff --check` 均通过。
- 测试采用本地数据库与模拟模型/工具，不代表真实模型或线上端到端验收。
