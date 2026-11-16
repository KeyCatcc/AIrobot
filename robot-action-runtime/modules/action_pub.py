"""Publish one body trajectory and its timed finger events in one ROS session."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Sequence, Tuple

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import String
from ymbot_motor_control.msg import MotorTrajectory

from modules.motor_pub import JOINTS, TOPIC_DEFAULT, _message


FINGER_TOPIC_DEFAULT = "/finger_command"


def play_action_on_robot(
    body_command: Any | None,
    finger_events: Sequence[Tuple[float, Dict[str, Any]]],
    shm_cfg: Dict[str, Any],
) -> None:
    """Publish available body/finger channels; an absent channel is simply skipped."""
    os.environ["ROS_DOMAIN_ID"] = "10"
    os.environ["ROS_LOCALHOST_ONLY"] = "0"
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = Node("custom_action_pub")
    body_pub = None
    body_msg = None
    body_duration = 0.0
    if body_command is not None:
        frames = np.asarray(body_command.targets_deg, dtype=np.float64)
        if frames.ndim == 1:
            frames = frames.reshape(1, -1)
        if frames.shape[1] != JOINTS:
            raise ValueError(f"电机角宽度应为 {JOINTS}, 收到 {frames.shape[1]}")
        topic = str(shm_cfg.get("topic") or TOPIC_DEFAULT)
        qos = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL, depth=1)
        body_pub = node.create_publisher(MotorTrajectory, topic, qos)
        fps = max(float(body_command.fps or 30.0), 1e-6)
        start_ramp = float(shm_cfg.get("start_ramp_sec") or 0.0)
        end_ramp = float(shm_cfg.get("end_ramp_sec") or 0.0)
        body_msg = _message(
            MotorTrajectory, frames, fps=fps,
            rate_hz=float(shm_cfg.get("rate_hz") or 50.0),
            home=float(body_command.home_deg or 180.0), start_ramp=start_ramp,
            end_ramp=end_ramp, loop=False, cancel=False,
        )
        body_duration = start_ramp + frames.shape[0] / fps + end_ramp
    finger_pub = node.create_publisher(String, FINGER_TOPIC_DEFAULT, 10) if finger_events else None
    try:
        deadline = time.perf_counter() + 30.0
        while True:
            body_ready = body_pub is None or body_pub.get_subscription_count() > 0
            finger_ready = finger_pub is None or finger_pub.get_subscription_count() > 0
            if body_ready and finger_ready:
                break
            if time.perf_counter() > deadline:
                missing = []
                if not body_ready:
                    missing.append("身体轨迹")
                if not finger_ready:
                    missing.append("手指命令")
                raise TimeoutError(f"30 秒内没有订阅者: {', '.join(missing)}")
            rclpy.spin_once(node, timeout_sec=0.1)
        start = time.perf_counter()
        if body_pub is not None:
            body_pub.publish(body_msg)
        next_event = 0
        total_duration = max(body_duration, max((offset for offset, _ in finger_events), default=0.0) + 0.2)
        while time.perf_counter() - start < total_duration:
            elapsed = time.perf_counter() - start
            while next_event < len(finger_events) and finger_events[next_event][0] <= elapsed:
                message = String()
                message.data = json.dumps(finger_events[next_event][1], ensure_ascii=False, separators=(",", ":"))
                finger_pub.publish(message)
                next_event += 1
            rclpy.spin_once(node, timeout_sec=0.01)
            time.sleep(0.01)
    finally:
        node.destroy_node()
        rclpy.shutdown()
