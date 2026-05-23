from __future__ import annotations

import json
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .config import settings
from .mission import Mission, MissionStore
from .notifier import AndroidNotifier
from .parsing import (
    decode_image_data,
    infer_bridge_status,
    load_json_field,
    normalize_report_classification,
    normalize_verification,
)
from .rocketride_pipelines import RocketRidePipelineError, RocketRidePipelines


store = MissionStore()
pipelines = RocketRidePipelines(settings)
notifier = AndroidNotifier(settings)
app = FastAPI(title="RocketRide Cleanup Mission Coordinator", version="0.1.0")


class BridgeCallback(BaseModel):
    mission_id: str
    status: str
    steps: int | None = None
    distance: int | None = None
    message: str | None = None
    hub_response: str | None = None
    raw: dict[str, Any] | None = None


class MissionIdPayload(BaseModel):
    mission_id: str


@app.on_event("shutdown")
async def shutdown() -> None:
    await pipelines.close()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "dispatch_mode": settings.dispatch_mode,
        "bridge_command_url": settings.bridge_command_url,
        "missions": len(await store.list()),
    }


@app.post("/android/report")
async def receive_report(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    incoming = await _read_photo_payload(request)
    mission = await store.create(
        mission_id=incoming.get("mission_id"),
        reported_trash_type=incoming.get("trash_type"),
        location=incoming.get("location"),
        callback_url=incoming.get("callback_url"),
    )
    mission.transition("classifying")

    try:
        result = await pipelines.classify_report(
            incoming["image"],
            incoming["mimetype"],
            objinfo={
                "mission_id": mission.mission_id,
                "trash_type": mission.reported_trash_type,
                "location": mission.location,
            },
        )
        classification = normalize_report_classification(result)
    except RocketRidePipelineError as exc:
        mission.error = str(exc)
        mission.transition("error", {"stage": "classification", "error": str(exc)})
        await notifier.notify(mission, "classification_error", {"error": str(exc)})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        mission.error = str(exc)
        mission.transition("error", {"stage": "classification", "error": str(exc)})
        await notifier.notify(mission, "classification_error", {"error": str(exc)})
        raise HTTPException(status_code=502, detail=f"RocketRide classification failed: {exc}") from exc

    mission.classification = classification

    if classification["is_trash"] != "yes":
        mission.transition("rejected", classification)
        await notifier.notify(mission, "report_rejected", {"classification": classification})
        return {
            "mission_id": mission.mission_id,
            "status": "rejected",
            "classification": classification,
        }

    mission.transition("confirmed", classification)
    background_tasks.add_task(_dispatch_robot_for_mission, mission.mission_id)
    return {
        "mission_id": mission.mission_id,
        "status": "confirmed",
        "classification": classification,
        "next": "dispatching_robot",
    }


@app.post("/bridge/callback")
async def bridge_callback(payload: BridgeCallback) -> dict[str, Any]:
    try:
        mission = await store.require(payload.mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown mission_id") from exc

    bridge_result = {
        "status": payload.status.upper(),
        "steps": payload.steps,
        "distance": payload.distance,
        "bridge_response": payload.hub_response or payload.message or "",
        "raw": payload.raw or _model_dump(payload),
    }
    await _apply_bridge_result(mission, bridge_result)
    return mission.public_dict()


@app.post("/android/confirmation")
async def receive_confirmation(request: Request) -> dict[str, Any]:
    incoming = await _read_photo_payload(request)
    mission_id = incoming.get("mission_id")
    if not mission_id:
        raise HTTPException(status_code=400, detail="mission_id is required")

    try:
        mission = await store.require(mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown mission_id") from exc

    mission.transition("verifying_cleanup")
    try:
        result = await pipelines.verify_cleanup(
            incoming["image"],
            incoming["mimetype"],
            objinfo={
                "mission_id": mission.mission_id,
                "reported_trash_type": mission.reported_trash_type,
                "location": mission.location,
                "classification": mission.classification,
            },
        )
        verification = normalize_verification(result)
    except RocketRidePipelineError as exc:
        mission.error = str(exc)
        mission.transition("error", {"stage": "verification", "error": str(exc)})
        await notifier.notify(mission, "verification_error", {"error": str(exc)})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        mission.error = str(exc)
        mission.transition("error", {"stage": "verification", "error": str(exc)})
        await notifier.notify(mission, "verification_error", {"error": str(exc)})
        raise HTTPException(status_code=502, detail=f"RocketRide verification failed: {exc}") from exc

    mission.verification = verification
    if verification["cleaned"] == "cleaned":
        mission.transition("cleaned", verification)
        await notifier.notify(mission, "cleanup_verified", {"verification": verification})
    else:
        mission.transition("not_cleaned", verification)
        await notifier.notify(mission, "cleanup_not_verified", {"verification": verification})

    return mission.public_dict()


@app.get("/missions/{mission_id}")
async def get_mission(mission_id: str) -> dict[str, Any]:
    try:
        mission = await store.require(mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown mission_id") from exc
    return mission.public_dict()


@app.get("/missions")
async def list_missions() -> list[dict[str, Any]]:
    return [mission.public_dict() for mission in await store.list()]


async def _dispatch_robot_for_mission(mission_id: str) -> None:
    mission = await store.require(mission_id)
    mission.transition("dispatching")

    try:
        if settings.dispatch_mode == "direct":
            result = await _direct_bridge_dispatch(mission)
        else:
            result = await pipelines.dispatch_robot(
                {
                    "mission_id": mission.mission_id,
                    "trash_type": mission.classification.get("trash_type") if mission.classification else None,
                    "location": mission.location,
                }
            )
        bridge_result = infer_bridge_status(result)
        mission.dispatch = bridge_result
    except Exception as exc:
        mission.error = str(exc)
        mission.transition("error", {"stage": "dispatch", "error": str(exc)})
        await notifier.notify(mission, "dispatch_error", {"error": str(exc)})
        return

    await _apply_bridge_result(mission, bridge_result)


async def _direct_bridge_dispatch(mission: Mission) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is not installed. Run: pip install -r requirements.txt") from exc

    async with httpx.AsyncClient(timeout=settings.bridge_callback_timeout_seconds) as client:
        response = await client.post(
            settings.bridge_command_url,
            json={
                "command": "SEARCH_AND_SWEEP",
                "mission_id": mission.mission_id,
                "max_steps": 15,
            },
        )
        response.raise_for_status()
        return response.json()


async def _apply_bridge_result(mission: Mission, bridge_result: dict[str, Any]) -> None:
    mission.bridge = bridge_result
    status = bridge_result.get("status")
    if status == "FAILED":
        mission.transition("robot_failed", bridge_result)
        await notifier.notify(mission, "robot_failed", bridge_result)
    elif status == "COMPLETE":
        mission.transition("awaiting_confirmation_photo", bridge_result)
        await notifier.notify(
            mission,
            "cleanup_complete_take_confirmation_photo",
            {
                "message": "cleanup complete, please take a confirmation photo",
                **bridge_result,
            },
        )
    else:
        mission.transition("awaiting_bridge_callback", bridge_result)
        await notifier.notify(mission, "robot_dispatched", bridge_result)


async def _read_photo_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("image") or form.get("photo") or form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="multipart field 'image' is required")
        image = await upload.read()
        if not image:
            raise HTTPException(status_code=400, detail="image is empty")
        return {
            "image": image,
            "mimetype": getattr(upload, "content_type", None) or "image/jpeg",
            "mission_id": _optional_str(form.get("mission_id")),
            "trash_type": _optional_str(form.get("trash_type") or form.get("trashType")),
            "location": load_json_field(form.get("location")),
            "callback_url": _optional_str(form.get("callback_url") or form.get("callbackUrl")),
        }

    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Expected multipart/form-data or JSON") from exc

    image_value = body.get("image_base64") or body.get("image") or body.get("photo_base64")
    if not image_value:
        raise HTTPException(status_code=400, detail="JSON field image_base64 is required")
    try:
        image, mimetype = decode_image_data(str(image_value))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="image_base64 could not be decoded") from exc

    return {
        "image": image,
        "mimetype": body.get("mimetype") or mimetype,
        "mission_id": _optional_str(body.get("mission_id")),
        "trash_type": _optional_str(body.get("trash_type") or body.get("trashType")),
        "location": body.get("location"),
        "callback_url": _optional_str(body.get("callback_url") or body.get("callbackUrl")),
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
