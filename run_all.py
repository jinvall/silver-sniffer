#!/usr/bin/env python3
"""Single launch point for Silver Sniffer: ingest + websocket + analytics + dashboard

Usage: python run_all.py [--no-dashboard] [--dashboard-port 8000] [--analytics-port 8090]
       [--serial /dev/ttyUSB0] [--baud 115200] [--no-ble]

This script starts the Parquet-backed analytics server (port 8090 by default),
starts a static HTTP server to serve the `dashboard/` directory (port 8000 by
default), and runs the websocket + ingest loop (same as `ingest.py`).

It's intended for development / demos where a single command is convenient.
"""
import argparse
import threading
import asyncio
import logging
import os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

# import local modules
import ingest
import ws_broadcast
import analytics_server


def serve_dashboard(port):
    cwd = os.getcwd()
    webdir = os.path.join(cwd, "dashboard")
    os.chdir(webdir)
    server = ThreadingHTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    print(f"Dashboard static server: http://0.0.0.0:{port} (serving {webdir})")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def serve_analytics(port):
    # analytics_server.run() binds to ANALYTICS_PORT from the module; override
    analytics_server.ANALYTICS_PORT = port
    print(f"Starting analytics REST server on http://0.0.0.0:{port}")
    analytics_server.run()


async def start_ws_and_ingest(serial_port, baud, no_ble):
    # configure ingest globals from CLI args
    ingest.SERIAL_PORT = serial_port
    ingest.BAUD = baud
    if no_ble:
        # ingest.py reads BleakScanner conditionally; setting a flag is enough
        ingest.NO_BLE = True

    # start websocket server (ws://:8765) and then run ingest_loop()
   
    print("Starting ingest loop (serial + optional BLE)")
    await ingest._run(no_ble=no_ble)



def main():
    parser = argparse.ArgumentParser(prog="run_all.py")
    parser.add_argument("--dashboard-port", type=int, default=8000)
    parser.add_argument("--analytics-port", type=int, default=8090)
    parser.add_argument("--serial", default=ingest.SERIAL_PORT)
    parser.add_argument("--baud", type=int, default=ingest.BAUD)
    parser.add_argument("--no-ble", action="store_true")
    parser.add_argument("--no-dashboard", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    # start analytics server in a thread
    t_analytics = threading.Thread(target=serve_analytics, args=(args.analytics_port,), daemon=True)
    t_analytics.start()

    # optionally start dashboard static server in a thread
    if not args.no_dashboard:
        t_dashboard = threading.Thread(target=serve_dashboard, args=(args.dashboard_port,), daemon=True)
        t_dashboard.start()

    # run websocket + ingest in the main asyncio loop
    try:
        asyncio.run(start_ws_and_ingest(args.serial, args.baud, args.no_ble))
    except KeyboardInterrupt:
        print("Shutting down (keyboard interrupt)")


if __name__ == "__main__":
    main()
