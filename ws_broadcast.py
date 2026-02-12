import asyncio
import websockets
import json

clients = set()

async def handler(websocket):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)   # discard avoids KeyError

async def broadcast(obj):
    if not clients:
        return

    msg = json.dumps(obj)
    dead = []

    # iterate over a snapshot to avoid mutation errors
    for c in list(clients):
        try:
            await c.send(msg)
        except:
            dead.append(c)

    # remove dead clients AFTER iteration
    for d in dead:
        clients.discard(d)

async def start_server():
    return await websockets.serve(handler, "0.0.0.0", 8765)

