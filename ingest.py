#!/usr/bin/env python3
"""Silver Sniffer ingest service — read ESP32 serial + BLE → websocket + Parquet.

Usage: python ingest.py [--serial /dev/ttyUSB0] [--baud 115200] [--no-ble] [-v]
"""
import json
import base64
import os
import serial
import asyncio

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None
    pq = None
    import logging as _logging
    _logging.warning("pyarrow is not installed; Parquet persistence is disabled")
from ws_broadcast import start_server, broadcast
try:
    from scapy.all import Dot11, Dot11Elt
except ImportError:
    Dot11 = None
    Dot11Elt = None
    import logging as _logging
    _logging.warning("scapy is not installed; WiFi packet parsing will be disabled")
from datetime import datetime, UTC
try:
    from bleak import BleakScanner
except ImportError:
    BleakScanner = None
    import logging as _logging
    _logging.warning("bleak is not installed; local BLE scanning is disabled")
from collections import defaultdict
import time
import argparse
import logging
import signal
import sys

#UTC = timezone.utc
timestamp = datetime.now(UTC)
SERIAL_PORT = "/dev/ttyUSB0"
BAUD = 115200
PARQUET_WIFI = "wifi_capture.parquet"
PARQUET_BLE = "ble_capture.parquet"

if pa is not None:
    wifi_schema = pa.schema([
        ("timestamp", pa.timestamp("us")),
        ("bssid", pa.string()),
        ("ssid", pa.string()),
        ("src", pa.string()),
        ("dst", pa.string()),
        ("rssi", pa.int32()),
        ("channel", pa.int32()),
        ("frame_type", pa.int32()),
        ("frame_subtype", pa.int32()),
        ("frame_len", pa.int32()),
    ])

    ble_schema = pa.schema([
        ("timestamp", pa.timestamp("us")),
        ("addr", pa.string()),
        ("rssi", pa.int32()),
        ("payload_b64", pa.string()),
        ("name", pa.string()),
    ])
else:
    wifi_schema = None
    ble_schema = None

HISTORY = {
    "wifi": [],
    "ble": [],
    "movement": [],
    "vendors": {},
    "fingerprints_wifi": {},
    "fingerprints_ble": {},
}

# keep ws_broadcast history in sync when module is available
try:
    import ws_broadcast
    ws_broadcast.HISTORY = HISTORY
except Exception:
    pass

# --- WiFi analytics state ---

wifi_last = {}  # mac -> {"rssi": int, "ts_us": int}
wifi_fp = {}    # mac -> fingerprint stats
ble_fp = {}     # addr -> fingerprint stats
wifi_timeline = defaultdict(int)  # second -> count
wifi_heatmap = defaultdict(lambda: defaultdict(int))  # second -> channel -> count


def normalize_row(row, schema):
    if pa is None:
        # If pyarrow is not available we keep the raw row.
        return row

    normalized = {}
    for field in schema:
        name = field.name
        if name in row and row[name] is not None:
            normalized[name] = row[name]
        else:
            if pa.types.is_timestamp(field.type):
                normalized[name] = datetime.now(UTC)
            elif pa.types.is_integer(field.type):
                normalized[name] = 0
            else:
                normalized[name] = ""
    return normalized


def _ensure_parquet_root(path):
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            logging.warning("Could not remove stale parquet file %s", path)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        logging.exception("Failed to prepare parquet directory %s", path)
        raise


def _write_parquet(table, root_path, label):
    _ensure_parquet_root(root_path)
    pq.write_to_dataset(table, root_path=root_path)
    logging.info("Flushed %d %s rows to %s", table.num_rows, label, root_path)


def _append_parquet_rows(rows, schema, root_path, label):
    if pq is None or pa is None:
        return False
    if not rows:
        return True
    try:
        table = pa.Table.from_pylist(rows, schema=schema)
        _write_parquet(table, root_path, label)
        return True
    except Exception:
        logging.exception("Failed to flush %s rows to Parquet", label)
        return False


def _flush_parquet(wifi_rows, ble_rows):
    """Persist any buffered rows to Parquet files."""
    if pq is None or pa is None:
        logging.warning("Parquet persistence unavailable, dropping %d wifi rows and %d ble rows", len(wifi_rows), len(ble_rows))
        wifi_rows.clear()
        ble_rows.clear()
        return

    if ble_rows:
        if _append_parquet_rows(ble_rows, ble_schema, PARQUET_BLE, "BLE"):
            ble_rows.clear()

    if wifi_rows:
        if _append_parquet_rows(wifi_rows, wifi_schema, PARQUET_WIFI, "WiFi"):
            wifi_rows.clear()


