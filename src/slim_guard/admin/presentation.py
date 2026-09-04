from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

TOOL_LABELS = {
    "clear_user_memories": "清空用户记忆",
    "configure_checkin_schedule": "设置提醒日程",
    "forget_user_memory": "删除一条用户记忆",
    "get_checkin_schedule": "读取提醒日程",
    "get_recent_exercise": "查询近期运动",
    "get_recent_body_fat_trend": "查询近期体脂趋势",
    "get_recent_meals": "查询近期饮食",
    "get_recent_weight_trend": "查询近期体重趋势",
    "inspect_image": "识别图片内容",
    "list_user_memories": "读取用户记忆",
    "record_exercise": "记录运动",
    "record_body_fat": "记录体脂",
    "record_meal": "记录饮食",
    "record_user_constraint": "保存用户限制条件",
    "record_weight": "记录体重",
    "select_relevant_memories": "选择相关记忆",
    "resolve_conversation_handoff": "完成跨轮待办",
    "resolve_pending_user_action": "处理待确认操作",
    "set_behavior_goal": "设置行为目标",
    "set_body_fat_goal": "设置目标体脂",
    "set_body_profile": "保存身高档案",
    "set_coaching_profile": "更新陪伴偏好",
    "set_exercise_profile": "保存运动习惯",
    "set_conversation_handoff": "保存跨轮待办",
    "set_weight_goal": "设置目标体重",
    "update_record_status": "修改健康记录状态",
    "upsert_exercise_preference": "更新运动偏好",
    "upsert_food_preference": "更新饮食偏好",
}

FIELD_LABELS = {
    "activity_name": "运动项目",
    "behavior": "行为目标",
    "category": "类别",
    "constraint": "限制内容",
    "distance_meters": "距离（米）",
    "duration_minutes": "时长（分钟）",
    "food_name": "食物",
    "foods": "食物",
    "goal": "目标",
    "handoff_id": "跨轮待办",
    "memory_id": "记忆记录",
    "note": "备注",
    "objective": "下轮目标",
    "occurred_at": "发生时间",
    "preference": "偏好",
    "record_id": "记录",
    "record_type": "记录类型",
    "reported_energy_kcal": "热量（千卡）",
    "status": "状态",
    "steps": "步数",
    "target_weight_kg": "目标体重（kg）",
    "height_value": "身高",
    "height_unit": "身高单位",
    "time": "时间",
    "timezone": "时区",
    "weight_kg": "体重（kg）",
    "body_fat_percent": "体脂率（%）",
}

OPERATION_LABELS = {
    "download_media": ("input", "下载用户发送的图片"),
    "ensure_agent_control": ("system", "确认当前由智能助手接待"),
    "generate_reply": ("context", "启动 Harness Agent 回合"),
    "generate_scheduled_reply": ("context", "启动定时 Harness Agent 回合"),
    "historical_trace_backfilled": ("system", "补建历史输出链路"),
    "job_skipped": ("system", "跳过本次提醒任务"),
    "send_proactive_text": ("delivery", "发送主动提醒到企业微信"),
    "send_text": ("delivery", "发送回复到企业微信"),
    "turn_finished": ("output", "Harness Agent 回合结束"),
}

AGENT_ROLE_LABELS = {
    "memory_ingestion": "长期记忆核对模块",
    "memory_recall": "记忆召回模块",
    "orchestrator": "对话编排 Agent",
    "business_tool": "业务工具",
    "evidence_builder": "证据整理模块",
    "nutrition_expert": "营养专业 Agent",
    "nutrition_tool": "营养只读工具",
    "style_resolver": "风格配置模块",
    "response_style": "表达风格 Agent",
    "response_reviewer": "回复审查 Agent",
    "coordinator": "工作流协调器",
}

ARTIFACT_TYPE_LABELS = {
    "directive": "工作流指令",
    "turn_directive": "工作流指令",
    "response_plan": "回复内容计划",
    "assessment": "专业评估",
    "nutrition_assessment": "营养专业评估",
    "citation": "资料引用",
    "knowledge_citation": "专业资料引用",
    "styled_response": "风格化回复",
    "reviewer_verdict": "回复审查结论",
}

