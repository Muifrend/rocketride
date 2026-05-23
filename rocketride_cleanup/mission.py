from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Mission:
    mission_id: str
    reported_trash_type: str | None = None
    location: Any = None
    callback_url: str | None = None
    state: str = "received"
    classification: dict[str, Any] | None = None
    dispatch: dict[str, Any] | None = None
    bridge: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    events: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, state: str, detail: dict[str, Any] | None = None) -> None:
        self.state = state
        self.updated_at = utc_now()
        self.events.append(
            {
                "state": state,
                "at": self.updated_at,
                "detail": detail or {},
            }
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "state": self.state,
            "reported_trash_type": self.reported_trash_type,
            "location": self.location,
            "classification": self.classification,
            "dispatch": self.dispatch,
            "bridge": self.bridge,
            "verification": self.verification,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events": self.events,
        }


class MissionStore:
    def __init__(self) -> None:
        self._missions: dict[str, Mission] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        mission_id: str | None,
        reported_trash_type: str | None,
        location: Any,
        callback_url: str | None,
    ) -> Mission:
        mission = Mission(
            mission_id=mission_id or f"cleanup-{uuid4()}",
            reported_trash_type=reported_trash_type,
            location=location,
            callback_url=callback_url,
        )
        mission.transition("received")
        async with self._lock:
            self._missions[mission.mission_id] = mission
        return mission

    async def get(self, mission_id: str) -> Mission | None:
        async with self._lock:
            return self._missions.get(mission_id)

    async def require(self, mission_id: str) -> Mission:
        mission = await self.get(mission_id)
        if mission is None:
            raise KeyError(mission_id)
        return mission

    async def list(self) -> list[Mission]:
        async with self._lock:
            return list(self._missions.values())