def extract_ssid(pkt):
    if Dot11Elt is None or pkt is None:
        return ""
    elt = pkt.getlayer(Dot11Elt)
    while elt:
        if elt.ID == 0:
            try:
                return elt.info.decode(errors="ignore")
            except Exception:
                return ""
        elt = elt.payload.getlayer(Dot11Elt)
    return ""


def mac(x):
    return x if x else ""


def _vendor_from_mac(mac_addr):
    if not mac_addr:
        return "unknown"
    parts = mac_addr.replace('-', ':').split(':')
    if len(parts) < 3:
        return "unknown"
    return ":".join(parts[:3]).upper()


def add_history(record_type, item, max_length=5000):
    bucket = HISTORY.get(record_type)
    if bucket is None:
        return
    if isinstance(bucket, list):
        bucket.append(item)
        if len(bucket) > max_length:
            del bucket[0: len(bucket) - max_length]
    elif isinstance(bucket, dict) and isinstance(item, dict):
        key = item.get("mac") or item.get("addr")
        if key:
            bucket[key] = item


async def ingest_loop():
    wifi_rows = []
    ble_rows = []

    try:
        while True:
            try:
                # Attempt to open the serial port
                ser = serial.Serial(
                    SERIAL_PORT,
                    BAUD,
                    timeout=1,
                    dsrdtr=False,
                    rtscts=False,
                    xonxoff=False,
                    write_timeout=1,
                )
                logging.info("Serial port opened, listening on %s @ %d", SERIAL_PORT, BAUD)
                loop = asyncio.get_running_loop()

                while True:
                    try:
                        raw = await loop.run_in_executor(None, ser.readline)
                    except serial.SerialException:
                        logging.warning("Serial read error, reconnecting...")
                        break

                    if not raw:
                        continue

                    line = raw.decode(errors="ignore").strip()
                    if not line:
                        continue

                    try:
                        obj = json.loads(line)
                    except Exception:
                        logging.debug("Invalid JSON frame: %s", line)
                        continue

                    # handle BLE frames from the ESP32 before broadcasting
                    if obj.get("type") == "ble":
                        addr = obj.get("addr", "")
                        rssi = obj.get("rssi", 0)

                        # enrich object with the same distance/movement logic
                        dist, mov, col = compute_ble_metrics(addr, rssi)
                        obj["distance_m"] = dist
                        obj["movement"] = mov
                        obj["color"] = col

                        await broadcast(obj)

                        HISTORY["ble"].append({
                            "t": time.time(),
                            "addr": addr,
                            "rssi": rssi,
                        })

                        row = {
                            "timestamp": datetime.now(UTC),
                            "addr": addr,
                            "rssi": rssi,
                            "payload_b64": obj.get("payload_b64", ""),
                            "name": obj.get("name", ""),
                        }

                        ble_rows.append(normalize_row(row, ble_schema))

                        if len(ble_rows) >= 200:
                            if _append_parquet_rows(ble_rows, ble_schema, PARQUET_BLE, "BLE"):
                                ble_rows = []

                        continue

                    # for non-BLE frames we still broadcast early so analytics can
                    # pick them up quickly
                    await broadcast(obj)

                    HISTORY["wifi"].append({
                        "t": time.time(),
                        "bssid": obj.get("bssid"),
                        "rssi": obj.get("rssi"),
                        "channel": obj.get("channel"),
                    })

                    # WIFI
                    if obj.get("type") != "wifi":
                        continue

                    if Dot11 is None:
                        logging.debug("Dot11 parser unavailable; skipping WiFi packet")
                        continue

                    try:
                        raw = base64.b64decode(obj.get("frame_b64", ""))
                        pkt = Dot11(raw)
                    except Exception:
                        logging.debug("Failed to decode WiFi frame for obj: %s", obj)
                        continue

                    if not pkt.haslayer(Dot11):
                        continue

                    dot11 = pkt[Dot11]
                    ssid = extract_ssid(pkt)

                    row = {
                        "timestamp": datetime.fromtimestamp(
                            obj.get("ts_us", 0) / 1_000_000, tz=UTC
                        ),
                        "bssid": obj.get("bssid", ""),
                        "ssid": ssid,
                        "src": mac(dot11.addr2),
                        "dst": mac(dot11.addr1),
                        "rssi": obj.get("rssi", 0),
                        "channel": obj.get("channel", 0),
                        "frame_type": dot11.type,
                        "frame_subtype": dot11.subtype,
                        "frame_len": obj.get("frame_len", 0),
                    }

                    # --- WiFi analytics + broadcast ---
                    ts_us = obj.get("ts_us", 0)
                    rssi = obj.get("rssi", 0)
                    channel = obj.get("channel", 0)
                    src_mac = mac(dot11.addr2)

                    movement_msg = update_wifi_movement(src_mac, rssi, ts_us)
                    fp_msg = update_wifi_fingerprint(src_mac, rssi, channel)
                    class_msg = classify_wifi_device(fp_msg)
                    timeline_msg = update_wifi_timeline(ts_us)
                    heatmap_msg = update_wifi_heatmap(channel, ts_us)

                    await broadcast(movement_msg)
                    await broadcast(fp_msg)
                    await broadcast(class_msg)
                    await broadcast(timeline_msg)
                    await broadcast(heatmap_msg)

                    wifi_rows.append(normalize_row(row, wifi_schema))

                    if len(wifi_rows) >= 500:
                        if _append_parquet_rows(wifi_rows, wifi_schema, PARQUET_WIFI, "WiFi"):
                            wifi_rows = []

            except serial.SerialException:
                logging.warning("Serial port unavailable (%s), retrying in 1s...", SERIAL_PORT)

            except Exception:
                logging.exception("Unexpected error in ingest_loop")

            # Sleep before retrying to open the port
            await asyncio.sleep(1)

    except asyncio.CancelledError:
        logging.info("ingest_loop cancelled; flushing pending rows")
        _flush_parquet(wifi_rows, ble_rows)
        raise

    except Exception:
        logging.exception("ingest_loop top-level exception; flushing pending rows")
        _flush_parquet(wifi_rows, ble_rows)
        raise


