from __future__ import annotations

from typing import Any

from .config import Settings
from .mission import Mission


class AndroidNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def notify(
        self,
        mission: Mission,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        url = mission.callback_url or self.settings.android_status_url
        detail = {
            "mission_id": mission.mission_id,
            "event": event,
            "state": mission.state,
            **(payload or {}),
        }
        mission.events.append({"state": f"notify:{event}", "at": mission.updated_at, "detail": detail})

        if not url:
            return

        try:
            import httpx
        except ImportError:
            mission.events.append(
                {
                    "state": "notify:error",
                    "at": mission.updated_at,
                    "detail": {"error": "httpx is not installed", "url": url},
                }
            )
            return

        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                await client.post(url, json=detail)
        except Exception as exc:
            mission.events.append(
                {
                    "state": "notify:error",
                    "at": mission.updated_at,
                    "detail": {"error": str(exc), "url": url},
                }
            )
