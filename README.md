# Trash Patrol Orchestration Layer

This service coordinates the Trash Patrol cleanup mission flow between the Android app, GMI Cloud vision models, RocketRide, and the laptop robot bridge.

It is intentionally small and stateful: missions live in memory while the FastAPI process is running, and Android can poll the mission endpoint to render the current state.

## What It Does

1. Receives a report from Android at `POST /android/report`.
2. Sends the report image directly to GMI Cloud's OpenAI-compatible Gemini vision model.
3. Decides whether the image contains cleanup-worthy trash.
4. If trash is confirmed, dispatches the robot through `pipelines/robot_dispatch.pipe`.
5. Sends exactly one `SEARCH_AND_SWEEP` command with `max_steps=15` to the laptop bridge.
6. Records the bridge result as mission state.
7. If the bridge reports `COMPLETE`, asks Android for a confirmation photo.
8. Sends the confirmation photo to GMI Cloud to verify `cleaned` or `not_cleaned`.

Classification and cleanup verification do not require the object to be outdoors. The vision task is only: decide whether the visible target is trash, classify it, and later decide whether it has been removed.

## Architecture

```text
Android app
  |
  | POST /android/report
  v
FastAPI orchestration layer
  |
  | image classification
  v
GMI Cloud OpenAI-compatible Gemini model
  |
  | if is_trash == yes
  v
RocketRide robot_dispatch.pipe
  |
  | POST SEARCH_AND_SWEEP max_steps=15
  v
Laptop bridge / robot
  |
  | bridge result or /bridge/callback
  v
FastAPI mission state
  |
  | Android polls /missions/{mission_id}
  v
Android confirmation photo flow
```

## Runtime Responsibilities

The orchestration layer owns:

- Mission IDs and in-memory mission state.
- Report image ingestion.
- GMI vision calls for trash classification.
- Robot dispatch through RocketRide or direct debug mode.
- Bridge callback handling.
- Confirmation-photo verification.
- Status payloads for Android polling or callback URLs.

It does not own:

- Android UI state beyond the API contract below.
- Robot movement logic.
- Persistent mission storage.
- Long-term auth/user management.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
python check.py
```

Fill in `.env` before running the server. Do not commit `.env`.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `ROCKETRIDE_URI` | RocketRide engine URL. For local RocketRide this is usually `http://localhost:5565`. |
| `ROCKETRIDE_APIKEY` | Auth used by the RocketRide SDK. Local engines may still require this. |
| `GMI_API_KEY` | GMI Cloud API key. |
| `GMI_BASE_URL` | GMI OpenAI-compatible base URL, usually `https://api.gmi-serving.com/v1`. |
| `GMI_VISION_MODEL` | Vision model, currently `google/gemini-3.1-flash-lite-preview`. |
| `ROCKETRIDE_BRIDGE_COMMAND_URL` | URL RocketRide should POST the robot command to. |
| `ROCKETRIDE_BRIDGE_COMMAND_URL_PATTERN` | Whitelist pattern for RocketRide's HTTP tool node. Must match the bridge command URL. |
| `ANDROID_STATUS_URL` | Optional fallback callback URL for Android status events. Android can also send `callback_url` per mission. |
| `ROCKETRIDE_DISPATCH_MODE` | `rocketride` for normal dispatch, `direct` for local bridge debugging without the RocketRide agent. |
| `BRIDGE_CALLBACK_TIMEOUT_SECONDS` | HTTP timeout for direct bridge dispatch mode. |
| `REQUEST_TIMEOUT_SECONDS` | Timeout for GMI HTTP requests. |

## Run Locally

```bash
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8080
```

Use `--reload` during development if you want code changes to take effect automatically:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

If Android is running on a physical phone, it cannot call `localhost:8080` unless the server is on the phone. Use a LAN IP or a tunnel such as ngrok, and make sure the tunnel is connected.

## RocketRide Notes

Normal dispatch uses `pipelines/robot_dispatch.pipe`. That pipeline contains:

- A webhook source.
- A question node.
- A RocketRide agent.
- A GMI LLM control node for the agent.
- A `tool_http_request` node that posts to the laptop bridge.

