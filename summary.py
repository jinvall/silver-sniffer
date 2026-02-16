#!/usr/bin/env python3
import polars as pl

wifi = pl.read_parquet("wifi_capture.parquet")
ble  = pl.read_parquet("ble_capture.parquet")

print("WiFi rows:", wifi.height)
print("BLE rows:", ble.height)
print("Total rows:", wifi.height + ble.height)
