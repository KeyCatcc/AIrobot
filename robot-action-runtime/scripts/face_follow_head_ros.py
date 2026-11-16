"""Conservative camera face-following publisher for the neck yaw joint.

This is intentionally separate from ``face_follow_camera_dry_run.py``.  ROS
publication occurs only with --publish-neck and an explicit interactive
confirmation.  It publishes *only* neck_yaw_joint; the verified bridge keeps
all omitted joints at its cached value.

Safety assumptions before --publish-neck:
  - robot starts in neutral posture;
  - bridge was started in neutral posture and has no unsafe cached arm values;
  - no other /joint_states publisher is active;
  - operator has clear space and an external stop procedure.

``--simulate-greeting`` only exercises the interaction-state transition.  It
prints a proposed greeting and enters the normal per-visitor cooldown; it does
not speak, call RAI, contact the action service, or move either arm.

When ``--publish-neck`` and ``--dispatch-greeting-action`` are both selected,
the runtime owns their handoff: it returns the head toward neutral and closes
its neck publisher before the action service starts its full-body trajectory.
It recreates the neck publisher only after the action service reports that the
trajectory process is no longer running.
"""

from __future__ import annotations

import argparse
import os
import math
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from face_follow_camera_dry_run import SimpleFaceTracker, load_configs  # noqa: E402
from modules.face_detection import build_face_detector  # noqa: E402
from modules.duplex_status import DuplexStatusClient  # noqa: E402
from modules.greeting_action import GreetingActionDispatcher  # noqa: E402
from modules.greeting_speech import GreetingSpeechDispatcher  # noqa: E402
from modules.head_follow import HeadFollowController  # noqa: E402
from modules.head_follow_control import HeadFollowControlServer  # noqa: E402
from modules.interaction_state import InteractionStateMachine  # noqa: E402


