from __future__ import annotations

import base64
import json
import re
from typing import Any


DATA_URL_RE = re.compile(r"^data:(?P<mimetype>[^;]+);base64,(?P<data>.+)$", re.DOTALL)
SAS_RE = re.compile(r"OK:SAS:(?P<status>COMPLETE|FAILED):(?P<steps>\d+)(?::(?P<distance>\d+))?")


def decode_image_data(value: str) -> tuple[bytes, str]:
    match = DATA_URL_RE.match(value)
    if match:
        return base64.b64decode(match.group("data")), match.group("mimetype")
    return base64.b64decode(value), "image/jpeg"


def load_json_field(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = value.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def extract_text(result: Any, preferred_keys: tuple[str, ...] = ()) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, bytes):
        return result.decode("utf-8", errors="replace")
    if isinstance(result, list):
        return "\n".join(extract_text(item, preferred_keys) for item in result if item is not None)
    if isinstance(result, dict):
        for key in preferred_keys + ("classification", "verification", "dispatch", "answers", "text", "result"):
            if key in result:
                text = extract_text(result[key], preferred_keys)
                if text:
                    return text
        if "content" in result:
            return extract_text(result["content"], preferred_keys)
        if "answer" in result:
            return extract_text(result["answer"], preferred_keys)
        return json.dumps(result, sort_keys=True)
    return str(result)


def parse_jsonish(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    text = extract_text(value).strip()
    if not text:
        return {}

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            return loaded
        return {"value": loaded}
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            loaded = json.loads(text[start : end + 1])
            if isinstance(loaded, dict):
                return loaded
            return {"value": loaded}
        except json.JSONDecodeError:
            pass

    return {"raw": text}


def normalize_report_classification(raw: Any) -> dict[str, Any]:
    data = parse_jsonish(raw)
    is_trash = data.get("is_trash", data.get("trash", data.get("status", "uncertain")))
    if isinstance(is_trash, bool):
        normalized = "yes" if is_trash else "no"
    else:
        normalized = str(is_trash).strip().lower()
        if normalized in {"true", "trash", "confirmed", "cleanable"}:
            normalized = "yes"
        elif normalized in {"false", "not_trash", "reject", "rejected"}:
            normalized = "no"
        elif normalized not in {"yes", "no", "uncertain"}:
            normalized = "uncertain"

    return {
        "is_trash": normalized,
        "trash_type": str(data.get("trash_type") or data.get("type") or "unknown").strip().lower(),
        "confidence": data.get("confidence"),
        "rejection_reason": data.get("rejection_reason") or data.get("reason") or "",
        "visual_evidence": data.get("visual_evidence") or data.get("evidence") or data.get("raw") or "",
        "raw": data,
    }


def normalize_verification(raw: Any) -> dict[str, Any]:
    data = parse_jsonish(raw)
    cleaned = data.get("cleaned", data.get("status", data.get("result", "not_cleaned")))
    if isinstance(cleaned, bool):
        normalized = "cleaned" if cleaned else "not_cleaned"
    else:
        normalized = str(cleaned).strip().lower().replace(" ", "_")
        if normalized in {"yes", "clear", "cleared", "complete", "completed", "success", "clean"}:
            normalized = "cleaned"
        elif normalized != "cleaned":
            normalized = "not_cleaned"

    return {
        "cleaned": normalized,
        "confidence": data.get("confidence"),
        "reason": data.get("reason") or data.get("evidence") or data.get("raw") or "",
        "needs_retry": bool(data.get("needs_retry", normalized != "cleaned")),
        "raw": data,
    }


def infer_bridge_status(raw: Any) -> dict[str, Any]:
    text = extract_text(raw, ("dispatch",)).strip()
    data = parse_jsonish(raw)
    combined = f"{text}\n{json.dumps(data, default=str)}"
    match = SAS_RE.search(combined)

    status = str(data.get("status") or "").strip().upper()
    if match:
        status = match.group("status")
    elif "OK:SAS:COMPLETE" in combined or "COMPLETE" in combined:
        status = "COMPLETE"
    elif "OK:SAS:FAILED" in combined or "FAILED" in combined:
        status = "FAILED"
    elif not status:
        status = "PENDING"

    steps = data.get("steps")
    distance = data.get("distance")
    if match:
        steps = int(match.group("steps"))
        distance = int(match.group("distance")) if match.group("distance") else distance

    return {
        "status": status if status in {"COMPLETE", "FAILED", "PENDING", "ERROR"} else "PENDING",
        "steps": steps,
        "distance": distance,
        "bridge_response": data.get("bridge_response") or data.get("hub_response") or text,
        "raw": data or raw,
    }