The code starts a fresh RocketRide dispatch task for each mission with a runtime-generated `project_id`. This avoids stale-task collisions such as:

```text
Pipeline is already running.
```

If RocketRide reports:

```text
No module named 'jmespath'
```

install `jmespath` in the RocketRide engine environment, not only in this repo's `.venv`. The RocketRide engine is a separate Python environment.

## API Reference

### `GET /health`

Returns basic process health.

```json
{
  "ok": true,
  "dispatch_mode": "rocketride",
  "bridge_command_url": "http://localhost:8000/robot/command",
  "missions": 1
}
```

### `POST /android/report`

Starts a mission from an Android report photo.

Multipart fields:

| Field | Required | Notes |
| --- | --- | --- |
| `image` | yes | Report photo. `photo` or `file` are also accepted. |
| `mission_id` | no | If omitted, the server creates `cleanup-<uuid>`. |
| `trash_type` | no | Android's guessed trash type, if available. |
| `location` | no | String or JSON string. |
| `callback_url` | no | Per-mission Android status callback URL. |

JSON is also accepted using `image_base64`, `image`, or `photo_base64`.

Example response when trash is confirmed:

```json
{
  "mission_id": "cleanup-37c50de6",
  "status": "confirmed",
  "classification": {
    "is_trash": "yes",
    "trash_type": "cup",
    "confidence": 0.95,
    "rejection_reason": "",
    "visual_evidence": "An empty clear plastic cup is sitting on the table."
  },
  "next": "dispatching_robot"
}
```

Example response when rejected:

```json
{
  "mission_id": "cleanup-123",
  "status": "rejected",
  "classification": {
    "is_trash": "uncertain",
    "trash_type": "unknown",
    "confidence": null,
    "rejection_reason": "Image is too blurry.",
    "visual_evidence": ""
  }
}
```

### `GET /missions/{mission_id}`

Returns the full in-memory mission state. Android should poll this endpoint after `POST /android/report`.

```bash
curl -sS http://localhost:8080/missions/cleanup-37c50de6 | python3 -m json.tool
```

Important fields:

- `state`: current mission state.
- `classification`: GMI report classification.
- `dispatch`: RocketRide dispatch result.
- `bridge`: bridge result after parsing.
- `verification`: cleanup verification result.
- `error`: error message if state is `error`.
- `updated_at`: last state transition timestamp.
- `events`: chronological state history.

### `GET /missions`

Lists all missions in memory.

```bash
curl -sS http://localhost:8080/missions | python3 -m json.tool
```

### `POST /bridge/callback`

Lets the laptop bridge report a later result if the initial dispatch response is pending.

```json
{
  "mission_id": "cleanup-37c50de6",
  "status": "COMPLETE",
  "steps": 1,
  "distance": 42,
  "hub_response": "OK:SAS:COMPLETE:1:42"
}
```

Supported statuses:

- `COMPLETE`
- `FAILED`
- Anything else is treated as pending and moves the mission to `awaiting_bridge_callback`.

### `POST /android/confirmation`

Android sends the after-cleanup confirmation photo here.

Multipart fields:

| Field | Required | Notes |
| --- | --- | --- |
| `image` | yes | Confirmation photo. `photo` or `file` are also accepted. |
| `mission_id` | yes | The mission being verified. |

JSON with `image_base64` is also accepted.

The response is the updated mission object. Terminal verification states are `cleaned` and `not_cleaned`.

## Mission States

| State | Meaning | Android behavior |
| --- | --- | --- |
| `received` | Report accepted by server. | Show loading. |
| `classifying` | GMI is classifying the report image. | Show AI scan/loading. |
| `rejected` | Image was not confirmed as trash. | Stop mission and show rejection. |
| `confirmed` | Trash was confirmed. | Show dispatch starting. |
| `dispatching` | Robot dispatch is running through RocketRide or direct mode. | Keep polling. Do not timeout at 30 seconds. |
| `awaiting_bridge_callback` | Robot command was accepted, but final bridge result has not arrived. | Keep polling. |
| `robot_failed` | Robot did not find/clean the target. | Stop mission and show failure. |
| `awaiting_confirmation_photo` | Robot reported complete. | Ask user to take/send confirmation photo. |
| `verifying_cleanup` | GMI is checking the confirmation photo. | Show verification loading. |
| `cleaned` | Cleanup verified. | Mission success. |
| `not_cleaned` | Trash still appears present or verification was uncertain. | Show retry/failure path. |
| `error` | A classification, dispatch, or verification error occurred. | Show error and include `error` text if useful. |

