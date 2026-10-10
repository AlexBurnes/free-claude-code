"""Secret-free named routing configuration and atomic persistence."""

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from free_claude_code.core.interprocess_lock import InterprocessFileLock

from . import paths
from .model_refs import split_provider_model_ref
from .reasoning import ReasoningPreference
from .settings import NonEmptyString, Settings

PROFILE_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def validate_profile_name(name: str, *, allow_default: bool = True) -> str:
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Profile name must be lowercase ASCII, starting with a letter (1-64 characters)."
        )
    if name == "default" and not allow_default:
        raise ValueError("The default profile is implicit and cannot be stored.")
    return name


class ProfileNotFoundError(ValueError):
    """The requested saved profile does not exist."""


class ProfileConflictError(ValueError):
    """A profile mutation conflicts with an existing or implicit profile."""


class ProfileSettings(Settings):
    """Per-request frozen routing overlay; provider credentials remain shared."""

    model_config = ConfigDict(frozen=True)
    routing_profile_name: str = Field(default="default", exclude=True)
    routing_allowed_models: tuple[str, ...] | None = Field(default=None, exclude=True)


class RoutingProfile(BaseModel):
    """A complete, immutable routing choice without provider configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    model: NonEmptyString
    model_fable: NonEmptyString | None = None
    model_opus: NonEmptyString | None = None
    model_sonnet: NonEmptyString | None = None
    model_haiku: NonEmptyString | None = None
    model_fallbacks: tuple[NonEmptyString, ...] | None = None
    reasoning_policy: ReasoningPreference = ReasoningPreference.CLIENT
    reasoning_fable: ReasoningPreference = ReasoningPreference.INHERIT
    reasoning_opus: ReasoningPreference = ReasoningPreference.INHERIT
    reasoning_sonnet: ReasoningPreference = ReasoningPreference.INHERIT
    reasoning_haiku: ReasoningPreference = ReasoningPreference.INHERIT

    @field_validator(
        "model", "model_fable", "model_opus", "model_sonnet", "model_haiku"
    )
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is not None:
            split_provider_model_ref(value)
        return value

    @field_validator("model_fallbacks")
    @classmethod
    def validate_fallbacks(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        for ref in value or ():
            split_provider_model_ref(ref)
        return value

    @field_validator("reasoning_policy")
    @classmethod
    def validate_root_reasoning(cls, value: ReasoningPreference) -> ReasoningPreference:
        if value is ReasoningPreference.INHERIT:
            raise ValueError("Root reasoning policy cannot inherit.")
        return value

    @classmethod
    def from_settings(cls, settings: Settings) -> RoutingProfile:
        return cls.model_validate(
            {name: getattr(settings, name) for name in cls.model_fields}
        )

    @property
    def allowed_models(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                ref
                for ref in (
                    self.model,
                    self.model_fable,
                    self.model_opus,
                    self.model_sonnet,
                    self.model_haiku,
                    *(self.model_fallbacks or ()),
                )
                if ref is not None
            )
        )

    def overlay(self, settings: Settings, name: str = "default") -> ProfileSettings:
        return ProfileSettings.model_validate(
            {
                **settings.model_dump(),
                **self.model_dump(),
                "routing_profile_name": name,
                "routing_allowed_models": None
                if name == "default"
                else self.allowed_models,
            }
        )


class ProfilesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    profiles: dict[str, RoutingProfile] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("Unsupported profiles storage version.")
        return value

    @field_validator("profiles")
    @classmethod
    def validate_names(
        cls, value: dict[str, RoutingProfile]
    ) -> dict[str, RoutingProfile]:
        for name in value:
            validate_profile_name(name, allow_default=False)
        return value


class ProfileStore:
    """Read fresh storage and serialize writes across server processes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else paths.profiles_path()

    def _read(self) -> ProfilesDocument:
        if not self.path.exists():
            return ProfilesDocument()
        return ProfilesDocument.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def list(self, settings: Settings) -> dict[str, RoutingProfile]:
        return {
            "default": RoutingProfile.from_settings(settings),
            **self._read().profiles,
        }

    def get(self, name: str, settings: Settings) -> RoutingProfile:
        validate_profile_name(name)
        if name == "default":
            return RoutingProfile.from_settings(settings)
        profile = self._read().profiles.get(name)
        if profile is None:
            raise ProfileNotFoundError(f"Unknown profile: {name}")
        profile.overlay(settings, name)
        return profile

    def save(
        self,
        name: str,
        profile: RoutingProfile,
        settings: Settings,
        *,
        create: bool = False,
    ) -> RoutingProfile:
        validate_profile_name(name)
        if name == "default":
            raise ProfileConflictError(
                "Edit the implicit default through Model Config."
            )
        profile.overlay(settings, name)
        lock = InterprocessFileLock(self.path.with_suffix(".lock"))
        if not lock.acquire(wait=True, timeout=10.0):
            raise TimeoutError("Could not acquire profiles storage lock.")
        try:
            document = self._read()
            exists = name in document.profiles
            if create and exists:
                raise ProfileConflictError(f"Profile already exists: {name}")
            if not create and not exists:
                raise ProfileNotFoundError(f"Unknown profile: {name}")
            document.profiles[name] = profile
            self._write(document)
        finally:
            lock.release()
        return profile

    def delete(self, name: str) -> None:
        validate_profile_name(name)
        if name == "default":
            raise ProfileConflictError(
                "The implicit default profile cannot be deleted."
            )
        lock = InterprocessFileLock(self.path.with_suffix(".lock"))
        if not lock.acquire(wait=True, timeout=10.0):
            raise TimeoutError("Could not acquire profiles storage lock.")
        try:
            document = self._read()
            if name not in document.profiles:
                raise ProfileNotFoundError(f"Unknown profile: {name}")
            del document.profiles[name]
            self._write(document)
        finally:
            lock.release()

    def _write(self, document: ProfilesDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix="profiles-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document.model_dump(mode="json"), handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
