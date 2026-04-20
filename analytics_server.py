#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime
import socket
import urllib.request
import logging

try:
    import pyarrow.dataset as ds
    import pyarrow.compute as pc
except ImportError:
    ds = None
    pc = None
    logging.warning("pyarrow is not installed; analytics endpoints are disabled")

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
    if ds is None:
        raise RuntimeError("pyarrow is required for analytics_dataset operations")
    if not os.path.exists(path):
        return None
    if os.path.isdir(path):
        # remove any zero-byte parquet fragments left behind by crashes
        for root, dirs, files in os.walk(path):
            for f in files:
                full = os.path.join(root, f)
                if full.endswith(".parquet") and os.path.getsize(full) == 0:
                    logging.getLogger("analytics_server").warning("Removing empty parquet file: %s", full)
                    os.remove(full)
        # if the directory now has no parquet files, return None
        if not any(f.endswith(".parquet") for _, _, files in os.walk(path) for f in files):
            return None
    else:
        if os.path.getsize(path) == 0:
            return None
    return ds.dataset(path, format="parquet")


# -------------------------------------------------------------------
# TIME RANGE PARSING
# -------------------------------------------------------------------
def parse_time_range(table, qs):
    """
    Returns (filtered_table, min_ts_s, max_ts_s) where min/max are epoch seconds
    after applying optional ?since=, ?start=, ?end= filters.
    """
    if pc is None:
        raise RuntimeError("pyarrow compute is required for parse_time_range")
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
    if pc is None:
        raise RuntimeError("pyarrow compute is required for bucketize_timestamp")
    ts = pc.cast(table["timestamp"], "timestamp[s]", safe=False)
    ts_int = pc.cast(ts, "int64")
    bucket = pc.cast(pc.divide(ts_int, bucket_seconds), "int64")
    return bucket


# -------------------------------------------------------------------
# WIFI TIMELINE
# -------------------------------------------------------------------
def wifi_timeline(qs, bucket_seconds=5):
    dset = safe_dataset(WIFI_PARQUET)
    if dset is None:
        return {"buckets": [], "bucket_seconds": bucket_seconds}
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
    if dset is None:
        return {"cells": [], "bucket_seconds": bucket_seconds}
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
    if dset is None:
        return {"buckets": [], "bucket_seconds": bucket_seconds}
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
    if wifi_dset is not None:
        wifi_table = wifi_dset.to_table(columns=["timestamp", "bssid"])
    else:
        wifi_table = None

    if wifi_table is not None and wifi_table.num_rows > 0:
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
    if ble_dset is not None:
        ble_table = ble_dset.to_table(columns=["timestamp", "addr"])
    else:
        ble_table = None
    if ble_table is not None and ble_table.num_rows > 0:
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


def normalize_pyarrow_value(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8', errors='ignore')
        except Exception:
            return str(value)
    if isinstance(value, datetime):
        return int(value.timestamp())
    return value


def row_matches_query(row, query, search_fields=None):
    lower_query = query.lower()
    fields = search_fields if search_fields is not None else row.keys()
    for field in fields:
        value = row.get(field)
        if value is None:
            continue
        if lower_query in str(value).lower():
            return True
    return False


def search_rows(table, query, search_fields=None, limit=100):
    if table is None or table.num_rows == 0 or not query:
        return []

    rows = []
    for i in range(table.num_rows):
        row = {}
        for name, col in zip(table.column_names, table.columns):
            if col[i].is_valid:
                row[name] = normalize_pyarrow_value(col[i].as_py())
            else:
                row[name] = None

        if row_matches_query(row, query, search_fields):
            rows.append(row)
            if len(rows) >= limit:
                break

    return rows


def top_counts(items, limit=5):
    counts = {}
    for item in items:
        if item is None:
            continue
        counts[item] = counts.get(item, 0) + 1
    pairs = sorted(counts.items(), key=lambda x: (-x[1], str(x[0])))
    return [{'value': key, 'count': value} for key, value in pairs[:limit]]


def extract_device_ids_from_rows(wifi_rows, ble_rows):
    devices = set()
    for row in wifi_rows:
        if row.get('bssid'):
            devices.add(f"wifi:{row['bssid']}")
    for row in ble_rows:
        if row.get('addr'):
            devices.add(f"ble:{row['addr']}")
    return devices


def summarize_behavior(rows):
    if not rows:
        return None

    candidates = []
    for field in ['movement', 'frame_type', 'ssid', 'name']:
        values = [row.get(field) for row in rows if row.get(field) is not None]
        if not values:
            continue
        top = top_counts(values, limit=3)
        if top:
            primary = top[0]
            score = round(primary['count'] / len(values), 2)
            candidates.append({
                'field': field,
                'dominant': primary['value'],
                'score': score,
                'details': top,
            })
    if not candidates:
        return None

    # choose most predictive field by score, then by available count
    candidates.sort(key=lambda c: (-c['score'], -len(c['details'])))
    return candidates[0]


def build_search_cooccurrence(qs, wifi_table, ble_table, query_devices, bucket_seconds=60, top_n=10):
    if not query_devices:
        return []

    presence = {}

    def merge_presence(table, id_field, prefix):
        if table is None or table.num_rows == 0 or id_field not in table.column_names:
            return
        bucket = bucketize_timestamp(table, bucket_seconds)
        table = table.append_column('bucket', bucket)
        for identity, bucket_idx in zip(table[id_field], table['bucket']):
            if not identity.is_valid or not bucket_idx.is_valid:
                continue
            dev = f"{prefix}:{identity.as_py()}"
            buck = int(bucket_idx.as_py())
            presence.setdefault(buck, set()).add(dev)

    merge_presence(wifi_table, 'bssid', 'wifi')
    merge_presence(ble_table, 'addr', 'ble')

    if not presence:
        return []

    counts = {}
    for devices in presence.values():
        if not devices.intersection(query_devices):
            continue
        for other in devices:
            if other in query_devices:
                continue
            counts[other] = counts.get(other, 0) + 1

    if not counts:
        return []

    return [{'device': device, 'count': count} for device, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]]


