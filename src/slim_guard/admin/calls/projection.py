"""Project persisted execution facts; never invent steps, timings, or delivery claims."""

import json
from datetime import UTC, datetime
from typing import Any

from slim_guard.admin.presentation import tool_label

VISIBLE_MUTATIONS = {"health_record", "user_profile", "routine_settings", "user_memory"}
ROLE_LABELS = {
    "core": "Core 编排",
    "coach_response": "医生直接生成",
    "coach_review": "审查医生回复",
    "nutrition_expert": "营养专业评估",
    "response_style": "历史风格改写",
    "response_reviewer": "历史回复审查",
    "dish_recognition": "菜品识别",
}


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def instant(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def duration(start: Any, end: Any) -> int | None:
    a, b = instant(start), instant(end)
    return max(0, int((b - a).total_seconds() * 1000)) if a and b else None


def outputs(data: dict[str, Any]) -> list[dict[str, Any]]:
    persisted = [
        mapping(e.get("details"))
        for e in data.get("timeline", [])
        if e.get("operation") == "agent_message"
        and not e.get("redacted")
        and mapping(e.get("details")).get("participant") in {"coach", "system_assistant"}
    ]
    if persisted:
        return persisted
    artifacts = data.get("artifacts") or mapping(data.get("workflow")).get("artifacts") or []
    for artifact in reversed(artifacts):
        if artifact.get("artifact_type") == "outgoing_messages":
            messages = mapping(artifact.get("payload")).get("messages")
            if isinstance(messages, list):
                return messages
    return []


def business_status(data: dict[str, Any]) -> str:
    # Fold the real operation identity, including later authoritative status queries.
    receipts: dict[str, str] = {}
    observed = False
    for event in data.get("timeline", []):
        execution = mapping(mapping(event.get("details")).get("execution"))
        observed = observed or bool(execution)
        result = mapping(execution.get("result"))
        receipt = mapping(result.get("operation"))
        if execution.get("mutation_kind") in VISIBLE_MUTATIONS:
            if receipt:
                receipts[str(receipt.get("operation_id"))] = str(receipt.get("status", "unknown"))
            else:
                failure = mapping(result.get("failure"))
                receipts[str(execution.get("tool_call_id"))] = (
                    "succeeded"
                    if result.get("status") == "succeeded"
                    else "not_applied"
                    if failure.get("effect_status") == "not_applied"
                    else "unknown"
                )
        resolved = mapping(mapping(result.get("output")).get("operation"))
        if resolved.get("operation_id") in receipts:
            receipts[str(resolved["operation_id"])] = str(resolved.get("status", "unknown"))
    if not receipts:
        return "unknown" if data.get("tool_executions") and not observed else "none"
    states = set(receipts.values())
    if states == {"succeeded"}:
        return "succeeded"
    if "succeeded" in states:
        return "partial"
    if states <= {"not_applied"}:
        return "failed"
    return "unknown"


def summary(data: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    trace = data["trace"]
    invocations = data.get("invocations") or mapping(data.get("workflow")).get("invocations") or []
    messages = outputs(data)
    reply = (
        "succeeded"
        if any(m.get("participant") == "coach" for m in messages)
        else (
            "failed"
            if trace.get("generation_status") in {"failed", "degraded"}
            else "running"
            if trace.get("generation_status") in {"running", "pending"}
            else "unknown"
        )
    )
    repaired = any(i.get("attempt", 1) > 1 for i in invocations) or any(
        mapping(e.get("details")).get("schema")
        for e in data.get("timeline", [])
        if e.get("operation") == "structured_output_rejected"
    )
    running = trace.get("generation_status") in {"running", "pending"}
    main = [c for c in calls if not c.get("job_id") and c.get("category") != "background"]
    failed_calls = any(c.get("status") in {"failed", "cancelled"} for c in main)
    business = business_status(data)
    waiting = any(m.get("requires_response") for m in messages)
    outcome = (
        "running"
        if running
        else "waiting"
        if waiting
        else "partial"
        if business == "succeeded" and reply == "failed"
        else "partial"
        if business == "failed" and reply == "succeeded"
        else "failed"
        if trace.get("generation_status") in {"failed", "degraded"}
        or trace.get("delivery_status") == "failed"
        else "recovered"
        if repaired or failed_calls
        else "completed"
    )
    expected = sum(i.get("model_call_count") or 0 for i in invocations)
    return {
        "business_status": business,
        "reply_status": reply,
        "outcome": outcome,
        "repaired": repaired,
        "has_issues": outcome in {"partial", "failed", "recovered"},
        "model_calls": len(main) if main or not expected else None,
        "tool_calls": len(data.get("tool_executions", [])),
        "background_model_calls": len(calls) - len(main),
        "client_status": "not_reported",
        "delivery_status": trace.get("delivery_status"),
        "coverage": "按实际请求账本计数；历史缺失不补造；外部服务内部调用不在覆盖范围内。",
    }


def project(
    data: dict[str, Any], calls: list[dict[str, Any]], jobs: list[dict[str, Any]]
) -> dict[str, Any]:
    trace = data["trace"]
    timeline = data.get("timeline", [])
    artifacts = data.get("artifacts") or mapping(data.get("workflow")).get("artifacts") or []
    invocations = data.get("invocations") or mapping(data.get("workflow")).get("invocations") or []
    nodes: list[dict[str, Any]] = []

    def node(
        identity: str,
        title: str,
        kind: str,
        *,
        start: Any = None,
        end: Any = None,
        status: str = "completed",
        parent: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        value = {
            "id": identity,
            "title": title,
            "kind": kind,
            "parent_id": parent,
            "started_at": instant(start),
            "completed_at": instant(end),
            "duration_ms": duration(start, end),
            "status": status,
            "summary": "",
            "input": None,
            "output": None,
            "evidence_refs": [],
            "issues": [],
            "raw": None,
            **fields,
        }
        nodes.append(value)
        return value

    node(
        "input",
        "接收消息与防重",
        "input",
        start=trace.get("created_at"),
        input=data.get("input"),
        summary="原始文字与图片附件；重复请求不再次执行业务工具。",
        raw={"trace_id": trace["id"], "inbound_msgid": trace.get("inbound_msgid")},
    )
    source_nodes: dict[str, str | None] = {
        m.get("item_id"): "input" for m in mapping(data.get("input")).get("messages", [])
    }
    source_nodes.update(
        {m.get("asset_id"): "input" for m in mapping(data.get("input")).get("images", [])}
    )
    context_events = [e for e in timeline if e.get("operation") == "context_snapshot"]
    if context_events:
        event = context_events[-1]
        node(
            "context",
            "准备上下文",
            "context",
            start=event.get("started_at"),
            input=data.get("context_sources"),
            output=event.get("details"),
            summary="用户资料、近期对话、任务与相关记忆；快照时间不冒充执行耗时。",
        )
    invocation_ids = {i["invocation_id"] for i in invocations}
    for invocation in invocations:
        identity = invocation["invocation_id"]
        # A review links to a candidate causally, but runs after its generation span.
        # Keep it on the chronological main line; expose parent relation in raw data.
        parent = invocation.get("parent_invocation_id")
        if invocation.get("agent_role") in {"coach_response", "coach_review"}:
            parent = None
        own_artifacts = [a for a in artifacts if a.get("invocation_id") == identity]
        out = next(
            (
                mapping(a.get("payload"))
                for a in own_artifacts
                if a.get("artifact_id") == invocation.get("output_artifact_id")
            ),
            None,
        )
        check = mapping(mapping(out).get("output"))
        issues = check.get("issues") or []
        status = invocation.get("status") or "running"
        rejected = issues or any(
            check.get(key) is False
            for key in ("fidelity_passed", "role_passed", "expression_passed")
        )
        if rejected and status == "succeeded":
            status = (
                "warning"
                if invocation.get("agent_role") == "coach_review"
                and check.get("fidelity_passed") is True
                and check.get("role_passed") is True
                else "rejected"
            )
        node(
            identity,
            ROLE_LABELS.get(invocation.get("agent_role"), invocation.get("agent_role"))
            + (f" · 第 {invocation['attempt']} 次" if invocation.get("attempt", 1) > 1 else ""),
            "invocation",
            start=invocation.get("started_at"),
            end=invocation.get("completed_at"),
            status=status,
            parent=parent if parent in invocation_ids else None,
            summary=invocation.get("failure_code") or invocation.get("reason_summary") or "",
            input=[
                a
                for a in artifacts
                if a.get("artifact_id") in invocation.get("input_artifact_ids", [])
            ],
            output=out,
            evidence_refs=invocation.get("input_artifact_ids", []),
            issues=issues,
            raw=invocation,
        )
    core_ids = [i["invocation_id"] for i in invocations if i.get("agent_role") == "core"]
    tool_nodes: dict[str, str] = {}
    for tool in data.get("tool_executions", []):
        call_id = tool.get("tool_call_id")
        identity = f"tool:{call_id}"
        tool_nodes[str(call_id)] = identity
        tool_event: dict[str, Any] = next(
            (
                e
                for e in timeline
                if e.get("operation") == "tool_result"
                and mapping(e.get("details")).get("tool_call_id") == call_id
            ),
            {},
        )
        details = mapping(tool_event.get("details"))
        result = mapping(tool.get("result"))
        execution = mapping(details.get("execution"))
        receipt = mapping(result.get("operation"))
        parent = details.get("invocation_id") or (core_ids[0] if len(core_ids) == 1 else None)
        node(
            identity,
            tool_label(str(tool.get("tool_name"))),
            "tool",
            parent=parent,
            start=details.get("started_at") or tool.get("created_at"),
            end=details.get("completed_at") or tool.get("completed_at"),
            status=tool.get("status", "unknown"),
            input=tool.get("arguments"),
            output=result,
            summary=(
                f"操作回执：{receipt.get('status')} · {receipt.get('operation_id')}"
                if receipt
                else "只读／内部操作，或未创建业务操作；以实际输出为准。"
            ),
            raw={**tool, "mutation_kind": execution.get("mutation_kind")},
        )
        source_nodes[call_id] = identity
        for ref in result.get("source_ids", []):
            source_nodes[ref] = identity
    exchanges = {
        mapping(e.get("details")).get("call_id"): e
        for e in timeline
        if e.get("operation") == "model_exchange"
    }
    attempt_numbers: dict[str, int] = {}
    for call in calls:
        if call.get("job_id") or call.get("category") == "background":
            continue
        snapshot = mapping(mapping(exchanges.get(call["id"])).get("details"))
        parent = tool_nodes.get(str(call.get("tool_call_id")))
        if parent is None and call.get("invocation_id") in invocation_ids:
            parent = call["invocation_id"]
        if parent is None and call.get("component") == "memory_recall" and context_events:
            parent = "context"
        counter_key = str(call.get("invocation_id") or call.get("component"))
        attempt_numbers[counter_key] = attempt_numbers.get(counter_key, 0) + 1
        call_title = (
            "Core"
            if call.get("invocation_id") in core_ids
            else "记忆筛选"
            if call.get("component") == "memory_recall"
            else "模型请求"
        )
        node(
            f"model:{call['id']}",
            f"{call_title} · 第 {attempt_numbers[counter_key]} 次请求",
            "model",
            start=call.get("started_at"),
            end=call.get("completed_at"),
            status=call.get("status") or "unknown",
            parent=parent,
            summary=call.get("error_type") or str(call.get("model", "")),
            input=snapshot.get("input"),
            output=snapshot.get("output"),
            raw=call,
        )
    for event in timeline:
        if event.get("operation") != "structured_output_rejected":
            continue
        rejected = mapping(event.get("details"))
        try:
            errors = json.loads(rejected.get("errors_json") or "[]")
        except (ValueError, TypeError):
            errors = rejected.get("errors_json")
        node(
            f"validation:{event['id']}",
            "输出格式校验反馈",
            "validation",
            start=event.get("started_at"),
            status="rejected",
            output=errors,
            summary=f"{rejected.get('schema')} · 第 {rejected.get('attempt')} 次校验未通过",
            raw=event,
        )
    for artifact in artifacts:
        identity = artifact["artifact_id"]
        if artifact.get("artifact_type") in {"coach_evidence", "style_resolution"}:
            node(
                identity,
                "冻结本轮证据"
                if artifact["artifact_type"] == "coach_evidence"
                else "加载已发布医生风格",
                "snapshot",
                start=artifact.get("created_at"),
                output=artifact.get("payload"),
                evidence_refs=artifact.get("parent_artifact_ids", []),
                summary="读取与保存快照，不是额外的模型调用。",
                raw=artifact,
            )
        else:
            source_nodes[identity] = artifact.get("invocation_id")
    messages = outputs(data)
    node(
        "delivery",
        "保存最终输出与返回",
        "delivery",
        start=mapping(data.get("output")).get("attempt_started_at"),
        end=trace.get("completed_at"),
        status=trace.get("delivery_status", "unknown"),
        output=messages or data.get("output"),
        summary="服务返回不等于客户端确认接收，也不等于用户已阅读。",
        raw=data.get("output"),
    )
    # Do not turn a broad outbound request interval into exclusive delivery duration.
    nodes[-1]["duration_ms"] = None
    nodes[-1]["started_at"] = instant(trace.get("completed_at"))
    known = {n["id"] for n in nodes}
    sources = [
        {"id": key, "node_id": target if target in known else None}
        for key, target in source_nodes.items()
        if key
    ]
    nodes.sort(
        key=lambda n: (instant(n.get("started_at")) or datetime.max.replace(tzinfo=UTC), n["id"])
    )
    return {
        "schema_version": "1",
        "summary": summary(data, calls),
        "nodes": nodes,
        "sources": sources,
        "artifacts": artifacts,
        "messages": messages,
        "background_jobs": jobs,
        "background_calls": [
            c for c in calls if c.get("job_id") or c.get("category") == "background"
        ],
    }
