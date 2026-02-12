#!/usr/bin/env python3
import json
import base64
import serial
import pyarrow as pa
import pyarrow.parquet as pq
import asyncio
from ws_broadcast import start_server, broadcast
from scapy.all import Dot11, Dot11Elt
from datetime import datetime
from bleak import BleakScanner

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
    ser = serial.Serial(
        SERIAL_PORT,
        BAUD,
        timeout=1,
        dsrdtr=False,
        rtscts=False,
        xonxoff=False,
        write_timeout=1
    )

    wifi_rows = []
    ble_rows = []

    while True:
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, ser.readline)
        line = raw.decode(errors="ignore").strip()

        if not line:
            continue

        try:
            obj = json.loads(line)
        except:
            continue

        await broadcast(obj)

        # BLE from ESP32
        if obj.get("type") == "ble":
            row = {
                "timestamp": datetime.utcnow(),
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
            "timestamp": datetime.fromtimestamp(obj["ts_us"] / 1_000_000),
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

        wifi_rows.append(normalize_row(row, wifi_schema))

        if len(wifi_rows) >= 500:
            table = pa.Table.from_pylist(wifi_rows, schema=wifi_schema)
            pq.write_to_dataset(table, root_path=PARQUET_WIFI)
            wifi_rows = []

# distance/movement state for Silver BLE
ble_last_distance = {}

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
            "timestamp": datetime.utcnow(),
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