def build_search_convoy_info(qs, query_devices, bucket_seconds=30):
    if not query_devices:
        return []
    convoys = convoy_detection(qs, bucket_seconds=bucket_seconds).get('convoys', [])
    filtered = [c for c in convoys if any(member in query_devices for member in c.get('members', []))]
    return filtered


def build_search_summary(query, wifi_table, ble_table, wifi_results, ble_results, qs):
    summary = {
        'total_matches': len(wifi_results) + len(ble_results),
        'query': query,
        'seen_with': [],
        'behavior': None,
        'frequency_per_hour': None,
        'duration_seconds': None,
        'first_seen': None,
        'last_seen': None,
        'convoys': [],
    }

    rows = wifi_results + ble_results
    if rows:
        timestamps = []
        for row in rows:
            ts = row.get('timestamp')
            if ts is None:
                continue
            if isinstance(ts, datetime):
                timestamps.append(ts.timestamp())
            else:
                try:
                    timestamps.append(float(ts))
                except Exception:
                    continue
        if timestamps:
            min_ts = min(timestamps)
            max_ts = max(timestamps)
            if max_ts > min_ts:
                duration = int(max_ts - min_ts)
                summary['duration_seconds'] = duration
                summary['frequency_per_hour'] = round(summary['total_matches'] / duration * 3600, 2)
            else:
                summary['duration_seconds'] = 0
                summary['frequency_per_hour'] = summary['total_matches']
            summary['first_seen'] = int(min_ts)
            summary['last_seen'] = int(max_ts)

    summary['behavior'] = summarize_behavior(rows)
    query_devices = extract_device_ids_from_rows(wifi_results, ble_results)
    summary['seen_with'] = build_search_cooccurrence(qs, wifi_table, ble_table, query_devices, bucket_seconds=60, top_n=10)
    summary['convoys'] = build_search_convoy_info(qs, query_devices, bucket_seconds=30)

    return summary


def search_capture(qs):
    query = qs.get('q', [''])[0].strip()
    if not query:
        return {'query': '', 'wifi': {'count': 0, 'results': []}, 'ble': {'count': 0, 'results': []}, 'summary': None}

    limit = 100
    try:
        limit = min(200, max(10, int(qs.get('limit', [100])[0])))
    except Exception:
        limit = 100

    wifi_dset = safe_dataset(WIFI_PARQUET)
    if wifi_dset is not None:
        wifi_table = wifi_dset.to_table()
        if wifi_table.num_rows > 0:
            wifi_table, _, _ = parse_time_range(wifi_table, qs)
        else:
            wifi_table = None
    else:
        wifi_table = None

    ble_dset = safe_dataset(BLE_PARQUET)
    if ble_dset is not None:
        ble_table = ble_dset.to_table()
        if ble_table.num_rows > 0:
            ble_table, _, _ = parse_time_range(ble_table, qs)
        else:
            ble_table = None
    else:
        ble_table = None

    wifi_fields = [f for f in ['bssid', 'ssid', 'channel', 'rssi', 'frame_type', 'source', 'destination'] if wifi_table is not None and f in wifi_table.column_names]
    ble_fields = [f for f in ['addr', 'name', 'movement', 'rssi', 'distance_m', 'frame_type'] if ble_table is not None and f in ble_table.column_names]

    wifi_results = search_rows(wifi_table, query, search_fields=wifi_fields, limit=limit) if wifi_table is not None else []
    ble_results = search_rows(ble_table, query, search_fields=ble_fields, limit=limit) if ble_table is not None else []
    summary = build_search_summary(query, wifi_table, ble_table, wifi_results, ble_results, qs)

    return {
        'query': query,
        'wifi': {'count': len(wifi_results), 'results': wifi_results},
        'ble': {'count': len(ble_results), 'results': ble_results},
        'summary': summary,
    }


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

        if path == "/analytics/search":
            return self._json(search_capture(qs))

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
