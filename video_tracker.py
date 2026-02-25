"""CPU-based video ingest + tracker (MOG2 + Kalman) — broadcasts `video_track` events.

Designed for CPU-only execution (no GPU). Lightweight, best-effort detection + SORT-like tracking.

Usage (from ingest.py):
    await start_video_sources(["rtsp://...", 0], px_to_m=0.02)

Emits messages via existing websocket broadcast() with schema:
{
  "type": "video_track",
  "camera": "cam1",
  "id": "cam1-1",
  "ts_us": 167...,
  "bbox": [x,y,w,h],           # pixels
  "cx": cx, "cy": cy,        # pixels
  "x_m": cx * px_to_m,        # meters (approx)
  "y_m": cy * px_to_m,
  "vx": vx, "vy": vy,       # px/s
  "frame_w": width, "frame_h": height,
  "confidence": area         # contour area
}
"""
from __future__ import annotations

import time
import threading
import asyncio
import logging
from typing import List, Union

import numpy as np

try:
    import cv2
except Exception as e:
    raise RuntimeError("OpenCV is required for video tracking (pip install opencv-python-headless)") from e

from kalman import Kalman2D
from ws_broadcast import broadcast


def _norm_name(i: int) -> str:
    return f"cam{i}"


class _Track:
    def __init__(self, tid: str, x: float, y: float, bbox, ts):
        self.id = tid
        self.filter = Kalman2D(x, y)
        self.bbox = bbox
        self.last_seen = ts
        self.disappeared = 0

    def update(self, x, y, bbox, ts):
        state = self.filter.update(x, y)
        self.bbox = bbox
        self.last_seen = ts
        self.disappeared = 0
        return state


def _capture_worker(source, cam_name, px_to_m, min_area, max_disappeared, detect_interval, loop, stop_event):
    logger = logging.getLogger("video_tracker")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        logger.warning("Camera %s failed to open: %s", cam_name, source)
        return

    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    logger.info("%s opened (w=%d h=%d)", cam_name, frame_w, frame_h)

    backsub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=25, detectShadows=False)

    tracks = {}
    next_id = 1
    last_tick = time.time()

    # morphological kernel
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        fg = backsub.apply(gray)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
        fg = cv2.dilate(fg, kernel, iterations=2)

        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            cx = x + w / 2.0
            cy = y + h / 2.0
            detections.append((cx, cy, x, y, w, h, area))

        ts = time.time()
        assigned = set()
        # simple nearest-neighbor association
        for det in detections:
            cx, cy, x, y, w, h, area = det
            best_tid = None
            best_dist = float('inf')
            for tid, tr in tracks.items():
                tx = tr.filter.x[0, 0]
                ty = tr.filter.x[1, 0]
                d = (tx - cx) ** 2 + (ty - cy) ** 2
                if d < best_dist:
                    best_dist = d
                    best_tid = tid
            # gating (squared pixels)
            if best_tid is not None and best_dist < (100 ** 2):
                # update existing
                tr = tracks[best_tid]
                state = tr.update(cx, cy, (int(x), int(y), int(w), int(h)), ts)
                obj = {
                    "type": "video_track",
                    "camera": cam_name,
                    "id": best_tid,
                    "ts_us": int(ts * 1_000_000),
                    "bbox": [int(x), int(y), int(w), int(h)],
                    "cx": float(cx),
                    "cy": float(cy),
                    "x_m": float(cx * px_to_m),
                    "y_m": float(cy * px_to_m),
                    "vx": float(state["vx"]),
                    "vy": float(state["vy"]),
                    "frame_w": frame_w,
                    "frame_h": frame_h,
                    "confidence": float(area),
                }
                try:
                    asyncio.run_coroutine_threadsafe(broadcast(obj), loop)
                except Exception:
                    pass
                assigned.add(best_tid)
            else:
                # new track
                tid = f"{cam_name}-{next_id}"
                next_id += 1
                tr = _Track(tid, cx, cy, (int(x), int(y), int(w), int(h)), ts)
                tracks[tid] = tr
                state = tr.filter.update(cx, cy)
                obj = {
                    "type": "video_track",
                    "camera": cam_name,
                    "id": tid,
                    "ts_us": int(ts * 1_000_000),
                    "bbox": [int(x), int(y), int(w), int(h)],
                    "cx": float(cx),
                    "cy": float(cy),
                    "x_m": float(cx * px_to_m),
                    "y_m": float(cy * px_to_m),
                    "vx": float(state["vx"]),
                    "vy": float(state["vy"]),
                    "frame_w": frame_w,
                    "frame_h": frame_h,
                    "confidence": float(area),
                }
                try:
                    asyncio.run_coroutine_threadsafe(broadcast(obj), loop)
                except Exception:
                    pass

        # increment disappeared for unassigned tracks
        remove_keys = []
        for tid, tr in list(tracks.items()):
            if tid not in assigned:
                tr.disappeared += 1
                if tr.disappeared > max_disappeared:
                    # emit removed event
                    rem = {
                        "type": "video_track_removed",
                        "camera": cam_name,
                        "id": tid,
                        "ts_us": int(time.time() * 1_000_000),
                    }
                    try:
                        asyncio.run_coroutine_threadsafe(broadcast(rem), loop)
                    except Exception:
                        pass
                    remove_keys.append(tid)
        for k in remove_keys:
            tracks.pop(k, None)

        # throttle
        elapsed = time.time() - last_tick
        if detect_interval and elapsed < detect_interval:
            time.sleep(detect_interval - elapsed)
        last_tick = time.time()

    try:
        cap.release()
    except Exception:
        pass
    logger.info("%s worker stopped", cam_name)


async def start_video_sources(sources: List[Union[str, int]], px_to_m: float = 0.01, min_area: int = 300, max_disappeared: int = 10, detect_interval: float = 0.08):
    """Start tracker threads for each provided source and keep running until cancelled.

    sources: list of RTSP/HLS/WS URLs or integer camera indices.
    """
    loop = asyncio.get_running_loop()
    stop_event = threading.Event()
    threads = []
    for i, src in enumerate(sources):
        cam_name = _norm_name(i + 1)
        t = threading.Thread(target=_capture_worker, args=(src, cam_name, px_to_m, min_area, max_disappeared, detect_interval, loop, stop_event), daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        stop_event.set()
        # give threads a moment to exit
        for t in threads:
            t.join(timeout=1.0)
        raise
