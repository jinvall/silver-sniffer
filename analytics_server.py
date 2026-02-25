#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import socket
import urllib.request

import pyarrow.dataset as ds
import pyarrow.compute as pc

WIFI_PARQUET = "wifi_capture.parquet"
BLE_PARQUET = "ble_capture.parquet"

ANALYTICS_HOST = "0.0.0.0"
ANALYTICS_PORT = 8090

# hard caps so responses don't explode the browser
MAX_TIMELINE_BUCKETS = 500
MAX_HEATMAP_CELLS = 20000

# convoy-specific caps
MAX_CONVOY_DEVICES = 200
MIN_BUCKETS_FOR_DEVICE = 3
MIN_JACCARD = 0.3
MAX_CONVOYS = 20

# persisted list of blocked devices (used to filter 'junk' from convoys / UI)
BLOCKED_JSON = "blocked_devices.json"
blocked_set = set()

def load_blocked():
    global blocked_set
    try:
        if os.path.exists(BLOCKED_JSON):
            with open(BLOCKED_JSON, 'r') as f:
                arr = json.load(f)
                blocked_set = set(arr or [])
        else:
            blocked_set = set()
    except Exception:
        blocked_set = set()

def save_blocked():
    try:
        with open(BLOCKED_JSON, 'w') as f:
            json.dump(sorted(list(blocked_set)), f)
    except Exception:
        pass

# attempt load at import time (best-effort)
load_blocked()


# -------------------------------------------------------------------
# SAFETY: Remove 0-byte parquet files before loading dataset
# -------------------------------------------------------------------
def safe_dataset(path):
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            for f in files:
                full = os.path.join(root, f)
                if full.endswith(".parquet") and os.path.getsize(full) == 0:
                    print("Removing empty parquet file:", full)
                    os.remove(full)
    return ds.dataset(path, format="parquet")


# -------------------------------------------------------------------
# TIME RANGE PARSING
# -------------------------------------------------------------------
def parse_time_range(table, qs):
    """
    Returns (filtered_table, min_ts_s, max_ts_s) where min/max are epoch seconds
    after applying optional ?since=, ?start=, ?end= filters.
    """
    col = table["timestamp"]
    ts_s = pc.cast(col, "timestamp[s]", safe=False)
    ts_int = pc.cast(ts_s, "int64")

    # defaults: full range
    min_ts = pc.min(ts_int).as_py()
    max_ts = pc.max(ts_int).as_py()

    def get_int_param(name):
        try:
            return int(qs.get(name, [None])[0])
        except Exception:
            return None

    since = get_int_param("since")
    start = get_int_param("start")
    end = get_int_param("end")

    if start is not None:
        min_ts = max(min_ts, start)
    elif since is not None:
        min_ts = max(min_ts, max_ts - since)

    if end is not None:
        max_ts = min(max_ts, end)

    # build mask: min_ts <= ts_int <= max_ts
    mask = pc.and_(
        pc.greater_equal(ts_int, min_ts),
        pc.less_equal(ts_int, max_ts),
    )

    filtered = table.filter(mask)
    return filtered, min_ts, max_ts


# -------------------------------------------------------------------
# UNIVERSAL TIMESTAMP → BUCKET LOGIC (works on all PyArrow versions)
# -------------------------------------------------------------------
def bucketize_timestamp(table, bucket_seconds):
    ts = pc.cast(table["timestamp"], "timestamp[s]", safe=False)
    ts_int = pc.cast(ts, "int64")
    bucket = pc.cast(pc.divide(ts_int, bucket_seconds), "int64")
    return bucket


