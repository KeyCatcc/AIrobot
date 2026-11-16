"""Local, authenticated handoff between head following and body gestures.

The head-follow process owns this control server.  It is deliberately not a
ROS publisher itself: requests are acknowledged by the camera loop only after
that loop has closed its ``/joint_states`` publisher.
"""

from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Literal


RequestKind = Literal["release", "resume"]


class HeadFollowControlServer:
    """Thread-safe request/ack server for a single local head-follow owner."""

    def __init__(self, *, host: str, port: int, token: str) -> None:
        if len(token) < 24:
            raise RuntimeError("head-follow control token must contain at least 24 characters")
        self._token = token
        self._lock = threading.Lock()
        self._pending_kind: RequestKind | None = None
        self._pending_request_id: str | None = None
        self._ack = threading.Event()
        self._ack_body: dict | None = None

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

            def respond(self, code: int, body: dict) -> None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + outer._token):
                    return self.respond(401, {"error": "unauthorized"})
                if self.path != "/status":
                    return self.respond(404, {"error": "not_found"})
                return self.respond(200, outer.status())

            def do_POST(self) -> None:  # noqa: N802
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + outer._token):
                    return self.respond(401, {"error": "unauthorized"})
                if self.path not in ("/release", "/resume"):
                    return self.respond(404, {"error": "not_found"})
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        raise ValueError("invalid size")
                    body = json.loads(self.rfile.read(size))
                    request_id = body.get("request_id")
                    if not isinstance(request_id, str) or not 0 < len(request_id) <= 128:
                        raise ValueError("invalid request_id")
                except (ValueError, AttributeError, json.JSONDecodeError):
                    return self.respond(400, {"error": "invalid_request"})
                result = outer.request(self.path.lstrip("/"), request_id)
                return self.respond(200 if result.get("status") != "timeout" else 503, result)

        self._server = HTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="head-follow-control", daemon=True)
        self._thread.start()
        print(f"Head-follow control: http://{host}:{port}", flush=True)

    def request(self, kind: RequestKind, request_id: str) -> dict:
        with self._lock:
            if self._pending_kind is not None:
                return {"status": "busy", "request_id": request_id}
            self._pending_kind = kind
            self._pending_request_id = request_id
            self._ack_body = None
            self._ack.clear()
        if not self._ack.wait(timeout=3.0):
            with self._lock:
                if self._pending_request_id == request_id:
                    self._pending_kind = None
                    self._pending_request_id = None
            return {"status": "timeout", "request_id": request_id}
        with self._lock:
            return dict(self._ack_body or {"status": "failed", "request_id": request_id})

    def take_request(self) -> tuple[RequestKind, str] | None:
        with self._lock:
            if self._pending_kind is None or self._pending_request_id is None:
                return None
            return self._pending_kind, self._pending_request_id

    def acknowledge(self, *, kind: RequestKind, request_id: str, status: str, **extra) -> None:
        with self._lock:
            if self._pending_kind != kind or self._pending_request_id != request_id:
                return
            self._ack_body = {"status": status, "request_id": request_id, **extra}
            self._pending_kind = None
            self._pending_request_id = None
            self._ack.set()

    def status(self) -> dict:
        with self._lock:
            return {
                "status": "ok",
                "pending": self._pending_kind,
                "request_id": self._pending_request_id,
            }

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)
