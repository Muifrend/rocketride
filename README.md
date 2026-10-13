# Trash Patrol — Backend

**FastAPI mission orchestration and RocketRide robot dispatch.**

Part of **[Trash Patrol — World Cup Edition](https://github.com/Muifrend/Trash_Patrol)**, the first-place project at the Google I/O Kickoff: Pre-World Cup Hack. [Project overview and developer contribution](https://github.com/Muifrend/Trash_Patrol#my-contribution-and-team) · [Demo](https://www.youtube.com/shorts/33VFazESk8A) · [Published results](https://www.gmicloud.ai/en/blog/what-32-teams-built-for-the-world-cup-in-one-day)

This repository contains Trash Patrol's application backend; RocketRide is the workflow engine it uses for dispatch.

## Responsibilities

- Accept report photos from the Android app.
- Call Gemini through GMI Cloud directly to classify trash.
- Track mission state in memory.
- Dispatch a confirmed mission through RocketRide's `robot_dispatch.pipe`.
- Process the robot bridge's response or callback.
- Accept a confirmation photo and call GMI vision again to verify cleanup.
- Expose mission state for Android polling.

The backend owns the mission lifecycle. RocketRide handles the dispatch pipeline; the LEGO bridge owns hardware communication and robot movement.

## Components

| Component | Repository |
| --- | --- |
| Project overview and Android app | [Trash_Patrol](https://github.com/Muifrend/Trash_Patrol) |
| Backend | **This repository** |
| Laptop bridge and hub program | [LEGO_bridge](https://github.com/Muifrend/LEGO_bridge) |

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
```

Fill in `.env` with your GMI credentials, RocketRide connection settings, and the reachable bridge URL. Normal dispatch requires a running RocketRide engine.

```bash
python check.py
uvicorn main:app --host 0.0.0.0 --port 8080
```

Point Android Settings at this backend's reachable address. A phone cannot use the laptop's `localhost`.

For local bridge debugging, `ROCKETRIDE_DISPATCH_MODE=direct` sends the robot command without RocketRide. GMI vision credentials are still required for report classification and confirmation.

## API at a glance

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process health and dispatch configuration |
| `POST /android/report` | Classify a report and start a mission |
| `GET /missions/{mission_id}` | Read mission state |
| `GET /missions` | List in-memory missions |
| `POST /bridge/callback` | Receive a later robot result |
| `POST /android/confirmation` | Verify a confirmation photo |

## Integration limits

- Missions reset when the process restarts.
- These are demo APIs without application-level authentication.
- The app's 30-second state timeout and the current bridge's 20-second command wait can interrupt longer robot operations.
- The dispatch payload includes `max_steps=15`, but the current bridge accepts only `command` and `mission_id`; the hub uses its own fixed 15-step default.
- Bridge mock mode checks HTTP command handling. Its `MOCK_OK` response does not simulate a complete cleanup result.

## Documentation

- [Environment, API payloads, mission states, and troubleshooting](docs/DEVELOPMENT.md)
- [Hackathon case study](https://github.com/Muifrend/Trash_Patrol/blob/main/HACKATHON.md)