# -------------------------------------------------------------------
# WIFI TIMELINE
# -------------------------------------------------------------------
def wifi_timeline(qs, bucket_seconds=5):
    dset = safe_dataset(WIFI_PARQUET)
    table = dset.to_table(columns=["timestamp", "bssid", "rssi", "channel"])

    if table.num_rows == 0:
        return {"buckets": [], "bucket_seconds": bucket_seconds}

    table, _, _ = parse_time_range(table, qs)
    if table.num_rows == 0:
        return {"buckets": [], "bucket_seconds": bucket_seconds}

    bucket = bucketize_timestamp(table, bucket_seconds)
    table = table.append_column("bucket", bucket)

    # build a mapping from bucket -> set(bssid) to allow tooltip info
    bucket_map = {}
    bssid_col = table["bssid"]
    bucket_col = table["bucket"]
    for bb, b in zip(bssid_col, bucket_col):
        if not bb.is_valid or not b.is_valid:
            continue
        buck = int(b.as_py())
        bucket_map.setdefault(buck, set()).add(bb.as_py())

    # count distinct BSSIDs per bucket (see comment earlier)
    grouped = table.group_by("bucket").aggregate(
        [
            ("bssid", "count_distinct"),
            ("rssi", "mean"),
        ]
    )

    buckets = []
    for b, uniq, avg_rssi in zip(
        grouped["bucket"], grouped["bssid_count_distinct"], grouped["rssi_mean"]
    ):
        buck = int(b.as_py())
        buckets.append(
            {
                "bucket": buck,
                "start_ts": int(buck * bucket_seconds),
                "count": int(uniq.as_py()),
                "avg_rssi": float(avg_rssi.as_py()) if avg_rssi.is_valid else None,
                # include list of bssids for hover tooltips
                "bssids": sorted(bucket_map.get(buck, [])),
            }
        )

    # server-side: ensure chronological order before returning (clients may rely on this)
    buckets.sort(key=lambda x: x["start_ts"])

    if len(buckets) > MAX_TIMELINE_BUCKETS:
        buckets = buckets[-MAX_TIMELINE_BUCKETS:]

    return {"buckets": buckets, "bucket_seconds": bucket_seconds}


# -------------------------------------------------------------------
# WIFI CHANNEL HEATMAP
# -------------------------------------------------------------------
def wifi_channel_heatmap(qs, bucket_seconds=30):
    dset = safe_dataset(WIFI_PARQUET)
    table = dset.to_table(columns=["timestamp", "channel"])

    if table.num_rows == 0:
        return {"cells": [], "bucket_seconds": bucket_seconds}

    table, _, _ = parse_time_range(table, qs)
    if table.num_rows == 0:
        return {"cells": [], "bucket_seconds": bucket_seconds}

    bucket = bucketize_timestamp(table, bucket_seconds)
    table = table.append_column("bucket", bucket)

    grouped = table.group_by(["bucket", "channel"]).aggregate(
        [("channel", "count")]
    )

    cells = []
    for b, ch, count in zip(
        grouped["bucket"], grouped["channel"], grouped["channel_count"]
    ):
        cells.append(
            {
                "bucket": int(b.as_py()),
                "start_ts": int(b.as_py() * bucket_seconds),
                "channel": int(ch.as_py()),
                "count": int(count.as_py()),
            }
        )

    # ensure consistent ordering: primary by time, then by channel
    cells.sort(key=lambda c: (c["start_ts"], c["channel"]))

    if len(cells) > MAX_HEATMAP_CELLS:
        cells = cells[-MAX_HEATMAP_CELLS:]

    return {"cells": cells, "bucket_seconds": bucket_seconds}