Terminal states:

- `rejected`
- `robot_failed`
- `cleaned`
- `not_cleaned`
- `error`

## Android Polling Guidance

Android should poll:

```text
GET /missions/{mission_id}
```

after `POST /android/report` returns a mission ID.

Recommended behavior:

- Poll every 1-3 seconds while the mission is active.
- Use `state`, not just HTTP success, to decide what screen to show.
- Treat `awaiting_confirmation_photo` as a successful dispatch state.
- Use `updated_at` and `events` for diagnostics.
- Do not mark the mission as stalled after only 30 seconds during `dispatching`.

Robot dispatch can take slightly more than 30 seconds. We saw a real mission move from `dispatching` to `awaiting_confirmation_photo` after about 32 seconds:

```text
dispatching -> awaiting_confirmation_photo
bridge_response: OK:SAS:COMPLETE:1:42
```

Use a longer dispatch timeout, such as 90-120 seconds. The Android "Retry Sync" button usually appears to fix the screen because it refetches the mission and discovers the backend already advanced to `awaiting_confirmation_photo`.

## Status Callbacks

Polling is enough for Android, but callbacks are also supported.

The server sends callback events to:

1. The mission's `callback_url`, if Android provided one.
2. `ANDROID_STATUS_URL`, if configured.

Callback payloads include:

```json
{
  "mission_id": "cleanup-37c50de6",
  "event": "cleanup_complete_take_confirmation_photo",
  "state": "awaiting_confirmation_photo",
  "message": "cleanup complete, please take a confirmation photo"
}
```

Callbacks are best-effort. The mission state remains the source of truth.

## Useful Debug Commands

Check health:

```bash
curl -sS http://localhost:8080/health | python3 -m json.tool
```

List missions:

```bash
curl -sS http://localhost:8080/missions | python3 -m json.tool
```

Inspect one mission:

```bash
curl -sS http://localhost:8080/missions/cleanup-37c50de6 | python3 -m json.tool
```

Validate local config and pipeline files:

```bash
python check.py
```

Check Python syntax:

```bash
python -m py_compile main.py check.py rocketride_cleanup/*.py
```

## Common Issues

### `GET /favicon.ico 404`

Harmless. A browser requested a favicon and this API does not serve one.

### `GET / 404`

Harmless if you opened the base URL in a browser. Use `/health` or `/missions`.

### `POST /android/report 503`

Usually means the orchestration layer could not reach GMI or RocketRide, or a required environment variable is missing. Check the mission `error` field and the Uvicorn logs.

### `No authorization provided`

RocketRide did not receive usable auth. Check `ROCKETRIDE_APIKEY` in `.env` and restart the FastAPI server after changing env vars.

### `No module named 'jmespath'`

Install `jmespath` in the RocketRide engine Python environment. Installing it only in this repo's `.venv` is not enough for RocketRide's internal tool execution.

### `Pipeline is already running.`

RocketRide thinks the same pipeline task is still active. The current dispatch code creates a fresh runtime `project_id` per mission to avoid this. Restart the FastAPI server after updating the code.

### Android shows timeout, then Retry Sync fixes it

The backend likely finished just after Android's local timeout. Check:

```bash
curl -sS http://localhost:8080/missions/<mission_id> | python3 -m json.tool
```

If state is `awaiting_confirmation_photo`, Android should show the confirmation photo prompt.

### ngrok says reconnecting

The phone cannot reliably poll while the tunnel is reconnecting. Restart ngrok or use the machine's LAN IP when possible.

## Notes For Future Work

- Add persistent mission storage if missions must survive server restarts.
- Add authentication before exposing this beyond a local demo/tunnel.
- Consider server-sent events or WebSockets if Android polling becomes noisy.
- Add structured logs for GMI calls, RocketRide dispatch start/end, and bridge callbacks.