# distance/movement state for Silver BLE
ble_last_distance = {}


def compute_ble_metrics(addr, rssi):
    """Estimate distance and movement for a BLE advertisement.

    Returns a tuple (distance_m, movement, color) and updates
    :data:`ble_last_distance` state.  The algorithm mirrors the logic
    previously duplicated in ``ble_loop`` so both local scanning and
    ESP32-sourced BLE frames behave the same way.
    """
    # simple log-distance path loss model
    P0 = -59  # RSSI at 1m (typical BLE)
    N = 2.0   # path loss exponent
    distance_m = 10 ** ((P0 - rssi) / (10 * N))

    prev = ble_last_distance.get(addr)
    if prev is None:
        movement = "unknown"
    elif distance_m < prev:
        movement = "approach"
    elif distance_m > prev:
        movement = "depart"
    else:
        movement = "steady"

    ble_last_distance[addr] = distance_m

    color = {
        "approach": "green",
        "depart": "red",
        "steady": "yellow",
        "unknown": "gray",
    }[movement]

    return distance_m, movement, color


def update_wifi_movement(mac_addr, rssi, ts_us):
    prev = wifi_last.get(mac_addr)
    moving = False
    rssi_delta = 0
    dt_ms = 0

    if prev:
        rssi_delta = rssi - prev["rssi"]
        dt_ms = (ts_us - prev["ts_us"]) / 1000
        moving = abs(rssi_delta) > 8 and dt_ms < 5000

    wifi_last[mac_addr] = {"rssi": rssi, "ts_us": ts_us}

    msg = {
        "type": "wifi_movement",
        "mac": mac_addr,
        "moving": moving,
        "rssi_delta": rssi_delta,
        "dt_ms": dt_ms,
    }
    add_history("movement", {**msg, "t": ts_us / 1_000_000})
    return msg


def update_wifi_fingerprint(mac_addr, rssi, channel):
    fp = wifi_fp.setdefault(mac_addr, {
        "count": 0,
        "channels": set(),
        "rssi_sum": 0,
        "rssi_min": 999,
        "rssi_max": -999,
    })

    fp["count"] += 1
    fp["channels"].add(channel)
    fp["rssi_sum"] += rssi
    fp["rssi_min"] = min(fp["rssi_min"], rssi)
    fp["rssi_max"] = max(fp["rssi_max"], rssi)

    avg_rssi = fp["rssi_sum"] / fp["count"]

    msg = {
        "type": "wifi_fingerprint",
        "mac": mac_addr,
        "count": fp["count"],
        "channels": len(fp["channels"]),
        "avg_rssi": avg_rssi,
        "rssi_min": fp["rssi_min"],
        "rssi_max": fp["rssi_max"],
    }
    add_history("fingerprints_wifi", msg)
    vendor = _vendor_from_mac(mac_addr)
    HISTORY["vendors"][vendor] = HISTORY["vendors"].get(vendor, 0) + 1
    return msg


def update_ble_fingerprint(addr, rssi):
    fp = ble_fp.setdefault(addr, {
        "count": 0,
        "rssi_sum": 0,
        "rssi_min": 999,
        "rssi_max": -999,
    })
    fp["count"] += 1
    fp["rssi_sum"] += rssi
    fp["rssi_min"] = min(fp["rssi_min"], rssi)
    fp["rssi_max"] = max(fp["rssi_max"], rssi)
    avg_rssi = fp["rssi_sum"] / fp["count"]
    msg = {
        "type": "ble_fingerprint",
        "addr": addr,
        "count": fp["count"],
        "avg_rssi": avg_rssi,
        "rssi_min": fp["rssi_min"],
        "rssi_max": fp["rssi_max"],
    }
    add_history("fingerprints_ble", {**msg, "addr": addr})
    return msg


