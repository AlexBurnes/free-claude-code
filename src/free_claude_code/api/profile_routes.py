"""Named routing profiles and authenticated per-launch snapshots."""

import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel, ConfigDict, field_validator

from free_claude_code.application.ports import ModelCatalogSnapshot
from free_claude_code.config.profiles import (
    ProfileConflictError,
    ProfileNotFoundError,
    ProfileStore,
    RoutingProfile,
    validate_profile_name,
)

from .admin_security import require_loopback_admin
from .dependencies import get_services, require_proxy_auth
from .ports import ApiServices

router = APIRouter()
SESSION_IDLE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class ProfileSession:
    session_id: str
    name: str
    profile: RoutingProfile
    catalog: ModelCatalogSnapshot


class ProfileSessions:
    """Application-owned bindings; changing saved profiles never mutates entries."""

    def __init__(self, store: ProfileStore) -> None:
        self.store = store
        self._sessions: dict[str, tuple[ProfileSession, float]] = {}
        self._lock = Lock()

    def create(self, name: str, snapshot: ModelCatalogSnapshot) -> ProfileSession:
        profile = self.store.get(name, snapshot.settings)
        settings = profile.overlay(snapshot.settings, name)
        allowed = settings.routing_allowed_models
        infos = tuple(
            info
            for info in snapshot.model_infos
            if allowed is None or info.model_id in allowed
        )
        session = ProfileSession(
            secrets.token_urlsafe(24),
            name,
            profile,
            ModelCatalogSnapshot(settings, infos),
        )
        with self._lock:
            self._expire()
            self._sessions[session.session_id] = (session, time.monotonic())
        logger.info(
            "FCC session created: profile={} session={}", name, session.session_id
        )
        return session

    def get(self, session_id: str) -> ProfileSession:
        with self._lock:
            self._expire()
            entry = self._sessions.get(session_id)
            if entry is None:
                raise ProfileNotFoundError("Unknown or expired launch session.")
            session, _ = entry
            self._sessions[session_id] = (session, time.monotonic())
            return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            if self._sessions.pop(session_id, None) is None:
                raise ProfileNotFoundError("Unknown or expired launch session.")

    def _expire(self) -> None:
        deadline = time.monotonic() - SESSION_IDLE_SECONDS
        for session_id, (_, seen) in tuple(self._sessions.items()):
            if seen < deadline:
                del self._sessions[session_id]


class ProfileNamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return validate_profile_name(value)


class ProfileCreatePayload(ProfileNamePayload):
    configuration: RoutingProfile


class ProfileUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    configuration: RoutingProfile


class LaunchSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    profile: str = "default"

    @field_validator("profile")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return validate_profile_name(value)


@contextmanager
def profile_errors() -> Iterator[None]:
    try:
        yield
    except ProfileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except ProfileConflictError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def get_profile_sessions(request: Request) -> ProfileSessions:
    return request.app.state.profile_sessions


def get_profile_session(request: Request) -> ProfileSession | None:
    session_id = request.path_params.get("session_id")
    if session_id is None:
        return None
    with profile_errors():
        session = get_profile_sessions(request).get(str(session_id))
    request.state.fcc_profile_name = session.name
    return session


def profile_record(name: str, profile: RoutingProfile) -> dict[str, object]:
    return {
        "name": name,
        "configuration": profile.model_dump(mode="json"),
        "implicit": name == "default",
    }


def _admin_store(request: Request) -> ProfileStore:
    require_loopback_admin(request)
    return get_profile_sessions(request).store


@router.get("/admin/api/profiles")
def list_profiles(request: Request, services: ApiServices = Depends(get_services)):
    store = _admin_store(request)
    with profile_errors():
        profiles = store.list(services.requests.current_settings())
    return {
        "profiles": [
            profile_record(name, profile) for name, profile in profiles.items()
        ],
        "default_profile": "default",
    }


@router.get("/admin/api/profiles/{name}")
def get_profile(
    name: str, request: Request, services: ApiServices = Depends(get_services)
):
    store = _admin_store(request)
    with profile_errors():
        profile = store.get(name, services.requests.current_settings())
    return profile_record(name, profile)


@router.post("/admin/api/profiles", status_code=201)
def create_profile(
    payload: ProfileCreatePayload,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    store = _admin_store(request)
    with profile_errors():
        profile = store.save(
            payload.name,
            payload.configuration,
            services.requests.current_settings(),
            create=True,
        )
    return profile_record(payload.name, profile)


@router.put("/admin/api/profiles/{name}")
def update_profile(
    name: str,
    payload: ProfileUpdatePayload,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    store = _admin_store(request)
    with profile_errors():
        profile = store.save(
            name, payload.configuration, services.requests.current_settings()
        )
    return profile_record(name, profile)


@router.post("/admin/api/profiles/{name}/duplicate", status_code=201)
def duplicate_profile(
    name: str,
    payload: ProfileNamePayload,
    request: Request,
    services: ApiServices = Depends(get_services),
):
    store = _admin_store(request)
    settings = services.requests.current_settings()
    with profile_errors():
        profile = store.get(name, settings)
        store.save(payload.name, profile, settings, create=True)
    return profile_record(payload.name, profile)


@router.delete("/admin/api/profiles/{name}", status_code=204)
def delete_profile(name: str, request: Request):
    store = _admin_store(request)
    with profile_errors():
        store.delete(name)
    return Response(status_code=204)


@router.post("/api/launch-sessions", status_code=201)
async def create_launch_session(
    payload: LaunchSessionPayload,
    request: Request,
    services: ApiServices = Depends(get_services),
    _auth=Depends(require_proxy_auth),
):
    snapshot = await services.requests.wait_for_catalog()
    with profile_errors():
        session = get_profile_sessions(request).create(payload.profile, snapshot)
    return {
        "session_id": session.session_id,
        "profile": session.name,
        "configuration": session.profile.model_dump(mode="json"),
        "base_path": f"/fcc/sessions/{session.session_id}",
    }


@router.delete("/api/launch-sessions/{session_id}", status_code=204)
def delete_launch_session(
    session_id: str, request: Request, _auth=Depends(require_proxy_auth)
):
    with profile_errors():
        get_profile_sessions(request).delete(session_id)
    return Response(status_code=204)
