from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID


ROOT = Path(__file__).resolve().parent
PIPELINES = [
    ROOT / "pipelines" / "report_classification.pipe",
    ROOT / "pipelines" / "robot_dispatch.pipe",
    ROOT / "pipelines" / "confirmation_verification.pipe",
]
REQUIRED_ENV = [
    "ROCKETRIDE_URI",
    "ROCKETRIDE_APIKEY",
    "GMI_API_KEY",
    "GMI_BASE_URL",
    "GMI_VISION_MODEL",
    "ROCKETRIDE_BRIDGE_COMMAND_URL",
    "ROCKETRIDE_BRIDGE_COMMAND_URL_PATTERN",
]


def load_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if not env_file.exists():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_pipeline(path: Path) -> list[str]:
    errors: list[str] = []
    if path.suffix != ".pipe":
        errors.append(f"{path}: pipeline file must use .pipe extension")

    try:
        text = path.read_text(encoding="utf-8")
        pairs = json.loads(text, object_pairs_hook=list)
        data = json.loads(text)
    except Exception as exc:
        return [f"{path}: invalid JSON: {exc}"]

    if not pairs or pairs[0][0] != "components":
        errors.append(f"{path}: top-level components field must be first")

    components = data.get("components")
    if not isinstance(components, list) or not components:
        errors.append(f"{path}: components must be a non-empty list")
        return errors

    ids = [component.get("id") for component in components if isinstance(component, dict)]
    if len(ids) != len(set(ids)):
        errors.append(f"{path}: component ids must be unique")

    id_set = set(ids)
    for component in components:
        if not isinstance(component, dict):
            errors.append(f"{path}: component entries must be objects")
            continue
        if not component.get("id") or not component.get("provider"):
            errors.append(f"{path}: every component needs id and provider")
        if component.get("provider") in {"webhook", "chat", "dropper"}:
            config = component.get("config") or {}
            for key in ("hideForm", "mode", "type"):
                if key not in config:
                    errors.append(f"{path}: source {component.get('id')} missing config.{key}")
        for edge in component.get("input", []):
            if edge.get("from") not in id_set:
                errors.append(
                    f"{path}: input on {component.get('id')} references missing component {edge.get('from')}"
                )
        for edge in component.get("control", []):
            if edge.get("from") not in id_set:
                errors.append(
                    f"{path}: control on {component.get('id')} references missing component {edge.get('from')}"
                )

    try:
        UUID(str(data.get("project_id")))
    except Exception:
        errors.append(f"{path}: project_id must be a literal UUID")

    if data.get("viewport") is None:
        errors.append(f"{path}: viewport is required")
    if data.get("version") != 1:
        errors.append(f"{path}: version must be 1")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-env", action="store_true", help="Fail when required env vars are missing")
    args = parser.parse_args()

    errors: list[str] = []
    for path in PIPELINES:
        if not path.exists():
            errors.append(f"{path}: missing")
            continue
        errors.extend(validate_pipeline(path))

    env = {**load_dotenv(), **os.environ}
    missing = [key for key in REQUIRED_ENV if not env.get(key)]

    if errors:
        print("Pipeline validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("Pipeline validation passed.")
    if missing:
        print("Missing environment variables:")
        for key in missing:
            print(f"  - {key}")
        if args.strict_env:
            return 1
    else:
        print("Required environment variables are present.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
