"""Non-blocking client for the duplex loopback proactive-greeting endpoint."""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass


@dataclass(frozen=True)
class GreetingSpeechResult:
    request_id: str
    response: dict


class GreetingSpeechDispatcher:
    """Submit fixed greeting text without sharing a duplex/audio implementation."""

    def __init__(self, *, base_url: str, token_env: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = os.environ.get(token_env, "")
        if len(self.token) < 24:
            raise RuntimeError(
                f"{token_env} must contain the local duplex-control token (at least 24 characters)"
            )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="greeting-speech")

    def submit(self, *, text: str) -> Future[GreetingSpeechResult]:
        request_id = "proactive-speech-" + uuid.uuid4().hex

        def run() -> GreetingSpeechResult:
            payload = json.dumps({"request_id": request_id, "text": text}, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                self.base_url + "/greet",
                data=payload,
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Content-Type": "application/json",
                },
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=3.0) as response:
                body = json.loads(response.read().decode("utf-8"))
            return GreetingSpeechResult(request_id=request_id, response=body)

        return self._executor.submit(run)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
