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
    "get_recent_meals": "查询近期饮食",
    "get_recent_weight_trend": "查询近期体重趋势",
    "inspect_image": "识别图片内容",
    "list_user_memories": "读取用户记忆",
    "record_exercise": "记录运动",
    "record_meal": "记录饮食",
    "record_user_constraint": "保存用户限制条件",
    "record_weight": "记录体重",
    "resolve_conversation_handoff": "完成跨轮待办",
    "resolve_pending_user_action": "处理待确认操作",
    "set_behavior_goal": "设置行为目标",
    "set_body_profile": "保存身高档案",
    "set_coaching_profile": "更新陪伴偏好",
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
    return {
        "architecture": "harness" if context_snapshots or model_calls else "service",
        "model_call_count": model_calls,
        "tool_call_count": tool_calls,
        "observation_count": observations,
        "context_snapshot_count": context_snapshots,
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
    health_items: list[dict[str, str]] = []
    health_items.extend(_weight_source_items(authoritative.get("recent_weights")))
    health_items.extend(_meal_source_items(authoritative.get("recent_meals")))
    health_items.extend(_exercise_source_items(authoritative.get("recent_exercise")))
    other_authoritative_items = _other_authoritative_source_items(authoritative)
    working_items = _working_source_items(working)
    return [
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


def _present_agent_item(operation: str, details: Mapping[str, Any]) -> dict[str, Any]:
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


def _context_presentation(details: Mapping[str, Any]) -> dict[str, Any]:
    request = _mapping(details.get("request"))
    messages = request.get("messages")
    message_rows = messages if isinstance(messages, list) else []
    contents = [
        str(message.get("content") or "")
        for message in message_rows
        if isinstance(message, dict)
    ]
    has_authoritative = any(content.startswith("权威用户事实") for content in contents)
    has_working_memory = any(content.startswith("近期对话工作记忆") for content in contents)
    context_parts = []
    if has_authoritative:
        context_parts.append("长期记忆和权威健康事实")
    if has_working_memory:
        context_parts.append("最近对话工作记忆")
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
    facts = [
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
                "模型选择下一步动作",
                "模型明确请求调用：" + "、".join(names) + "。这不是推测的隐藏思维。",
            ),
            "facts": facts,
        }
    content = message.get("content")
    return {
        **_presentation(
            "decision",
            "模型形成回复",
            _quote(content) if isinstance(content, str) else "模型结束本轮判断并返回文本。",
        ),
        "facts": facts,
    }


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
    labels = {
        "identity.preferred_name": "常用称呼",
        "profile.height": "身高",
        "coaching.response_style": "回复风格",
        "food.preference": "饮食偏好",
        "exercise.preference": "运动偏好",
        "goal.target_weight": "目标体重",
        "goal.behavior": "行为目标",
        "constraint.dietary": "饮食限制",
        "constraint.exercise": "运动限制",
        "constraint.health_context": "健康背景",
    }
    if key == "profile.height" and isinstance(value.get("millimeters"), (int, float)):
        display = f"{float(value['millimeters']) / 10:g} cm"
    elif key == "goal.target_weight" and isinstance(value.get("grams"), (int, float)):
        display = f"{float(value['grams']) / 1000:g} kg"
    else:
        display = _display(value)
    return {
        "label": labels.get(key, _humanize_identifier(key)),
        "value": display,
        "detail": "已保存" + (" · 待复核" if row.get("stale") else ""),
    }


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
