"""Camera-only dry run for conservative face following and greeting triggers.

It uses OpenCV YuNet DNN face detection by default. It never imports rclpy,
starts RAI, calls an action service, publishes ROS, speaks, or controls a
motor. Haar is retained only as an explicit diagnostic fallback.

Example (headless robot):
    python3 scripts/face_follow_camera_dry_run.py --camera-index 0

Example (desktop preview available):
    python3 scripts/face_follow_camera_dry_run.py --camera-index 0 --preview
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.head_follow import HeadFollowConfig, HeadFollowController  # noqa: E402
from modules.face_detection import build_face_detector  # noqa: E402
from modules.interaction_state import InteractionConfig, InteractionStateMachine  # noqa: E402


@dataclass
class Face:
    track_id: int
    bbox: tuple[float, float, float, float]
    confidence: float = 0.9


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1, ix2, iy2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection
    return intersection / union if union > 0 else 0.0


class SimpleFaceTracker:
    """Small IoU tracker sufficient for dry-run target stability tests."""

    def __init__(self, *, match_iou: float = 0.30, max_missing_frames: int = 10) -> None:
        self.match_iou = match_iou
        self.max_missing_frames = max_missing_frames
        self._next_id = 1
        self._tracks: dict[int, tuple[tuple[float, float, float, float], int]] = {}

    def update(self, boxes: Iterable[tuple[float, float, float, float]]) -> list[Face]:
        boxes = list(boxes)
        available_ids = set(self._tracks)
        assigned: list[Face] = []
        for box in sorted(boxes, key=lambda item: (item[2] - item[0]) * (item[3] - item[1]), reverse=True):
            candidates = [(iou(box, self._tracks[track_id][0]), track_id) for track_id in available_ids]
            score, track_id = max(candidates, default=(0.0, -1))
            if score < self.match_iou:
                track_id = self._next_id
                self._next_id += 1
            else:
                available_ids.remove(track_id)
            self._tracks[track_id] = (box, 0)
            assigned.append(Face(track_id, box))

        seen_ids = {face.track_id for face in assigned}
        for track_id, (old_box, missing) in list(self._tracks.items()):
            if track_id in seen_ids:
                continue
            if missing + 1 > self.max_missing_frames:
                del self._tracks[track_id]
            else:
                self._tracks[track_id] = (old_box, missing + 1)
        return assigned


def load_configs(head_path: Path, interaction_path: Path, *, simulate_greeting: bool) -> tuple[HeadFollowConfig, InteractionConfig]:
    head_data = yaml.safe_load(head_path.read_text(encoding="utf-8"))["head_follow"]
    interaction_data = yaml.safe_load(interaction_path.read_text(encoding="utf-8"))["interaction"]
    head = HeadFollowConfig(
        min_face_area_ratio=float(head_data["min_face_area_ratio"]),
        min_confidence=float(head_data["min_confidence"]),
        stable_frames_required=int(head_data["stable_frames_required"]),
        deadband_x_ratio=float(head_data["deadband_x_ratio"]),
        deadband_y_ratio=float(head_data["deadband_y_ratio"]),
        max_yaw_rad=math.radians(float(head_data["max_yaw_deg"])),
        max_pitch_rad=math.radians(float(head_data["max_pitch_deg"])),
        max_speed_rad_per_sec=math.radians(float(head_data["max_speed_deg_per_sec"])),
        return_delay_sec=float(head_data["return_delay_sec"]),
        return_speed_rad_per_sec=math.radians(float(head_data["return_speed_deg_per_sec"])),
        yaw_sign=float(head_data["yaw_sign"]),
        pitch_sign=float(head_data["pitch_sign"]),
        enable_pitch=bool(head_data["enable_pitch"]),
    )
    interaction = InteractionConfig(
        greeting_enabled=simulate_greeting or bool(interaction_data["greeting_enabled"]),
        stable_before_attention_sec=float(interaction_data["stable_before_attention_sec"]),
        greeting_cooldown_sec=float(interaction_data["greeting_cooldown_sec"]),
        global_greeting_cooldown_sec=float(interaction_data["global_greeting_cooldown_sec"]),
        post_conversation_greeting_suppress_sec=float(interaction_data["post_conversation_greeting_suppress_sec"]),
        face_loss_to_idle_sec=float(interaction_data["face_loss_to_idle_sec"]),
    )
    return head, interaction


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--preview", action="store_true", help="Open an OpenCV window; requires a desktop display")
    parser.add_argument("--duration-sec", type=float, default=0.0, help="0 means run until Ctrl+C")
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
    parser.add_argument("--simulate-greeting", action="store_true", help="Emit greet_visitor event only; never executes it")
    parser.add_argument("--head-config", type=Path, default=ROOT / "configs/head_follow.yaml")
    parser.add_argument("--interaction-config", type=Path, default=ROOT / "configs/interaction.yaml")
    args = parser.parse_args()

    head_cfg, interaction_cfg = load_configs(args.head_config, args.interaction_config,
                                               simulate_greeting=args.simulate_greeting)
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

    tracker = SimpleFaceTracker()
    head = HeadFollowController(head_cfg)
    interaction = InteractionStateMachine(interaction_cfg)
    started_at = time.monotonic()
    last_report_at = 0.0
    last_event: tuple[object, ...] | None = None
    print("Camera Dry-run started; no ROS, speech, RAI, action service, or motor control.")
    print(f"camera=/dev/video{args.camera_index} detector={args.detector} "
          f"greeting_simulation={interaction_cfg.greeting_enabled} pitch_enabled={head_cfg.enable_pitch}")
    try:
        while True:
            ok, frame = capture.read()
            now = time.monotonic()
            if not ok or frame is None:
                print("[camera] frame read failed; keeping controller state")
                time.sleep(0.05)
                continue
            height, width = frame.shape[:2]
            detected = detector.detect(frame)
            boxes = [(x1, y1, x2, y2) for x1, y1, x2, y2, _confidence in detected]
            faces = tracker.update(boxes)
            target = head.update(faces, frame_width=width, frame_height=height, now=now)
            stable_id = str(target.target_id) if target.mode == "tracking" and target.target_id is not None else None
            decision = interaction.observe_target(stable_id, now=now)

            # Keep the last *emitted* event.  A no-event camera frame must not
            # reset this key, otherwise greeting_suppressed would be printed
            # again on every other frame while a visitor remains in view.
            if decision.event is not None:
                event_key = (decision.state, decision.event, decision.target_id, decision.reason)
                if event_key != last_event:
                    print(f"[interaction] state={decision.state} event={decision.event} target={decision.target_id} reason={decision.reason}")
                last_event = event_key
            if now - last_report_at >= 0.5:
                print("[follow] "
                      f"faces={len(faces)} target={target.target_id} mode={target.mode} "
                      f"yaw_target_deg={math.degrees(target.yaw_rad):.2f} "
                      f"pitch_target_deg={math.degrees(target.pitch_rad):.2f}")
                last_report_at = now

            if args.preview:
                for face in faces:
                    x1, y1, x2, y2 = map(int, face.bbox)
                    color = (0, 255, 0) if face.track_id == target.target_id else (80, 80, 255)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(frame, f"id={face.track_id}", (x1, max(20, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                cv2.putText(frame, f"{target.mode}: yaw={math.degrees(target.yaw_rad):.1f} deg", (15, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
                cv2.putText(frame, f"state={decision.state}", (15, 56),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
                cv2.imshow("face-follow-dry-run", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
            if args.duration_sec > 0 and now - started_at >= args.duration_sec:
                break
    except KeyboardInterrupt:
        print("Camera Dry-run stopped by user")
    finally:
        capture.release()
        if args.preview:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
