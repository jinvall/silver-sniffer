import time
from collections import defaultdict, deque
import math

class DeviceBehavior:
    def __init__(self):
        self.first_seen = time.time()
        self.last_seen = self.first_seen

        self.rssi = deque(maxlen=50)
        self.channels = deque(maxlen=100)
        self.timestamps = deque(maxlen=100)

    def update(self, rssi, channel, ts):
        self.last_seen = ts
        self.rssi.append(rssi)
        self.channels.append(channel)
        self.timestamps.append(ts)

    # -------------------------

    def dwell_time(self):
        return self.last_seen - self.first_seen

    # -------------------------

    def packet_rate(self):
        if len(self.timestamps) < 2:
            return 0
        dt = self.timestamps[-1] - self.timestamps[0]
        return len(self.timestamps)/dt if dt else 0

    # -------------------------

    def rssi_std(self):
        if len(self.rssi) < 2:
            return 0
        m = sum(self.rssi)/len(self.rssi)
        return math.sqrt(sum((x-m)**2 for x in self.rssi)/len(self.rssi))

    # -------------------------

    def channel_entropy(self):
        if not self.channels:
            return 0
        counts = defaultdict(int)
        for c in self.channels:
            counts[c]+=1
        total=len(self.channels)
        return -sum((n/total)*math.log2(n/total) for n in counts.values())

    # -------------------------

    def burstiness(self):
        if len(self.timestamps) < 3:
            return 0
        intervals = [
            self.timestamps[i]-self.timestamps[i-1]
            for i in range(1,len(self.timestamps))
        ]
        mean=sum(intervals)/len(intervals)
        std=(sum((x-mean)**2 for x in intervals)/len(intervals))**0.5
        return std/mean if mean else 0


# =======================================================

class BehaviorEngine:
    def __init__(self):
        self.devices = {}

    def update(self, mac, rssi, channel, ts):
        if mac not in self.devices:
            self.devices[mac]=DeviceBehavior()

        d=self.devices[mac]
        d.update(rssi,channel,ts)

        return {
            "mac": mac,
            "dwell": d.dwell_time(),
            "rate": d.packet_rate(),
            "rssi_std": d.rssi_std(),
            "channel_entropy": d.channel_entropy(),
            "burstiness": d.burstiness()
        }