class NeckPublisher:
    """Minimal ROS publisher.  rclpy is imported only in publish mode."""

    def __init__(self, *, rate_hz: float) -> None:
        import rclpy
        from sensor_msgs.msg import JointState

        self.rclpy = rclpy
        self.JointState = JointState
        rclpy.init()
        self.node = rclpy.create_node("face_follow_neck_yaw")
        existing_publishers = self.node.count_publishers("/joint_states")
        if existing_publishers:
            self.node.destroy_node()
            rclpy.shutdown()
            raise RuntimeError(f"Refusing to publish: /joint_states already has {existing_publishers} publisher(s)")
        self.pub = self.node.create_publisher(JointState, "/joint_states", 10)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and self.pub.get_subscription_count() == 0:
            rclpy.spin_once(self.node, timeout_sec=0.1)
        if self.pub.get_subscription_count() == 0:
            self.close()
            raise RuntimeError("No /joint_states subscriber; bridge is not ready")
        self.period = 1.0 / rate_hz
        self.last_publish_at = 0.0

    def publish(self, yaw_rad: float, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_publish_at < self.period:
            return
        msg = self.JointState()
        msg.header.stamp = self.node.get_clock().now().to_msg()
        msg.name = ["neck_yaw_joint"]
        msg.position = [yaw_rad]
        self.pub.publish(msg)
        self.rclpy.spin_once(self.node, timeout_sec=0)
        self.last_publish_at = now

    def return_home(self, start_yaw_rad: float, *, seconds: float = 1.0) -> None:
        steps = max(1, int(seconds / 0.1))
        for step in range(1, steps + 1):
            self.publish(start_yaw_rad * (1.0 - step / steps), force=True)
            time.sleep(seconds / steps)

    def close(self) -> None:
        self.node.destroy_node()
        self.rclpy.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--duration-sec", type=float, default=0.0, help="0 means run until Ctrl+C")
    parser.add_argument("--publish-neck", action="store_true", help="Actually publish neck_yaw_joint after confirmation")
    parser.add_argument("--simulate-greeting", action="store_true",
                        help="Print one proactive greeting event per stable visitor; never speaks or moves arms")
    parser.add_argument("--greeting-text", default="您好，欢迎来到展厅。",
                        help="Text recorded with --simulate-greeting only")
    parser.add_argument("--dispatch-greeting-action", action="store_true",
                        help="Request the fixed welcome action from the local action service after explicit catalog verification")
    parser.add_argument("--greeting-action", default="欢迎您",
                        help="Enabled robot catalog action used for proactive greeting")
    parser.add_argument("--greeting-action-url", default=os.environ.get("ROBOT_ACTION_URL", "http://127.0.0.1:8766"),
                        help="Local action service base URL")
    parser.add_argument("--greeting-token-env", default="ROBOT_ACTION_TOKEN",
                        help="Environment variable holding the action-service bearer token")
    parser.add_argument("--dispatch-greeting-speech", action="store_true",
                        help="Request fixed greeting speech from the local duplex loopback endpoint")
    parser.add_argument("--greeting-speech-url", default=os.environ.get("DUPLEX_GREETING_URL", "http://127.0.0.1:8767"),
                        help="Local duplex proactive-greeting base URL")
    parser.add_argument("--greeting-speech-token-env", default="DUPLEX_CONTROL_TOKEN",
                        help="Environment variable holding the duplex-control bearer token")
    parser.add_argument("--duplex-status-url", default=os.environ.get("DUPLEX_GREETING_URL", "http://127.0.0.1:8767"),
                        help="Local duplex interaction-status base URL")
    parser.add_argument("--duplex-status-token-env", default="DUPLEX_CONTROL_TOKEN",
                        help="Environment variable holding the duplex-status bearer token")
    parser.add_argument("--duplex-status-poll-sec", type=float, default=0.5,
                        help="How often to poll duplex idle/listening/speaking state when greeting speech is enabled")
    parser.add_argument("--gate-on-duplex-status", action="store_true",
                        help="Use the duplex idle/listening/speaking gate even in greeting simulation mode")
    parser.add_argument("--detector", choices=("yunet", "haar"), default="yunet",
                        help="YuNet is the default production detector; Haar is diagnostic only")
    parser.add_argument("--face-model", type=Path,
                        default=ROOT / "models/face_detection_yunet_2023mar.onnx",
                        help="YuNet ONNX model path")
    parser.add_argument("--score-threshold", type=float, default=0.75,
                        help="YuNet confidence threshold")
    parser.add_argument("--min-neighbors", type=int, default=5,
                        help="Haar-only strictness")
    parser.add_argument("--min-face-size", type=int, default=60,
                        help="Haar-only minimum face width/height in pixels")
    parser.add_argument("--max-yaw-deg", type=float, default=20.0,
                        help="Additional hard cap for ROS publish mode; calibrated live range is up to 20 degrees")
    parser.add_argument("--publish-rate-hz", type=float, default=5.0)
    parser.add_argument("--head-action-handoff-sec", type=float, default=0.5,
                        help="Seconds used to return neck toward neutral before a full-body action takes /joint_states")
    parser.add_argument("--head-control-host", default="127.0.0.1",
                        help="Loopback host for action-service head-follow handoff")
    parser.add_argument("--head-control-port", type=int, default=8768,
                        help="Loopback port for action-service head-follow handoff")
    parser.add_argument("--head-control-token-env", default="ROBOT_ACTION_TOKEN",
                        help="Bearer-token environment variable for head-follow handoff")
    parser.add_argument("--head-config", type=Path, default=ROOT / "configs/head_follow.yaml")
    parser.add_argument("--interaction-config", type=Path, default=ROOT / "configs/interaction.yaml")
    args = parser.parse_args()
    if not 0 < args.max_yaw_deg <= 20.0:
        parser.error("calibrated live max-yaw-deg must be within (0, 20.0]")
    if not 0 < args.publish_rate_hz <= 10.0:
        parser.error("publish-rate-hz must be within (0, 10]")
    if not 0.2 <= args.head_action_handoff_sec <= 2.0:
        parser.error("head-action-handoff-sec must be within [0.2, 2.0]")
    if not 0.2 <= args.duplex_status_poll_sec <= 5.0:
        parser.error("duplex-status-poll-sec must be within [0.2, 5]")
    if args.simulate_greeting and (args.dispatch_greeting_action or args.dispatch_greeting_speech):
        parser.error("--simulate-greeting cannot be combined with real greeting dispatch")

    head_cfg, interaction_cfg = load_configs(args.head_config, args.interaction_config,
                                               simulate_greeting=(args.simulate_greeting or args.dispatch_greeting_action
                                                                  or args.dispatch_greeting_speech))
    detector = build_face_detector(
        args.detector,
        model_path=args.face_model,
        score_threshold=args.score_threshold,
        min_neighbors=args.min_neighbors,
        min_face_size=args.min_face_size,
    )
    capture = cv2.VideoCapture(args.camera_index, cv2.CAP_V4L2)
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open /dev/video{args.camera_index}")

    publisher = None
    head_control = None
    greeting_dispatcher = None
    greeting_speech_dispatcher = None
    duplex_status_client = None
    duplex_status_future = None
    duplex_status_poll_after = 0.0
    duplex_mode = "unavailable"
    use_duplex_status_gate = args.dispatch_greeting_speech or args.gate_on_duplex_status
    # Before the first successful status read, block greeting conservatively
    # but do not pretend a real conversation occurred.  Otherwise the
    # post-conversation cooldown would suppress the first genuine visitor.
    duplex_conversation_active = False
    greeting_futures = {}
    action_status_future = None
    action_execution_id = None
    action_poll_after = 0.0
    head_released_for_action = False
    action_service_handoff = False
    external_head_reserved = False
    restore_neck_requested = False
    if args.publish_neck:
        print("LIVE NECK PUBLISH MODE: only neck_yaw_joint, maximum %.1f degrees." % args.max_yaw_deg)
        print("Confirm neutral posture, no other /joint_states publisher, clear workspace and external stop.")
        if input("Type PUBLISH_NECK to continue: ").strip() != "PUBLISH_NECK":
            capture.release()
            print("Skipped")
            return 0
        publisher = NeckPublisher(rate_hz=args.publish_rate_hz)
        head_control = HeadFollowControlServer(
            host=args.head_control_host,
            port=args.head_control_port,
            token=os.environ.get(args.head_control_token_env, ""),
        )

    if args.dispatch_greeting_action:
        greeting_dispatcher = GreetingActionDispatcher(
            base_url=args.greeting_action_url,
            token_env=args.greeting_token_env,
            action=args.greeting_action,
        )
        catalog = greeting_dispatcher.verify_catalog()
        action_service_handoff = bool(catalog.get("head_follow_handoff_enabled"))
        print("Proactive greeting action ready: action=%r service_execute_enabled=%s" % (
            args.greeting_action, catalog.get("service_execute_enabled")
        ))
        print(f"Proactive greeting head handoff handled by action service: {action_service_handoff}")
    if args.dispatch_greeting_speech:
        greeting_speech_dispatcher = GreetingSpeechDispatcher(
            base_url=args.greeting_speech_url,
            token_env=args.greeting_speech_token_env,
        )
        print(f"Proactive greeting speech ready: {args.greeting_speech_url.rstrip('/')}/greet")
    if use_duplex_status_gate:
        duplex_status_client = DuplexStatusClient(
            base_url=args.duplex_status_url,
            token_env=args.duplex_status_token_env,
        )
        print(f"Duplex status gate ready: {args.duplex_status_url.rstrip('/')}/status")

    tracker = SimpleFaceTracker()
    head = HeadFollowController(head_cfg)
    interaction = InteractionStateMachine(interaction_cfg)
    started_at = time.monotonic()
    last_report_at = 0.0
    last_yaw = 0.0
    cap_rad = math.radians(args.max_yaw_deg)
    if args.simulate_greeting:
        greeting_mode = "simulation only"
    elif args.dispatch_greeting_action and args.dispatch_greeting_speech:
        greeting_mode = "action + fixed speech dispatch"
    elif args.dispatch_greeting_action:
        greeting_mode = "action dispatch"
    elif args.dispatch_greeting_speech:
        greeting_mode = "fixed speech dispatch"
    else:
        greeting_mode = "disabled"
    unused = "RAI remains unused" if args.dispatch_greeting_speech else "RAI and speech remain unused"
    print(f"Face follow started; detector={args.detector}; proactive greeting={greeting_mode}; {unused}.")
    try:
        while True:
            ok, frame = capture.read()
            now = time.monotonic()
            if head_control is not None:
                control_request = head_control.take_request()
                if control_request is not None:
                    kind, request_id = control_request
                    if kind == "release":
                        print("[head arbitration] external gesture requested; returning neck and releasing /joint_states")
                        if publisher is not None:
                            publisher.return_home(last_yaw, seconds=args.head_action_handoff_sec)
                            publisher.close()
                            publisher = None
                        last_yaw = 0.0
                        head = HeadFollowController(head_cfg)
                        external_head_reserved = True
                        restore_neck_requested = False
                        head_control.acknowledge(kind=kind, request_id=request_id, status="released")
                    else:
                        # Do not recreate a publisher inside the HTTP handler:
                        # ROS/DDS may need a short moment to observe the action
                        # publisher's process exit.  Retry from this camera loop.
                        external_head_reserved = False
                        restore_neck_requested = args.publish_neck
                        head_control.acknowledge(kind=kind, request_id=request_id, status="resuming")
                        print("[head arbitration] external gesture completed; neck publisher will be restored")
            if restore_neck_requested and publisher is None:
                try:
                    publisher = NeckPublisher(rate_hz=args.publish_rate_hz)
                    head = HeadFollowController(head_cfg)
                    restore_neck_requested = False
                    print("[head arbitration] neck publisher restored after external gesture")
                except RuntimeError as exc:
                    # Keep the channel released and retry.  This is expected
                    # briefly while DDS removes the finished action publisher.
                    if int(now * 2) % 4 == 0:
                        print(f"[head arbitration] waiting to restore neck publisher: {exc}")
            if duplex_status_future is not None and duplex_status_future.done():
                try:
                    status_payload = duplex_status_future.result()
                    new_mode = status_payload["mode"]
                    if new_mode != duplex_mode:
                        print(f"[duplex status] mode={new_mode}")
                    duplex_mode = new_mode
                    duplex_conversation_active = new_mode != "idle"
                except Exception as exc:
                    if duplex_mode != "unavailable":
                        print(f"[duplex status] unavailable; greeting safely suppressed: {type(exc).__name__}")
                    duplex_mode = "unavailable"
                    duplex_conversation_active = True
                duplex_status_future = None
                duplex_status_poll_after = now + args.duplex_status_poll_sec
            if (duplex_status_client is not None and duplex_status_future is None and
                    now >= duplex_status_poll_after):
                duplex_status_future = duplex_status_client.submit()
            if action_status_future is not None and action_status_future.done():
                try:
                    execution = action_status_future.result()
                    status = execution.get("status")
                    reported_id = execution.get("execution_id")
                    if reported_id != action_execution_id:
                        print(f"[head arbitration] ignoring unrelated action status: {execution}")
                        action_poll_after = now + 0.25
                    elif status == "running":
                        action_poll_after = now + 0.25
                    else:
                        print(f"[head arbitration] action execution completed: {execution}")
                        action_status_future = None
                        action_execution_id = None
                        if head_released_for_action:
                            # The physical neck was returned to neutral before
                            # action takeover.  Forget the old follow target so
                            # resuming cannot publish a stale non-zero yaw.
                            head = HeadFollowController(head_cfg)
                            publisher = NeckPublisher(rate_hz=args.publish_rate_hz)
                            head_released_for_action = False
                            print("[head arbitration] neck publisher restored after action")
                        finished = interaction.greeting_finished(now=now)
                        print(f"[greeting] target={finished.target_id} event={finished.event} "
                              f"reason={finished.reason}; cooldown started")
                except Exception as exc:
                    print(f"[head arbitration] status poll failed: {type(exc).__name__}: {exc}")
                    action_poll_after = now + 0.5
                if action_execution_id is not None:
                    action_status_future = None
            if (action_execution_id is not None and action_status_future is None and
                    now >= action_poll_after and greeting_dispatcher is not None):
                action_status_future = greeting_dispatcher.submit_execution_status()
            if greeting_futures and all(future.done() for future in greeting_futures.values()):
                action_accepted = False
                for channel, future in greeting_futures.items():
                    try:
                        result = future.result()
                        print(f"[greeting {channel}] request_id={result.request_id} response={result.response}")
                        if channel == "action" and result.response.get("status") == "accepted":
                            action_execution_id = result.response.get("execution_id")
                            action_accepted = isinstance(action_execution_id, str) and bool(action_execution_id)
                    except Exception as exc:
                        print(f"[greeting {channel}] dispatch failed: {type(exc).__name__}: {exc}")
                greeting_futures = {}
                if action_accepted:
                    action_poll_after = now
                    print(f"[head arbitration] waiting for action execution_id={action_execution_id}")
                else:
                    if head_released_for_action:
                        head = HeadFollowController(head_cfg)
                        publisher = NeckPublisher(rate_hz=args.publish_rate_hz)
                        head_released_for_action = False
                        print("[head arbitration] neck publisher restored after action dispatch failure")
                    finished = interaction.greeting_finished(now=now)
                    print(f"[greeting] target={finished.target_id} event={finished.event} "
                          f"reason={finished.reason}; cooldown started")
            if not ok or frame is None:
                time.sleep(0.05)
                continue
            height, width = frame.shape[:2]
            detected = detector.detect(frame)
            boxes = [(x1, y1, x2, y2) for x1, y1, x2, y2, _confidence in detected]
            faces = tracker.update(boxes)
            # In the reception idle state, light visual following is useful.
            # Once duplex is listening, speaking or in a recent conversation,
            # hold the current head target rather than continuously tracking.
            # An unavailable status endpoint is also treated as non-idle.
            head_follow_paused = duplex_status_client is not None and duplex_mode != "idle"
            target = head.update(
                faces,
                frame_width=width,
                frame_height=height,
                now=now,
                head_channel_reserved=head_follow_paused or external_head_reserved,
            )
            stable_id = str(target.target_id) if target.mode == "tracking" and target.target_id is not None else None
            duplex_gate_busy = use_duplex_status_gate and duplex_mode != "idle"
            action_busy = bool(greeting_futures) or action_execution_id is not None or duplex_gate_busy
            decision = interaction.observe_target(
                stable_id,
                now=now,
                conversation_active=duplex_conversation_active,
                action_busy=action_busy,
            )
            if decision.event == "greet_visitor":
                if greeting_dispatcher is not None:
                    # Older action services do not expose the local head-control
                    # protocol, so preserve the original in-process fallback.
                    if publisher is not None and not action_service_handoff:
                        print("[head arbitration] returning neck toward neutral and releasing /joint_states for action")
                        publisher.return_home(last_yaw, seconds=args.head_action_handoff_sec)
                        publisher.close()
                        publisher = None
                        last_yaw = 0.0
                        head_released_for_action = True
                    greeting_futures["action"] = greeting_dispatcher.submit(
                        target_id=decision.target_id,
                    )
                    print(f"[greeting action] target={decision.target_id} action={args.greeting_action!r} "
                          "requested")
                if greeting_speech_dispatcher is not None:
                    greeting_futures["speech"] = greeting_speech_dispatcher.submit(text=args.greeting_text)
                    print(f"[greeting speech] target={decision.target_id} text={args.greeting_text!r} requested")
                else:
                    if greeting_dispatcher is None:
                        print(f"[greeting simulation] target={decision.target_id} text={args.greeting_text!r} "
                              "(no speech, RAI, action-service request, or arm motion)")
                        finished = interaction.greeting_finished(now=now)
                        print(f"[greeting simulation] target={finished.target_id} event={finished.event} "
                              f"reason={finished.reason}; cooldown started")
            elif decision.event in ("target_detected", "target_stable", "target_lost"):
                print(f"[interaction] state={decision.state} event={decision.event} "
                      f"target={decision.target_id} reason={decision.reason}")
            last_yaw = max(-cap_rad, min(cap_rad, target.yaw_rad))
            if publisher is not None:
                publisher.publish(last_yaw)
            if now - last_report_at >= 0.5:
                print(f"[neck-follow] faces={len(faces)} target={target.target_id} mode={target.mode} "
                      f"yaw_publish_deg={math.degrees(last_yaw):.2f}")
                last_report_at = now
            if args.duration_sec > 0 and now - started_at >= args.duration_sec:
                break
    except KeyboardInterrupt:
        print("Face follow stopped by user")
    finally:
        capture.release()
        if publisher is not None:
            try:
                publisher.return_home(last_yaw)
            finally:
                publisher.close()
            print("Neck yaw returned toward zero; physical completion is not confirmed")
        if greeting_dispatcher is not None:
            greeting_dispatcher.close()
        if greeting_speech_dispatcher is not None:
            greeting_speech_dispatcher.close()
        if duplex_status_client is not None:
            duplex_status_client.close()
        if head_control is not None:
            head_control.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
