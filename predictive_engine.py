import time

class PredictiveTracker:
    def __init__(self):
        self.history = {}  # mac -> list of (ts, distance_m)

    def update(self, mac, distance_m, ts):
        if mac not in self.history:
            self.history[mac] = []

        self.history[mac].append((ts, distance_m))
        if len(self.history[mac]) > 10:
            self.history[mac].pop(0)

        return self.predict(mac)

    def predict(self, mac, seconds_ahead=3):
        data = self.history.get(mac, [])
        if len(data) < 2:
            return {"pred_distance": distance_m if data else None, "velocity": 0}

        # simple linear velocity estimate
        dt = data[-1][0] - data[-2][0]
        if dt == 0:
            vel = 0
        else:
            vel = data[-1][1] - data[-2][1] / dt

        pred_distance = data[-1][1] + vel * seconds_ahead
        return {"pred_distance": pred_distance, "velocity": vel}
