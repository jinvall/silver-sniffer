import matplotlib.pyplot as plt
import time
from collections import defaultdict, deque

COLORS = {
    "phone": "lime",
    "iot": "orange",
    "unknown": "gray"
}

class LiveMap:
    def __init__(self):
        plt.ion()
        self.fig, self.ax = plt.subplots()
        self.trails = defaultdict(lambda: deque(maxlen=25))
        self.last = {}

        self.ax.set_title("RF Movement Map")
        self.ax.set_xlim(0, 10)
        self.ax.set_ylim(0, 10)
        self.ax.grid(True)

    def update(self, mac, dtype, x, y):
        self.trails[mac].append((x, y))
        self.last[mac] = (dtype, x, y)

    def draw(self):
        self.ax.clear()
        self.ax.set_xlim(0, 10)
        self.ax.set_ylim(0, 10)
        self.ax.grid(True)

        for mac, (dtype, x, y) in self.last.items():
            color = COLORS.get(dtype, "white")

            # draw trail
            trail = list(self.trails[mac])
            if len(trail) > 1:
                xs, ys = zip(*trail)
                self.ax.plot(xs, ys, alpha=0.4)

                # prediction vector
                dx = xs[-1] - xs[-2]
                dy = ys[-1] - ys[-2]
                self.ax.arrow(x, y, dx, dy,
                              head_width=0.15,
                              alpha=0.6,
                              color=color)

            # draw device
            self.ax.scatter(x, y, c=color, s=80)
            self.ax.text(x+0.05, y+0.05, mac[-5:], fontsize=8)

        plt.pause(0.01)

