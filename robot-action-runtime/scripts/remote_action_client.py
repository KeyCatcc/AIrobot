"""Manual HTTP client; no retry. Token comes from the environment."""
import argparse
import json
import os
import urllib.request
import uuid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://172.16.20.64:8766")
    p.add_argument("--status", action="store_true")
    args = p.parse_args()
    token = os.environ["ROBOT_ACTION_TOKEN"]
    body = None if args.status else json.dumps({
        "request_id": uuid.uuid4().hex, "action": "挥手/招手"}).encode()
    request = urllib.request.Request(
        args.url.rstrip("/") + ("/execution" if args.status else "/actions"),
        data=body, headers={"Authorization": "Bearer " + token,
                            "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        print(response.read().decode())


if __name__ == "__main__":
    main()
