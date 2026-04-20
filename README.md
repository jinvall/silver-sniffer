# Silver Sniffer — quickstart

Short: ESP32-based WiFi/BLE sniffer + Python ingestion, real-time WebSocket broadcast and Parquet-backed analytics with a small dashboard.

## Quickstart (local development) ⚡

1. Prepare Python virtualenv

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Start the ingest service (reads ESP32 on serial + optional local BLE)

```bash
# default reads /dev/ttyUSB0 @ 115200 and starts websocket on ws://0.0.0.0:8765
python ingest.py

# common options
python ingest.py --serial /dev/ttyUSB0 --baud 115200      # set serial port/baud
python ingest.py --no-ble                                # disable local BLE scanning
python ingest.py -v                                      # verbose logging
python ingest.py --parquet-wifi wifi_capture.parquet --parquet-ble ble_capture.parquet

# Start ingest with CPU-only video tracking (MOG2+Kalman)
# `--video-sources` accepts camera indices or URLs (comma-separated). Requires OpenCV.
python ingest.py --video-sources 0,rtsp://camera/stream --px-to-m 0.012
```

3. Start the analytics REST server (Parquet → aggregated endpoints)

```bash
python analytics_server.py   # serves on http://0.0.0.0:8090
```

4. Open the dashboard

```bash
# serve static UI files and open http://localhost:8000
cd dashboard && python -m http.server 8000
# dashboard expects analytics on http://localhost:8090 and websocket on ws://localhost:8765
```

Single-command (recommended for development)

```bash
# starts ingest (ws), analytics (Parquet REST) and a static dashboard server
python run_all.py
```

Options:
- `--dashboard-port` (default 8000)
- `--analytics-port` (default 8090)
- `--serial` and `--baud` to override serial device
- `--no-ble` to disable local BLE scanning
- `--no-dashboard` to skip serving static files from `dashboard/`

Container & service options

- Docker (build & run):

```bash
docker build -t silver-sniffer:latest .
docker run --rm -p 8000:8000 -p 8090:8090 -p 8765:8765 \
  -v "$PWD/wifi_capture.parquet:/app/wifi_capture.parquet" \
  -v "$PWD/ble_capture.parquet:/app/ble_capture.parquet" \
  silver-sniffer:latest
```

- docker-compose (convenience):

```bash
docker-compose up --build
```

- systemd (example unit provided in `packaging/`):

  1. Copy and edit `packaging/silver-sniffer.service` to set `User` and paths.
  2. sudo cp packaging/silver-sniffer.service /etc/systemd/system/silver-sniffer.service
  3. sudo systemctl daemon-reload
  4. sudo systemctl enable --now silver-sniffer

- Health endpoint: `http://localhost:8090/health` — reports analytics/parquet/ws/dashboard status (JSON).

Alternative: run `server.py` for a simple in-memory HTTP view (port 8081) and the websocket + ingest loop together:

```bash
python server.py
```

## Files of interest 🔎

- `rf_scout_wifi/` — ESP32 sniffer firmware (build with `idf.py build`) and prints JSON frames to UART
- `ingest.py` — canonical ingestion service (serial + BLE → websocket + Parquet). BLE packets from either the ESP32 or local scanner now include ``distance_m``, ``movement`` and ``color`` attributes calculated with a simple log‑distance model.
Use CLI options described above.

  The `/analytics/wifi_timeline` endpoint now reports the *number of unique BSSIDs* seen per bucket (previously it returned the raw frame count). This makes the timeline reflect how many devices were present rather than total frames; dashboards will automatically label the graph accordingly.
  Additionally the response includes a list of BSSIDs for each bucket so the UI tooltip can display exactly which devices were visible when hovering over the timeline.
- `ws_broadcast.py` — WebSocket server and live HISTORY used by the dashboard
- `analytics_server.py` — Parquet-backed REST analytics (port 8090)
- `dashboard/` — static front-end (JS + HTML)
- `wifi_capture.parquet`, `ble_capture.parquet` — Parquet dataset roots (created in repo root)

## Developer notes & gotchas ⚠️

- Serial permissions: add your user to the `dialout` group or run with sudo if `/dev/ttyUSB0` is restricted.
- BLE scanning requires appropriate OS permissions (Linux: BlueZ + capabilities).
- CPU-only video tracking uses OpenCV (install via `pip install opencv-python-headless`) — add `--video-sources` to `ingest.py` to enable the tracker.
- The browser cannot play RTSP directly; use an HLS or WebSocket proxy (ffmpeg examples are in `dashboard/` comments) to surface RTSP streams to the UI.
- There are two ingestion variants present (`ingest.py` and `digesterpy`). `ingest.py` is the canonical entrypoint we recommend.
- Parquet files are appended in batches (wifi: 500 rows, ble: 200 rows) — check `ingest.py` if you need different thresholds.

## Testing

Unit tests live under `tests/` and are executed with `PYTHONPATH=. pytest` (preferred) or `python -m unittest discover -v`.
New tests include BLE distance/movement logic, WiFi movement delta checks, and analytics time bucketing.

## CI / build

- Firmware: `cd rf_scout_wifi && idf.py build` (CI pins esp-idf v5.2)
- Python: CI runs `python -m py_compile` across `.py` files; add new dependencies to `requirements.txt` if needed
- Dashboard: `cd dashboard && npm install && npm run build` (Node 20 in CI)

## Contributing

- For UI changes edit `dashboard/dashboard.js` and `dashboard/analytics.js`.
- For ingestion/analytics changes look at `ingest.py`, `ws_broadcast.py`, and `analytics_server.py`.

If you want, I can also add a `CONTRIBUTING.md`, example recordings, or convert this README into a more detailed developer guide — tell me which next. 🚀
