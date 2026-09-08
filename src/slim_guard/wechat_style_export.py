"""Parse embedded WeChat JSON without executing HTML or loading media/network assets.

Preparation is local and unapproved. Eligibility means an explicit context link was
located; it does not attest medical correctness, privacy clearance or style approval.
Only pseudonyms, redacted text and digest-bound provenance leave this adapter.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Literal

from pydantic import Field

from slim_guard.agents.contracts import ContractModel
from slim_guard.style_corpus import ExportMessage, redact

MAX_EXPORT_BYTES = 8 * 1024 * 1024
_TYPES = frozenset({"text", "quote", "image", "voice", "video", "emoji", "link", "system"})
_MEDIA_TYPES = frozenset({"image", "voice", "video", "emoji"})


class StyleExportError(ValueError):
    """Safe error codes deliberately exclude raw input, identities and local paths."""


class PreparedStylePair(ContractModel):
    pair_id: str
    source_sha256: str
    segment_id: str
    context: str
    reply: str
    message_ids: tuple[str, ...]
    context_message_ids: tuple[str, ...]
    eligible_for_judgment: bool
    pending_reasons: tuple[str, ...] = ()
    exclusion_reasons: tuple[str, ...] = ()
    reply_type: str
    context_sender_alias: str | None = None


class PreparedExport(ContractModel):
    schema_version: Literal["1"] = "1"
    privacy_review_required: Literal[True] = True
    source_sha256: str
    pairs: tuple[PreparedStylePair, ...]
    statistics: dict[str, int] = Field(default_factory=dict)
    exclusion_counts: dict[str, int] = Field(default_factory=dict)

    def to_export_messages(self) -> tuple[ExportMessage, ...]:
        """Compatibility only: unique conversation IDs prevent cross-pair merging.

        Prefer importing prepared pairs directly to retain lineage and pending reasons.
        This adapter emits explicitly linked pairs only; it does not approve them.
        """
        return tuple(
            message
            for pair in self.pairs
            if pair.eligible_for_judgment
            for message in (
                ExportMessage(
                    sender=pair.context_sender_alias or "[参与者]",
                    text=pair.context,
                    conversation_id=pair.segment_id,
                ),
                ExportMessage(sender="[风格源]", text=pair.reply, conversation_id=pair.segment_id),
            )
        )


class _EmbeddedData(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.capturing = False
        self.scripts = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "chat-data":
            self.scripts += 1
            if self.scripts != 1 or dict(attrs).get("src"):
                raise StyleExportError("invalid_chat_data_script")
            self.capturing = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.capturing = False

    def handle_data(self, data: str) -> None:
        if self.capturing:
            self.parts.append(data)


@dataclass(frozen=True, slots=True)
class _Message:
    id: int
    sender: str
    timestamp: int
    type: str
    text: str
    quote_from: str = ""
    quote_text: str = ""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse(html: bytes, offset: int) -> tuple[_Message, ...]:
    if not html or len(html) > MAX_EXPORT_BYTES:
        raise StyleExportError("invalid_export_size")
    if isinstance(offset, bool) or not isinstance(offset, int) or not -12 <= offset <= 14:
        raise StyleExportError("invalid_timezone_offset")
    parser = _EmbeddedData()
    try:
        parser.feed(html.decode("utf-8-sig"))
        parser.close()
        raw = json.loads("".join(parser.parts))
    except (UnicodeError, ValueError, RecursionError):
        raise StyleExportError("invalid_embedded_chat_json") from None
    if parser.scripts != 1 or parser.capturing or not isinstance(raw, dict):
        raise StyleExportError("invalid_chat_data_structure")
    rows = raw.get("messages")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50_000:
        raise StyleExportError("invalid_message_count")
    messages: list[_Message] = []
    seen: set[int] = set()
    tz = timezone(timedelta(hours=offset))
    for row in rows:
        if not isinstance(row, dict):
            raise StyleExportError("invalid_message_structure")
        message_id = row.get("id")
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 0:
            raise StyleExportError("invalid_message_id")
        if message_id in seen:
            raise StyleExportError("duplicate_message_id")
        seen.add(message_id)
        sender, kind, text, stamp = (
            row.get(key) for key in ("sender", "type", "text", "timestamp")
        )
        if not isinstance(sender, str) or not sender.strip() or len(sender) > 256:
            raise StyleExportError("invalid_sender")
        if not isinstance(kind, str) or kind not in _TYPES:
            raise StyleExportError("unsupported_message_type")
        if row.get("role") not in {"me", "peer", "system"}:
            raise StyleExportError("invalid_message_role")
        if not isinstance(text, str) or len(text) > 16_000:
            raise StyleExportError("invalid_message_text")
        if isinstance(stamp, bool) or not isinstance(stamp, int) or not 0 <= stamp <= 4_102_444_800:
            raise StyleExportError("invalid_message_timestamp")
        try:
            display = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        except (KeyError, ValueError, TypeError):
            raise StyleExportError("invalid_message_time") from None
        if int(display.timestamp()) != stamp:
            raise StyleExportError("inconsistent_message_time")
        if messages and stamp < messages[-1].timestamp:
            raise StyleExportError("unordered_message_time")
        media = row.get("media", {})
        if not isinstance(media, dict):
            raise StyleExportError("invalid_media_metadata")
        quote_from, quote_text = media.get("quote_from", ""), media.get("quote_text", "")
        if any(
            not isinstance(item, str) or len(item) > 16_000 for item in (quote_from, quote_text)
        ):
            raise StyleExportError("invalid_quote_metadata")
        messages.append(
            _Message(
                message_id,
                sender.strip(),
                stamp,
                kind,
                text,
                quote_from.strip().rstrip(":："),
                quote_text,
            )
        )
    return tuple(messages)


_MENTION = re.compile(r"@[^\s\u2005\u2006，。！？、:：;；@]+")
_NUMBERS = re.compile(
    r"\d+(?:[.,:/年月日时点分秒%-]\d+)*(?:年|月|日|岁|kg|KG|斤|公斤|cm|厘米|%|％)?"
)
_NAMED = re.compile(r"(?:姓名|我叫|昵称|地址|住址|电话)\s*[:：]?\s*[^\s，。；！？]{1,80}")
_IDENTITY = re.compile(r"(?:我是|作为|本人是|我叫)[^\s，。！？]{0,12}(?:医生|医师|大夫)")
_DEPENDENCY = re.compile(r"图片|照片|这张|上图|下图|看图|语音|视频|听一下|看一下图")
_CHINESE_QUANTITY = re.compile(
    r"[零〇一二两三四五六七八九十百千万点半]+(?:公斤|斤|厘米|米|岁|年|月|日)"
)
_PRONOUN_LABELS = frozenset(
    {"我", "你", "您", "他", "她", "它", "我们", "你们", "他们", "大家", "本人"}
)
_NAMED_TITLE = re.compile(
    r"(?:欧阳|司马|上官|诸葛|[赵钱孙李周吴郑王冯陈蒋沈韩杨朱秦许何吕张曹严金魏姜谢邹章苏潘范彭鲁马"
    r"方俞任袁柳史唐薛雷贺倪汤罗郝安傅齐康伍余顾孟黄萧尹姚邵汪毛戴宋庞熊董梁杜阮贾江童颜郭梅"
    r"林钟徐邱高夏蔡田胡霍万卢柯莫陆荣程崔龚黎洪石廖谭肖刘丁曾邓龙叶侯白段易赖武])"
    r"(?:(?!看|问|找|去|请|的|咨询)[\u4e00-\u9fff]){0,2}(?:医生|医师|大夫|老师)"
)


class _Redactor:
    def __init__(self, messages: tuple[_Message, ...], target: str) -> None:
        names = sorted({message.sender for message in messages} - {target})
        self.aliases = {name: f"[参与者{index}]" for index, name in enumerate(names, 1)}
        self.aliases[target] = "[风格源]"
        self.terms = {
            name: alias for name, alias in self.aliases.items() if name not in _PRONOUN_LABELS
        }
        # Export sender labels can contain both a nickname and an account handle.
        for name, alias in self.aliases.items():
            for part in re.split(r"[()（）\[\]【】]", name):
                if len(part.strip()) >= 2 and part.strip() not in _PRONOUN_LABELS:
                    self.terms.setdefault(part.strip(), alias)
        for message in messages:
            if (
                message.quote_from
                and message.quote_from not in self.terms
                and message.quote_from not in _PRONOUN_LABELS
            ):
                self.terms[message.quote_from] = "[引用对象]"
            for mention in _MENTION.findall(message.text):
                if len(mention) > 1 and mention[1:] not in _PRONOUN_LABELS:
                    self.terms.setdefault(mention[1:], "[提及对象]")

    def clean(self, text: str) -> str:
        text = redact(text)
        text = _IDENTITY.sub("[身份自称]", text)
        text = _NAMED_TITLE.sub("[称谓人物]", text)
        for name in sorted(self.terms, key=len, reverse=True):
            text = text.replace(name, self.terms[name])
        text = _MENTION.sub("[提及对象]", text)
        text = _NAMED.sub("[个人信息]", text)
        text = redact(text)
        # Replace numbers without touching our generated pseudonym identifiers.
        tokens: dict[str, str] = {}
        for index, alias in enumerate(self.aliases.values()):
            token = "\ue000" + chr(0xE100 + index) + "\ue001"
            tokens[token] = alias
            text = text.replace(alias, token)
        text = _NUMBERS.sub("[数值]", text)
        text = _CHINESE_QUANTITY.sub("[数值]", text)
        for token, alias in tokens.items():
            text = text.replace(token, alias)
        # Never retain paths or executable markup from messages as assets.
        text = re.sub(r"(?:file://|(?:\./|\.\./|/)[\w\u4e00-\u9fff./%-]+)", "[本地路径]", text)
        text = re.sub(r"<[^>]*>", "[标记]", text)
        return text.strip()

    def targets(self, text: str) -> tuple[tuple[str, ...], bool]:
        remaining = text
        found: list[str] = []
        for name in sorted(self.aliases, key=len, reverse=True):
            if "@" + name in remaining:
                found.append(name)
                remaining = remaining.replace("@" + name, "")
        return tuple(sorted(found)), "@" in remaining


def prepare_html_export(
    html: bytes,
    *,
    target_sender: str = "章之文",
    timezone_offset_hours: int = 8,
    max_reply_gap_seconds: int = 300,
    max_context_age_seconds: int = 3600,
) -> PreparedExport:
    """Prepare de-identified pairs locally; never call a model or fetch linked files."""
    if not target_sender.strip() or min(max_reply_gap_seconds, max_context_age_seconds) < 1:
        raise StyleExportError("invalid_preparation_options")
    messages = _parse(html, timezone_offset_hours)
    if target_sender not in {message.sender for message in messages}:
        raise StyleExportError("target_sender_not_found")
    source_hash = hashlib.sha256(html).hexdigest()
    redactor = _Redactor(messages, target_sender)

    def message_id(message: _Message) -> str:
        return "message-" + _digest(source_hash + ":" + str(message.id))[:24]

    # One group per explicit addressee/type/time interval. Other senders and media
    # always end a group; quote-bearing replies are resolved separately.
    groups: list[tuple[list[_Message], tuple[str, ...], bool]] = []
    previous: _Message | None = None
    for message in messages:
        if message.sender == target_sender:
            targets, unknown = redactor.targets(message.text)
            if (
                previous is not None
                and previous.sender == target_sender
                and groups
                and message.type == previous.type == "text"
                and message.timestamp - previous.timestamp <= max_reply_gap_seconds
                and message.timestamp - groups[-1][0][0].timestamp <= max_reply_gap_seconds
                and sum(len(item.text) + 1 for item in groups[-1][0]) + len(message.text) <= 16_000
                and not unknown
            ):
                previous_targets, previous_unknown = groups[-1][1:]
                effective = targets or previous_targets
                if effective == previous_targets and not previous_unknown:
                    groups[-1][0].append(message)
                else:
                    groups.append(([message], targets, unknown))
            else:
                groups.append(([message], targets, unknown))
        previous = message

    pairs: list[PreparedStylePair] = []
    positions = {message.id: index for index, message in enumerate(messages)}
    recent_by_sender: dict[str, _Message] = {}
    quoted_by_content: dict[tuple[str, str], _Message] = {}
    cursor = 0
    for group, targets, unknown in groups:
        first = group[0]
        first_position = positions[first.id]
        while cursor < first_position:
            earlier = messages[cursor]
            recent_by_sender[earlier.sender] = earlier
            quoted_by_content[(earlier.sender, earlier.text)] = earlier
            cursor += 1
        pending: list[str] = []
        exclusions: list[str] = []
        located: list[_Message] = []
        if first.type not in {"text", "quote"}:
            exclusions.append("non_text_style_source")
        if unknown:
            pending.append("unresolved_mention")
        if first.type == "quote":
            quoted = quoted_by_content.get((first.quote_from, first.quote_text))
            if quoted is not None and first.quote_text.strip():
                located.append(quoted)
                if targets and targets != (quoted.sender,):
                    pending.append("quote_mention_conflict")
            else:
                pending.append("unresolved_quote")
        elif targets:
            if len(targets) != 1:
                pending.append("multiple_addressees")
            for target in targets:
                recent = recent_by_sender.get(target)
                if recent is None:
                    pending.append("no_addressee_context")
                else:
                    located.append(recent)
        elif first_position and messages[first_position - 1].sender != target_sender:
            located.append(messages[first_position - 1])
            pending.append("implicit_adjacency")
        else:
            pending.append("context_not_located")
        if any(message.sender == target_sender for message in located):
            pending.append("self_reference")
        if any(
            first.timestamp - message.timestamp > max_context_age_seconds for message in located
        ):
            pending.append("context_too_old")
        if any(message.type not in {"text", "quote"} for message in located):
            pending.append("non_text_context_dependency")
        if first.type in _MEDIA_TYPES or any(message.type in _MEDIA_TYPES for message in located):
            pending.append("uninterpreted_media_dependency")
        if any(_DEPENDENCY.search(message.text) for message in (*group, *located)):
            pending.append("uninterpreted_media_dependency")
        if not any(message.text.strip() for message in group) and not exclusions:
            exclusions.append("empty_reply")
        context = (
            "\n".join(
                redactor.aliases[message.sender]
                + ": "
                + (
                    redactor.clean(message.text)
                    if message.type in {"text", "quote"}
                    else "[非文字上下文，尚未解析]"
                )
                for message in located
            )
            or "[上下文未定位]"
        )
        reply = (
            "\n".join(redactor.clean(message.text) for message in group)
            if not exclusions
            else ("[非文字或不可用回复，尚未解析]")
        )
        ids = tuple(message_id(message) for message in group)
        segment = "segment-" + _digest("|".join(ids))[:24]
        pairs.append(
            PreparedStylePair(
                pair_id="pair-" + _digest(source_hash + segment)[:24],
                source_sha256=source_hash,
                segment_id=segment,
                context=context,
                reply=reply,
                message_ids=ids,
                context_message_ids=tuple(message_id(message) for message in located),
                eligible_for_judgment=bool(located) and not pending and not exclusions,
                pending_reasons=tuple(sorted(set(pending))),
                exclusion_reasons=tuple(exclusions),
                reply_type=first.type,
                context_sender_alias=(
                    redactor.aliases[located[0].sender] if len(located) == 1 else None
                ),
            )
        )
    reason_counts = Counter(
        reason for pair in pairs for reason in (*pair.pending_reasons, *pair.exclusion_reasons)
    )
    return PreparedExport(
        source_sha256=source_hash,
        pairs=tuple(pairs),
        statistics={
            "messages": len(messages),
            "style_source_messages": sum(message.sender == target_sender for message in messages),
            "pairs": len(pairs),
            "eligible_pairs": sum(pair.eligible_for_judgment for pair in pairs),
            "pending_pairs": sum(bool(pair.pending_reasons) for pair in pairs),
            "excluded_pairs": sum(bool(pair.exclusion_reasons) for pair in pairs),
        },
        exclusion_counts=dict(sorted(reason_counts.items())),
    )


__all__ = [
    "MAX_EXPORT_BYTES",
    "PreparedExport",
    "PreparedStylePair",
    "StyleExportError",
    "prepare_html_export",
]