WORKFLOW_NODE_LABELS = {
    "initialized": "工作流初始化",
    "input_guarded": "输入安全检查",
    "safety_rendered": "安全回复生成",
    "memory_ingested": "长期记忆核对",
    "memory_recall": "相关记忆召回",
    "context_ready": "上下文准备完成",
    "orchestrator": "对话编排",
    "orchestrator_running": "对话编排",
    "business_tool_running": "业务工具执行",
    "response_rendering": "回复内容整理",
    "evidence_ready": "证据准备完成",
    "nutrition_expert": "营养专业判断",
    "expert_running": "营养专业判断",
    "expert_tool_running": "营养只读工具执行",
    "style_resolved": "表达风格配置完成",
    "response_style": "表达风格处理",
    "style_running": "表达风格处理",
    "style_tool_running": "风格示例检索",
    "response_reviewer": "回复审查",
    "review_running": "回复审查",
    "neutral_fallback": "中性回复降级",
    "final_guard": "最终安全检查",
    "output_guarded": "最终安全检查",
    "legacy_response": "现有回复链路",
    "completed": "工作流完成",
    "delivery": "渠道发送",
}


def present_event(event: Mapping[str, Any]) -> dict[str, Any]:
    operation = str(event.get("operation") or "unknown")
    details = _mapping(event.get("details"))
    if event.get("event_type") == "agent_item":
        return _present_agent_item(operation, details)
    stage, title = OPERATION_LABELS.get(
        operation,
        ("system", f"执行系统步骤：{_humanize_identifier(operation)}"),
    )
    return {
        "stage": stage,
        "title": title,
        "summary": _span_summary(operation, details, str(event.get("status") or "")),
        "facts": _facts(details),
    }


