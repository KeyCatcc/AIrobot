"""Deterministic dry-run checks for the conservative face-follow controller.

No camera, ROS topic, or motor is used.  Run from robot-action-runtime:
    python scripts/test_head_follow_controller.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.head_follow import HeadFollowConfig, HeadFollowController  # noqa: E402


@dataclass
class Face:
    track_id: int
    bbox: tuple[float, float, float, float]
    confidence: float = 0.95


FRAME = (640, 480)


def frame(controller: HeadFollowController, t: float, *faces: Face, reserved: bool = False):
    return controller.update(faces, frame_width=FRAME[0], frame_height=FRAME[1], now=t,
                             head_channel_reserved=reserved)


def main() -> int:
    cfg = HeadFollowConfig(stable_frames_required=3, return_delay_sec=1.0,
                           max_speed_rad_per_sec=1.0, return_speed_rad_per_sec=1.0)
    controller = HeadFollowController(cfg)
    centered = Face(1, (270, 180, 370, 300))
    right = Face(1, (500, 180, 620, 300))
    larger_left = Face(2, (0, 100, 200, 380))

    checks = []
    checks.append(("稳定前不跟随", frame(controller, 0.0, right).mode == "holding"))
    checks.append(("稳定前第二帧不跟随", frame(controller, 0.1, right).mode == "holding"))
    tracked = frame(controller, 0.2, right)
    checks.append(("第三帧开始跟随", tracked.mode == "tracking" and tracked.yaw_rad > 0))
    yaw_after_right = tracked.yaw_rad

    deadband = frame(controller, 0.3, centered)
    checks.append(("中心死区不强制回正", deadband.mode == "tracking" and deadband.yaw_rad >= yaw_after_right))

    selection = frame(controller, 0.4, larger_left)
    checks.append(("临时更大人脸不抢占当前目标", selection.mode == "holding" and selection.target_id == 1))
    frame(controller, 1.3)  # active target loss exceeds the configured 1s grace period
    frame(controller, 1.4, larger_left)
    frame(controller, 1.5, larger_left)
    chosen = frame(controller, 1.6, larger_left)
    checks.append(("原目标丢失后最大稳定人脸成为目标", chosen.mode == "tracking" and chosen.target_id == 2))

    paused = frame(controller, 1.7, larger_left, reserved=True)
    checks.append(("头部动作占用时暂停", paused.mode == "paused"))
    held = frame(controller, 2.0)
    checks.append(("短暂丢脸保持", held.mode == "holding"))
    returning = frame(controller, 2.8)
    checks.append(("超时后回正", returning.mode in {"returning", "neutral"}))

    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    passed_count = sum(passed for _, passed in checks)
    print(f"{passed_count}/{len(checks)}; no camera, ROS, or motor used")
    return 0 if passed_count == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
