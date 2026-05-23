# RocketRide Cleanup Mission Coordinator

This repo coordinates the Android report, RocketRide inference, laptop bridge dispatch, and cleanup verification flow.

## Flow

1. Android posts a report photo to `POST /android/report` with `mission_id`, `trash_type`, `location`, and optional `callback_url`.
2. GMI Cloud's OpenAI-compatible Gemini vision model classifies the image and returns `is_trash`, `trash_type`, confidence, and evidence.
3. If the report is rejected or uncertain, the mission stops and Android is notified.
4. If confirmed, `pipelines/robot_dispatch.pipe` uses a RocketRide agent plus `tool_http_request` to POST exactly one `SEARCH_AND_SWEEP` command with `max_steps=15` to the laptop bridge.
5. The bridge either returns `OK:SAS:COMPLETE` / `OK:SAS:FAILED` immediately, or calls `POST /bridge/callback` later.
6. On complete, Android is notified to take a confirmation photo.
7. Android posts that photo to `POST /android/confirmation`.
8. The same GMI Cloud Gemini vision model verifies the confirmation photo and returns `cleaned` or `not_cleaned`.

The runtime uses one provider for model calls: GMI Cloud at `GMI_BASE_URL` with `GMI_API_KEY`. The RocketRide pipeline files remain in the repo for visual workflow ownership and robot dispatch; native image inference is sent directly to GMI's OpenAI-compatible `/chat/completions` endpoint so the image payload stays multimodal.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
python check.py
```

Fill in `.env`, especially the RocketRide, GMI, and bridge URL variables.

## Run

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

Start the laptop bridge separately, for example:

```bash
cd ../poop_patrol_bridge_starter
source .venv/bin/activate
python bridge_server.py --mock
```

## Android Endpoints

`POST /android/report`

Multipart fields:

- `image`: report photo
- `mission_id`: optional
- `trash_type`: optional Android/Gemini guess
- `location`: string or JSON string
- `callback_url`: optional per-mission status callback

JSON is also accepted with `image_base64`.

`POST /android/confirmation`

Same photo format, with required `mission_id`.

`GET /missions/{mission_id}`

Returns in-memory mission state for debugging.
