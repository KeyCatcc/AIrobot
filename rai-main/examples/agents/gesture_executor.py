"""Wall-clock simulation only. No ROS, audio or hardware outputs."""

import threading
import time
import uuid


class GestureExecutor:
    def __init__(self):
        self.lock = threading.Lock()
        self.worker = None
        self.stop = threading.Event()
        self.state = {"status": "idle", "hardware_executed": False}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def submit(self, gesture, frames, fps, home_frames=24):
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                return {"status": "skipped_busy", "hardware_executed": False}
            self.stop = threading.Event()
            self.state = {"status": "started", "execution_id": uuid.uuid4().hex,
                          "gesture": gesture, "frame": 0,
                          "frames": len(frames), "hardware_executed": False}
            result = dict(self.state)
            result["status"] = "accepted"
            self.worker = threading.Thread(
                target=self._run, args=(frames, fps, home_frames), daemon=True)
            self.worker.start()
            return result

    def _run(self, frames, fps, home_frames):
        start = time.monotonic()
        phase = None
        for i, pose in enumerate(frames):
            if self.stop.wait(max(0, start + (i + 1) / fps - time.monotonic())):
                self._update(status="cancelled")
                return
            current = "returning_home" if i >= len(frames) - home_frames else "executing"
            self._update(status=current, frame=i + 1, simulated_pose=list(pose))
            if current != phase:
                print("[EXECUTOR]", current, flush=True)
                phase = current
        self._update(status="completed")

    def _update(self, **values):
        with self.lock:
            self.state.update(values)
            if values.get("status") in ("completed", "cancelled"):
                print("[EXECUTOR]", values["status"], flush=True)

    def cancel(self):
        self.stop.set()

    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(timeout=2)


EXECUTOR = GestureExecutor()
