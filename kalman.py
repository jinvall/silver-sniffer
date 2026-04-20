import numpy as np
import time

class Kalman2D:
    def __init__(self, x, y):
        self.x = np.array([[x],[y],[0],[0]], float)

        self.P = np.eye(4) * 500
        self.F = np.eye(4)
        self.H = np.array([
            [1,0,0,0],
            [0,1,0,0]
        ])

        self.R = np.eye(2) * 5
        self.Q = np.eye(4) * 0.01

        self.last = time.time()

    def update(self, mx, my):
        now = time.time()
        dt = now - self.last
        self.last = now

        # state transition
        self.F = np.array([
            [1,0,dt,0],
            [0,1,0,dt],
            [0,0,1,0],
            [0,0,0,1]
        ])

        # predict
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q

        # measurement
        z = np.array([[mx],[my]])

        # update
        y = z - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)

        self.x = self.x + K @ y
        I = np.eye(4)
        self.P = (I - K @ self.H) @ self.P

        return {
            "x": float(self.x[0].item()),
            "y": float(self.x[1].item()),
            "vx": float(self.x[2].item()),
            "vy": float(self.x[3].item()),

        }

