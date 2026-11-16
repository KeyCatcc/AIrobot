"""Authenticated, non-blocking dispatch of the fixed proactive greeting action.

This module is intentionally small and does not know about cameras, ROS or
LLMs.  The action HTTP service remains the final authority for its catalog and
for whether a request is dry-run or physical execution.
"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass


@dataclass(frozen=True)
class GreetingActionResult:
    request_id: str
    response: dict


class GreetingActionDispatcher:
    """One-request dispatcher so face-follow control never blocks on HTTP."""

    def __init__(self, *, base_url: str, token_env: str, action: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.action = action
        self.token = os.environ.get(token_env, "")
        if len(self.token) < 24:
            raise RuntimeError(
                f"{token_env} must contain the local action-service token (at least 24 characters)"
            )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="greeting-action")
        self._catalog_version: str | None = None

    def _request(self, path: str, *, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        # Robot-local endpoints must not accidentally traverse a desktop proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=3.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def verify_catalog(self) -> dict:
        catalog = self._request("/catalog")
        available = {item.get("name") for item in catalog.get("actions", []) if item.get("enabled")}
        if self.action not in available:
            raise RuntimeError(f"Greeting action is not enabled in robot catalog: {self.action}")
        version = catalog.get("catalog_version")
        if not isinstance(version, str) or not version:
            raise RuntimeError("Robot action service returned no catalog_version")
        self._catalog_version = version
        return catalog

    def submit(self, *, target_id: str | None) -> Future[GreetingActionResult]:
        if self._catalog_version is None:
            raise RuntimeError("verify_catalog() must succeed before dispatch")
        request_id = "proactive-greeting-" + uuid.uuid4().hex

        def run() -> GreetingActionResult:
            response = self._request(
                "/actions",
                payload={
                    "request_id": request_id,
                    "action": self.action,
                    "catalog_version": self._catalog_version,
                },
            )
            return GreetingActionResult(request_id=request_id, response=response)

        return self._executor.submit(run)

    def submit_execution_status(self) -> Future[dict]:
        """Read the action-service execution state without blocking camera control."""
        return self._executor.submit(lambda: self._request("/execution"))

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
