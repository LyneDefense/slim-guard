"""Deterministic provider fixture; never sends data to a real model."""

import json

from slim_guard.agent_models.gateway import ModelMessage, ModelResponse, ModelUsage

QUESTIONS = [
    "你的名字是什么",
    "昨日记录有没有保存",
    "能把刚才的说法缩短吗",
    "这段建议的前提是什么",
    "请复述我设置的目标",
    "这张照片能确定配料吗",
    "为什么没有替我记账",
    "请问数据来自哪里",
    "下班回来我忘记称重了",
    "如何理解这里的不确定",
    "请告诉我助手的职责",
    "今天操作失败后该怎么办",
    "刚才的数字单位是什么",
    "这两条说明分别对应谁",
    "你能确认提醒已经关闭了吗",
    "我想了解条件不满足时的情况",
    "那份说明里有什么限制",
    "为什么不能给我确定结论",
    "原文中有哪些注意事项",
    "最后一段说的是哪天",
]


def suite_payload():
    cases = [
        {
            "id": f"case-{i}",
            "family": f"independent-{chr(65 + i)}",
            "user_input": question,
            "context": ["本题为独立合成表达测试"],
            "source_text": "原稿已经确认的内容。",
            "protected_literals": [],
        }
        for i, question in enumerate(QUESTIONS)
    ]
    return {"development": cases[:8], "acceptance": cases[8:]}


class TrainerGateway:
    def __init__(self):
        self.requests = []
        self.fail_schema = None

    async def close(self):
        pass

    async def complete(self, request):
        self.requests.append(request)
        if request.output_schema_name == self.fail_schema:
            raise RuntimeError("test provider offline")
        payload = json.loads(request.messages[1].content)
        schema = request.output_schema_name
        if schema == "AnalysisBatch":
            value = {
                "items": [
                    {
                        "example_id": m["id"],
                        "role": "positive" if m["desired_response"] else "negative",
                        "reason": "保留含义，缩短客套",
                        "expression_rule": "简洁自然",
                    }
                    for m in payload
                ]
            }
        elif schema == "Guide":
            if isinstance(payload[0], dict) and "analysis" in payload[0]:
                value = {
                    "summary": "简洁自然",
                    "rules": [
                        {
                            "rule_id": "concise",
                            "text": "减少无信息铺垫",
                            "boundary": "不删除任何事实或限定",
                            "evidence": [
                                {
                                    "example_id": m["id"],
                                    "revision": m["revision"],
                                    "quote": m["desired_response"] or m["correction_opinion"],
                                }
                                for m in payload
                                if m["analysis"]["role"] in {"positive", "negative"}
                            ],
                            "counterexample_ids": [],
                        }
                    ],
                    "prohibited_phrases": [],
                }
                if not value["rules"][0]["evidence"]:
                    value["rules"] = []
            else:
                evidence = {
                    e["example_id"]: e for p in payload for r in p["rules"] for e in r["evidence"]
                }
                value = {
                    "summary": "简洁自然",
                    "rules": [
                        {
                            "rule_id": "concise",
                            "text": "减少无信息铺垫",
                            "boundary": "不删除任何事实或限定",
                            "evidence": list(evidence.values()),
                            "counterexample_ids": [],
                        }
                    ],
                    "prohibited_phrases": [],
                }
        elif schema == "CandidateProposal":
            ids = [m["id"] for m in payload["eligible_examples"]]
            value = {
                "guide": payload["guide"],
                "example_ids": ids[:1],
                "explanation": "选择一条有证据且互补的表达",
            }
        elif schema == "ConsistencyCheck":
            value = {"passed": True, "issues": []}
        elif schema == "TestSuite":
            value = suite_payload()
        elif schema == "StyledResponse":
            plan = payload["style_context"]["response_plan"]
            value = {
                "text": "\n".join(b["text"] for b in plan["content_blocks"]),
                "style_profile_version": payload["output_requirements"][
                    "style_profile_version_exact"
                ],
                "used_block_ids": [b["block_id"] for b in plan["content_blocks"]],
            }
        elif schema == "ReplyCheck":
            value = {"fidelity_passed": True, "expression_passed": True, "issues": []}
        elif schema == "Comparison":
            scores = {"fidelity": 5, "style_match": 4, "naturalness": 4, "appropriateness": 4}
            value = {
                "winner": "tie",
                "reason": "两者同样忠实且自然，没有明确提升",
                "left_scores": scores,
                "right_scores": scores,
            }
        else:
            raise AssertionError(schema)
        return ModelResponse(
            message=ModelMessage(role="assistant", content=json.dumps(value, ensure_ascii=False)),
            usage=ModelUsage(total_tokens=10),
        )
