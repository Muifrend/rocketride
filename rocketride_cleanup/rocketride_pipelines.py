from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from .config import Settings


class RocketRidePipelineError(RuntimeError):
    pass


class RocketRidePipelines:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None
        self._tokens: dict[Path, str] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
            self._tokens.clear()

    async def classify_report(
        self,
        image: bytes,
        mimetype: str,
        objinfo: dict[str, Any],
    ) -> Any:
        return await self._gmi_chat_image(
            image=image,
            mimetype=mimetype,
            system_prompt=(
                "You are a strict visual classifier for an outdoor trash cleanup robot. "
                "Return only valid JSON. Do not include markdown."
            ),
            user_prompt=(
                "Inspect the photo from the Android report. Decide whether the target object "
                "is cleanup-worthy trash that a small sweeper robot should attempt to collect. "
                "Output exactly this JSON shape: "
                '{"is_trash":"yes|no|uncertain","trash_type":"bottle|can|bag|wrapper|paper|cup|food_waste|other|unknown",'
                '"confidence":0.0,"rejection_reason":"","visual_evidence":""}. '
                "Use uncertain if the image is too blurry, too dark, occluded, not outdoors, "
                "or the object cannot be localized. If it is a bottle, can, bag, wrapper, "
                "paper, cup, or loose food packaging, classify as yes."
            ),
            objinfo=objinfo,
        )

    async def dispatch_robot(self, payload: dict[str, Any]) -> Any:
        payload = {
            **payload,
            "command": "SEARCH_AND_SWEEP",
            "max_steps": 15,
            "bridge_command_url": self.settings.bridge_command_url,
        }
        return await self._send_text(
            self.settings.dispatch_pipeline,
            json.dumps(payload),
            objinfo={"mission_id": payload.get("mission_id"), "kind": "robot_dispatch"},
        )

    async def verify_cleanup(
        self,
        image: bytes,
        mimetype: str,
        objinfo: dict[str, Any],
    ) -> Any:
        return await self._gmi_chat_image(
            image=image,
            mimetype=mimetype,
            system_prompt=(
                "You are the final cleanup verifier for a trash collection mission. "
                "Return only valid JSON. Do not include markdown."
            ),
            user_prompt=(
                "Inspect the confirmation photo after robot cleanup. Decide whether the "
                "reported trash has been removed. Return exactly this JSON shape: "
                '{"cleaned":"cleaned|not_cleaned","confidence":0.0,"reason":"","needs_retry":false}. '
                "Use cleaned only when the target area appears clear and no likely reported "
                "trash remains. Use not_cleaned when trash remains visible or the image is too uncertain. "
                f"Mission context: {json.dumps(objinfo, default=str)}"
            ),
            objinfo=objinfo,
        )

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from rocketride import RocketRideClient
        except ImportError as exc:
            raise RocketRidePipelineError(
                "The rocketride package is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self._client = RocketRideClient()
        await self._client.connect()
        return self._client

    async def _get_token(self, filepath: Path) -> str:
        filepath = filepath.resolve()
        async with self._lock:
            if filepath in self._tokens:
                return self._tokens[filepath]

            client = await self._get_client()
            result = await client.use(pipeline=self._load_pipeline(filepath))
            token = result["token"]
            self._tokens[filepath] = token
            return token

    def _load_pipeline(self, filepath: Path) -> dict[str, Any]:
        text = filepath.read_text(encoding="utf-8")
        data = json.loads(text)
        return self._substitute_env(data)

    def _substitute_env(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._substitute_env(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._substitute_env(item) for item in value]
        if not isinstance(value, str):
            return value

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            return os.getenv(name, match.group(0))

        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)

    async def _gmi_chat_image(
        self,
        *,
        image: bytes,
        mimetype: str,
        system_prompt: str,
        user_prompt: str,
        objinfo: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.settings.gmi_api_key:
            raise RocketRidePipelineError("GMI_API_KEY is required for GMI vision inference")

        import httpx

        image_url = f"data:{mimetype};base64,{base64.b64encode(image).decode('ascii')}"
        url = self.settings.gmi_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.settings.gmi_vision_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.settings.gmi_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 400 and "response_format" in response.text:
                payload.pop("response_format", None)
                response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RocketRidePipelineError(f"Unexpected GMI response shape: {data}") from exc

        return {
            "content": content,
            "model": data.get("model", self.settings.gmi_vision_model),
            "usage": data.get("usage"),
            "raw_response": data,
        }

    async def _send_bytes(
        self,
        filepath: Path,
        data: bytes,
        *,
        mimetype: str,
        objinfo: dict[str, Any],
    ) -> Any:
        token = await self._get_token(filepath)
        client = await self._get_client()
        try:
            return await client.send(token, data, objinfo=objinfo, mimetype=mimetype)
        except Exception:
            async with self._lock:
                self._tokens.pop(filepath.resolve(), None)
            token = await self._get_token(filepath)
            return await client.send(token, data, objinfo=objinfo, mimetype=mimetype)

    async def _send_text(
        self,
        filepath: Path,
        text: str,
        *,
        objinfo: dict[str, Any],
    ) -> Any:
        token = await self._get_token(filepath)
        client = await self._get_client()
        try:
            return await client.send(token, text, objinfo=objinfo, mimetype="text/plain")
        except Exception:
            async with self._lock:
                self._tokens.pop(filepath.resolve(), None)
            token = await self._get_token(filepath)
            return await client.send(token, text, objinfo=objinfo, mimetype="text/plain")
