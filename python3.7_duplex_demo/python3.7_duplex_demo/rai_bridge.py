"""Optional bounded background forwarding; never changes duplex responses."""

import json
import hashlib
import os
import queue
import threading
import time
import urllib.request
import uuid
from collections import OrderedDict


class RAIBridge:
    def __init__(self, timing=None):
        self.timing = timing
        self.enabled = os.environ.get("RAI_WAVE_BRIDGE") == "1"
        self.source = os.environ.get("RAI_GESTURE_SOURCE", "user")
        self.pending = queue.Queue(maxsize=4)
        self.closed = threading.Event()
        self.session = uuid.uuid4().hex
        self.seen = set()
        self.locked_turns = set()
        self.turns = OrderedDict()
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True).start()

    def pair(self, turn_id, user_text=None, prefix=None, response_id=None):
        if not turn_id:
            print("[RAI pair] missing turn id; skipped", flush=True)
            return
        row = self.turns.setdefault(str(turn_id), {"created": time.monotonic()})
        while len(self.turns) > 256:
            self.turns.popitem(last=False)
        if user_text is not None:
            row["user_text"] = user_text
        if prefix is not None:
            row.update(prefix=prefix, response_id=response_id)
        if not row.get("user_text") or not row.get("prefix"):
            return
        if time.monotonic() - row["created"] > 15:
            print("[RAI pair] stale pair; skipped", flush=True)
            return
        if self.timing is not None:
            self.timing.mark(row["response_id"], "gesture_submit")
        self.submit(row["prefix"], turn_id, {
            "session_id": self.session, "turn_id": str(turn_id),
            "user_text": row["user_text"], "robot_reply_prefix": row["prefix"],
            "response_id": row["response_id"],
        })

    def submit(self, text, item_id, extra=None):
        if not self.enabled or self.closed.is_set() or not isinstance(text, str) or not text.strip():
            return
        key = self.session + ":" + (str(item_id) if item_id else uuid.uuid4().hex)
        if extra and self.source == "combined":
            key += ":" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key in self.seen:
            return
        if len(self.seen) >= 10000:
            return
        self.seen.add(key)
        try:
            data = {"id": key, "text": text}
            if extra:
                data.update(extra)
            self.pending.put_nowait((time.monotonic(), data))
        except queue.Full:
            print("[RAI bridge] busy: utterance dropped", flush=True)

    def _worker(self):
        # This bridge only targets loopback. Never send local robot decisions
        # through an ambient HTTP proxy (which can mask a stopped RAI as 502).
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not self.closed.is_set():
            try:
                created, data = self.pending.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.closed.is_set() or time.monotonic() - created > 5:
                continue
            turn = (data.get("session_id"), data.get("turn_id"))
            combined = self.source == "combined" and all(turn)
            if combined and turn in self.locked_turns:
                continue
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:8765/utterance",
                    data=json.dumps(data).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with opener.open(req, timeout=30) as response:
                    body = response.read().decode("utf-8")
                    if combined:
                        result = json.loads(body)
                        outputs = result.get("tool_results", [])
                        no_action = (result.get("status") == "processed"
                                     and len(outputs) == 1
                                     and json.loads(outputs[0]).get("status") == "no_action")
                        if not no_action:
                            self.locked_turns.add(turn)
                    if self.timing is not None:
                        self.timing.mark(data.get("response_id") or data["id"].split(":", 1)[1], "rai_return")
                    print("[RAI bridge]", body, flush=True)
            except Exception as exc:
                if combined:
                    self.locked_turns.add(turn)
                print("[RAI bridge] unavailable/failed; no retry:", str(exc), flush=True)

    def close(self):
        self.closed.set()
