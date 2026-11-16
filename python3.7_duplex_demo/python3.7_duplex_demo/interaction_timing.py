"""Local monotonic timestamps; never compare clocks across processes."""

import json
import threading
import time
from collections import OrderedDict


class InteractionTiming:
    def __init__(self):
        self.rows = OrderedDict()
        self.lock = threading.Lock()

    def mark(self, response_id, event):
        if not response_id:
            return
        with self.lock:
            key = str(response_id)
            row = self.rows.setdefault(key, {})
            if event in row:
                return
            row[event] = time.monotonic()
            while len(self.rows) > 256:
                self.rows.popitem(last=False)
            result = {"response_id": key, "event": event,
                      "local_monotonic_s": round(row[event], 6)}
            for a, b, label in [
                ("text_first", "gesture_submit", "text_first_to_submit_ms"),
                ("gesture_submit", "rai_return", "submit_to_rai_return_ms"),
                ("gesture_submit", "play_first_write", "submit_to_play_ms"),
                ("text_first", "play_first_write", "text_first_to_play_ms"),
                ("text_done", "play_first_write", "text_done_to_play_ms"),
                ("text_done", "rai_return", "text_done_to_rai_return_ms"),
                ("play_first_write", "rai_return", "play_to_rai_return_ms"),
            ]:
                if a in row and b in row:
                    result[label] = round((row[b] - row[a]) * 1000, 1)
            print("[TIMING] " + json.dumps(result), flush=True)