# -------------------------------------------------------------------
# BLE TIMELINE
# -------------------------------------------------------------------
def ble_timeline(qs, bucket_seconds=5):
    dset = safe_dataset(BLE_PARQUET)
    table = dset.to_table(columns=["timestamp", "addr", "rssi"])

    if table.num_rows == 0:
        return {"buckets": [], "bucket_seconds": bucket_seconds}

    table, _, _ = parse_time_range(table, qs)
    if table.num_rows == 0:
        return {"buckets": [], "bucket_seconds": bucket_seconds}

    bucket = bucketize_timestamp(table, bucket_seconds)
    table = table.append_column("bucket", bucket)

    grouped = table.group_by("bucket").aggregate(
        [
            ("addr", "count"),
            ("rssi", "mean"),
        ]
    )

    buckets = []
    for b, count, avg_rssi in zip(
        grouped["bucket"], grouped["addr_count"], grouped["rssi_mean"]
    ):
        buckets.append(
            {
                "bucket": int(b.as_py()),
                "start_ts": int(b.as_py() * bucket_seconds),
                "count": int(count.as_py()),
                "avg_rssi": float(avg_rssi.as_py()) if avg_rssi.is_valid else None,
            }
        )

    if len(buckets) > MAX_TIMELINE_BUCKETS:
        buckets = buckets[-MAX_TIMELINE_BUCKETS:]

    return {"buckets": buckets, "bucket_seconds": bucket_seconds}


# -------------------------------------------------------------------
# CONVOY DETECTION (MERGED WIFI + BLE)
# -------------------------------------------------------------------
def convoy_detection(qs, bucket_seconds=30):
    """
    Detects convoys by building presence sets per device (WiFi + BLE merged)
    over a time window defined by ?since / ?start / ?end, bucketized by
    bucket_seconds, and computing Jaccard similarity between devices.
    """
    presence = {}

    # ---- WiFi side ----
    wifi_dset = safe_dataset(WIFI_PARQUET)
    wifi_table = wifi_dset.to_table(columns=["timestamp", "bssid"])
    if wifi_table.num_rows > 0:
        wifi_table, _, _ = parse_time_range(wifi_table, qs)
        if wifi_table.num_rows > 0:
            wifi_bucket = bucketize_timestamp(wifi_table, bucket_seconds)
            wifi_table = wifi_table.append_column("bucket", wifi_bucket)
            bssid_col = wifi_table["bssid"]
            bucket_col = wifi_table["bucket"]
            for bssid, b in zip(bssid_col, bucket_col):
                if not bssid.is_valid or not b.is_valid:
                    continue
                dev = "wifi:" + str(bssid.as_py())
                buck = int(b.as_py())
                s = presence.get(dev)
                if s is None:
                    s = set()
                    presence[dev] = s
                s.add(buck)

    # ---- BLE side ----
    ble_dset = safe_dataset(BLE_PARQUET)
    ble_table = ble_dset.to_table(columns=["timestamp", "addr"])
    if ble_table.num_rows > 0:
        ble_table, _, _ = parse_time_range(ble_table, qs)
        if ble_table.num_rows > 0:
            ble_bucket = bucketize_timestamp(ble_table, bucket_seconds)
            ble_table = ble_table.append_column("bucket", ble_bucket)
            addr_col = ble_table["addr"]
            bucket_col = ble_table["bucket"]
            for addr, b in zip(addr_col, bucket_col):
                if not addr.is_valid or not b.is_valid:
                    continue
                dev = "ble:" + str(addr.as_py())
                buck = int(b.as_py())
                s = presence.get(dev)
                if s is None:
                    s = set()
                    presence[dev] = s
                s.add(buck)

    # filter out devices with too few buckets
    devices = [
        (dev, buckets)
        for dev, buckets in presence.items()
        if len(buckets) >= MIN_BUCKETS_FOR_DEVICE and dev not in blocked_set
    ]

    if len(devices) < 2:
        return {"convoys": [], "bucket_seconds": bucket_seconds}

    # sort by activity, keep top N devices
    devices.sort(key=lambda x: len(x[1]), reverse=True)
    devices = devices[:MAX_CONVOY_DEVICES]

    convoys = []
    n = len(devices)
    for i in range(n):
        dev_i, buckets_i = devices[i]
        for j in range(i + 1, n):
            dev_j, buckets_j = devices[j]
            inter = buckets_i.intersection(buckets_j)
            if not inter:
                continue
            union = buckets_i.union(buckets_j)
            jaccard = len(inter) / float(len(union))
            if jaccard >= MIN_JACCARD:
                convoys.append(
                    {
                        "members": [dev_i, dev_j],
                        "correlation": jaccard,
                        "buckets_compared": len(union),
                    }
                )

    # sort by correlation, keep top convoys
    convoys.sort(key=lambda c: c["correlation"], reverse=True)
    convoys = convoys[:MAX_CONVOYS]

    return {"convoys": convoys, "bucket_seconds": bucket_seconds}


