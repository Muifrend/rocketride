# Trash Patrol — World Cup Edition
### Hackathon Submission Document

---

## Overview

Trash Patrol is an autonomous trash detection and cleanup system built around a sense-decide-act-verify loop. Users report litter through a lightweight Android app, RocketRide handles the entire backend intelligence — image classification, mission orchestration, robot dispatch, and cleanup verification — and Gemini via GMI Cloud provides the visual reasoning at every decision point. The system is designed to be city-scale: anyone who wants a cleaner neighborhood can report trash in seconds, and the infrastructure handles the rest.

---

## Impact

Litter management is a universal urban problem. Current solutions rely on scheduled city crews or reactive 311-style complaint systems — both slow and expensive. Trash Patrol flips the model: make reporting frictionless for citizens, and let autonomous robots handle the physical response.

If deployed at scale, this architecture could meaningfully reduce street-level litter in dense urban environments while lowering the labor cost of cleanup operations. The individual act of reporting is trivial; the system effect is compounding. The more people report, the more ground the robots cover, and the cleaner the city gets.

---

## Architecture

The project is split into three deliberately separated layers:

**1. Consumer App (Android)**
A thin, purpose-built reporting interface. Users photograph suspected litter, attach GPS coordinates, and submit the report in seconds. The app is intentionally minimal — it exists to capture the report and hand it off. All intelligence lives in RocketRide downstream, which keeps the app easy to maintain and the core logic centralized and independently iterable.

**2. RocketRide Orchestration Layer**
The core product. RocketRide owns the entire backend intelligence stack — it receives reports from Android, calls GMI Cloud to classify images with Gemini, manages the full mission state machine, dispatches the robot through its agentic pipeline, handles bridge callbacks, and calls GMI Cloud again to verify cleanup from the confirmation photo. Every AI decision and every state transition in the system runs through RocketRide.

The mission state machine covers the complete flow — from `received` through `classifying`, `confirmed`, `dispatching`, `awaiting_confirmation_photo`, `verifying_cleanup`, to terminal states `cleaned` or `rejected`. Android polls RocketRide's `/missions/{mission_id}` endpoint to render the correct screen at each stage.

**3. Robot Bridge**
A standardized interface layer RocketRide calls to move the robot. The physical hardware is fully abstracted — swapping out the robot platform requires no changes to the AI or dispatch logic.

---

## Use of AI

AI is not a peripheral feature in Trash Patrol — it is the decision engine at every critical step of the pipeline, and RocketRide is the system that drives it.

### RocketRide — The Core Orchestration Engine

RocketRide is the backbone of the entire system. It is responsible for:

- **Trash classification** — calling GMI Cloud's Gemini vision model on every incoming report photo and deciding whether a mission should proceed
- **Mission lifecycle management** — owning all state transitions from report receipt through robot dispatch to final verification
- **Agentic robot dispatch** — running the `robot_dispatch.pipe` pipeline that interprets the confirmed mission and issues the `SEARCH_AND_SWEEP` command to the robot
- **Cleanup verification** — calling GMI Cloud a second time on the confirmation photo to independently verify the robot actually cleaned the target

The dispatch pipeline itself includes a webhook source, a question node for context framing, a RocketRide agent for task interpretation, a GMI LLM control node providing reasoning capability, and an HTTP tool node that posts the final command to the robot bridge. Each mission generates a fresh runtime `project_id` to prevent task collisions between concurrent missions.

The agent is constrained to issue exactly one command:

```json
{
  "command": "SEARCH_AND_SWEEP",
  "mission_id": "...",
  "max_steps": 15
}
```

This gives the agent full reasoning capability over the mission context while ensuring it produces a single, predictable physical action.

### Google Gemini (via GMI Cloud) — Visual Reasoning

Gemini is the multimodal model RocketRide calls at the two most important decision points.

**Step 1 — Trash Classification**
RocketRide sends the submitted photo (with GPS context) to Gemini as base64 image data and receives a strict JSON classification:

```json
{
  "is_trash": "yes",
  "trash_type": "cup",
  "confidence": 0.95,
  "rejection_reason": "",
  "visual_evidence": "An empty clear plastic cup is sitting on the table."
}
```

Gemini's judgment is the gate. If it confirms trash, RocketRide advances the mission to dispatch. If not, the mission is rejected with a human-readable reason surfaced in the Android UI.

**Step 2 — Cleanup Verification**
After the robot reports completion, RocketRide sends the confirmation photo to Gemini and receives an independent verdict:

```json
{
  "cleaned": "cleaned",
  "confidence": 0.95,
  "reason": "",
  "needs_retry": false
}
```

RocketRide doesn't assume the robot succeeded — it verifies with Gemini. This closes the loop with visual ground truth.

### GMI Cloud — Model Serving

GMI Cloud provides the OpenAI-compatible inference endpoint RocketRide uses for all Gemini vision calls, configured via `GMI_API_KEY`, `GMI_BASE_URL`, and `GMI_VISION_MODEL`. The current model is `google/gemini-3.1-flash-lite-preview`. GMI serves inference at every AI-driven decision point in the RocketRide pipeline.

### Google AI Studio — Prototyping & Development

Google AI Studio served as the primary development environment for the Android app. Rather than writing boilerplate from scratch, natural language commands directed the coding agent to generate layouts, Jetpack Compose screens, and Gradle configurations in real time.

Key contributions:
- **Generative theming** — a single prompt ("make it World Cup themed") produced the full 2D football pitch canvas, referee card state transitions (red cards for rejections, yellow cards for caution states), and a Material 3 midnight-dark arena theme with pitch-green accents and World Cup gold typography.
- **Live compilation feedback** — each change was immediately verified and installed on a browser-based streaming emulator, giving instant visual feedback without manual build cycles.
- **Expert diagnostics** — when sideloading to a physical device triggered a `SecurityException` on ADB permission grants, AI Studio identified the root cause (Xiaomi MIUI/HyperOS shell restrictions blocking `INSTALL_GRANT_RUNTIME_PERMISSIONS`) and guided the fix via Jetpack Compose's Accompanist Permissions framework.
- **Direct workspace edits** — the agent used `edit_file` and `multi_edit_file` tools directly in the project directory, keeping the package identifier, app name, and `metadata.json` in sync throughout development with no manual copy-pasting.

---

## Technology Summary

| Layer | Technology | Role |
|---|---|---|
| Core Orchestration | RocketRide | Full mission lifecycle: classification, dispatch, verification |
| Visual AI | Google Gemini | Trash classification & cleanup verification |
| Model Serving | GMI Cloud | OpenAI-compatible Gemini vision inference |
| Consumer App | Android / Jetpack Compose | GPS-tagged litter reporting with live mission tracking |
| Development | Google AI Studio | Generative prototyping, theming, diagnostics |

---

## Core Loop

```
User photographs litter + GPS coordinates
              ↓
RocketRide calls Gemini → confirmed or rejected
              ↓
RocketRide pipeline dispatches robot via SEARCH_AND_SWEEP
              ↓
Robot executes mission, reports completion
              ↓
User submits confirmation photo
              ↓
RocketRide calls Gemini → cleanup verified, mission closed
```

> "RocketRide is the intelligence layer that ties everything together — it calls Gemini to see, uses its agentic pipeline to act, and calls Gemini again to verify. The Android app is the eyes on the street; RocketRide is the brain that turns a photo into a clean neighborhood."
