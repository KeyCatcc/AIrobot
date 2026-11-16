"""Loopback-only authenticated ingress for one fixed proactive greeting.

The face/interaction runtime must not open its own realtime dialogue session
or audio device.  It asks the already-running duplex process to speak through
this small local control endpoint instead.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Awaitable, Callable


GreetingCallback = Callable[[str, str], Awaitable[dict]]
StatusCallback = Callable[[], dict]


class LocalGreetingControl:
    def __init__(self, *, token: str, callback: GreetingCallback, status_callback: StatusCallback) -> None:
        if len(token) < 24:
            raise ValueError("DUPLEX_CONTROL_TOKEN must be at least 24 characters")
        self._token = token
        self._callback = callback
        self._status_callback = status_callback

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, body = 500, {"status": "internal_error"}
        try:
            header_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3.0)
            lines = header_bytes.decode("iso-8859-1").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()
            if not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + self._token):
                status, body = 401, {"status": "unauthorized"}
            elif method == "GET" and path == "/status":
                status, body = 200, self._status_callback()
            elif method == "POST" and path == "/greet":
                content_length = int(headers.get("content-length", "0"))
                if content_length < 1 or content_length > 2048:
                    raise ValueError("invalid_content_length")
                payload = json.loads((await reader.readexactly(content_length)).decode("utf-8"))
                if set(payload) != {"request_id", "text"}:
                    raise ValueError("unexpected_fields")
                request_id, text = payload["request_id"], payload["text"]
                if (not isinstance(request_id, str) or not 0 < len(request_id) <= 128 or
                        not isinstance(text, str) or not 0 < len(text) <= 160):
                    raise ValueError("invalid_payload")
                status, body = 200, await self._callback(request_id, text)
            else:
                status, body = 404, {"status": "not_found"}
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            status, body = 400, {"status": "invalid_request", "reason": str(exc)}
        except Exception as exc:  # keep the control endpoint alive after one bad request
            status, body = 500, {"status": "internal_error", "reason": type(exc).__name__}
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        writer.write(
            (f"HTTP/1.1 {status} {'OK' if status == 200 else 'ERROR'}\r\n"
             "Content-Type: application/json; charset=utf-8\r\n"
             f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n").encode("ascii")
            + encoded
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()


async def start_local_greeting_control(
    *, port: int, token: str, callback: GreetingCallback, status_callback: StatusCallback
) -> asyncio.AbstractServer:
    control = LocalGreetingControl(token=token, callback=callback, status_callback=status_callback)
    return await asyncio.start_server(control.handle, host="127.0.0.1", port=port)
