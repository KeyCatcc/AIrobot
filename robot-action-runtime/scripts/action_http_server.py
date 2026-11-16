"""Authenticated lab action endpoint. Default dry-run; no physical completion ACK."""
import argparse
import hmac
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import threading
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from action_catalog import load_catalog, DEFAULT_CATALOG
from dance_catalog import load_dance_catalog, DEFAULT_DANCE_CATALOG


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--bridge-map", required=True, type=Path)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    p.add_argument("--dance-catalog", type=Path, default=DEFAULT_DANCE_CATALOG)
    p.add_argument("--dance-player", type=Path,
                   default=Path(os.environ.get(
                       "DANCE_PLAYER_PATH",
                       "/home/cat/xmov_new/robot_control/ymbot_smpl_control/scripts/csv_joint_state_player.py",
                   )),
                   help="Verified CSV dance player; only needed when an enabled dance is registered")
    p.add_argument("--head-follow-control-url", default=os.environ.get("HEAD_FOLLOW_CONTROL_URL", ""),
                   help="Optional local head-follow handoff service, for example http://127.0.0.1:8768")
    p.add_argument("--head-follow-token-env", default="ROBOT_ACTION_TOKEN",
                   help="Bearer-token environment variable for --head-follow-control-url")
    args = p.parse_args()
    token = os.environ.get("ROBOT_ACTION_TOKEN", "")
    if len(token) < 24:
        p.error("Set ROBOT_ACTION_TOKEN to a random token of at least 24 characters")
    head_control_url = args.head_follow_control_url.rstrip("/")
    head_control_token = os.environ.get(args.head_follow_token_env, "")
    if head_control_url and len(head_control_token) < 24:
        p.error(f"{args.head_follow_token_env} must contain a token of at least 24 characters")
    args.bridge_map = args.bridge_map.resolve(strict=True)
    catalog, catalog_version = load_catalog(args.catalog)
    dance_catalog, dance_catalog_version = load_dance_catalog(args.dance_catalog)
    args.dance_player = args.dance_player.expanduser()
    if any(item["enabled"] for item in dance_catalog.values()) and not args.dance_player.is_file():
        raise FileNotFoundError("Enabled dance requires csv_joint_state_player.py: " + str(args.dance_player))
    if any(item["enabled"] for item in dance_catalog.values()):
        if "DANCE_CANCEL_PROTOCOL_V1" not in args.dance_player.read_text(encoding="utf-8"):
            raise RuntimeError("Update dance player before enabling dances: cancellation protocol missing")
    from test_custom_actions import GestureLibrary, DEFAULT_ACTIONS, DEFAULT_GESTURES
    library = GestureLibrary(DEFAULT_ACTIONS, DEFAULT_GESTURES)
    for name in catalog:
        if not library.has_motion(name):
            raise ValueError("Enabled action missing from body library: " + name)
    lock = threading.Lock()
    state = {"status": "idle", "physical_completion_confirmed": False}
    seen = {}
    running_process = None

    def head_follow_request(path, request_id):
        if not head_control_url:
            return {"status": "not_configured"}
        payload = json.dumps({"request_id": request_id}).encode("utf-8")
        request = urllib.request.Request(
            head_control_url + path,
            data=payload,
            headers={
                "Authorization": "Bearer " + head_control_token,
                "Content-Type": "application/json",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=4.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def run(argv, request_id, head_follow_released):
        nonlocal running_process
        try:
            with lock:
                running_process = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
                process = running_process
            returncode = process.wait()
            with lock:
                status = ("cancelled_returned_home" if state.get("cancel_requested") else
                          "publication_finished" if args.execute else "dry_run_completed") if returncode == 0 else "failed"
                state.update(status=status, returncode=returncode)
                running_process = None
        except Exception as exc:
            with lock:
                state.update(status="failed", error=type(exc).__name__)
        finally:
            if head_follow_released:
                try:
                    result = head_follow_request("/resume", request_id)
                    with lock:
                        state["head_follow_resume"] = result.get("status")
                    print("[head arbitration] resume result:", result, flush=True)
                except Exception as exc:
                    with lock:
                        state["head_follow_resume"] = "failed"
                        state["head_follow_resume_error"] = type(exc).__name__
                    print("[head arbitration] resume failed:", type(exc).__name__, flush=True)

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, body):
            payload = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def authorized(self):
            return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def do_GET(self):
            if not self.authorized():
                return self.respond(401, {"error": "unauthorized"})
            if self.path == "/catalog":
                return self.respond(200, {"catalog_version": catalog_version,
                                         "dance_catalog_version": dance_catalog_version,
                                         "service_execute_enabled": args.execute,
                                         "head_follow_handoff_enabled": bool(head_control_url),
                                         "actions": list(catalog.values()),
                                         "dances": list(dance_catalog.values())})
            if self.path != "/execution":
                return self.respond(404, {"error": "not_found"})
            with lock:
                body = dict(state)
            body["service_execute_enabled"] = args.execute
            self.respond(200, body)

        def do_POST(self):
            if not self.authorized():
                return self.respond(401, {"error": "unauthorized"})
            if self.path == "/cancel":
                with lock:
                    if state.get("status") != "running" or state.get("kind") != "dance":
                        return self.respond(409, {"error": "no_running_dance"})
                    if running_process is None or running_process.poll() is not None:
                        return self.respond(409, {"error": "player_not_ready"})
                    if not state.get("cancel_requested"):
                        running_process.send_signal(signal.SIGUSR1)
                        state["cancel_requested"] = True
                    return self.respond(200, dict(state, status="cancel_requested"))
            if self.path not in ("/actions", "/dances"):
                return self.respond(404, {"error": "not_found"})
            is_dance = self.path == "/dances"
            item_field = "dance" if is_dance else "action"
            active_catalog = dance_catalog if is_dance else catalog
            active_version = dance_catalog_version if is_dance else catalog_version
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("invalid size")
                data = json.loads(self.rfile.read(size))
                key = data.get("request_id")
                if not isinstance(key, str) or not 0 < len(key) <= 128:
                    raise ValueError("invalid request_id")
                if (data.get(item_field) not in active_catalog or
                        not {"request_id", item_field} <= set(data) <= ({"request_id", item_field, "catalog_version", "speed"} if is_dance else {"request_id", item_field, "catalog_version"})):
                    return self.respond(400, {"error": f"{item_field}_not_enabled_or_unexpected_fields"})
                if not active_catalog[data[item_field]].get("enabled"):
                    return self.respond(400, {"error": f"{item_field}_not_enabled"})
                if is_dance and "speed" in data:
                    if isinstance(data["speed"], bool) or not isinstance(data["speed"], (int, float)) or not 0.05 <= data["speed"] <= 1.0:
                        return self.respond(400, {"error": "speed_must_be_between_0.05_and_1"})
                if data.get("catalog_version", active_version) != active_version:
                    return self.respond(409, {"error": "catalog_version_mismatch",
                                              "hint": "reload the relevant client catalog"})
            except (ValueError, AttributeError):
                return self.respond(400, {"error": "invalid_request"})
            with lock:
                if key in seen:
                    body = {"status": "duplicate_ignored", "original": seen[key]}
                elif len(seen) >= 10000:
                    body = {"status": "request_limit_reached"}
                elif state["status"] == "running":
                    body = {"status": "skipped_busy"}
                    seen[key] = dict(body)
                elif is_dance and not args.execute:
                    # The existing CSV player is a ROS publisher, not a dry-run
                    # tool.  Never let the action service's dry-run mode cause
                    # accidental physical dance publication.
                    body = {"status": "dance_requires_execute_mode"}
                    seen[key] = dict(body)
                else:
                    execution_id = uuid.uuid4().hex
                    state.clear()
                    state.update(status="running", execution_id=execution_id,
                                 request_id=key, kind="dance" if is_dance else "action",
                                 hardware_command_requested=args.execute,
                                 physical_completion_confirmed=False)
                    if is_dance:
                        dance = dict(active_catalog[data[item_field]])
                        dance["speed"] = data.get("speed", dance["speed"])
                        state.update(dance=dance["name"], speed=dance["speed"])
                    else:
                        action = active_catalog[data[item_field]]
                        state.update(action=action["name"], amplitude=action["amplitude"])
                    body = dict(state, status="accepted")
                    seen[key] = dict(body)
                    if is_dance:
                        dance = dict(active_catalog[data[item_field]])
                        dance["speed"] = data.get("speed", dance["speed"])
                        argv = [sys.executable, str(args.dance_player), "--ros-args",
                                "-p", f"csv_path:={dance['csv_path']}",
                                "-p", f"fps:={dance['fps']}",
                                "-p", f"speed:={dance['speed']}",
                                "-p", "loop:=false",
                                "-p", f"play_audio:={'true' if dance['play_audio'] else 'false'}",
                                "-p", f"use_wall_clock_motion:={'true' if dance['use_wall_clock_motion'] else 'false'}",
                                "-p", f"motion_poll_hz:={dance['motion_poll_hz']}",
                                "-p", f"no_waist:={'true' if dance['no_waist'] else 'false'}",
                                "-p", "return_home:=true",
                                "-p", f"home_duration_sec:={dance['home_duration_sec']}"]
                        if dance.get("audio_path"):
                            argv += ["-p", f"audio_path:={dance['audio_path']}"]
                    else:
                        argv = [sys.executable, str(Path(__file__).with_name("play_jointstate.py")),
                                "--action", data["action"], "--amplitude", str(catalog[data["action"]]["amplitude"]),
                                "--catalog", str(args.catalog.resolve()),
                                "--allow-larger-amplitude", "--bridge-map", str(args.bridge_map)]
                        if args.execute:
                            argv += ["--execute", "--http-confirmed"]
                    head_follow_released = False
                    if head_control_url:
                        try:
                            handoff = head_follow_request("/release", key)
                            if handoff.get("status") != "released":
                                raise RuntimeError("head_follow_" + str(handoff.get("status")))
                            head_follow_released = True
                            state["head_follow_release"] = "released"
                            body["head_follow_release"] = "released"
                            seen[key] = dict(body)
                            print("[head arbitration] release confirmed:", handoff, flush=True)
                        except Exception as exc:
                            state.update(status="blocked_head_follow", error=type(exc).__name__)
                            body = dict(state)
                            seen[key] = dict(body)
                            return self.respond(503, body)
                    threading.Thread(target=run, args=(argv, key, head_follow_released), daemon=True).start()
            self.respond(200, body)

    server = HTTPServer((args.host, args.port), Handler)
    print(f"Action service {args.host}:{args.port}; execute={args.execute}; "
          f"head_follow_handoff={bool(head_control_url)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Service stopping; any already running hardware command is NOT cancelled", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
