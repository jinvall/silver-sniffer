# GitHub Copilot / AI agent instructions — Silver Sniffer

Purpose
- Provide concise, actionable guidance for an AI coding agent working on this repository.
- Prioritize reliable, CPU-first movement/location tracking and safe integration with existing telemetry (WiFi/BLE/video).

Quick context (read first)
- Core components:
  - `rf_scout_wifi/` — ESP32 sniffer firmware (UART JSON frames).
  - `ingest.py` — canonical Python entrypoint: serial + BLE → WebSocket (`ws://:8765`) + Parquet persistence (`wifi_capture.parquet`, `ble_capture.parquet`).
  - `video_tracker.py` — CPU-only (MOG2 + Kalman) video tracking; emits `video_track` events.
  - `ws_broadcast.py` — websocket server used by the dashboard.
  - `analytics_server.py` — Parquet-backed REST API (port 8090).
  - `dashboard/` — static UI (index + analytics + camera panels).
- Ports & files the system expects:
  - WebSocket: `ws://localhost:8765`
  - Analytics REST: `http://localhost:8090`
  - Parquet roots: `wifi_capture.parquet`, `ble_capture.parquet`

Primary goals for the agent
1. Keep the system runnable and well-documented (fast developer feedback loop).
2. Maintain CPU-first tracking (video tracker should run on CPU by default).
3. Preserve event schema/backwards compatibility for analytics and dashboard.
4. Require tests, README updates, and CI changes for any dependency or API changes.

How to run locally (smoke tests)
- Setup
  - `python -m venv venv && source venv/bin/activate`
  - `pip install -r requirements.txt`
- Ingest (with optional CPU video)
  - `python ingest.py` (default: serial `/dev/ttyUSB0`, websocket on 8765)
  - With cameras: `python ingest.py --video-sources 0,rtsp://... --px-to-m 0.012`
- Analytics server: `python analytics_server.py` (port 8090)
- Dashboard: `cd dashboard && python -m http.server 8000` (open http://localhost:8000)

Event schemas (important — do NOT change without updating consumers)
- `wifi` / `ble`: already in repo — preserve fields used by `analytics_server.py` and `dashboard/analytics.js`.
- `video_track` (emitted by `video_tracker.py`):
```json
{
  "type": "video_track",
  "camera": "cam1",
  "id": "cam1-1",
  "ts_us": 167...,            
  "bbox": [x,y,w,h],
  "cx": 100.5,
  "cy": 200.5,
  "x_m": 1.2,
  "y_m": 2.4,
  "vx": 0.0,
  "vy": -1.2,
  "frame_w": 640,
  "frame_h": 480,
  "confidence": 1234.0
}
```
- Compatibility rule: if you change an event field name, update `dashboard/`, `analytics_server.py`, and any downstream consumers in the same PR.

Coding rules & conventions
- Tests: add unit tests for algorithmic changes (e.g., `kalman.py`, `tracking_engine.py`, `video_tracker.py`).
- New Python dependency → update `requirements.txt`, `README.md`, and CI config.
- Logging: use structured logging (existing pattern in `ingest.py`).
- Backward compatibility: avoid changing message schemas or Parquet layout without migration code and clear PR description.
- Performance: prefer CPU-friendly implementations; explicitly justify GPU-only choices in PR description.

PR checklist (must satisfy before merging)
- [ ] Code compiles: `python -m py_compile` passes for edited files.
- [ ] Unit tests added/updated and passing.
- [ ] `requirements.txt` updated for new deps and `README.md` updated with run instructions.
- [ ] Manual smoke test recorded in PR description (commands used + expected results).
- [ ] If schema/API changed: update `dashboard/`, `analytics_server.py`, and add a migration or version note.
- [ ] CI changes (if required) included in the PR.

Common tasks & example prompts for the agent
- Improve tracker accuracy (CPU-first):
  - "Add an optional CPU-only contour-filter + size gating to `video_tracker.py` and unit tests for `Kalman2D`." 
- Add RTSP→HLS proxy helper script:
  - "Create `tools/rtsp_proxy.py` (ffmpeg wrapper) + README example; expose HLS at `/tmp/stream.m3u8`. Add HTTP status check in dashboard." 
- Add CI coverage for OpenCV dependency:
  - "Modify CI to pip-install `opencv-python-headless` and run a lightweight smoke test that imports `video_tracker`." 
- Add unit tests:
  - "Add tests for `kalman.py` and `tracking_engine.py` that validate state convergence and removal behavior."

Acceptance criteria (example)
- Feature implemented and `python -m py_compile` passes.
- Unit tests covering behavior added; CI passes.
- `README.md` updated with run instructions and examples.
- Dashboard shows live data for the change (if UI-affecting).

Debugging & troubleshooting tips
- WebSocket issues: verify `ingest.py` is running and port `8765` is open.
- Camera/RTSP issues: test with `ffplay <source>` or `ffmpeg -i <source>` before wiring to tracker.
- Parquet persistence: inspect `wifi_capture.parquet` and `ble_capture.parquet` with `polars.read_parquet()`.

Do / Don't (summary)
- Do: add tests, update docs, keep CPU-first approaches, preserve event schemas.
- Don’t: add secrets/config in source, change message schemas silently, or introduce heavy GPU-only dependencies without explicit approval.

If uncertain
- Open a short issue describing the trade-offs and preferred test plan and ask for a maintainer decision.

Maintenance & triage
- Small, focused PRs are preferred.
- For cross-cutting changes (schema, CI, major deps), include a migration plan and a rollback strategy.

— end —
