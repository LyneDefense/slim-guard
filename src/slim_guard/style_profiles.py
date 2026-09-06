"""Versioned, append-only communication style assets."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1, StyleProfile
from slim_guard.db.models import (
    StyleProfileRecord,
    StyleProfileVersionRecord,
    new_uuid,
    utc_now,
)

if TYPE_CHECKING:
    from slim_guard.db.session import Database


DEFAULT_STYLE_PROFILE_ID = SLIMGUARD_DEFAULT_V1.profile_id
DEFAULT_STYLE_PROFILE_VERSION = SLIMGUARD_DEFAULT_V1.version
DEFAULT_STYLE_PROFILE_VERSION_ID = "style-default-v1"
DEFAULT_STYLE_PROFILE_PROMPT = (
    "Write clear, concise and supportive SlimGuard replies. Preserve every fact, "
    "record status, risk, uncertainty and citation. Never shame, frighten, impersonate "
    "a person, or add medical conclusions."
)
DEFAULT_STYLE_SPEC: dict[str, Any] = {
    "display_name": SLIMGUARD_DEFAULT_V1.display_name,
    "description": SLIMGUARD_DEFAULT_V1.description,
    "tone_rules": list(SLIMGUARD_DEFAULT_V1.tone_rules),
    "prohibited_phrases": list(SLIMGUARD_DEFAULT_V1.prohibited_phrases),
    "preferred_max_paragraphs": SLIMGUARD_DEFAULT_V1.preferred_max_paragraphs,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION_STATUSES = frozenset({"draft", "evaluated", "active", "retired"})


class StyleProfileRepositoryError(RuntimeError):
    pass


class StyleProfileNotFound(StyleProfileRepositoryError):
    pass


class StyleProfileAlreadyExists(StyleProfileRepositoryError):
    pass


class StyleProfileIntegrityError(StyleProfileRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class StyleProfileRef:
    id: str
    stable_name: str
    active_version_id: str | None
    is_default: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StyleProfileAsset:
    """Resolved immutable version, structurally compatible with the Style Agent contract."""

    profile_id: str
    version: str
    display_name: str
    description: str
    tone_rules: tuple[str, ...]
    prohibited_phrases: tuple[str, ...]
    preferred_max_paragraphs: int
    record_id: str
    status: str
    prompt_sha256: str
    source_corpus_sha256: str | None
    created_at: datetime

    def to_profile(self) -> StyleProfile:
        return StyleProfile(
            profile_id=self.profile_id,
            version=self.version,
            display_name=self.display_name,
            description=self.description,
            tone_rules=self.tone_rules,
            prohibited_phrases=self.prohibited_phrases,
            preferred_max_paragraphs=self.preferred_max_paragraphs,
        )


class StyleProfileRepository:
    """Stores immutable versions and resolves mutable stable-name activation aliases."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_profile(
        self,
        profile_id: str,
        *,
        is_default: bool = False,
        created_at: datetime | None = None,
    ) -> StyleProfileRef:
        stable_name = self._identifier(profile_id, field="profile_id")
        created_at = created_at or utc_now()
        self._require_aware(created_at)
        async with self.database.session() as session, session.begin():
            existing = await session.scalar(
                select(StyleProfileRecord).where(
                    StyleProfileRecord.stable_name == stable_name
                )
            )
            if existing is not None:
                return self._profile_ref(existing)
            if is_default:
                current_default = await session.scalar(
                    select(StyleProfileRecord.id).where(
                        StyleProfileRecord.is_default.is_(True)
                    )
                )
                if current_default is not None:
                    raise StyleProfileAlreadyExists(
                        "A default Style Profile already exists"
                    )
            row = StyleProfileRecord(
                id=(
                    stable_name
                    if len(stable_name) <= 36
                    else new_uuid()
                ),
                stable_name=stable_name,
                is_default=is_default,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(row)
            await session.flush()
            return self._profile_ref(row)

    async def append_version(
        self,
        *,
        profile_id: str,
        version: str,
        style_spec: Mapping[str, Any] | None = None,
        display_name: str | None = None,
        description: str | None = None,
        tone_rules: Sequence[str] = (),
        prohibited_phrases: Sequence[str] = (),
        preferred_max_paragraphs: int = 4,
        prompt_text: str | None = None,
        prompt_sha256: str | None = None,
        source_corpus_sha256: str | None = None,
        status: str = "draft",
        activate: bool = False,
        created_at: datetime | None = None,
    ) -> StyleProfileAsset:
        profile_key = self._identifier(profile_id, field="profile_id")
        version = self._identifier(version, field="version")
        if status not in _VERSION_STATUSES:
            raise ValueError(f"Unsupported Style Profile version status: {status}")
        created_at = created_at or utc_now()
        self._require_aware(created_at)
        spec = self._style_spec(
            style_spec=style_spec,
            display_name=display_name,
            description=description,
            tone_rules=tone_rules,
            prohibited_phrases=prohibited_phrases,
            preferred_max_paragraphs=preferred_max_paragraphs,
        )
        digest = self._prompt_digest(
            prompt_text=prompt_text,
            prompt_sha256=prompt_sha256,
        )
        source_digest = self._optional_digest(
            source_corpus_sha256,
            field="source_corpus_sha256",
        )

        try:
            async with self.database.session() as session, session.begin():
                profile = await session.scalar(
                    select(StyleProfileRecord).where(
                        or_(
                            StyleProfileRecord.id == profile_key,
                            StyleProfileRecord.stable_name == profile_key,
                        )
                    )
                )
                if profile is None:
                    raise StyleProfileNotFound(
                        f"Style Profile {profile_key} does not exist"
                    )
                existing = await session.scalar(
                    select(StyleProfileVersionRecord.id).where(
                        StyleProfileVersionRecord.version == version
                    )
                )
                if existing is not None:
                    raise StyleProfileAlreadyExists(
                        f"Style Profile version {version} already exists "
                        "and cannot be overwritten"
                    )
                row = StyleProfileVersionRecord(
                    id=new_uuid(),
                    profile_id=profile.id,
                    version=version,
                    style_spec_json=self._json(spec),
                    prompt_sha256=digest,
                    source_corpus_sha256=source_digest,
                    status=status,
                    created_at=created_at,
                )
                session.add(row)
                await session.flush()
                if activate:
                    profile.active_version_id = row.id
                    profile.updated_at = created_at
                    await session.flush()
                return self._asset(profile, row)
        except IntegrityError as error:
            raise StyleProfileAlreadyExists(
                f"Style Profile version {version} already exists"
            ) from error

    async def get_profile(self, version: str) -> StyleProfile | None:
        asset = await self.get_profile_asset(version)
        return asset.to_profile() if asset is not None else None

    async def get_profile_asset(self, version: str) -> StyleProfileAsset | None:
        version = self._identifier(version, field="version")
        async with self.database.session() as session:
            result = await session.execute(
                select(StyleProfileRecord, StyleProfileVersionRecord)
                .join(
                    StyleProfileVersionRecord,
                    StyleProfileVersionRecord.profile_id == StyleProfileRecord.id,
                )
                .where(StyleProfileVersionRecord.version == version)
            )
            row = result.one_or_none()
            return self._asset(*row) if row is not None else None

    async def get_active(
        self,
        profile_id: str,
    ) -> StyleProfile | None:
        asset = await self.get_active_asset(profile_id)
        return asset.to_profile() if asset is not None else None

    async def get_active_asset(
        self,
        profile_id: str,
    ) -> StyleProfileAsset | None:
        profile_key = self._identifier(profile_id, field="profile_id")
        async with self.database.session() as session:
            result = await session.execute(
                select(StyleProfileRecord, StyleProfileVersionRecord)
                .join(
                    StyleProfileVersionRecord,
                    StyleProfileVersionRecord.id
                    == StyleProfileRecord.active_version_id,
                )
                .where(
                    or_(
                        StyleProfileRecord.id == profile_key,
                        StyleProfileRecord.stable_name == profile_key,
                    )
                )
            )
            row = result.one_or_none()
            return self._asset(*row) if row is not None else None

    async def get_default(self) -> StyleProfile | None:
        asset = await self.get_default_asset()
        return asset.to_profile() if asset is not None else None

    async def get_default_asset(self) -> StyleProfileAsset | None:
        async with self.database.session() as session:
            result = await session.execute(
                select(StyleProfileRecord, StyleProfileVersionRecord)
                .join(
                    StyleProfileVersionRecord,
                    StyleProfileVersionRecord.id
                    == StyleProfileRecord.active_version_id,
                )
                .where(StyleProfileRecord.is_default.is_(True))
            )
            row = result.one_or_none()
            return self._asset(*row) if row is not None else None

    async def activate(
        self,
        *,
        profile_id: str,
        version: str,
        activated_at: datetime | None = None,
    ) -> StyleProfileAsset:
        profile_key = self._identifier(profile_id, field="profile_id")
        version = self._identifier(version, field="version")
        activated_at = activated_at or utc_now()
        self._require_aware(activated_at)
        async with self.database.session() as session, session.begin():
            profile = await session.scalar(
                select(StyleProfileRecord).where(
                    or_(
                        StyleProfileRecord.id == profile_key,
                        StyleProfileRecord.stable_name == profile_key,
                    )
                )
            )
            if profile is None:
                raise StyleProfileNotFound(
                    f"Style Profile {profile_key} does not exist"
                )
            row = await session.scalar(
                select(StyleProfileVersionRecord).where(
                    StyleProfileVersionRecord.profile_id == profile.id,
                    StyleProfileVersionRecord.version == version,
                )
            )
            if row is None:
                raise StyleProfileNotFound(
                    f"Style Profile version {version} does not exist for {profile_key}"
                )
            profile.active_version_id = row.id
            profile.updated_at = activated_at
            await session.flush()
            return self._asset(profile, row)

    async def ensure_default(self) -> StyleProfileAsset:
        current = await self.get_profile_asset(DEFAULT_STYLE_PROFILE_VERSION)
        if current is not None:
            return current
        await self.create_profile(DEFAULT_STYLE_PROFILE_ID, is_default=True)
        return await self.append_version(
            profile_id=DEFAULT_STYLE_PROFILE_ID,
            version=DEFAULT_STYLE_PROFILE_VERSION,
            style_spec=DEFAULT_STYLE_SPEC,
            prompt_text=DEFAULT_STYLE_PROFILE_PROMPT,
            status="active",
            activate=True,
        )

    @classmethod
    def _asset(
        cls,
        profile: StyleProfileRecord,
        row: StyleProfileVersionRecord,
    ) -> StyleProfileAsset:
        spec = cls._load_spec(row.style_spec_json)
        return StyleProfileAsset(
            profile_id=profile.stable_name,
            version=row.version,
            display_name=spec["display_name"],
            description=spec["description"],
            tone_rules=tuple(spec["tone_rules"]),
            prohibited_phrases=tuple(spec["prohibited_phrases"]),
            preferred_max_paragraphs=spec["preferred_max_paragraphs"],
            record_id=row.id,
            status=row.status,
            prompt_sha256=row.prompt_sha256,
            source_corpus_sha256=row.source_corpus_sha256,
            created_at=cls._aware(row.created_at),
        )

    @classmethod
    def _load_spec(cls, value: str) -> dict[str, Any]:
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as error:
            raise StyleProfileIntegrityError(
                "Stored Style Profile specification is invalid JSON"
            ) from error
        try:
            return cls._style_spec(style_spec=decoded)
        except (TypeError, ValueError) as error:
            raise StyleProfileIntegrityError(
                "Stored Style Profile specification is invalid"
            ) from error

    @staticmethod
    def _style_spec(
        *,
        style_spec: Mapping[str, Any] | None,
        display_name: str | None = None,
        description: str | None = None,
        tone_rules: Sequence[str] = (),
        prohibited_phrases: Sequence[str] = (),
        preferred_max_paragraphs: int = 4,
    ) -> dict[str, Any]:
        source = dict(style_spec) if style_spec is not None else {
            "display_name": display_name,
            "description": description,
            "tone_rules": list(tone_rules),
            "prohibited_phrases": list(prohibited_phrases),
            "preferred_max_paragraphs": preferred_max_paragraphs,
        }
        expected = {
            "display_name",
            "description",
            "tone_rules",
            "prohibited_phrases",
            "preferred_max_paragraphs",
        }
        if set(source) != expected:
            raise ValueError(
                "Style Profile specification must contain exactly: "
                + ", ".join(sorted(expected))
            )
        normalized_display = StyleProfileRepository._text(
            source["display_name"],
            field="display_name",
            maximum=128,
        )
        normalized_description = StyleProfileRepository._text(
            source["description"],
            field="description",
            maximum=1000,
        )
        normalized_rules = StyleProfileRepository._strings(
            source["tone_rules"],
            field="tone_rules",
            maximum_items=32,
        )
        normalized_prohibited = StyleProfileRepository._strings(
            source["prohibited_phrases"],
            field="prohibited_phrases",
            maximum_items=64,
        )
        paragraph_count = source["preferred_max_paragraphs"]
        if (
            isinstance(paragraph_count, bool)
            or not isinstance(paragraph_count, int)
            or not 1 <= paragraph_count <= 12
        ):
            raise ValueError("preferred_max_paragraphs must be between 1 and 12")
        return {
            "display_name": normalized_display,
            "description": normalized_description,
            "tone_rules": list(normalized_rules),
            "prohibited_phrases": list(normalized_prohibited),
            "preferred_max_paragraphs": paragraph_count,
        }

    @staticmethod
    def _prompt_digest(
        *,
        prompt_text: str | None,
        prompt_sha256: str | None,
    ) -> str:
        calculated = (
            hashlib.sha256(prompt_text.encode()).hexdigest()
            if prompt_text is not None
            else None
        )
        if prompt_sha256 is not None:
            supplied = StyleProfileRepository._digest(
                prompt_sha256,
                field="prompt_sha256",
            )
            if calculated is not None and supplied != calculated:
                raise ValueError("prompt_sha256 does not match prompt_text")
            return supplied
        return calculated or hashlib.sha256(b"").hexdigest()

    @staticmethod
    def _optional_digest(value: str | None, *, field: str) -> str | None:
        return (
            StyleProfileRepository._digest(value, field=field)
            if value is not None
            else None
        )

    @staticmethod
    def _digest(value: str, *, field: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError(f"{field} must be lowercase SHA-256 hex")
        return value

    @staticmethod
    def _identifier(value: str, *, field: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError(f"{field} must contain 1 to 128 characters")
        return normalized

    @staticmethod
    def _text(value: object, *, field: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise ValueError(f"{field} must contain 1 to {maximum} characters")
        return normalized

    @staticmethod
    def _strings(
        value: object,
        *,
        field: str,
        maximum_items: int,
    ) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError(f"{field} must be a sequence of strings")
        normalized = tuple(
            StyleProfileRepository._text(
                item,
                field=field,
                maximum=500,
            )
            for item in value
        )
        if len(normalized) > maximum_items or len(normalized) != len(set(normalized)):
            raise ValueError(f"{field} is too large or contains duplicates")
        return normalized

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _profile_ref(row: StyleProfileRecord) -> StyleProfileRef:
        return StyleProfileRef(
            id=row.id,
            stable_name=row.stable_name,
            active_version_id=row.active_version_id,
            is_default=row.is_default,
            created_at=StyleProfileRepository._aware(row.created_at),
        )

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.utcoffset() is None:
            raise ValueError("Style Profile timestamps must be timezone-aware")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "DEFAULT_STYLE_PROFILE_ID",
    "DEFAULT_STYLE_PROFILE_PROMPT",
    "DEFAULT_STYLE_PROFILE_VERSION",
    "DEFAULT_STYLE_PROFILE_VERSION_ID",
    "DEFAULT_STYLE_SPEC",
    "StyleProfileAlreadyExists",
    "StyleProfileAsset",
    "StyleProfileIntegrityError",
    "StyleProfileNotFound",
    "StyleProfileRef",
    "StyleProfileRepository",
    "StyleProfileRepositoryError",
]
