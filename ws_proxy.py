#!/usr/bin/env python3
import asyncio
import websockets
import sys

async def handler(websocket):
    loop = asyncio.get_event_loop()
    while True:
        data = await loop.run_in_executor(None, sys.stdin.buffer.read1, 4096)
        if not data:
            break
        await websocket.send(data)

async def main():
    async with websockets.serve(handler, "0.0.0.0", 9999):
        print("WebSocket proxy running on ws://0.0.0.0:9999")
        await asyncio.Future()

asyncio.run(main())
