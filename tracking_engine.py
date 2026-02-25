import time
from kalman import Kalman2D


class DeviceTrack:
    def __init__(self, mac, x, y, ts):
        self.mac = mac
        self.filter = Kalman2D(x, y)
        self.last_seen = ts
        self.first_seen = ts

    def step(self, x, y, ts):
        dt = ts - self.last_seen
        self.last_seen = ts
        state = self.filter.update(x, y)

        return {
            "mac": self.mac,
            "x": state["x"],
            "y": state["y"],
            "vx": state["vx"],
            "vy": state["vy"],
            "age": ts - self.first_seen,
            "dt": dt
        }


class TrackingEngine:
    def __init__(self, timeout=15):
        self.devices = {}
        self.timeout = timeout

    # -----------------------------
    # RSSI → pseudo spatial mapping
    # -----------------------------
    def _rssi_to_xy(self, mac, rssi):
        """
        Converts signal strength into pseudo-coordinates.

        X axis = distance estimate
        Y axis = stable device lane (hash)
        """
        x = (rssi + 100) / 12
        y = (hash(mac) % 200) / 20
        return x, y

    # -----------------------------
    # Main update entrypoint
    # -----------------------------
    def update(self, mac, rssi, ts=None):
        if ts is None:
            ts = time.time()

        x, y = self._rssi_to_xy(mac, rssi)

        if mac not in self.devices:
            self.devices[mac] = DeviceTrack(mac, x, y, ts)

        return self.devices[mac].step(x, y, ts)

    # -----------------------------
    # Remove stale devices
    # -----------------------------
    def prune(self, now=None):
        if now is None:
            now = time.time()

        dead = [
            mac for mac, dev in self.devices.items()
            if now - dev.last_seen > self.timeout
        ]

        for mac in dead:
            del self.devices[mac]

        return dead

    # -----------------------------
    # Get active tracks
    # -----------------------------
    def active(self):
        return list(self.devices.keys())

    # -----------------------------
    # Hard delete
    # -----------------------------
    def remove(self, mac):
        self.devices.pop(mac, None)

