"""Authenticated polling client for the duplex loopback interaction status."""

from __future__ import annotations

import json
import os
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor


class DuplexStatusClient:
    def __init__(self, *, base_url: str, token_env: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = os.environ.get(token_env, "")
        if len(self.token) < 24:
            raise RuntimeError(
                f"{token_env} must contain the local duplex-control token (at least 24 characters)"
            )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="duplex-status")

    def submit(self) -> Future[dict]:
        def run() -> dict:
            request = urllib.request.Request(
                self.base_url + "/status",
                headers={"Authorization": "Bearer " + self.token},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") != "ok" or payload.get("mode") not in {
                "idle", "listening", "speaking", "conversing"
            }:
                raise RuntimeError("invalid duplex status payload")
            return payload

        return self._executor.submit(run)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