# -------------------------------------------------------------------
# HEALTH CHECK (used by /health endpoint)
# -------------------------------------------------------------------
def health_status(ws_host='127.0.0.1', ws_port=8765, dashboard_url='http://127.0.0.1:8000'):
    """Return a small health summary for the running environment.
    - quick TCP connect to websocket port
    - try a HEAD/GET to dashboard URL
    - check presence of parquet files
    """
    status = {
        'analytics': True,
        'parquet': {
            'wifi_exists': os.path.exists(WIFI_PARQUET),
            'ble_exists': os.path.exists(BLE_PARQUET),
        },
        'ws': False,
        'dashboard': False,
        'blocked_count': len(blocked_set) if 'blocked_set' in globals() else 0,
    }

    # quick TCP check for websocket port (non-blocking, short timeout)
    try:
        with socket.create_connection((ws_host, int(ws_port)), timeout=0.4):
            status['ws'] = True
    except Exception:
        status['ws'] = False

    # quick HTTP probe for dashboard (if present)
    try:
        req = urllib.request.Request(dashboard_url, method='HEAD')
        with urllib.request.urlopen(req, timeout=0.6) as r:
            status['dashboard'] = r.status == 200
    except Exception:
        status['dashboard'] = False

    return status


# -------------------------------------------------------------------
# HTTP HANDLER
# -------------------------------------------------------------------
class AnalyticsHandler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        def get_int(name, default):
            try:
                return int(qs.get(name, [default])[0])
            except Exception:
                return default

        # lightweight health probe (aggregates dashboard/ws/parquet status)
        if path == "/health":
            return self._json(health_status())

        if path == "/analytics/wifi_timeline":
            bucket = get_int("bucket", 5)
            return self._json(wifi_timeline(qs, bucket))

        if path == "/analytics/wifi_heatmap":
            bucket = get_int("bucket", 30)
            return self._json(wifi_channel_heatmap(qs, bucket))

        if path == "/analytics/ble_timeline":
            bucket = get_int("bucket", 5)
            return self._json(ble_timeline(qs, bucket))

        if path == "/analytics/convoys":
            bucket = get_int("bucket", 30)
            return self._json(convoy_detection(qs, bucket))

        if path == "/analytics/blocked":
            return self._json({"blocked": sorted(list(blocked_set))})

        self._json({"error": "not found"}, code=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/analytics/blocked":
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length) if length else b''
                j = json.loads(body.decode('utf-8') or '{}')
                device = j.get('device')
                if device:
                    blocked_set.add(device)
                    save_blocked()
                    return self._json({"blocked": sorted(list(blocked_set))})
                return self._json({"error": "missing 'device'"}, code=400)
            except Exception as e:
                return self._json({"error": "bad request", "detail": str(e)}, code=400)

        self._json({"error": "not found"}, code=404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/analytics/blocked":
            device = qs.get('device', [None])[0]
            if device and device in blocked_set:
                blocked_set.remove(device)
                save_blocked()
            return self._json({"blocked": sorted(list(blocked_set))})

        self._json({"error": "not found"}, code=404)


# -------------------------------------------------------------------
# SERVER
# -------------------------------------------------------------------
def run():
    server = HTTPServer((ANALYTICS_HOST, ANALYTICS_PORT), AnalyticsHandler)
    print(f"Analytics server on http://{ANALYTICS_HOST}:{ANALYTICS_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()