def execution_summary(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    operations = [str(event.get("operation") or "") for event in events]
    model_calls = operations.count("model_message")
    tool_calls = operations.count("tool_call")
    observations = operations.count("tool_result")
    context_snapshots = operations.count("context_snapshot")
    memory_ingestions = operations.count("memory_ingestion")
    memory_recalls = operations.count("memory_recall")
    return {
        "architecture": "harness" if context_snapshots or model_calls else "service",
        "model_call_count": model_calls,
        "tool_call_count": tool_calls,
        "observation_count": observations,
        "context_snapshot_count": context_snapshots,
        "memory_ingestion_count": memory_ingestions,
        "memory_recall_count": memory_recalls,
    }


def context_sources(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Explain the frozen context by retention and authority, not by JSON shape."""
    snapshot = next(
        (event for event in events if event.get("operation") == "context_snapshot"),
        None,
    )
    if snapshot is None:
        return []
    details = _mapping(snapshot.get("details"))
    request = _mapping(details.get("request"))
    messages = request.get("messages")
    rows = messages if isinstance(messages, list) else []
    authoritative: Mapping[str, Any] = {}
    working: Mapping[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        content = row.get("content")
        if not isinstance(content, str):
            continue
        if content.startswith("权威用户事实"):
            authoritative = _embedded_json(content)
        elif content.startswith("近期对话工作记忆"):
            working = _embedded_json(content)

    memories = authoritative.get("profile_memory")
    memory_rows = memories if isinstance(memories, list) else []
    receipt = _mapping(authoritative.get("current_turn_memory_receipt"))
    raw_changes = receipt.get("changes")
    receipt_changes = raw_changes if isinstance(raw_changes, list) else []
    health_items: list[dict[str, str]] = []
    health_items.extend(_weight_source_items(authoritative.get("recent_weights")))
    health_items.extend(_body_fat_source_items(authoritative.get("recent_body_fat")))
    health_items.extend(_meal_source_items(authoritative.get("recent_meals")))
    health_items.extend(_exercise_source_items(authoritative.get("recent_exercise")))
    other_authoritative_items = _other_authoritative_source_items(authoritative)
    working_items = _working_source_items(working)
    sources = [
        {
            "kind": "durable_memory",
            "title": "长期记忆",
            "retention": "跨对话持久保存",
            "description": "只包含用户明确表达且已通过记忆工具保存的资料。",
            "items": [_memory_source_item(row) for row in memory_rows if isinstance(row, dict)],
        },
        {
            "kind": "health_records",
            "title": "权威健康记录",
            "retention": "独立业务记录",
            "description": "体重、饮食和运动记录；它们不是长期记忆。",
            "items": health_items,
        },
        {
            "kind": "working_memory",
            "title": "最近对话 Working Memory",
            "retention": "有限窗口，自动淘汰",
            "description": "仅用于理解“刚才那个”等指代，不代表已经正式记住。",
            "items": working_items,
        },
        {
            "kind": "authoritative_context",
            "title": "其他权威上下文",
            "retention": "账户资料与当前设置",
            "description": "昵称、提醒计划和今日打卡状态等本轮只读资料。",
            "items": other_authoritative_items,
        },
    ]
    if receipt_changes:
        sources.insert(
            1,
            {
                "kind": "current_turn_memory_receipt",
                "title": "本轮记忆变更",
                "retention": "本轮写入结果",
                "description": "数据库对本轮长期记忆新增、更新或未变化的权威回执。",
                "items": [
                    _memory_change_source_item(row)
                    for row in receipt_changes
                    if isinstance(row, dict)
                ],
            },
        )
    return sources


def _present_agent_item(operation: str, details: Mapping[str, Any]) -> dict[str, Any]:
    if operation == "invocation_started":
        return _invocation_started_presentation(details)
    if operation == "invocation_result":
        return _invocation_result_presentation(details)
    if operation == "artifact_created":
        return _artifact_created_presentation(details)
    if operation == "workflow_transition":
        return _workflow_transition_presentation(details)
    if operation == "response_adopted":
        return _response_adopted_presentation(details)
    if operation == "response_degraded":
        return _response_degraded_presentation(details)
    if operation == "user_message":
        text = details.get("text")
        return _presentation(
            "input",
            "收到用户消息",
            _quote(text) if isinstance(text, str) else "收到一条文本消息。",
        )
    if operation == "image_attachment":
        mime_type = details.get("mime_type")
        return _presentation(
            "input",
            "收到用户图片",
            f"图片类型为 {mime_type}，后续只能通过图片识别工具观察内容。",
        )
    if operation == "context_snapshot":
        return _context_presentation(details)
    if operation == "model_message":
        return _model_presentation(details)
    if operation == "tool_call":
        tool_name = str(details.get("tool_name") or "unknown")
        return {
            **_presentation(
                "action",
                f"准备调用：{tool_label(tool_name)}",
                "这是模型明确选择的动作；Harness 接下来会校验参数、权限和执行策略。",
            ),
            "facts": _facts(_mapping(details.get("arguments"))),
        }
    if operation == "tool_result":
        return _tool_result_presentation(details)
    if operation == "agent_message":
        return _presentation(
            "output",
            "Agent 确定最终回复",
            "Harness 已确定本轮最终回复；正文已在页面顶部展示，随后进入发送流程。",
        )
    if operation == "output_guard":
        return _presentation(
            "output",
            "回复经过输出安全处理",
            "输出保护器发现需要调整的内容，并在发送前完成了处理。",
        )
    if operation == "approval_request":
        return _presentation("action", "等待操作确认", "这项操作需要用户确认后才能执行。")
    if operation == "approval_result":
        return _presentation("observation", "收到确认结果", "Harness 已收到用户的确认或取消结果。")
    if operation == "memory_compaction":
        return _presentation("context", "整理长期记忆", "系统完成了一次记忆压缩或生命周期处理。")
    if operation == "memory_ingestion":
        return _memory_ingestion_presentation(details)
    if operation == "memory_recall":
        engine_status = str(details.get("engine_status") or "unknown")
        degraded = bool(details.get("degraded"))
        return {
            **_presentation(
                "context",
                "筛选本轮相关记忆",
                (
                    f"从 {details.get('candidate_count', 0)} 条数据库候选中选择了 "
                    f"{details.get('selected_count', 0)} 条。"
                    f"{details.get('reason_summary') or ''}"
                ),
            ),
            "facts": [
                {"label": "语义索引", "value": engine_status},
                {
                    "label": "Mem0 候选",
                    "value": _display(details.get("engine_candidate_count")),
                },
                {"label": "召回状态", "value": "降级" if degraded else "正常"},
            ],
        }
    if operation == "error":
        return _presentation(
            "system",
            "Harness 执行失败",
            "本轮未能正常完成，可展开技术详情查看错误码。",
        )
    return _presentation(
        "system",
        _humanize_identifier(operation),
        "这是 Harness 持久化的一项运行事件。",
    )


def _invocation_started_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    role = str(details.get("agent_role") or "unknown")
    role_label = _agent_role_label(role)
    reason = str(details.get("reason_summary") or "工作流需要这个 Agent 处理下一步。")
    artifact_ids = _string_rows(details.get("input_artifact_ids"))
    tool_names = _string_rows(details.get("allowed_tool_names"))
    privacy_scopes = _string_rows(details.get("privacy_scopes"))
    parent_id = details.get("parent_invocation_id")
    facts = [
        {"label": "Agent 角色", "value": role_label},
        {"label": "Agent 版本", "value": _display(details.get("agent_version"))},
        {"label": "执行次数", "value": f"第 {_display(details.get('attempt'))} 次"},
        {"label": "输入产物", "value": f"{len(artifact_ids)} 个引用"},
        {
            "label": "允许的工具",
            "value": "、".join(tool_label(name) for name in tool_names) or "无",
        },
        {
            "label": "可见信息范围",
            "value": "、".join(_privacy_scope_label(scope) for scope in privacy_scopes) or "未声明",
        },
    ]
    if parent_id:
        facts.append({"label": "上游调用", "value": _short_reference(parent_id)})
    return {
        **_presentation(
            "decision",
            f"启动{role_label}",
            f"调用原因：{reason} 系统只提供已声明的信息范围、产物引用和工具权限。",
        ),
        "facts": facts,
    }


def _invocation_result_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    status = str(details.get("status") or "unknown")
    status_label = _invocation_status_label(status)
    failure_code = details.get("failure_code")
    output_artifact_id = details.get("output_artifact_id")
    if status in {"succeeded", "completed", "passed"}:
        summary = "这次 Agent 调用已正常结束"
        if output_artifact_id:
            summary += "，并交付了一个经过类型校验的结构化产物。"
        else:
            summary += "，没有生成新的结构化产物。"
    else:
        summary = "这次 Agent 调用没有正常产出"
        summary += (
            f"，失败代码为 {_humanize_identifier(str(failure_code))}。"
            if failure_code
            else "；工作流会根据策略决定跳过、重试或降级。"
        )
    facts = [
        {"label": "调用状态", "value": status_label},
        {"label": "模型调用", "value": f"{_display(details.get('model_call_count'))} 次"},
        {"label": "工具调用", "value": f"{_display(details.get('tool_call_count'))} 次"},
        {"label": "总 Token", "value": _display(details.get("total_token_count"))},
    ]
    if output_artifact_id:
        facts.append({"label": "输出产物", "value": _short_reference(output_artifact_id)})
    if failure_code:
        facts.append({"label": "失败代码", "value": _humanize_identifier(str(failure_code))})
    return {
        **_presentation("observation", f"Agent 调用结果：{status_label}", summary),
        "facts": facts,
    }


def _artifact_created_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    artifact_type = str(details.get("artifact_type") or "unknown")
    producer_role = str(details.get("producer_role") or "unknown")
    parents = _string_rows(details.get("parent_artifact_ids"))
    digest = str(details.get("payload_sha256") or "")
    return {
        **_presentation(
            "observation",
            f"生成结构化产物：{_artifact_type_label(artifact_type)}",
            (
                f"{_agent_role_label(producer_role)}生成了经过 Schema 校验的产物。"
                "Trace 只保存类型、来源引用和内容指纹，不复制敏感正文。"
            ),
        ),
        "facts": [
            {"label": "产物引用", "value": _short_reference(details.get("artifact_id"))},
            {"label": "Schema 版本", "value": _display(details.get("schema_version"))},
            {"label": "上游产物", "value": f"{len(parents)} 个引用"},
            {"label": "内容指纹", "value": _short_digest(digest)},
        ],
    }


def _workflow_transition_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    from_node = str(details.get("from_node") or "unknown")
    to_node = str(details.get("to_node") or "unknown")
    transition_type = str(details.get("transition_type") or "unknown")
    reason_code = str(details.get("reason_code") or "unknown")
    return {
        **_presentation(
            "decision",
            f"工作流进入：{_workflow_node_label(to_node)}",
            (
                f"系统从“{_workflow_node_label(from_node)}”转到"
                f"“{_workflow_node_label(to_node)}”。类型为"
                f"{_transition_type_label(transition_type)}，原因代码为"
                f"{_humanize_identifier(reason_code)}。"
            ),
        ),
        "facts": [
            {"label": "上一阶段", "value": _workflow_node_label(from_node)},
            {"label": "下一阶段", "value": _workflow_node_label(to_node)},
            {"label": "流转类型", "value": _transition_type_label(transition_type)},
            {"label": "执行次数", "value": f"第 {_display(details.get('attempt'))} 次"},
        ],
    }


def _response_adopted_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    mode = str(details.get("mode") or "unknown")
    final = details.get("final") is True
    mode_label = _response_mode_label(mode)
    return {
        **_presentation(
            "output",
            "采用最终回复" if final else "采用候选回复",
            (
                f"Harness 在{mode_label}下采用了这个产物"
                + (
                    "，它将继续进入最终安全检查和发送流程。"
                    if final
                    else "，但尚未作为最终回复发送。"
                )
            ),
        ),
        "facts": [
            {"label": "回复产物", "value": _short_reference(details.get("artifact_id"))},
            {"label": "运行模式", "value": mode_label},
            {"label": "是否最终采用", "value": "是" if final else "否"},
        ],
    }


def _response_degraded_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    reason_code = str(details.get("reason_code") or "unknown")
    fallback_type = str(details.get("fallback_type") or "unknown")
    artifact_id = details.get("artifact_id")
    facts = [
        {"label": "降级原因", "value": _humanize_identifier(reason_code)},
        {"label": "回退方式", "value": _fallback_type_label(fallback_type)},
    ]
    if artifact_id:
        facts.append({"label": "原候选产物", "value": _short_reference(artifact_id)})
    return {
        **_presentation(
            "output",
            "回复进入降级路径",
            (
                f"候选回复因 {_humanize_identifier(reason_code)} 未被直接采用；"
                f"系统改用{_fallback_type_label(fallback_type)}，并保留本次降级记录。"
            ),
        ),
        "facts": facts,
    }


def _context_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    request = _mapping(details.get("request"))
    messages = request.get("messages")
    message_rows = messages if isinstance(messages, list) else []
    contents = [
        str(message.get("content") or "") for message in message_rows if isinstance(message, dict)
    ]
    has_authoritative = any(content.startswith("权威用户事实") for content in contents)
    has_working_memory = any(content.startswith("近期对话工作记忆") for content in contents)
    has_memory_receipt = any(
        content.startswith("权威用户事实") and "current_turn_memory_receipt" in content
        for content in contents
    )
    context_parts = []
    if has_authoritative:
        context_parts.append("长期记忆和权威健康事实")
    if has_working_memory:
        context_parts.append("最近对话工作记忆")
    if has_memory_receipt:
        context_parts.append("本轮记忆变更结果")
    context_text = "、".join(context_parts) if context_parts else "本轮输入和系统约束"
    allowed_tools = details.get("allowed_tool_names")
    tool_count = len(allowed_tools) if isinstance(allowed_tools, list) else 0
    facts = [
        {"label": "模型", "value": _display(request.get("model"))},
        {"label": "上下文消息", "value": f"{len(message_rows)} 条"},
        {"label": "本轮可用工具", "value": f"{tool_count} 个"},
        {"label": "带入的信息", "value": context_text},
    ]
    return {
        **_presentation(
            "context",
            "Harness 整理本轮上下文",
            f"已把{context_text}整理成冻结快照，再交给模型判断。",
        ),
        "facts": facts,
    }


def _model_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    message = _mapping(details.get("message"))
    tool_calls_value = message.get("tool_calls")
    tool_calls = tool_calls_value if isinstance(tool_calls_value, list) else []
    usage = _mapping(details.get("usage"))
    purpose = str(details.get("purpose") or "")
    facts = [
        {"label": "调用用途", "value": _model_purpose_label(purpose)},
        {"label": "第几次模型调用", "value": _display(details.get("call_index"))},
        {"label": "输入 Token", "value": _display(usage.get("input_tokens"))},
        {"label": "输出 Token", "value": _display(usage.get("output_tokens"))},
        {"label": "结束原因", "value": _display(details.get("finish_reason"))},
    ]
    if tool_calls:
        names = [
            tool_label(str(call.get("name") or "unknown"))
            for call in tool_calls
            if isinstance(call, dict)
        ]
        return {
            **_presentation(
                "decision",
                (
                    "模型提取需要写入的长期记忆"
                    if purpose == "memory_ingestion"
                    else "模型选择本轮相关记忆"
                    if purpose == "memory_recall"
                    else "模型选择下一步动作"
                ),
                (
                    "记忆摄取模型根据用户原话提出写入："
                    if purpose == "memory_ingestion"
                    else "召回模型明确提交筛选结果："
                    if purpose == "memory_recall"
                    else "模型明确请求调用："
                )
                + "、".join(names)
                + "。这不是推测的隐藏思维。",
            ),
            "facts": facts,
        }
    content = message.get("content")
    return {
        **_presentation(
            "decision",
            (
                "记忆摄取模型未发现需要更新的资料"
                if purpose == "memory_ingestion"
                else "模型形成回复"
            ),
            (
                "数据库记忆保持不变。"
                if purpose == "memory_ingestion"
                else (
                    _quote(content) if isinstance(content, str) else "模型结束本轮判断并返回文本。"
                )
            ),
        ),
        "facts": facts,
    }


def _model_purpose_label(purpose: str) -> str:
    return {
        "memory_ingestion": "提取并核对长期记忆",
        "memory_recall": "筛选本轮相关记忆",
        "harness_turn": "理解用户并形成回复",
        "vision_inspection": "识别图片内容",
    }.get(purpose, _humanize_identifier(purpose) if purpose else "未标注")


def _tool_result_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    execution = _mapping(details.get("execution"))
    tool_name = str(execution.get("tool_name") or details.get("tool_name") or "unknown")
    result = _mapping(execution.get("result"))
    failure = _mapping(result.get("failure"))
    succeeded = result.get("status") == "succeeded"
    summary = (
        "工具执行成功，结果作为新的观察返回给模型，供下一次判断使用。"
        if succeeded
        else f"工具执行失败：{failure.get('message') or '未提供失败说明'}"
    )
    facts = [
        {"label": "执行策略", "value": _display(execution.get("policy_decision"))},
        {"label": "结果", "value": "成功" if succeeded else "失败"},
    ]
    facts.extend(_facts(_mapping(result.get("output"))))
    return {
        **_presentation(
            "observation",
            f"观察到工具结果：{tool_label(tool_name)}",
            summary,
        ),
        "facts": facts,
    }


def _memory_ingestion_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    raw_changes = details.get("changes")
    changes = raw_changes if isinstance(raw_changes, list) else []
    facts: list[dict[str, str]] = []
    summaries: list[str] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        key = str(change.get("key") or "unknown")
        action = str(change.get("action") or "unknown")
        current = _mapping(change.get("current_value"))
        previous = _mapping(change.get("previous_value"))
        current_text = _memory_value_display(key, current)
        previous_text = _memory_value_display(key, previous) if previous else "—"
        label = _memory_label(key)
        if action == "updated":
            value = f"{previous_text} → {current_text}"
            detail = "已更新"
            summaries.append(f"{label}从 {previous_text} 更新为 {current_text}")
        elif action == "created":
            value = current_text
            detail = "新保存"
            summaries.append(f"新保存{label} {current_text}")
        elif action == "unchanged":
            value = current_text
            detail = "与原记录相同"
            summaries.append(f"{label}保持 {current_text} 不变")
        else:
            value = current_text
            detail = action
        facts.append({"label": label, "value": value, "detail": detail})
    failure_codes = details.get("failure_codes")
    failures = failure_codes if isinstance(failure_codes, list) else []
    if summaries:
        summary = "；".join(summaries) + "。"
    elif failures:
        summary = "记忆摄取没有完整成功，可展开技术详情查看失败原因。"
    else:
        summary = "已核对数据库，本轮没有新增或变更长期记忆。"
    return {
        **_presentation("observation", "核对本轮长期记忆变更", summary),
        "facts": facts,
    }


def _span_summary(operation: str, details: Mapping[str, Any], status: str) -> str:
    if status in {"failed", "unknown"}:
        return "这个系统步骤没有正常完成，请结合错误信息和技术详情排查。"
    if operation == "ensure_agent_control":
        return "系统确认企业微信会话目前允许 SlimGuard 自动回复。"
    if operation in {"generate_reply", "generate_scheduled_reply"}:
        return "应用把请求交给 Harness；内部的上下文、模型动作和工具观察会在后续步骤展开。"
    if operation == "download_media":
        return "图片已经从企业微信下载，随后交给受控的图片识别工具处理。"
    if operation in {"send_text", "send_proactive_text"}:
        return "最终文本已提交给企业微信发送接口。"
    if operation == "turn_finished":
        return "Harness 已结束本轮运行，并给出明确的结束状态。"
    if operation == "job_skipped":
        return "提醒任务因当前条件不满足而未继续生成或发送。"
    return "系统完成了这个运行步骤。"


def _agent_role_label(role: str) -> str:
    return AGENT_ROLE_LABELS.get(role, _humanize_identifier(role))


def _artifact_type_label(artifact_type: str) -> str:
    return ARTIFACT_TYPE_LABELS.get(artifact_type, _humanize_identifier(artifact_type))


def _workflow_node_label(node: str) -> str:
    return WORKFLOW_NODE_LABELS.get(node, _humanize_identifier(node))


def _privacy_scope_label(scope: str) -> str:
    return {
        "current_user_message": "当前用户消息",
        "working_memory": "近期对话",
        "durable_memory": "相关长期记忆",
        "health_records": "相关健康记录",
        "image_observations": "图片观察结果",
        "knowledge_sources": "专业资料",
        "style_examples": "脱敏风格示例",
    }.get(scope, _humanize_identifier(scope))


def _invocation_status_label(status: str) -> str:
    return {
        "started": "执行中",
        "succeeded": "成功",
        "completed": "完成",
        "passed": "通过",
        "failed": "失败",
        "timed_out": "超时",
        "cancelled": "已取消",
        "skipped": "已跳过",
    }.get(status, _humanize_identifier(status))


def _transition_type_label(transition_type: str) -> str:
    return {
        "route": "正常流转",
        "retry": "重试",
        "repair": "返回修复",
        "skip": "跳过",
        "fallback": "进入降级",
        "complete": "完成",
    }.get(transition_type, _humanize_identifier(transition_type))


def _response_mode_label(mode: str) -> str:
    return {
        "legacy": "现有单 Agent 模式",
        "shadow": "影子模式",
        "canary": "灰度模式",
        "multi_agent": "多 Agent 模式",
        "on": "多 Agent 模式",
    }.get(mode, _humanize_identifier(mode))


def _fallback_type_label(fallback_type: str) -> str:
    return {
        "legacy_response": "现有回复",
        "neutral_renderer": "中性模板回复",
        "safe_response": "安全兜底回复",
        "no_response": "不发送回复",
    }.get(fallback_type, _humanize_identifier(fallback_type))


def _string_rows(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _short_reference(value: Any) -> str:
    rendered = str(value or "")
    if not rendered:
        return "—"
    return rendered if len(rendered) <= 24 else rendered[:12] + "…" + rendered[-6:]


def _short_digest(value: str) -> str:
    if not value:
        return "—"
    return value if len(value) <= 18 else value[:16] + "…"


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, _humanize_identifier(name))


def _presentation(stage: str, title: str, summary: str) -> dict[str, Any]:
    return {"stage": stage, "title": title, "summary": summary, "facts": []}


def _facts(values: Mapping[str, Any], *, limit: int = 8) -> list[dict[str, str]]:
    return [
        {"label": FIELD_LABELS.get(key, _humanize_identifier(key)), "value": _display(value)}
        for key, value in list(values.items())[:limit]
        if key not in {"request", "message", "execution"}
    ]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _quote(value: str, *, limit: int = 220) -> str:
    normalized = " ".join(value.split())
    clipped = normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"
    return f"“{clipped}”"


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if value is True:
        return "是"
    if value is False:
        return "否"
    if isinstance(value, str):
        return value if len(value) <= 180 else value[:179] + "…"
    if isinstance(value, (int, float)):
        return str(value)
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return rendered if len(rendered) <= 180 else rendered[:179] + "…"


def _humanize_identifier(value: str) -> str:
    return value.replace("_", " ")


def _embedded_json(content: str) -> Mapping[str, Any]:
    _, separator, payload = content.partition("：")
    if not separator:
        return {}
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return _mapping(value)


def _memory_source_item(row: Mapping[str, Any]) -> dict[str, str]:
    key = str(row.get("key") or "unknown")
    value = _mapping(row.get("value"))
    display = _memory_value_display(key, value)
    return {
        "label": _memory_label(key),
        "value": display,
        "detail": "已保存" + (" · 待复核" if row.get("stale") else ""),
    }


def _memory_change_source_item(row: Mapping[str, Any]) -> dict[str, str]:
    key = str(row.get("key") or "unknown")
    action = str(row.get("action") or "unknown")
    current = _memory_value_display(key, _mapping(row.get("current_value")))
    previous_value = row.get("previous_value")
    previous = (
        _memory_value_display(key, _mapping(previous_value))
        if isinstance(previous_value, Mapping)
        else None
    )
    details = {
        "created": "本轮新保存",
        "updated": "本轮已更新",
        "unchanged": "与原记录相同",
    }
    return {
        "label": _memory_label(key),
        "value": (f"{previous} → {current}" if action == "updated" and previous else current),
        "detail": details.get(action, _humanize_identifier(action)),
    }


def _memory_label(key: str) -> str:
    labels = {
        "identity.preferred_name": "常用称呼",
        "profile.height": "身高",
        "profile.exercise_habit": "运动习惯",
        "coaching.response_style": "回复风格",
        "food.preference": "饮食偏好",
        "exercise.preference": "运动偏好",
        "goal.target_weight": "目标体重",
        "goal.target_body_fat": "目标体脂",
        "goal.behavior": "行为目标",
        "constraint.dietary": "饮食限制",
        "constraint.exercise": "运动限制",
        "constraint.health_context": "健康背景",
    }
    return labels.get(key, _humanize_identifier(key))


def _memory_value_display(key: str, value: Mapping[str, Any]) -> str:
    if key == "profile.height" and isinstance(value.get("millimeters"), (int, float)):
        return f"{float(value['millimeters']) / 10:g} cm"
    if key == "goal.target_weight" and isinstance(value.get("grams"), (int, float)):
        return f"{float(value['grams']) / 1000:g} kg"
    if key == "goal.target_body_fat" and isinstance(value.get("basis_points"), (int, float)):
        return f"{float(value['basis_points']) / 100:g}%"
    return _display(value)


def _weight_source_items(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    return [
        {
            "label": "体重记录",
            "value": f"{row.get('weight_kg')} kg",
            "detail": str(row.get("measured_at") or ""),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def _meal_source_items(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    return [
        {
            "label": "饮食记录",
            "value": _display(row.get("foods")),
            "detail": str(row.get("occurred_at") or ""),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def _body_fat_source_items(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    return [
        {
            "label": "体脂记录",
            "value": f"{row.get('body_fat_percent')}%",
            "detail": str(row.get("measured_at") or ""),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def _exercise_source_items(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    return [
        {
            "label": "运动记录",
            "value": str(row.get("activity_name") or "未命名运动"),
            "detail": str(row.get("occurred_at") or ""),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def _working_source_items(working: Mapping[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    dialogue = working.get("recent_dialogue")
    dialogue_rows = dialogue if isinstance(dialogue, list) else []
    for turn_number, turn in enumerate(dialogue_rows, start=1):
        if not isinstance(turn, dict):
            continue
        messages = turn.get("messages")
        message_rows = messages if isinstance(messages, list) else []
        for message in message_rows:
            if not isinstance(message, dict):
                continue
            role = "用户" if message.get("role") == "user" else "助手"
            items.append(
                {
                    "label": f"近期对话 {turn_number} · {role}",
                    "value": str(message.get("content") or ""),
                    "detail": "对话原文，不是长期记忆",
                }
            )
    handoff = working.get("active_handoff")
    if isinstance(handoff, dict):
        items.append(
            {
                "label": "跨轮待办",
                "value": str(handoff.get("objective") or ""),
                "detail": "完成或过期后失效",
            }
        )
    images = working.get("recent_images")
    if isinstance(images, list):
        items.append(
            {
                "label": "近期图片",
                "value": f"{len(images)} 张",
                "detail": "短期图片引用",
            }
        )
    pending = working.get("pending_user_confirmations")
    if isinstance(pending, list):
        items.append(
            {
                "label": "待确认操作",
                "value": f"{len(pending)} 项",
                "detail": "等待用户确认",
            }
        )
    return items


def _other_authoritative_source_items(
    authoritative: Mapping[str, Any],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    profile = authoritative.get("profile")
    if isinstance(profile, dict):
        if profile.get("nickname"):
            items.append(
                {
                    "label": "账户昵称",
                    "value": str(profile["nickname"]),
                    "detail": "用户账户资料",
                }
            )
        if profile.get("first_seen_at"):
            items.append(
                {
                    "label": "首次出现时间",
                    "value": str(profile["first_seen_at"]),
                    "detail": "用户账户资料",
                }
            )
    schedule = authoritative.get("checkin_schedule")
    if isinstance(schedule, dict):
        items.append(
            {
                "label": "提醒计划",
                "value": _display(schedule),
                "detail": "当前生效的提醒设置",
            }
        )
    checkin = authoritative.get("today_checkin_status")
    if isinstance(checkin, dict):
        items.append(
            {
                "label": "今日打卡状态",
                "value": _display(checkin),
                "detail": "根据当天记录计算",
            }
        )
    return items
