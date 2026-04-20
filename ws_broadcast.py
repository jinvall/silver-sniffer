import asyncio
import json
import time
import logging

try:
    import websockets
except ImportError:
    websockets = None
    logging.warning("websockets is not installed; server/broadcast functionality is disabled")
MAX_HISTORY = 60 * 60  # 1 hour
clients = set()
# --- Historical storage for analytics ---
HISTORY = {
    "wifi": [],
    "ble": [],
    "movement": [],
    "vendors": {},
    "fingerprints_wifi": {},
    "fingerprints_ble": {},
}



def prune_history():
    cutoff = time.time() - MAX_HISTORY
    HISTORY["wifi"] = [x for x in HISTORY["wifi"] if x["t"] >= cutoff]
    HISTORY["ble"] = [x for x in HISTORY["ble"] if x["t"] >= cutoff]

def make_timeline(records):
    if not records:
        return {"start": 0, "end": 0, "interval": 5, "devices": []}

    start = records[0]["t"]
    end = records[-1]["t"]

    # group by BSSID/MAC
    devices = {}
    for r in records:
        key = r.get("bssid") or r.get("mac")
        if key not in devices:
            devices[key] = {"id": key, "rssi": [], "channel": [], "t": []}
        devices[key]["rssi"].append(r["rssi"])
        devices[key]["channel"].append(r["channel"])
        devices[key]["t"].append(r["t"])

    return {
        "start": start,
        "end": end,
        "interval": 5,
        "devices": list(devices.values())
    }

async def handler(websocket):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)   # discard avoids KeyError

async def broadcast(obj):
    if not clients:
        return

    msg = json.dumps(obj)
    dead = []

    # iterate over a snapshot to avoid mutation errors
    for c in list(clients):
        try:
            await c.send(msg)
        except:
            dead.append(c)

    # remove dead clients AFTER iteration
    for d in dead:
        clients.discard(d)

async def start_server():
    if websockets is None:
        raise RuntimeError("websockets is required for websocket server")
    return await websockets.serve(handler, "0.0.0.0", 8765)

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

ANALYTICS_PORT = 8081

class AnalyticsHandler(BaseHTTPRequestHandler):
    def _json(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/analytics/timeline_wifi":
            return self._json(make_timeline(HISTORY["wifi"]))

        if self.path == "/analytics/timeline_ble":
            return self._json(make_timeline(HISTORY["ble"]))

        if self.path == "/analytics/movement":
            return self._json({"movement": HISTORY["movement"]})

        if self.path == "/analytics/vendors":
            return self._json({"vendors": HISTORY["vendors"]})

        if self.path == "/analytics/fingerprint_wifi":
            return self._json({"devices": list(HISTORY["fingerprints_wifi"].values())})

        if self.path == "/analytics/fingerprint_ble":
            return self._json({"devices": list(HISTORY["fingerprints_ble"].values())})

        self.send_response(404)
        self.end_headers()

def start_analytics_server():
    server = HTTPServer(("0.0.0.0", ANALYTICS_PORT), AnalyticsHandler)
    server.serve_forever()

