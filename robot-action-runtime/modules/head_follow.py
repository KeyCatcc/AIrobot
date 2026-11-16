"""Conservative face-following decision core.

This module is deliberately ROS- and camera-independent.  It converts face
bounding boxes into a *safe desired head target*; a later adapter is solely
responsible for publishing that target to hardware.

Do not expose this loop as an LLM tool: it is a continuous control loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import radians
from typing import Iterable, Optional, Protocol, Tuple


class FaceLike(Protocol):
    """Minimum contract accepted from a visual tracker."""

    track_id: int
    bbox: Tuple[float, float, float, float]
    confidence: float


@dataclass(frozen=True)
class HeadFollowConfig:
    """All angles are expressed in physical robot-joint radians."""

    min_face_area_ratio: float = 0.015
    min_confidence: float = 0.55
    stable_frames_required: int = 8
    deadband_x_ratio: float = 0.10
    deadband_y_ratio: float = 0.12
    max_yaw_rad: float = radians(12.0)
    max_pitch_rad: float = radians(6.0)
    max_speed_rad_per_sec: float = radians(6.0)
    return_delay_sec: float = 2.0
    return_speed_rad_per_sec: float = radians(3.0)
    yaw_sign: float = 1.0
    pitch_sign: float = 1.0
    enable_pitch: bool = False


@dataclass(frozen=True)
class HeadTarget:
    yaw_rad: float
    pitch_rad: float
    target_id: Optional[int]
    mode: str  # tracking / holding / returning / neutral / paused
    reason: str


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class HeadFollowController:
    """Select one stable face and generate rate-limited head targets.

    ``update`` must be called with a monotonic timestamp.  The controller
    never sends a motor/ROS command; its output is safe to inspect in dry-run
    before an actuator adapter is connected.
    """

    def __init__(self, config: HeadFollowConfig | None = None) -> None:
        self.config = config or HeadFollowConfig()
        self._candidate_id: Optional[int] = None
        self._candidate_frames = 0
        self._active_id: Optional[int] = None
        self._last_seen_at: Optional[float] = None
        self._last_at: Optional[float] = None
        self._yaw_rad = 0.0
        self._pitch_rad = 0.0

    @staticmethod
    def _area_ratio(face: FaceLike, width: int, height: int) -> float:
        x1, y1, x2, y2 = face.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1) / float(width * height)

    def _select_largest(self, faces: Iterable[FaceLike], width: int, height: int) -> Optional[FaceLike]:
        usable = [
            face for face in faces
            if face.confidence >= self.config.min_confidence
            and self._area_ratio(face, width, height) >= self.config.min_face_area_ratio
        ]
        return max(usable, key=lambda face: self._area_ratio(face, width, height), default=None)

    @staticmethod
    def _approach(current: float, goal: float, speed: float, dt: float) -> float:
        delta = goal - current
        maximum_step = max(0.0, speed * dt)
        if abs(delta) <= maximum_step:
            return goal
        return current + maximum_step if delta > 0 else current - maximum_step

    def _move(self, yaw_goal: float, pitch_goal: float, speed: float, dt: float) -> None:
        self._yaw_rad = self._approach(self._yaw_rad, yaw_goal, speed, dt)
        self._pitch_rad = self._approach(self._pitch_rad, pitch_goal, speed, dt)

    def _face_goal(self, face: FaceLike, width: int, height: int) -> tuple[float, float]:
        x1, y1, x2, y2 = face.bbox
        center_x = (x1 + x2) / (2.0 * width)
        center_y = (y1 + y2) / (2.0 * height)
        offset_x = center_x - 0.5
        offset_y = 0.5 - center_y

        if abs(offset_x) <= self.config.deadband_x_ratio:
            yaw_goal = self._yaw_rad
        else:
            normalized = _clamp(offset_x / 0.5, -1.0, 1.0)
            yaw_goal = normalized * self.config.max_yaw_rad * self.config.yaw_sign

        if not self.config.enable_pitch or abs(offset_y) <= self.config.deadband_y_ratio:
            pitch_goal = self._pitch_rad
        else:
            normalized = _clamp(offset_y / 0.5, -1.0, 1.0)
            pitch_goal = normalized * self.config.max_pitch_rad * self.config.pitch_sign
        return yaw_goal, pitch_goal

    def update(
        self,
        faces: Iterable[FaceLike],
        *,
        frame_width: int,
        frame_height: int,
        now: float,
        head_channel_reserved: bool = False,
    ) -> HeadTarget:
        """Return a safe target for this frame.

        ``head_channel_reserved`` must be true while a nod, shake, or other
        head gesture is executing.  In that period this controller holds its
        current target and does not fight the gesture.
        """
        if frame_width <= 0 or frame_height <= 0:
            raise ValueError("frame dimensions must be positive")
        if self._last_at is not None and now < self._last_at:
            raise ValueError("timestamps must be monotonic")
        dt = 0.0 if self._last_at is None else now - self._last_at
        self._last_at = now

        if head_channel_reserved:
            return HeadTarget(self._yaw_rad, self._pitch_rad, self._active_id, "paused", "head_channel_reserved")

        # Once a stable face has become the active target, do not let a
        # transient larger detection steal it or reset its loss timer.  A new
        # target is considered only after the active target has genuinely
        # timed out and the controller has started returning home.
        faces = list(faces)
        if self._active_id is not None:
            face = next((item for item in faces if item.track_id == self._active_id), None)
        else:
            face = self._select_largest(faces, frame_width, frame_height)
        if face is not None:
            self._last_seen_at = now
            if face.track_id == self._candidate_id:
                self._candidate_frames += 1
            else:
                self._candidate_id = face.track_id
                self._candidate_frames = 1

            if self._candidate_frames >= self.config.stable_frames_required:
                self._active_id = face.track_id

            if self._active_id == face.track_id:
                yaw_goal, pitch_goal = self._face_goal(face, frame_width, frame_height)
                self._move(yaw_goal, pitch_goal, self.config.max_speed_rad_per_sec, dt)
                return HeadTarget(self._yaw_rad, self._pitch_rad, self._active_id, "tracking", "stable_target")

            return HeadTarget(self._yaw_rad, self._pitch_rad, None, "holding", "awaiting_stable_target")

        self._candidate_id = None
        self._candidate_frames = 0
        if self._last_seen_at is None or now - self._last_seen_at < self.config.return_delay_sec:
            return HeadTarget(self._yaw_rad, self._pitch_rad, self._active_id, "holding", "face_loss_grace_period")

        self._active_id = None
        self._move(0.0, 0.0, self.config.return_speed_rad_per_sec, dt)
        mode = "neutral" if self._yaw_rad == 0.0 and self._pitch_rad == 0.0 else "returning"
        return HeadTarget(self._yaw_rad, self._pitch_rad, None, mode, "face_lost_return_home")
