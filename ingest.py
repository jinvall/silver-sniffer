#!/usr/bin/env python3
import json
import base64
import serial
import pyarrow as pa
import pyarrow.parquet as pq
import asyncio
from ws_broadcast import start_server, broadcast
from scapy.all import Dot11, Dot11Elt
from datetime import datetime, UTC
from bleak import BleakScanner
from collections import defaultdict
import time

timestamp = datetime.now(UTC)
SERIAL_PORT = "/dev/ttyUSB0"
BAUD = 115200
PARQUET_WIFI = "wifi_capture.parquet"
PARQUET_BLE = "ble_capture.parquet"

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
HISTORY = {
    "wifi": [],
    "ble": [],
    "movement": [],
    "vendors": {},
    "fingerprints_wifi": {},
    "fingerprints_ble": {}
}

# --- WiFi analytics state ---

wifi_last = {}          # mac -> {"rssi": int, "ts_us": int}
wifi_fp = {}            # mac -> fingerprint stats
wifi_timeline = defaultdict(int)          # second -> count
wifi_heatmap = defaultdict(lambda: defaultdict(int))  # second -> channel -> count

def normalize_row(row, schema):
    normalized = {}
    for field in schema:
        name = field.name
        if name in row and row[name] is not None:
            normalized[name] = row[name]
        else:
            if pa.types.is_timestamp(field.type):
                normalized[name] = datetime.utcnow()
            elif pa.types.is_integer(field.type):
                normalized[name] = 0
            else:
                normalized[name] = ""
    return normalized

def extract_ssid(pkt):
    elt = pkt.getlayer(Dot11Elt)
    while elt:
        if elt.ID == 0:
            try:
                return elt.info.decode(errors="ignore")
            except:
                return ""
        elt = elt.payload.getlayer(Dot11Elt)
    return ""

def mac(x):
    return x if x else ""

async def ingest_loop():
    wifi_rows = []
    ble_rows = []

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
                write_timeout=1
            )

            print("Serial port opened, listening...")

            loop = asyncio.get_running_loop()

            while True:
                try:
                    raw = await loop.run_in_executor(None, ser.readline)
                except serial.SerialException:
                    print("Serial read error, breaking to reconnect...")
                    break

                if not raw:
                    continue

                line = raw.decode(errors="ignore").strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except:
                    continue

                await broadcast(obj)
                HISTORY["wifi"].append({
                "t": time.time(),
                "bssid": obj.get("bssid"),
                "rssi": obj.get("rssi"),
                "channel": obj.get("channel")
            })

                # BLE from ESP32
                if obj.get("type") == "ble":
                    row = {
                        "timestamp": datetime.now(datetime.UTC),
                        "addr": obj.get("addr", ""),
                        "rssi": obj.get("rssi", 0),
                        "payload_b64": obj.get("payload_b64", ""),
                        "name": obj.get("name", ""),
                    }

                    ble_rows.append(normalize_row(row, ble_schema))

                    if len(ble_rows) >= 200:
                        table = pa.Table.from_pylist(ble_rows, schema=ble_schema)
                        pq.write_to_dataset(table, root_path=PARQUET_BLE)
                        ble_rows = []

                    continue

                # WIFI
                if obj.get("type") != "wifi":
                    continue

                try:
                    raw = base64.b64decode(obj["frame_b64"])
                    pkt = Dot11(raw)
                except:
                    continue

                if not pkt.haslayer(Dot11):
                    continue

                dot11 = pkt[Dot11]
                ssid = extract_ssid(pkt)

                row = {
                    "timestamp": datetime.fromtimestamp(obj["ts_us"] / 1_000_000, tz=UTC),

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
                    table = pa.Table.from_pylist(wifi_rows, schema=wifi_schema)
                    pq.write_to_dataset(table, root_path=PARQUET_WIFI)
                    wifi_rows = []

        except serial.SerialException:
            print("Serial port unavailable, retrying in 1s...")

        # Sleep before retrying to open the port
        await asyncio.sleep(1)



# distance/movement state for Silver BLE
ble_last_distance = {}
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

    return {
        "type": "wifi_movement",
        "mac": mac_addr,
        "moving": moving,
        "rssi_delta": rssi_delta,
        "dt_ms": dt_ms,
    }


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

    return {
        "type": "wifi_fingerprint",
        "mac": mac_addr,
        "count": fp["count"],
        "channels": len(fp["channels"]),
        "avg_rssi": avg_rssi,
        "rssi_min": fp["rssi_min"],
        "rssi_max": fp["rssi_max"],
    }


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

        # Distance estimate (log-distance path loss)
        P0 = -59  # RSSI at 1m (typical BLE)
        N = 2.0   # path loss exponent (tune if you want)
        distance_m = 10 ** ((P0 - rssi) / (10 * N))

        # Movement classification
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

        # Color mapping
        color = {
            "approach": "green",
            "depart": "red",
            "steady": "yellow",
            "unknown": "gray",
        }[movement]

        obj = {
            "type": "ble",
            "addr": addr,
            "rssi": rssi,
            "name": device.name or "",
            "distance_m": distance_m,
            "movement": movement,
            "color": color,
        }
        asyncio.create_task(broadcast(obj))

        row = {
            "timestamp": datetime.now(UTC),
            "addr": addr,
            "rssi": rssi,
            "payload_b64": "",
            "name": device.name or "",
        }
        ble_rows.append(normalize_row(row, ble_schema))

        if len(ble_rows) >= 200:
            table = pa.Table.from_pylist(ble_rows, schema=ble_schema)
            pq.write_to_dataset(table, root_path=PARQUET_BLE)
            ble_rows.clear()

    scanner = BleakScanner(detection_callback)
    await scanner.start()

    while True:
        await asyncio.sleep(1.0)

async def main():
    await start_server()
    await asyncio.gather(
        ingest_loop(),
        ble_loop(),
    )

if __name__ == "__main__":
    asyncio.run(main())

