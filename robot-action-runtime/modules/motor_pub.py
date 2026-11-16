'''
本地实机下发: 把一段电机绝对角发到话题, 不打开共享内存

机器人本体先启动 ymbot_eumotor_interface, 再启动 motor_target_bridge
'''

from __future__ import annotations

import os
import sys
import time
import numpy as np
from pathlib import Path
from typing import Any, Dict

import rclpy
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from ymbot_motor_control.msg import MotorTrajectory


TOPIC_DEFAULT = "/ymbot/motor_trajectory"
JOINTS = 14


def play_on_robot(command: Any, shm_cfg: Dict[str, Any], *, loop: bool = False) -> None:
    '''
    阻塞到这段动作播完, loop 为真时一直发, Ctrl+C 再发一帧 cancel, 让对端斜坡回中位
    '''
    frames = np.asarray(command.targets_deg, dtype=np.float64)
    if frames.ndim == 1:
        frames = frames.reshape(1, -1)
    if frames.shape[1] != JOINTS:
        raise ValueError(f"电机角宽度应为 {JOINTS}, 收到 {frames.shape[1]}")
    topic = str(shm_cfg.get("topic") or TOPIC_DEFAULT)
    rate_hz = float(shm_cfg.get("rate_hz") or 50.0)
    start_ramp = float(shm_cfg.get("start_ramp_sec") or 0.0)
    end_ramp = float(shm_cfg.get("end_ramp_sec") or 0.0)
    fps = max(float(command.fps or 30.0), 1e-6)
    home = float(command.home_deg or 180.0)

    qos = QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        depth=1,
    )

    # 必须在 rclpy.init 之前写入, DDS 用域名选端口, 用 LOCALHOST_ONLY 决定是否只走本机
    os.environ["ROS_DOMAIN_ID"] = "10"
    os.environ["ROS_LOCALHOST_ONLY"] = "0"
    domain = os.environ["ROS_DOMAIN_ID"]
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = Node("motor_trajectory_pub")
    pub = node.create_publisher(MotorTrajectory, topic, qos)

    sent = False
    try:
        print(
            f"[motor_pub] 等待订阅者 {topic}  ROS_DOMAIN_ID={domain}  ROS_LOCALHOST_ONLY=0 机器人终端需要出现: 已订阅 {topic}",
            flush=True,
        )
        deadline = time.perf_counter() + 30.0
        while pub.get_subscription_count() < 1:
            if time.perf_counter() > deadline:
                raise TimeoutError(f"30 秒内没有订阅者: {topic} ROS_DOMAIN_ID={domain} 请看机器人终端是否有 motor_sub 的“已订阅”, 两边的 ROS_DOMAIN_ID 要相同")
            rclpy.spin_once(node, timeout_sec=0.1)
        msg = _message(
            MotorTrajectory,
            frames,
            fps=fps,
            rate_hz=rate_hz,
            home=home,
            start_ramp=start_ramp,
            end_ramp=end_ramp,
            loop=loop,
            cancel=False,
        )
        pub.publish(msg)
        sent = True
        print(
            f"[motor_pub] {frames.shape[0]} 帧 -> {topic}  fps={fps:g}  rate={rate_hz:g}",
            flush=True,
        )
        hold = start_ramp + (frames.shape[0] / fps) + (0.0 if loop else end_ramp)
        if loop:
            print("[motor_pub] 循环播放, Ctrl+C 后通知对端回中位", flush=True)
        _wait(node, hold if not loop else None)
    except KeyboardInterrupt:
        if not sent:
            print("[motor_pub] 已中断，轨迹还没有发出", flush=True)
        elif rclpy.ok():
            try:
                cancel = MotorTrajectory()
                cancel.home_deg = home
                cancel.end_ramp_sec = end_ramp
                cancel.rate_hz = rate_hz
                cancel.cancel = True
                pub.publish(cancel)
                print("[motor_pub] 已请求对端斜坡回中位", flush=True)
                _wait(node, end_ramp + 0.5)
            except Exception as exc:
                print(f"[motor_pub] 取消未发出: {exc}", flush=True)
    finally:
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()


def _message(cls, frames, *, fps, rate_hz, home, start_ramp, end_ramp, loop, cancel):
    msg = cls()
    msg.fps = float(fps)
    msg.rate_hz = float(rate_hz)
    msg.home_deg = float(home)
    msg.start_ramp_sec = float(start_ramp)
    msg.end_ramp_sec = float(end_ramp)
    msg.loop = bool(loop)
    msg.cancel = bool(cancel)
    msg.targets_deg = frames.reshape(-1).astype(np.float64).tolist()
    return msg


def _wait(node, seconds) -> None:
    if seconds is None:
        rclpy.spin(node)
        return
    end = time.perf_counter() + max(float(seconds), 0.0)
    while rclpy.ok() and time.perf_counter() < end:
        rclpy.spin_once(node, timeout_sec=0.2)
