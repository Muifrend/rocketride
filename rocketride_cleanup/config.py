from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    report_pipeline: Path = ROOT / "pipelines" / "report_classification.pipe"
    dispatch_pipeline: Path = ROOT / "pipelines" / "robot_dispatch.pipe"
    verification_pipeline: Path = ROOT / "pipelines" / "confirmation_verification.pipe"
    gmi_api_key: str = _env("GMI_API_KEY")
    gmi_base_url: str = _env("GMI_BASE_URL", "https://api.gmi-serving.com/v1")
    gmi_vision_model: str = _env(
        "GMI_VISION_MODEL",
        "google/gemini-3.1-flash-lite-preview",
    )
    bridge_command_url: str = _env(
        "ROCKETRIDE_BRIDGE_COMMAND_URL",
        "http://localhost:8000/robot/command",
    )
    bridge_callback_timeout_seconds: float = float(
        _env("BRIDGE_CALLBACK_TIMEOUT_SECONDS", "45") or "45"
    )
    android_status_url: str = _env("ANDROID_STATUS_URL")
    dispatch_mode: str = _env("ROCKETRIDE_DISPATCH_MODE", "rocketride").lower()
    request_timeout_seconds: float = float(_env("REQUEST_TIMEOUT_SECONDS", "30") or "30")


settings = Settings()