def classify_wifi_device(fp_msg):
    count = fp_msg["count"]
    channels = fp_msg["channels"]
    avg_rssi = fp_msg["avg_rssi"]

    if count > 50000:
        cls = "access_point"
    elif channels > 3:
        cls = "roaming_client"
    elif avg_rssi > -50:
        cls = "nearby_device"
    else:
        cls = "iot_or_low_power"

    return {
        "type": "wifi_class",
        "mac": fp_msg["mac"],
        "class": cls,
    }


def update_wifi_timeline(ts_us):
    second = ts_us // 1_000_000
    wifi_timeline[second] += 1
    return {
        "type": "wifi_timeline",
        "second": int(second),
        "events": wifi_timeline[second],
    }


def update_wifi_heatmap(channel, ts_us):
    second = ts_us // 1_000_000
    wifi_heatmap[second][channel] += 1
    return {
        "type": "wifi_heatmap",
        "second": int(second),
        "channel": int(channel),
        "count": wifi_heatmap[second][channel],
    }


async def ble_loop():
    ble_rows = []

    def detection_callback(device, adv):
        addr = device.address
        rssi = adv.rssi

        dist, movement, color = compute_ble_metrics(addr, rssi)

        obj = {
            "type": "ble",
            "addr": addr,
            "rssi": rssi,
            "name": device.name or "",
            "distance_m": dist,
            "movement": movement,
            "color": color,
        }
        asyncio.create_task(broadcast(obj))

        HISTORY["ble"].append({
            "t": time.time(),
            "addr": addr,
            "rssi": rssi,
        })

        row = {
            "timestamp": datetime.now(UTC),
            "addr": addr,
            "rssi": rssi,
            "payload_b64": "",
            "name": device.name or "",
        }
        ble_rows.append(normalize_row(row, ble_schema))
        update_ble_fingerprint(addr, rssi)

        if len(ble_rows) >= 200:
            if _append_parquet_rows(ble_rows, ble_schema, PARQUET_BLE, "BLE"):
                ble_rows.clear()

    scanner = BleakScanner(detection_callback)
    await scanner.start()

    while True:
        await asyncio.sleep(1.0)


async def _run(no_ble=False, video_sources=None, px_to_m=0.01):
    """Start websocket server and ingestion tasks.

    Set `no_ble=True` to disable local BLE scanning (useful in headless environments).
    If `video_sources` is provided (list), start CPU-based video trackers.
    """
    await start_server()
    tasks = [asyncio.create_task(ingest_loop())]
    if not no_ble and BleakScanner is not None:
        tasks.append(asyncio.create_task(ble_loop()))
    elif not no_ble:
        logging.warning("BLE loop disabled because BleakScanner is not available")

    if video_sources:
        from video_tracker import start_video_sources
        tasks.append(asyncio.create_task(start_video_sources(video_sources, px_to_m=px_to_m)))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main():
    global SERIAL_PORT, BAUD, PARQUET_WIFI, PARQUET_BLE
    parser = argparse.ArgumentParser(description="Ingest serial + BLE → websocket + Parquet")
    parser.add_argument("--serial", default=SERIAL_PORT, help="Serial port for ESP32 (default: %(default)s)")
    parser.add_argument("--baud", type=int, default=BAUD, help="Serial baud rate")
    parser.add_argument("--parquet-wifi", dest="parquet_wifi", default=PARQUET_WIFI, help="WiFi Parquet root")
    parser.add_argument("--parquet-ble", dest="parquet_ble", default=PARQUET_BLE, help="BLE Parquet root")
    parser.add_argument("--no-ble", action="store_true", help="Disable local BLE scanning")
    parser.add_argument("--video-sources", default=None, help="Comma-separated list of video sources (rtsp://, http(s) .m3u8, or camera index). Example: --video-sources rtsp://...,0")
    parser.add_argument("--px-to-m", type=float, default=0.01, help="Pixels-to-meters scale for video tracks (approx)")
    parser.add_argument("-v","--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    SERIAL_PORT = args.serial
    BAUD = args.baud
    PARQUET_WIFI = args.parquet_wifi
    PARQUET_BLE = args.parquet_ble

    video_sources = None
    if args.video_sources:
        parts = [s.strip() for s in args.video_sources.split(',') if s.strip()]
        parsed = []
        for p in parts:
            try:
                parsed.append(int(p))
            except Exception:
                parsed.append(p)
        video_sources = parsed

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    try:
        asyncio.run(_run(no_ble=args.no_ble, video_sources=video_sources, px_to_m=args.px_to_m))
    except KeyboardInterrupt:
        logging.info("Interrupted — shutting down")


if __name__ == "__main__":
    main()

