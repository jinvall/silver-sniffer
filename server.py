import asyncio
import threading
from ws_broadcast import start_server
from ingest import ingest_loop, HISTORY, make_timeline
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

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

if __name__ == "__main__":
    # Start analytics HTTP server
    threading.Thread(target=start_analytics_server, daemon=True).start()

    # Start WebSocket server
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_server())

    # Start ingest loop
    loop.run_until_complete(ingest_loop())
