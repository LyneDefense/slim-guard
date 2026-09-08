"""Synthetic export fixtures only; no real chat content is stored in these tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from slim_guard.style_corpus import merge_messages
from slim_guard.wechat_style_export import MAX_EXPORT_BYTES, StyleExportError, prepare_html_export

NOW = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
TARGET = "测试风格账号"


def message(mid, sender, text, *, seconds=0, kind="text", media=None):
    instant = NOW + timedelta(seconds=seconds)
    return {
        "id": mid,
        "sender": sender,
        "text": text,
        "type": kind,
        "role": "peer",
        "timestamp": int(instant.timestamp()),
        "time": instant.strftime("%Y-%m-%d %H:%M:%S"),
        "media": media or {},
    }


def html(*messages, extra=""):
    return (
        extra
        + '<script id="chat-data" type="application/json">'
        + json.dumps({"meta": {"peer": "测试私密群名"}, "messages": messages}, ensure_ascii=False)
        + "</script>"
    ).encode()


def prepare(*messages, **kwargs):
    return prepare_html_export(html(*messages), target_sender=TARGET, **kwargs)


def test_explicit_addressees_select_their_own_context_and_segment_different_targets():
    result = prepare(
        message(1, "测试甲", "甲的测试问题"),
        message(2, "测试乙", "乙的测试问题", seconds=1),
        message(3, TARGET, "@测试甲 收到你的问题。", seconds=2),
        message(4, TARGET, "接着补一句。", seconds=3),
        message(5, TARGET, "@测试乙 你的问题另看。", seconds=4),
    )
    assert len(result.pairs) == 2
    first, second = result.pairs
    assert first.eligible_for_judgment and second.eligible_for_judgment
    assert len(first.message_ids) == 2 and len(second.message_ids) == 1
    assert "甲的测试问题" in first.context
    assert "乙的测试问题" not in first.context
    assert "乙的测试问题" in second.context
    assert first.context_sender_alias != second.context_sender_alias
    assert first.segment_id != second.segment_id
    exported = result.to_export_messages()
    assert len(merge_messages(exported)) == 4
    assert exported[0].conversation_id != exported[2].conversation_id
    assert exported[1].sender == "[风格源]"


def test_same_person_interruption_and_long_time_gap_end_segments():
    result = prepare(
        message(1, "测试甲", "测试问题"),
        message(2, TARGET, "@测试甲 收到。", seconds=1),
        message(3, "测试乙", "不同人的插话。", seconds=2),
        message(4, TARGET, "@测试甲 继续。", seconds=3),
        message(5, TARGET, "@测试甲 很久后补充。", seconds=900),
    )
    assert len(result.pairs) == 3
    assert all(len(pair.message_ids) == 1 for pair in result.pairs)


def test_adjacency_and_unknown_mentions_remain_pending_without_fabricated_understanding():
    result = prepare(
        message(1, "测试甲", "测试问题"),
        message(2, TARGET, "收到。", seconds=1),
        message(3, TARGET, "@测试未知对象 另一个回复。", seconds=2),
    )
    assert "implicit_adjacency" in result.pairs[0].pending_reasons
    assert "unresolved_mention" in result.pairs[1].pending_reasons
    assert not any(pair.eligible_for_judgment for pair in result.pairs)
    assert result.to_export_messages() == ()
    assert "测试未知对象" not in result.model_dump_json()


@pytest.mark.parametrize("kind", ["image", "voice", "video", "emoji", "link"])
def test_media_dependencies_are_pending_and_media_paths_are_never_emitted(kind):
    result = prepare(
        message(
            1,
            "测试甲",
            "测试媒体说明",
            kind=kind,
            media={"src": "../../private-secret.jpg", "url": "https://private.invalid/secret"},
        ),
        message(2, TARGET, "@测试甲 我看到了。", seconds=1),
    )
    pair = result.pairs[0]
    assert not pair.eligible_for_judgment
    assert "non_text_context_dependency" in pair.pending_reasons
    assert "private-secret" not in result.model_dump_json()
    assert "private.invalid" not in result.model_dump_json()
    assert pair.context_message_ids


def test_style_source_media_and_textual_image_references_are_not_interpreted():
    result = prepare(
        message(1, "测试甲", "测试问题"),
        message(2, TARGET, "", seconds=1, kind="voice", media={"src": "secret.wav"}),
        message(3, TARGET, "@测试甲 看一下图片再说。", seconds=2),
    )
    assert result.pairs[0].exclusion_reasons == ("non_text_style_source",)
    assert "uninterpreted_media_dependency" in result.pairs[1].pending_reasons


def test_quote_resolves_verified_original_even_when_other_people_intervene():
    result = prepare(
        message(1, "测试甲", "完整的测试问题"),
        message(2, "测试乙", "另一段测试话题", seconds=1),
        message(
            3,
            TARGET,
            "对此回复。",
            seconds=2,
            kind="quote",
            media={"quote_from": "测试甲", "quote_text": "完整的测试问题"},
        ),
    )
    assert result.pairs[0].eligible_for_judgment
    assert "完整的测试问题" in result.pairs[0].context
    assert "另一段" not in result.pairs[0].context


def test_unresolved_or_conflicting_quotes_remain_pending():
    result = prepare(
        message(1, "测试甲", "测试问题"),
        message(2, "测试乙", "另一个问题", seconds=1),
        message(
            3,
            TARGET,
            "@测试乙 回复。",
            seconds=2,
            kind="quote",
            media={"quote_from": "测试甲", "quote_text": "测试问题"},
        ),
        message(
            4,
            TARGET,
            "引用内容没有原文。",
            seconds=3,
            kind="quote",
            media={"quote_from": "测试甲", "quote_text": "并不存在的原文"},
        ),
    )
    assert "quote_mention_conflict" in result.pairs[0].pending_reasons
    assert "unresolved_quote" in result.pairs[1].pending_reasons


def test_old_context_and_multiple_mentions_require_review():
    result = prepare(
        message(1, "测试甲", "测试问题"),
        message(2, "测试乙", "另一个问题", seconds=1),
        message(3, TARGET, "@测试甲 @测试乙 回复。", seconds=4000),
    )
    assert set(result.pairs[0].pending_reasons) == {"context_too_old", "multiple_addressees"}


def test_redaction_removes_personal_identifiers_dates_and_measurements_from_both_sides():
    sensitive = (
        "测试：测试甲(测试昵称) 电话13800138000 test@example.com 2026-01-01 体重77.6kg 七十公斤"
    )
    result = prepare(
        message(1, "测试甲(测试昵称)", sensitive),
        message(2, TARGET, "@测试甲(测试昵称) 我是测试医生，" + sensitive, seconds=1),
    )
    serialized = result.model_dump_json()
    for value in (
        TARGET,
        "测试甲",
        "测试昵称",
        "13800138000",
        "test@example.com",
        "2026-01-01",
        "77.6",
        "七十公斤",
        "我是测试医生",
        "测试私密群名",
    ):
        assert value not in serialized
    assert result.privacy_review_required


def test_html_javascript_and_media_are_never_executed_or_loaded(tmp_path):
    marker = tmp_path / "must-not-exist"
    dangerous = f'<script>require("fs").writeFileSync("{marker}","executed")</script>'
    raw = html(
        message(1, "测试甲", "测试问题"),
        message(2, TARGET, "@测试甲 收到。", seconds=1),
        extra=dangerous,
    )
    result = prepare_html_export(raw, target_sender=TARGET)
    assert result.statistics["messages"] == 2
    assert not marker.exists()
    assert 'require("fs")' not in result.model_dump_json()


@pytest.mark.parametrize(
    "change,code",
    [
        ({"id": True}, "invalid_message_id"),
        ({"sender": " "}, "invalid_sender"),
        ({"type": "executable"}, "unsupported_message_type"),
        ({"timestamp": True}, "invalid_message_timestamp"),
        ({"time": "private bad time"}, "invalid_message_time"),
        ({"timestamp": int(NOW.timestamp()) + 999}, "inconsistent_message_time"),
        ({"role": "unknown"}, "invalid_message_role"),
    ],
)
def test_invalid_export_fields_fail_with_safe_codes(change, code):
    malformed = {**message(1, TARGET, "PRIVATE TEST TEXT"), **change}
    with pytest.raises(StyleExportError, match=code) as caught:
        prepare(malformed)
    assert "PRIVATE" not in str(caught.value)


def test_duplicate_ids_unordered_time_and_oversized_input_are_rejected():
    with pytest.raises(StyleExportError, match="duplicate_message_id"):
        prepare(message(1, "测试甲", "测试"), message(1, TARGET, "测试", seconds=1))
    with pytest.raises(StyleExportError, match="unordered_message_time"):
        prepare(message(1, "测试甲", "测试", seconds=2), message(2, TARGET, "测试", seconds=1))
    with pytest.raises(StyleExportError, match="invalid_export_size"):
        prepare_html_export(b" " * (MAX_EXPORT_BYTES + 1))


def test_provenance_is_stable_digest_bound_and_has_no_original_sender_mapping():
    raw = html(message(1, "测试甲", "测试问题"), message(2, TARGET, "@测试甲 收到。", seconds=1))
    first = prepare_html_export(raw, target_sender=TARGET)
    assert first == prepare_html_export(raw, target_sender=TARGET)
    changed = prepare_html_export(raw + b"\n", target_sender=TARGET)
    assert first.source_sha256 != changed.source_sha256
    assert first.pairs[0].message_ids != changed.pairs[0].message_ids
    assert len(first.source_sha256) == 64
    assert "aliases" not in first.model_dump()


@pytest.mark.parametrize(
    "raw",
    [
        b'<script id="chat-data" src="https://private.invalid/data"></script>',
        b'<script id="chat-data">{}</script><script id="chat-data">{}</script>',
        b'<script id="chat-data">{"messages":[]}',
        b"<html>No embedded data</html>",
        b"\xff\xfe",
    ],
)
def test_missing_duplicate_external_unclosed_or_non_utf8_chat_data_is_rejected(raw):
    with pytest.raises(StyleExportError):
        prepare_html_export(raw)


@pytest.mark.parametrize("sender", ["我", "你", "他", "她", "我们"])
def test_pronoun_sender_labels_do_not_change_first_person_meaning(sender):
    result = prepare(
        message(1, sender, "测试提问"),
        message(2, TARGET, f"@{sender} 我知道，你先记下来，他和她也告诉我们了。", seconds=1),
    )
    reply = result.pairs[0].reply
    assert "我知道" in reply
    assert "你先记下来" in reply
    assert "他和她也告诉我们了" in reply


def test_non_sender_named_doctor_teacher_is_redacted_without_changing_generic_doctor():
    result = prepare(
        message(1, "测试甲", "胡医生和马小明老师说过，可以看医生，先咨询医生。"),
        message(2, TARGET, "@测试甲 可以问胡医生。医生会说明情况，你可以看医生。", seconds=1),
    )
    text = result.pairs[0].context + result.pairs[0].reply
    assert "胡医生" not in text
    assert "马小明老师" not in text
    assert "看医生" in text
    assert "咨询医生" in text
    assert "医生会说明情况" in text
