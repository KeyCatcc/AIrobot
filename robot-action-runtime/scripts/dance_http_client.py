"""Manually request one enabled dance from the local action service."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
import uuid


def request(url: str, token: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": "Bearer " + token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url + path, data=data, headers=headers), timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dance")
    parser.add_argument("--speed", type=float, help="Override speed for this playback only")
    parser.add_argument("--url", default=os.environ.get("ROBOT_ACTION_URL", "http://127.0.0.1:8766"))
    parser.add_argument("--status", action="store_true", help="Only print the latest execution state")
    parser.add_argument("--cancel", action="store_true", help="Cancel dance and smoothly return to zero")
    args = parser.parse_args()
    token = os.environ.get("ROBOT_ACTION_TOKEN", "")
    if len(token) < 24:
        parser.error("Set ROBOT_ACTION_TOKEN before making a dance request")
    base_url = args.url.rstrip("/")
    if args.cancel:
        print(json.dumps(request(base_url, token, "/cancel", {}), ensure_ascii=False))
        return 0
    if args.status:
        print(json.dumps(request(base_url, token, "/execution"), ensure_ascii=False))
        return 0
    if not args.dance:
        parser.error("--dance is required unless --status is used")
    catalog = request(base_url, token, "/catalog")
    payload = {
        "request_id": "manual-dance-" + uuid.uuid4().hex,
        "dance": args.dance,
        "catalog_version": catalog["dance_catalog_version"],
    }
    if args.speed is not None:
        payload["speed"] = args.speed
    print(json.dumps(request(base_url, token, "/dances", payload), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
