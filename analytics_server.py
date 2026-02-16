#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

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

    grouped = table.group_by("bucket").aggregate(
        [
            ("bssid", "count"),
            ("rssi", "mean"),
        ]
    )

    buckets = []
    for b, count, avg_rssi in zip(
        grouped["bucket"], grouped["bssid_count"], grouped["rssi_mean"]
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
        if len(buckets) >= MIN_BUCKETS_FOR_DEVICE
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
